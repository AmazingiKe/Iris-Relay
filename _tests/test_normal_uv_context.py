"""命名法线 UV 只有实际几何证据证明为渲染默认集时，才允许显式默认基底展开。"""

from copy import deepcopy

import pytest

from _scripts.native_fixture import renderer
from _tests.test_materialx_hsv import profile
from _tests.test_materialx_link_casts import export
from _tests.test_materialx_native_nodes import convert, math_fixture
from _tests.test_maya_materialx import maya_profile
from iris_relay import RelayAPI


def source():
    data = profile()
    assert "uv_map" in data["nodes"]["ShaderNodeNormalMap"]["inputs"]
    _, material = math_fixture()
    material["nodes"] = [
        material["nodes"][0],
        {
            "id": "normal",
            "type": "ShaderNodeNormalMap",
            "inputs": {"Color": [0.5, 0.5, 1.0], "Strength": 0.8, "uv_map": "UVMap"},
            "outputs": ["Normal"],
            "metadata": {
                "uv_binding": {
                    "name": "UVMap",
                    "owners": [{"mesh": "mesh", "render_default": "UVMap", "uv_sets": ["UVMap"]}],
                }
            },
        },
    ]
    material["links"] = [
        {
            "from": {"node": "normal", "socket": "Normal"},
            "to": {"node": "surface_standard", "socket": "Normal"},
        }
    ]
    return data, material


def test_verified_uv_alias_can_roundtrip_native_name_and_convert_to_arnold():
    data, material = source()
    exported = export(data, material)
    assert exported["result"]["document"], exported
    restored = convert(data, material, exported["result"]["document"])
    assert restored["result"]["material"], restored
    normal = next(n for n in restored["result"]["material"]["nodes"] if n["id"] == "normal")
    assert normal["inputs"]["uv_map"] == "UVMap"
    target = maya_profile()
    response = RelayAPI([target]).handle(
        {
            "api_version": "1",
            "request_id": "uv",
            "operation": "materialx.convert",
            "payload": {"document": exported["result"]["document"], "target": renderer(target)},
        }
    )
    assert response["result"]["material"], response


@pytest.mark.parametrize("failure", ["no_owners", "other_default", "missing_name"])
def test_uv_alias_needs_current_complete_geometry_evidence(failure):
    data, material = source()
    material = deepcopy(material)
    binding = material["nodes"][1]["metadata"]["uv_binding"]
    if failure == "no_owners":
        binding["owners"] = []
    if failure == "other_default":
        binding["owners"][0]["render_default"] = "OtherUV"
    if failure == "missing_name":
        binding["owners"][0]["uv_sets"] = []
    result = export(data, material)
    assert result["status"] == "error" and result["result"]["document"] is None
