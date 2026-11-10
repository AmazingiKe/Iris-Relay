"""Maya 导入流程必须重新校验、尊重警告策略，并在错误时保留场景。"""

import json
from xml.etree import ElementTree as ET

import pytest

from _scripts.native_fixture import renderer
from _tests.test_maya_materialx import hsv_document, maya_profile
from iris_relay import RelayAPI


class Adapter:
    """记录创建请求；具体宿主节点和撤销另由 Maya 实机检查。"""

    def __init__(self):
        self.created = []

    def preflight(self, material):
        return []

    def create_material(self, material):
        self.created.append(material)
        return {"material": "new_shader", "shading_group": "new_SG", "nodes": {}}


@pytest.mark.parametrize("accept", [False, True])
def test_import_warning_policy_and_relative_texture(tmp_path, accept):
    from plugins.maya.controller import import_material

    target = maya_profile()
    source = tmp_path / "test.mtlx"
    document = hsv_document()
    xml = ET.fromstring(document)
    for image in xml.findall("image"):
        name = image.find("input[@name='file']").get("value")
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(b"mock image; native decoder covered in host checks")
    source.write_text(document, encoding="utf-8")
    adapter = Adapter()
    result = import_material(
        adapter, RelayAPI([target]), source, renderer(target), accept_warnings=accept
    )
    assert bool(result["created"]) == accept, result
    assert len(adapter.created) == int(accept)
    assert all(
        r["uri"] == (tmp_path / r["uri"]).resolve().as_posix()
        for r in result["material"]["textures"].values()
    )
    json.dumps(result, ensure_ascii=False)


@pytest.mark.parametrize(
    "failure", ["missing_texture", "preflight", "creation", "native_exception"]
)
def test_import_failure_is_reported_without_success(tmp_path, failure):
    from iris_relay.protocol import ValidationError
    from plugins.maya.controller import import_material

    class FailingAdapter(Adapter):
        def preflight(self, material):
            if failure == "preflight":
                raise ValidationError("不支持的贴图通道", "MAYA_IMAGE_CHANNELS_UNSUPPORTED")
            return []

        def create_material(self, material):
            if failure == "native_exception":
                raise SystemError("原生 API 抛出的非 RuntimeError 异常")
            raise RuntimeError("原生创建失败，宿主负责回滚")

    document = hsv_document()
    source = tmp_path / "test.mtlx"
    source.write_text(document, encoding="utf-8")
    if failure != "missing_texture":
        for image in ET.fromstring(document).findall("image"):
            name = image.find("input[@name='file']").get("value")
            (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / name).write_bytes(b"host decoder tested separately")
    target = maya_profile()
    adapter = FailingAdapter()
    result = import_material(
        adapter, RelayAPI([target]), source, renderer(target), accept_warnings=True
    )
    expected = {
        "missing_texture": "TEXTURE_MISSING",
        "preflight": "MAYA_IMAGE_CHANNELS_UNSUPPORTED",
        "creation": "HOST_CREATE_FAILED",
        "native_exception": "HOST_CREATE_FAILED",
    }
    assert result["status"] == "error" and result["created"] is None
    assert result["issues"][-1]["code"] == expected[failure]
    assert not adapter.created


def test_service_requires_environment_and_cleans_maya_python_vars(tmp_path, monkeypatch):
    from plugins.maya.controller import ROOT, create_service

    with pytest.raises(ValueError, match=".venv"):
        create_service(tmp_path)
    monkeypatch.setenv("PYTHONHOME", "invalid-maya-python")
    monkeypatch.setenv("PYTHONPATH", "invalid-maya-python")
    response = create_service(ROOT).handle(
        {"api_version": "1", "request_id": "maya-env", "operation": "capabilities", "payload": {}}
    )
    assert response["status"] == "ok", response


def test_bad_file_never_creates_nodes(tmp_path):
    from plugins.maya.controller import import_material

    path = tmp_path / "invalid.mtlx"
    path.write_text("<invalid", encoding="utf-8")
    adapter = Adapter()
    target = maya_profile()
    result = import_material(
        adapter, RelayAPI([target]), path, renderer(target), accept_warnings=True
    )
    assert result["status"] == "error" and not adapter.created


def test_bad_response_cannot_be_applied():
    from plugins.maya.controller import request

    class Broken:
        def handle(self, request):
            return {"api_version": "1", "request_id": "wrong", "status": "ok"}

    with pytest.raises(ValueError, match="请求 ID"):
        request(Broken(), "capabilities")


def test_malformed_target_material_is_rejected_before_host_creation(tmp_path):
    from plugins.core import import_material

    target = maya_profile()
    real = RelayAPI([target])

    class Broken:
        def handle(self, request):
            response = real.handle(request)
            if request["operation"] == "materialx.convert":
                response["result"]["material"]["nodes"] = "invalid graph"
            return response

    path = tmp_path / "test.mtlx"
    path.write_text(hsv_document(), encoding="utf-8")
    adapter = Adapter()
    result = import_material(adapter, Broken(), path, renderer(target), accept_warnings=True)
    assert result["status"] == "error" and not adapter.created
    assert result["issues"][-1]["code"] == "INVALID_RESPONSE"
