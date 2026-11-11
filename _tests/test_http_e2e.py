"""实际启动 HTTP 服务，用同一客户端核对协议、材质图与参考插件行为。"""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

import pytest

from _scripts.native_fixture import fixture, renderer
from iris_relay import RelayAPI
from iris_relay.http_client import HttpRelayService

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    """启动本次独占的服务子进程，退出时仅清理自己创建的进程。"""
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    log_path = tmp_path_factory.mktemp("http") / "server.log"
    url = f"http://127.0.0.1:{port}"
    with log_path.open("wb") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "iris_relay.server",
                "--dictionaries",
                "dictionaries",
                "--port",
                str(port),
            ],
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            opener = build_opener(ProxyHandler({}))
            deadline = time.monotonic() + 30
            while True:
                assert process.poll() is None, log_path.read_text("utf-8", errors="replace")
                try:
                    with opener.open(url + "/health", timeout=1) as response:
                        assert response.status == 200
                    break
                except (URLError, TimeoutError):
                    assert time.monotonic() < deadline, "HTTP 服务未在限时内就绪"
                    time.sleep(0.05)
            yield url
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


def request(operation, **payload):
    """为端到端验证生成合法的统一请求。"""
    return {
        "api_version": "1",
        "request_id": "HTTP端到端",
        "operation": operation,
        "payload": payload,
    }


def test_real_http_matches_native_materialx_graph_and_failure(live_server):
    """真实网络往返保留贴图、连接及失败报告，业务错误不终止服务。"""
    api = RelayAPI.from_directory(ROOT / "dictionaries")
    remote = HttpRelayService(live_server)
    profiles = [json.loads(p.read_text("utf-8")) for p in (ROOT / "dictionaries").glob("*.json")]
    source_profile = next(p for p in profiles if p["renderer"]["id"] == "cycles")
    source = fixture(source_profile, "textures/中文贴图.png", normal_semantic="normal.tangent")
    original = json.dumps(source, ensure_ascii=False, sort_keys=True)
    export_request = request("materialx.export", material=source)
    exported = remote.handle(export_request)
    assert exported == api.handle(export_request)
    assert exported["result"]["document"]
    document = exported["result"]["document"]
    for target in profiles:
        conversion = request("materialx.convert", document=document, target=renderer(target))
        actual = remote.handle(conversion)
        assert actual == api.handle(conversion)
        material = actual["result"]["material"]
        assert material and material["links"] and material["textures"]
        assert next(iter(material["textures"].values()))["uri"] == "textures/中文贴图.png"
    invalid = request("unknown.operation")
    assert remote.handle(invalid) == api.handle(invalid)
    caps = request("capabilities")
    assert remote.handle(caps) == api.handle(caps)
    assert json.dumps(source, ensure_ascii=False, sort_keys=True) == original


def test_plugin_http_example_matches_python_and_process(tmp_path):
    """参考插件切换三种传输，目标结果和源保护一致。"""
    # 参考字典与真实字典独立，独立服务使用参考配置。
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    with (tmp_path / "reference-server.log").open("wb") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "iris_relay.server",
                "--dictionaries",
                "_examples/dictionaries",
                "--port",
                str(port),
            ],
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            opener = build_opener(ProxyHandler({}))
            deadline = time.monotonic() + 30
            while True:
                assert process.poll() is None
                try:
                    with opener.open(url + "/health", timeout=1):
                        break
                except (URLError, TimeoutError):
                    assert time.monotonic() < deadline
                    time.sleep(0.05)
            results = []
            for transport in ("python", "process", "http"):
                command = [sys.executable, "_examples/plugin_demo.py", "--transport", transport]
                if transport == "http":
                    command.extend(["--url", url])
                result = subprocess.run(
                    command,
                    cwd=ROOT,
                    capture_output=True,
                    timeout=60,
                    env=dict(os.environ, PYTHONUTF8="1"),
                    check=True,
                )
                summary = json.loads(result.stdout)
                assert summary.pop("传输方式") == transport
                assert summary["源材质保持不变"] is True
                assert summary["新材质"] is not None
                results.append(summary)
            assert results[0] == results[1] == results[2]
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
