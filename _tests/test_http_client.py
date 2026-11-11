"""轻量 HTTP 客户端的真实网络与宿主兼容性检查。"""

import ast
import json
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from iris_relay import http_client
from iris_relay.http_client import HttpRelayService
from iris_relay.protocol import ValidationError


def request():
    return {"api_version": "1", "request_id": "材质-1", "operation": "capabilities", "payload": {}}


def response(**changes):
    value = {
        "api_version": "1",
        "request_id": "材质-1",
        "status": "ok",
        "result": {"说明": "贴图与节点"},
        "error": None,
    }
    value.update(changes)
    return value


@pytest.fixture
def endpoint():
    state = {"body": response(), "status": 200, "type": "application/json", "calls": []}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            state["calls"].append((self.path, dict(self.headers), body))
            if state.get("disconnect"):
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            time.sleep(state.get("delay", 0))
            self.send_response(state["status"])
            self.send_header("Content-Type", state["type"])
            if state.get("encoding"):
                self.send_header("Content-Encoding", state["encoding"])
            if state.get("redirect"):
                self.send_header("Location", state["redirect"])
            content = state["body"]
            if not isinstance(content, bytes):
                content = json.dumps(content, ensure_ascii=False).encode("utf-8")
            if not state.get("omit_length"):
                self.send_header("Content-Length", str(state.get("length", len(content))))
            self.end_headers()
            try:
                self.wfile.write(content)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    worker.start()
    state["url"] = "http://127.0.0.1:" + str(server.server_port)
    yield state
    server.shutdown()
    server.server_close()
    worker.join(timeout=2)


def test_unicode_request_and_result_ignore_system_proxy(endpoint, monkeypatch):
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    monkeypatch.setenv("no_proxy", "")
    assert HttpRelayService(endpoint["url"] + "/").handle(request()) == response()
    path, headers, body = endpoint["calls"][0]
    assert path == "/v1/handle"
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    assert "Authorization" not in headers
    assert json.loads(body) == request()
    assert "材质".encode() in body


@pytest.mark.parametrize("status", ["error", "warning"])
def test_business_diagnostics_are_preserved(endpoint, status):
    endpoint["body"] = response(
        status=status,
        error={"code": "MATERIAL_REJECTED", "message": "节点不支持"} if status == "error" else None,
        result={"report": {"issues": [{"code": "NODE_UNSUPPORTED"}]}},
    )
    assert HttpRelayService(endpoint["url"]).handle(request()) == endpoint["body"]


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "file:///tmp/a",
        "ftp://localhost",
        "http://u:p@host",
        "http://host/path",
        "http://host?x=1",
        "http://host#x",
        "http://host:99999",
        "http://",
        "http://ho\nst",
        " http://host",
        "http://[broken",
        "http://host?",
        "http://host#",
    ],
)
def test_invalid_endpoint_is_rejected(url):
    with pytest.raises(ValidationError):
        HttpRelayService(url)


@pytest.mark.parametrize("timeout", [0, -1, True, None, "1", float("nan"), float("inf")])
def test_invalid_timeout_is_rejected(timeout):
    with pytest.raises(ValidationError):
        HttpRelayService("http://localhost", timeout=timeout)


@pytest.mark.parametrize("status", [201, 301, 302, 307, 400, 503])
def test_http_failure_never_retries_or_follows_redirect(endpoint, status):
    endpoint.update(status=status, redirect=endpoint["url"] + "/other")
    with pytest.raises(ValidationError) as exc:
        HttpRelayService(endpoint["url"]).handle(request())
    assert exc.value.code == "TRANSPORT_ERROR"
    assert len(endpoint["calls"]) == 1


def test_busy_http_error_preserves_actionable_diagnostic(endpoint):
    endpoint.update(
        status=503,
        body=response(
            status="error",
            result=None,
            error={"code": "SERVICE_BUSY", "message": "服务正在处理另一个请求"},
        ),
    )
    with pytest.raises(ValidationError) as exc:
        HttpRelayService(endpoint["url"]).handle(request())
    assert exc.value.code == "TRANSPORT_ERROR"
    assert "503" in str(exc.value)
    assert "SERVICE_BUSY" in str(exc.value)
    assert "服务正在处理另一个请求" in str(exc.value)
    assert len(endpoint["calls"]) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"status": 307},
        {"body": b"broken"},
        {"body": b"x" * 20000},
        {"type": "text/html"},
        {"length": "broken"},
        {
            "body": response(
                status="error",
                request_id="other",
                result=None,
                error={"code": "PRIVATE", "message": "wrong request"},
            )
        },
    ],
)
def test_http_failure_uses_generic_message_for_untrusted_body(endpoint, changes):
    endpoint.update(
        status=503,
        body=response(status="error", result=None, error={"code": "PRIVATE", "message": "detail"}),
    )
    endpoint.update(changes)
    with pytest.raises(ValidationError) as exc:
        HttpRelayService(endpoint["url"]).handle(request())
    assert exc.value.code == "TRANSPORT_ERROR"
    assert "PRIVATE" not in str(exc.value)


