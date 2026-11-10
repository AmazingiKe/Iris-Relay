"""Blender 导入控制：验证真实核心往返、警告确认、资源定位和失败保护。"""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from _scripts.native_fixture import fixture
from iris_relay import RelayAPI
from plugins.blender import controller

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def imported(tmp_path):
    """先通过导出控制层生成文档，记录输入文件与宿主创建调用。"""
    profile = json.loads((ROOT / "dictionaries/blender-cycles-5.2.3.json").read_text("utf-8"))
    texture = tmp_path / "图片.png"
    texture.write_bytes(b"resource preservation fixture")
    source = fixture(profile, str(texture))
    service = RelayAPI([profile])

    class Adapter:
        created = []

        def read_material(self):
            return deepcopy(source)

        def create_material(self, material):
            self.created.append(deepcopy(material))
            return type("Material", (), {"name": "新材质_IrisRelay"})()

    adapter = Adapter()
    path = tmp_path / "材质.mtlx"
    assert controller.export_material(adapter, service, path, accept_warnings=True).saved_path
    return adapter, service, path, source["renderer"]


def test_export_check_import_then_create_preserves_files(imported):
    adapter, service, path, target = imported
    before = {p: p.read_bytes() for p in path.parent.iterdir()}
    checked = controller.check_import(service, path, target)
    assert checked.status == "warning"
    assert checked.validation_response["result"]["valid"] is True
    blocked = controller.import_material(adapter, service, path, target)
    assert blocked.status == "warning" and blocked.created_material is None
    assert not adapter.created
    result = controller.import_material(adapter, service, path, target, accept_warnings=True)
    assert result.created_material == "新材质_IrisRelay"
    assert len(adapter.created) == 1
    assert adapter.created[0]["renderer"] == target
    assert result.conversion_response["result"]["source"] == path.read_text("utf-8")
    assert {p: p.read_bytes() for p in path.parent.iterdir()} == before


@pytest.mark.parametrize(
    "name", ["", "relative.mtlx", "//relative.mtlx", "missing.mtlx", "bad.txt"]
)
def test_import_invalid_paths_do_not_call_service(imported, name):
    adapter, _, path, target = imported

    class Service:
        def handle(self, request):
            pytest.fail("无效路径不应调用核心")

    value = name if name in ("", "relative.mtlx", "//relative.mtlx") else path.parent / name
    result = controller.import_material(adapter, Service(), value, target, accept_warnings=True)
    assert result.status == "error" and not adapter.created


@pytest.mark.parametrize("content", [b"<broken", b"\xff\xfe", b"", b"<!DOCTYPE x><materialx/>"])
def test_invalid_documents_never_create(imported, content):
    adapter, service, path, target = imported
    path.write_bytes(content)
    result = controller.import_material(adapter, service, path, target, accept_warnings=True)
    assert result.status == "error" and result.created_material is None
    assert not adapter.created and path.read_bytes() == content


def test_import_rechecks_file_after_preview(imported):
    adapter, service, path, target = imported
    assert controller.check_import(service, path, target).status == "warning"
    path.write_text("<broken", "utf-8")
    result = controller.import_material(adapter, service, path, target, accept_warnings=True)
    assert result.status == "error" and not adapter.created


def test_relative_texture_is_resolved_from_document_directory(imported, monkeypatch, tmp_path):
    adapter, service, path, target = imported
    texture = path.parent / "图片.png"
    document = path.read_text("utf-8").replace(str(texture), "图片.png")
    path.write_text(document, "utf-8")
    elsewhere = tmp_path / "另一个目录"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    result = controller.import_material(adapter, service, path, target, accept_warnings=True)
    assert result.created_material
    assert {r["uri"] for r in adapter.created[0]["textures"].values()} == {str(texture)}
    assert path.read_text("utf-8") == document


def test_missing_texture_blocks_before_host_creation(imported):
    adapter, service, path, target = imported
    (path.parent / "图片.png").unlink()
    result = controller.import_material(adapter, service, path, target, accept_warnings=True)
    assert result.status == "error" and not adapter.created
    assert any(i["code"] == "TEXTURE_NOT_FOUND" for i in result.issues)


@pytest.mark.parametrize("problem", ["request_id", "material", "target", "validation", "transport"])
def test_bad_response_never_reaches_host(imported, problem):
    adapter, real, path, target = imported

    class Service:
        def handle(self, request):
            if problem == "transport":
                raise OSError("核心不可用")
            response = real.handle(request)
            if problem == "request_id":
                response["request_id"] = "wrong"
            if problem == "validation" and request["operation"] == "materialx.validate":
                response["result"]["valid"] = False
            if request["operation"] == "materialx.convert":
                if problem == "material":
                    response["result"]["material"] = None
                if problem == "target":
                    response["result"]["material"]["renderer"]["version"] = "0"
            return response

    result = controller.import_material(adapter, Service(), path, target, accept_warnings=True)
    assert result.status == "error" and not adapter.created


def test_unsupported_target_keeps_conversion_report(imported):
    adapter, service, path, target = imported
    target = dict(target, version="0")
    result = controller.import_material(adapter, service, path, target, accept_warnings=True)
    assert result.status == "error" and not adapter.created
    assert result.conversion_response["status"] == "error"


def test_host_failure_is_reported_with_conversion_evidence(imported):
    adapter, service, path, target = imported

    def fail(material):
        raise RuntimeError("宿主创建失败")

    adapter.create_material = fail
    result = controller.import_material(adapter, service, path, target, accept_warnings=True)
    assert result.status == "error" and result.created_material is None
    assert result.conversion_response["result"]["material"]
    assert any(i["code"] == "IMPORT_FAILED" for i in result.issues)
