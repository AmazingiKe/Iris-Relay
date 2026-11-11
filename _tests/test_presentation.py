"""三宿主共用的连接、结果摘要及只检查导出行为。"""

import importlib
import os
import subprocess
import sys

import pytest

from plugins import core


def presentation():
    return importlib.import_module("plugins.presentation")


def test_status_never_hides_failure_or_warning():
    ui = presentation()
    assert ui.status_label("error", created=True) == "已阻断"
    assert ui.status_label("ok") == "检查通过"
    assert ui.status_label("warning", saved=True) == "已导出 · 有警告"
    assert ui.status_label("ok", created=True) == "已导入"


def test_report_retains_action_state_and_issue_location():
    ui = presentation()
    report = ui.format_report(
        {
            "status": "warning",
            "message": "等待确认",
            "saved_path": None,
            "issues": [
                {
                    "severity": "warning",
                    "code": "APPROXIMATE",
                    "message": "近似采样",
                    "node": "贴图",
                    "port": "Alpha",
                }
            ],
        }
    )
    assert "已导出" not in report
    assert all(
        value in report for value in ("等待确认", "APPROXIMATE", "近似采样", "贴图", "Alpha")
    )
    assert ui.issue_counts([{"severity": "error"}, {"severity": "warning"}]) == {
        "error": 1,
        "warning": 1,
        "info": 0,
    }


class Service:
    def __init__(self, version="1.39.5"):
        self.version, self.closed = version, False

    def handle(self, req):
        return {
            "api_version": "1",
            "request_id": req["request_id"],
            "status": "ok",
            "result": {"materialx": {"available": True, "version": self.version}},
            "error": None,
        }

    def close(self):
        self.closed = True


def test_connection_checks_capabilities_and_releases_incompatible_service():
    ui = presentation()
    service = Service()
    calls = []

    def factory(**kwargs):
        calls.append(kwargs)
        return service

    actual, capabilities = ui.connect_service(factory, "LOCAL", ui.DEFAULT_URL)
    assert actual is service and capabilities["materialx"]["version"] == "1.39.5"
    assert calls == [{"transport": "LOCAL", "url": ui.DEFAULT_URL}]
    service.version = "1.39.4"
    with pytest.raises(ValueError, match="1.39.5"):
        ui.connect_service(factory, "LOCAL", ui.DEFAULT_URL)
    assert service.closed


def test_local_selection_ignores_http_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("IRIS_RELAY_HTTP_URL", "http://127.0.0.1:8765")
    with pytest.raises(ValueError, match=".venv"):
        core.create_service(tmp_path, transport="LOCAL")
    from iris_relay.http_client import HttpRelayService

    assert isinstance(
        core.create_service(tmp_path, transport="HTTP", url="http://localhost:9876"),
        HttpRelayService,
    )
    with pytest.raises(ValueError):
        core.create_service(tmp_path, transport="unknown")
    assert os.environ["IRIS_RELAY_HTTP_URL"] == "http://127.0.0.1:8765"


class ExportService:
    def handle(self, req):
        result = {"document": '<materialx version="1.39"/>', "report": {"issues": []}}
        if req["operation"] == "materialx.validate":
            result = {"valid": True}
        return {
            "api_version": "1",
            "request_id": req["request_id"],
            "status": "ok",
            "result": result,
            "error": None,
        }


def test_check_export_is_readonly_and_export_still_rechecks(tmp_path):
    class Adapter:
        calls = 0

        def read_material(self, surface):
            self.calls += 1
            return {"name": surface}

    adapter = Adapter()
    result = core.check_export(adapter, ExportService(), "材质")
    assert result["status"] == "ok" and result["saved_path"] is None
    assert not list(tmp_path.iterdir())
    target = tmp_path / "test.mtlx"
    saved = core.export_material(adapter, ExportService(), "材质", target)
    assert saved["saved_path"] == str(target) and adapter.calls == 2
    blocked = core.export_material(adapter, ExportService(), "材质", target)
    assert blocked["status"] == "error" and adapter.calls == 2


def test_presentation_import_keeps_host_startup_light():
    code = (
        "import sys; import plugins.presentation; "
        "assert not any(n.startswith('iris_relay') for n in sys.modules)"
    )
    subprocess.run([sys.executable, "-S", "-c", code], check=True, timeout=15)
