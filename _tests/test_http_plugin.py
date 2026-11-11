"""HTTP 插件接入验证：显式选择服务、保留默认行为及失败时不创建材质。"""

import ast
import importlib
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from iris_relay.protocol import ValidationError
from plugins import core
from plugins.blender import controller as blender

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def http_stub(monkeypatch):
    class Service:
        def __init__(self, base_url, *, timeout=60):
            self.base_url = base_url
            self.timeout = timeout

        def handle(self, request):
            return {
                "api_version": "1",
                "request_id": request["request_id"],
                "status": "ok",
                "result": {"materialx": {"available": True, "version": "1.39.5"}},
                "error": None,
            }

    monkeypatch.setitem(
        sys.modules, "iris_relay.http_client", SimpleNamespace(HttpRelayService=Service)
    )
    return Service


@pytest.mark.parametrize("factory", [core.create_service, blender.create_service])
def test_explicit_http_uses_service_without_local_environment(
    factory, tmp_path, monkeypatch, http_stub
):
    monkeypatch.setenv("IRIS_RELAY_HTTP_URL", "http://127.0.0.1:8765")
    service = factory(tmp_path)
    assert isinstance(service, http_stub)
    assert service.base_url == "http://127.0.0.1:8765"


def test_default_common_service_still_requires_repository_environment(tmp_path, monkeypatch):
    monkeypatch.delenv("IRIS_RELAY_HTTP_URL", raising=False)
    with pytest.raises(ValidationError, match=".venv"):
        core.create_service(tmp_path)


@pytest.mark.parametrize("factory", [core.create_service, blender.create_service])
def test_blank_http_configuration_is_not_silent_fallback(factory, tmp_path, monkeypatch):
    monkeypatch.setenv("IRIS_RELAY_HTTP_URL", "   ")
    with pytest.raises(ValidationError, match="IRIS_RELAY_HTTP_URL"):
        factory(tmp_path)


def test_maya_and_c4d_share_common_service_factory():
    from plugins import cinema4d
    from plugins.maya import controller

    assert controller.create_service is core.create_service
    assert cinema4d.create_service is core.create_service


def test_http_connection_failure_does_not_select_local_service(tmp_path, monkeypatch, http_stub):
    def fail(request):
        raise ValidationError("HTTP 服务连接失败", "TRANSPORT_ERROR")

    monkeypatch.setenv("IRIS_RELAY_HTTP_URL", "http://127.0.0.1:8765")
    monkeypatch.setattr(http_stub, "handle", staticmethod(fail))
    with pytest.raises(ValidationError, match="HTTP 服务连接失败"):
        blender.create_service(tmp_path)


def test_failed_http_import_never_creates_material(tmp_path):
    path = tmp_path / "material.mtlx"
    path.write_text('<materialx version="1.39"/>', encoding="utf-8")

    class Adapter:
        def create_material(self, material):
            pytest.fail("连接失败不能创建或替换材质")

    class Unavailable:
        def handle(self, request):
            raise ValidationError("HTTP 服务连接失败", "TRANSPORT_ERROR")

    result = core.import_material(Adapter(), Unavailable(), path, {"id": "x", "version": "1"})
    assert result["created"] is None
    assert result["status"] == "error"
    assert result["issues"][0]["code"] == "TRANSPORT_ERROR"


def test_demo_http_transport_uses_shared_conversion_flow(monkeypatch, http_stub, capsys):
    demo = importlib.import_module("_examples.plugin_demo")
    captured = []

    def convert(adapter, service, target):
        captured.append(service)
        adapter.create_material({"name": "HTTP 新材质"})
        return SimpleNamespace(response={"status": "ok"}, created="new-material-1")

    monkeypatch.setattr(demo, "convert_material", convert)
    monkeypatch.setattr(
        sys, "argv", ["plugin_demo.py", "--transport", "http", "--url", "http://127.0.0.1:9876"]
    )
    assert demo.main() == 0
    assert captured[0].base_url == "http://127.0.0.1:9876"
    assert "HTTP 新材质" in capsys.readouterr().out


def test_common_service_and_maya_import_remain_python37_compatible():
    for relative in (
        "plugins/core.py",
        "plugins/maya/controller.py",
        "plugins/cinema4d/__init__.py",
    ):
        ast.parse((ROOT / relative).read_text("utf-8"), feature_version=(3, 7))


def test_host_controllers_import_without_core_or_third_party_dependencies():
    script = """
import sys
from plugins.blender import controller
from plugins.maya import controller
from plugins.cinema4d import create_service
assert not any(name in sys.modules for name in (
    'iris_relay.api', 'MaterialX', 'fastapi', 'uvicorn', 'bpy', 'maya.cmds', 'c4d'
))
"""
    result = subprocess.run(
        [sys.executable, "-S", "-c", script],
        cwd=ROOT,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
