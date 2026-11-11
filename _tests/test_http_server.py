"""HTTP 接入保持核心协议，并验证本机服务的生命周期及传输边界。"""

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from iris_relay import RelayAPI
from iris_relay.server.app import create_app
from iris_relay.server.runtime import ServerSettings, ServiceRuntime


def envelope(request, result=None):
    return {
        "api_version": "1",
        "request_id": request.get("request_id"),
        "status": "ok",
        "result": result,
        "error": None,
    }


class EchoService:
    def handle(self, request):
        return envelope(request, request)


def client_for(service=None, settings=None):
    return TestClient(create_app(service or EchoService(), settings), base_url="http://127.0.0.1")


def test_conversion_and_business_errors_match_direct_api(profiles, request_data):
    api = RelayAPI(profiles)
    with client_for(api) as client:
        for request in (request_data, {**request_data, "api_version": "99"}):
            response = client.post("/v1/handle", json=request)
            assert response.status_code == 200
            assert response.json() == api.handle(request)
        assert client.get("/health").json() == {"status": "ok", "api_version": "1"}


@pytest.mark.parametrize("body", [b'{"a":1,"a":2}', b'{"a":NaN}', b"\xff", b"{", b'{"a":Infinity}'])
def test_invalid_json_rejected(body):
    with client_for() as client:
        response = client.post(
            "/v1/handle", content=body, headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 400
        assert response.json()["status"] == "error"


@pytest.mark.parametrize(
    "headers, status",
    [
        ({"Content-Type": "text/plain"}, 415),
        ({"Origin": "https://example.com"}, 403),
        ({"Origin": "null"}, 403),
        ({"Host": "attacker.example"}, 403),
        ({"Host": "["}, 403),
        ({"Content-Encoding": "gzip"}, 415),
    ],
)
def test_reject_browser_and_unsupported_transport(headers, status):
    with client_for() as client:
        response = client.post("/v1/handle", json={"request_id": "x"}, headers=headers)
        assert response.status_code == status
        assert response.json()["error"] is not None
        assert "access-control-allow-origin" not in response.headers


def test_request_and_response_limits(monkeypatch):
    import iris_relay.server.app as module

    monkeypatch.setattr(module, "MAX_JSON_BYTES", 256)
    with client_for() as client:
        assert (
            client.post(
                "/v1/handle", content=b"x" * 257, headers={"Content-Type": "application/json"}
            ).status_code
            == 413
        )
        # 请求本身未超限，但回传的响应信封超过限制。
        response = client.post("/v1/handle", json={"request_id": "x", "data": "x" * 180})
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "RESPONSE_TOO_LARGE"


def test_chunked_limit_without_content_length(monkeypatch):
    import iris_relay.server.app as module

    monkeypatch.setattr(module, "MAX_JSON_BYTES", 256)

    async def run():
        app = create_app(EchoService())
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
            ) as client:

                async def content():
                    yield b" " * 128
                    yield b" " * 129

                return await client.post(
                    "/v1/handle", content=content(), headers={"Content-Type": "application/json"}
                )

    assert asyncio.run(run()).status_code == 413


def test_slow_body_times_out():
    async def run():
        app = create_app(EchoService(), ServerSettings(read_timeout=0.02))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
            ) as client:

                async def content():
                    yield b"{"
                    await asyncio.sleep(0.1)
                    yield b"}"

                return await client.post(
                    "/v1/handle", content=content(), headers={"Content-Type": "application/json"}
                )

    assert asyncio.run(run()).status_code == 408


def test_busy_is_rejected_without_blocking_health():
    started, release = threading.Event(), threading.Event()

    class SlowService:
        def handle(self, request):
            started.set()
            assert release.wait(5)
            return envelope(request)

    with client_for(SlowService()) as client, ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.post, "/v1/handle", json={"request_id": "first"})
        try:
            assert started.wait(5)
            assert client.get("/health").status_code == 200
            rejected = client.post("/v1/handle", json={"request_id": "second"})
            assert rejected.status_code == 503
            assert rejected.json()["error"]["code"] == "SERVICE_BUSY"
        finally:
            release.set()
        assert pending.result().status_code == 200
        assert client.post("/v1/handle", json={"request_id": "third"}).status_code == 200


def test_unexpected_exception_is_sanitized_and_service_recovers():
    class FaultService:
        def handle(self, request):
            if request.get("fail"):
                raise RuntimeError("private-file-and-secret")
            return envelope(request)

    with client_for(FaultService()) as client:
        failure = client.post("/v1/handle", json={"request_id": "x", "fail": True})
        assert failure.status_code == 500
        assert "private-file-and-secret" not in failure.text
        assert failure.json()["request_id"] == "x"
        assert client.post("/v1/handle", json={"request_id": "y"}).status_code == 200


@pytest.mark.parametrize(
    "path, method, status", [("/missing", "get", 404), ("/v1/handle", "get", 405)]
)
def test_routing_errors_use_envelope(path, method, status):
    with client_for() as client:
        response = getattr(client, method)(path)
        assert response.status_code == status
        assert response.json()["api_version"] == "1"
        assert response.json()["status"] == "error"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"host": "0.0.0.0"},
        {"host": "example.com"},
        {"port": 0},
        {"port": True},
        {"read_timeout": 0},
        {"read_timeout": float("nan")},
    ],
)
def test_invalid_settings_rejected(kwargs):
    with pytest.raises(ValueError):
        ServerSettings(**kwargs)


