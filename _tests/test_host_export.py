"""统一导出流程读取当前宿主图，拒绝覆盖，失败不留下成功文档。"""

import pytest

from _scripts.native_fixture import fixture
from _tests.test_maya_materialx import maya_profile
from iris_relay import RelayAPI


class Reader:
    """模拟当前材质读取；图中参数可在两次操作之间变化。"""

    def __init__(self):
        self.material = fixture(maya_profile())
        self.reads = 0

    def read_material(self, surface):
        assert surface == "shader"
        self.reads += 1
        return self.material


def test_export_rereads_live_material_and_obeys_warning_policy(tmp_path):
    from plugins.core import export_material

    reader = Reader()
    api = RelayAPI([maya_profile()])
    path = tmp_path / "maya.mtlx"
    first = export_material(reader, api, "shader", path)
    assert not path.exists() and first["status"] == "warning"
    surface = next(n for n in reader.material["nodes"] if n["type"] == "standard_surface")
    surface["inputs"]["specularRoughness"] = 0.619
    second = export_material(reader, api, "shader", path, accept_warnings=True)
    assert second["saved_path"] == str(path) and reader.reads == 2
    assert 'value="0.619"' in path.read_text("utf-8")


def test_export_never_overwrites_existing_document(tmp_path):
    from plugins.core import export_material

    path = tmp_path / "original.mtlx"
    path.write_text("existing content", encoding="utf-8")
    result = export_material(
        Reader(), RelayAPI([maya_profile()]), "shader", path, accept_warnings=True
    )
    assert result["status"] == "error" and result["saved_path"] is None
    assert path.read_text("utf-8") == "existing content"


@pytest.mark.parametrize("path", ["relative.mtlx", "file.txt"])
def test_export_rejects_ambiguous_or_wrong_output_path(tmp_path, path):
    from plugins.core import export_material

    output = path if path.startswith("relative") else tmp_path / path
    result = export_material(
        Reader(), RelayAPI([maya_profile()]), "shader", output, accept_warnings=True
    )
    assert result["status"] == "error" and result["saved_path"] is None


def test_unknown_live_node_blocks_without_creating_file(tmp_path):
    from plugins.core import export_material

    reader = Reader()
    reader.material["nodes"][0]["type"] = "unknown_native_function"
    path = tmp_path / "blocked.mtlx"
    result = export_material(
        reader, RelayAPI([maya_profile()]), "shader", path, accept_warnings=True
    )
    assert result["status"] == "error" and not path.exists()
    assert any(i["code"] == "UNSUPPORTED_NODE" for i in result["issues"])
