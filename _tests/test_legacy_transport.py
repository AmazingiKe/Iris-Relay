"""旧宿主只加载公开传输和原生辅助层，Python 3.7 无须导入核心。"""

import ast
import subprocess
import sys
from pathlib import Path

from iris_relay.plugin import JsonCommandService


def test_transport_is_shared_and_has_no_eager_core_dependency():
    from iris_relay.transport import JsonCommandService as StandaloneService

    assert StandaloneService is JsonCommandService
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            "import iris_relay.transport, iris_relay.native, sys; "
            "assert 'iris_relay.api' not in sys.modules; "
            "assert 'iris_relay.model' not in sys.modules",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    for name in ("__init__", "protocol", "transport", "native"):
        content = Path(f"iris_relay/{name}.py").read_text("utf-8")
        ast.parse(content, feature_version=(3, 7))


def test_legacy_transport_calls_same_public_api():
    from iris_relay.transport import JsonCommandService as StandaloneService

    service = StandaloneService(
        [sys.executable, "-m", "iris_relay", "--dictionaries", "dictionaries"]
    )
    response = service.handle(
        {"api_version": "1", "request_id": "legacy", "operation": "capabilities", "payload": {}}
    )
    assert response["status"] == "ok", response
    assert response["request_id"] == "legacy"


def test_transport_can_isolate_host_python_environment(monkeypatch):
    import os

    from iris_relay.transport import JsonCommandService as StandaloneService

    monkeypatch.setenv("PYTHONHOME", "invalid-host-python-home")
    environment = dict(os.environ)
    environment.pop("PYTHONHOME")
    service = StandaloneService(
        [sys.executable, "-m", "iris_relay", "--dictionaries", "dictionaries"],
        env=environment,
    )
    response = service.handle(
        {
            "api_version": "1",
            "request_id": "environment",
            "operation": "capabilities",
            "payload": {},
        }
    )
    assert response["status"] == "ok", response
