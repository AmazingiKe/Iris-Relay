"""导出控制流程的离线验证：真实核心、源图保护、警告策略及文件失败。"""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from _scripts.native_fixture import fixture
from iris_relay import RelayAPI
from iris_relay.model import ValidationError
from plugins.blender.controller import check_material, create_service, export_material

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def setup_export():
    profile = json.loads((ROOT / "dictionaries/blender-cycles-5.2.3.json").read_text("utf-8"))
    source = fixture(profile, "fixture.png")

    class Adapter:
        def read_material(self):
            return deepcopy(source)

    return Adapter(), RelayAPI([profile]), source


def test_check_preserves_source_and_reports_approximation(setup_export):
    adapter, service, source = setup_export
    original = deepcopy(source)
    result = check_material(adapter, service)
    assert result.status == "warning"
    assert result.saved_path is None
    assert result.validation_response["result"]["valid"] is True
    assert any(i["code"] == "SHADING_MODEL_APPROXIMATION" for i in result.issues)
    assert result.export_response["result"]["source"] == original == source


def test_export_requires_warning_acceptance_and_writes_valid_xml(setup_export, tmp_path):
    adapter, service, source = setup_export
    path = tmp_path / "中文材质.mtlx"
    blocked = export_material(adapter, service, str(path))
    assert blocked.status == "warning" and not path.exists()
    assert blocked.saved_path is None
    saved = export_material(adapter, service, str(path), accept_warnings=True)
    assert saved.status == "warning" and saved.saved_path == str(path)
    assert path.read_text("utf-8") == saved.export_response["result"]["document"]
    assert saved.validation_response["result"]["valid"] is True
    assert saved.export_response["result"]["source"] == source


@pytest.mark.parametrize(
    "name", ["", "relative.mtlx", "//relative.mtlx", "bad.txt", "missing/a.mtlx"]
)
def test_invalid_paths_never_read_material(setup_export, tmp_path, name):
    adapter, service, _ = setup_export
    path = name if name in ("", "relative.mtlx", "//relative.mtlx") else str(tmp_path / name)
    adapter.read_material = lambda: pytest.fail("路径错误时不应读取材质")
    result = export_material(adapter, service, path, accept_warnings=True)
    assert result.status == "error" and result.saved_path is None


def test_existing_file_is_preserved(setup_export, tmp_path):
    path = tmp_path / "existing.mtlx"
    path.write_bytes(b"original")
    result = export_material(*setup_export[:2], str(path), accept_warnings=True)
    assert result.status == "error"
    assert path.read_bytes() == b"original"


def test_host_read_error_is_reported_without_file(setup_export, tmp_path):
    adapter, service, _ = setup_export

    def fail():
        raise ValidationError("节点组尚不支持", "UNSUPPORTED_NODE")

    adapter.read_material = fail
    result = export_material(adapter, service, str(tmp_path / "bad.mtlx"))
    assert result.status == "error"
    assert result.issues[0]["code"] == "UNSUPPORTED_NODE"
    assert list(tmp_path.iterdir()) == []


def test_host_snapshot_and_diagnostics_survive_read_failure(setup_export):
    adapter, service, _ = setup_export
    adapter.source_snapshot = {"nodes": [{"type": "ShaderNodeDisplacement"}]}
    adapter.read_issues = [
        {
            "severity": "warning",
            "code": "DISPLACEMENT_EXCLUDED",
            "message": "排除位移",
            "node": "output",
            "port": "Displacement",
        }
    ]

    def fail():
        raise ValidationError("坐标链尚不支持", "UV_UNSUPPORTED")

    adapter.read_material = fail
    result = check_material(adapter, service)
    assert result.status == "error"
    assert result.source_snapshot == adapter.source_snapshot
    assert [i["code"] for i in result.issues] == ["DISPLACEMENT_EXCLUDED", "UV_UNSUPPORTED"]


def test_unsupported_material_preserves_core_report(setup_export, tmp_path):
    adapter, service, source = setup_export
    source["nodes"][0]["type"] = "UnsupportedNode"
    result = export_material(adapter, service, str(tmp_path / "bad.mtlx"), accept_warnings=True)
    assert result.status == "error" and result.saved_path is None
    assert result.export_response["result"]["source"] == source
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("failure", ["request_id", "document", "validation", "transport"])
def test_service_failures_do_not_write(setup_export, tmp_path, failure):
    adapter, real_service, _ = setup_export

    class Service:
        def handle(self, request):
            if failure == "transport":
                raise ValidationError("核心响应超时", "TRANSPORT_ERROR")
            response = real_service.handle(request)
            if failure == "request_id":
                response["request_id"] = "wrong"
            elif failure == "document" and request["operation"] == "materialx.export":
                response["result"]["document"] = None
            elif failure == "validation" and request["operation"] == "materialx.validate":
                response["result"]["valid"] = False
            return response

    result = export_material(adapter, Service(), str(tmp_path / "bad.mtlx"), accept_warnings=True)
    assert result.status == "error" and result.saved_path is None
    assert list(tmp_path.iterdir()) == []


def test_write_failure_removes_partial_file(setup_export, tmp_path, monkeypatch):
    path = tmp_path / "partial.mtlx"
    original_open = Path.open

    class FailingFile:
        def __enter__(self):
            self.file = original_open(path, "x", encoding="utf-8")
            return self

        def write(self, data):
            self.file.write(data[:10])
            raise OSError("磁盘写入失败")

        def __exit__(self, *args):
            self.file.close()

    monkeypatch.setattr(Path, "open", lambda self, *a, **kw: FailingFile())
    result = export_material(*setup_export[:2], str(path), accept_warnings=True)
    assert result.status == "error" and not path.exists()
    assert result.export_response is not None


def test_project_service_uses_pinned_sdk():
    service = create_service(ROOT)
    response = service.handle(
        {"api_version": "1", "request_id": "环境检查", "operation": "capabilities", "payload": {}}
    )
    assert response["status"] == "ok"
    assert response["result"]["materialx"]["version"] == "1.39.5"


def test_file_created_during_validation_is_never_overwritten(setup_export, tmp_path):
    adapter, real_service, _ = setup_export
    path = tmp_path / "race.mtlx"

    class Service:
        def handle(self, request):
            response = real_service.handle(request)
            if request["operation"] == "materialx.validate":
                path.write_bytes(b"another writer")
            return response

    result = export_material(adapter, Service(), str(path), accept_warnings=True)
    assert result.status == "error"
    assert path.read_bytes() == b"another writer"


@pytest.mark.parametrize("version", ["1.39.4", None])
def test_service_rejects_incompatible_or_missing_sdk(tmp_path, monkeypatch, version):
    from plugins.blender import controller

    class Service:
        def handle(self, request):
            return {
                "api_version": "1",
                "request_id": request["request_id"],
                "status": "ok",
                "result": {"materialx": {"available": version is not None, "version": version}},
            }

    monkeypatch.setattr(controller.RelayAPI, "from_directory", lambda path: Service())
    with pytest.raises(ValidationError, match="1.39.5"):
        create_service(tmp_path)