def test_runtime_loads_one_core_and_releases_it(monkeypatch, tmp_path):
    import iris_relay.server.runtime as module

    calls = []
    service = EchoService()
    monkeypatch.setattr(module, "build_service", lambda settings: calls.append(settings) or service)
    app = create_app(settings=ServerSettings(dictionaries=tmp_path))
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert len(calls) == 1
        for _ in range(2):
            assert client.post("/v1/handle", json={}).status_code == 200
    assert app.state.runtime.service is None
    assert len(calls) == 1


def test_runtime_builds_real_core_without_model():
    from iris_relay.server.runtime import build_service

    service = build_service(ServerSettings(dictionaries=Path("_examples/dictionaries")))
    response = service.handle(
        {"api_version": "1", "request_id": "health", "operation": "capabilities", "payload": {}}
    )
    assert response["status"] == "ok"


def test_model_is_only_loaded_from_startup_settings(monkeypatch, tmp_path):
    from iris_relay.neural import NeuralJointPredictor
    from iris_relay.server.runtime import build_service

    marker = object()
    paths = []
    monkeypatch.setattr(
        NeuralJointPredictor, "from_file", lambda path: paths.append(path) or marker
    )
    service = build_service(
        ServerSettings(dictionaries=Path("_examples/dictionaries"), model=tmp_path / "model.json")
    )
    assert service._predictor is marker
    assert paths == [tmp_path / "model.json"]


def test_closed_runtime_refuses_calls():
    runtime = ServiceRuntime(ServerSettings(), EchoService())
    runtime.start()
    runtime.close()
    with pytest.raises(RuntimeError):
        runtime.submit({})


def test_openapi_documents_original_envelope():
    schema = create_app(EchoService()).openapi()
    operation = schema["paths"]["/v1/handle"]["post"]
    body = operation["requestBody"]["content"]["application/json"]["schema"]
    assert set(body["required"]) == {"api_version", "request_id", "operation", "payload"}
    response = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert set(response["required"]) == {"api_version", "request_id", "status", "result", "error"}


def test_bad_content_length_is_transport_error():
    with client_for() as client:
        for value in ("-1", "garbage", "9" * 5000):
            response = client.post("/v1/handle", json={}, headers={"Content-Length": value})
            assert response.status_code in {400, 413}
            assert response.json()["status"] == "error"
        response = client.post("/v1/handle", json={}, headers={"Content-Length": "0" * 5000 + "2"})
        assert response.status_code == 200


def test_cancelled_http_request_keeps_execution_slot():
    started, release = threading.Event(), threading.Event()

    class SlowService:
        def handle(self, request):
            started.set()
            assert release.wait(5)
            return envelope(request)

    async def run():
        app = create_app(SlowService())
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
            ) as client:
                task = asyncio.create_task(client.post("/v1/handle", json={}))
                try:
                    assert await asyncio.to_thread(started.wait, 5)
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                    assert (await client.post("/v1/handle", json={})).status_code == 503
                finally:
                    release.set()

    asyncio.run(run())


def test_invalid_startup_dictionary_fails_before_health(tmp_path):
    with pytest.raises(ValueError):
        with TestClient(create_app(settings=ServerSettings(dictionaries=tmp_path))):
            pass


def test_cli_configures_single_local_worker(monkeypatch, tmp_path):
    import uvicorn

    from iris_relay.server.__main__ import main

    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.append((app, kwargs)))
    assert main(["--dictionaries", str(tmp_path), "--port", "9876"]) == 0
    app, config = calls[0]
    assert app.state.runtime.settings.dictionaries == tmp_path
    assert config["host"] == "127.0.0.1"
    assert config["port"] == 9876
    assert config["workers"] == 1
    assert config["proxy_headers"] is False
    with pytest.raises(SystemExit) as caught:
        main(["--host", "0.0.0.0"])
    assert caught.value.code == 2


@pytest.mark.parametrize("request_id", ["\ud800", "\udfff"])
def test_escaped_surrogate_matches_core_response(profiles, request_id):
    api = RelayAPI(profiles)
    request = {
        "api_version": "1",
        "request_id": request_id,
        "operation": "unknown",
        "payload": {},
    }
    with client_for(api) as client:
        response = client.post(
            "/v1/handle",
            content=json.dumps(request).encode("ascii"),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 200
        assert response.json() == api.handle(request)
        assert response.headers["content-type"] == "application/json"


def test_nested_escaped_surrogate_preserves_json_value():
    request = {"request_id": "valid", "data": ["\ud800", {"\udfff": "text"}]}
    with client_for() as client:
        response = client.post(
            "/v1/handle",
            content=json.dumps(request).encode("ascii"),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 200
        assert response.json()["result"] == request


def test_unencodable_request_id_cannot_break_transport_error():
    class FaultService:
        def handle(self, request):
            raise RuntimeError("private error")

    with client_for(FaultService()) as client:
        response = client.post(
            "/v1/handle",
            content=b'{"request_id":"\\ud800"}',
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 500
        assert response.json()["request_id"] is None
        assert response.json()["error"]["code"] == "INTERNAL_ERROR"
        assert "private error" not in response.text