@pytest.mark.parametrize(
    "body",
    [
        b"no json",
        b'{"x":1,"x":2}',
        b'{"x":NaN}',
        b"\xff",
        [],
        response(api_version="2"),
        response(request_id="other"),
        response(status="done"),
        response(error={"code": "X", "message": "bad"}),
        response(result=None),
        response(status="error", error=None),
        response(status="error", error={"code": 5}),
        response(unexpected=True),
    ],
)
def test_bad_response_is_rejected(endpoint, body):
    endpoint["body"] = body
    with pytest.raises(ValidationError) as exc:
        HttpRelayService(endpoint["url"]).handle(request())
    assert exc.value.code == "INVALID_RESPONSE"


def test_non_json_response_rejected(endpoint):
    endpoint["type"] = "text/html"
    with pytest.raises(ValidationError, match="JSON"):
        HttpRelayService(endpoint["url"]).handle(request())


def test_json_integer_beyond_python_limit_is_invalid_response(endpoint):
    endpoint["body"] = b'{"oversized_integer":' + b"9" * 5000 + b"}"
    with pytest.raises(ValidationError) as exc:
        HttpRelayService(endpoint["url"]).handle(request())
    assert exc.value.code == "INVALID_RESPONSE"


@pytest.mark.parametrize("changes", [{"encoding": "gzip"}, {"length": "broken"}, {"length": -1}])
def test_invalid_response_headers_rejected(endpoint, changes):
    endpoint.update(changes)
    with pytest.raises(ValidationError) as exc:
        HttpRelayService(endpoint["url"]).handle(request())
    assert exc.value.code == "INVALID_RESPONSE"


@pytest.mark.parametrize("mode", ["length", "stream"])
def test_oversized_response_rejected(endpoint, monkeypatch, mode):
    monkeypatch.setattr(http_client, "MAX_JSON_BYTES", 256)
    endpoint["body"] = b"x" * 300
    endpoint["omit_length"] = mode == "stream"
    with pytest.raises(ValidationError) as exc:
        HttpRelayService(endpoint["url"]).handle(request())
    assert exc.value.code == "INVALID_RESPONSE"


def test_oversized_request_never_sent(endpoint, monkeypatch):
    monkeypatch.setattr(http_client, "MAX_JSON_BYTES", 10)
    with pytest.raises(ValidationError):
        HttpRelayService(endpoint["url"]).handle(request())
    assert not endpoint["calls"]


@pytest.mark.parametrize("bad", [[], {"x": float("nan")}, {1: "x"}, {"x": object()}])
def test_non_json_request_never_sent(endpoint, bad):
    with pytest.raises(ValidationError):
        HttpRelayService(endpoint["url"]).handle(bad)
    assert not endpoint["calls"]


@pytest.mark.parametrize("mode", ["disconnect", "timeout", "truncated"])
def test_network_failure_is_reported_once(endpoint, mode):
    endpoint.update(
        {"disconnect": True}
        if mode == "disconnect"
        else {"delay": 0.15}
        if mode == "timeout"
        else {"length": 10000}
    )
    with pytest.raises(ValidationError) as exc:
        HttpRelayService(endpoint["url"], timeout=0.05).handle(request())
    assert exc.value.code in {"TRANSPORT_ERROR", "INVALID_RESPONSE"}
    assert len(endpoint["calls"]) == 1


def test_legacy_host_import_does_not_load_core():
    source = Path(http_client.__file__).read_text(encoding="utf-8")
    ast.parse(source, feature_version=(3, 7))
    code = (
        "import sys; from iris_relay.http_client import HttpRelayService; "
        "assert not any(x in sys.modules for x in "
        "['iris_relay.api', 'MaterialX', 'fastapi', 'uvicorn', 'torch'])"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
