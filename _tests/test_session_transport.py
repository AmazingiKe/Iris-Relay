"""持续子进程仍逐请求使用统一协议，隔离错误并限制等待和资源生命周期。"""

import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from iris_relay.protocol import ValidationError


def test_session_reuses_worker_and_recovers_after_business_error():
    from iris_relay.transport import JsonSessionService

    with JsonSessionService(
        [sys.executable, "-m", "iris_relay", "--dictionaries", "dictionaries", "--stream"]
    ) as service:
        request = {
            "api_version": "1",
            "request_id": "first",
            "operation": "capabilities",
            "payload": {},
        }
        assert service.handle(request)["status"] == "ok"
        pid = service.pid
        assert service.handle(dict(request, operation="not_supported"))["status"] == "error"
        assert service.handle(dict(request, request_id="last"))["request_id"] == "last"
        assert service.pid == pid
    with pytest.raises(ValidationError, match="关闭"):
        service.handle(request)


def test_session_serializes_concurrent_calls_without_crossing_responses():
    from iris_relay.transport import JsonSessionService

    code = (
        "import sys,json\nfor line in sys.stdin:\n"
        " x=json.loads(line);print(json.dumps(x),flush=True)"
    )
    with JsonSessionService([sys.executable, "-u", "-c", code]) as service:

        def call(index):
            return service.handle({"request_id": index})["request_id"]

        with ThreadPoolExecutor(max_workers=4) as pool:
            assert list(pool.map(call, range(20))) == list(range(20))


def test_session_timeout_closes_owned_worker():
    from iris_relay.transport import JsonSessionService

    service = JsonSessionService([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.1)
    with pytest.raises(ValidationError, match="超时"):
        service.handle({})
    assert service.pid is None


def test_timeout_also_bounds_a_full_stdin_pipe():
    from iris_relay.transport import JsonSessionService

    service = JsonSessionService([sys.executable, "-c", "import time;time.sleep(30)"], timeout=0.1)
    errors = []

    def call():
        try:
            service.handle({"large": "x" * 1024 * 1024})
        except ValidationError as exc:
            errors.append(exc)

    worker = threading.Thread(target=call, daemon=True)
    started = time.monotonic()
    worker.start()
    worker.join(2)
    bounded = not worker.is_alive()
    # 旧实现会卡在写满的管道；只清理本测试实际启动的进程，避免失败测试留下后台任务。
    if worker.is_alive() and service._process is not None:
        service._process.terminate()
        worker.join(3)
    service.close()
    assert bounded and time.monotonic() - started < 2, errors
    assert errors and errors[0].code == "TRANSPORT_ERROR"


@pytest.mark.parametrize("code", ["print('not json')", "print('[]')", "pass"])
def test_bad_session_response_fails_closed(code):
    from iris_relay.transport import JsonSessionService

    with JsonSessionService([sys.executable, "-c", code]) as service:
        with pytest.raises(ValidationError):
            service.handle({})
