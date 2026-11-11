"""图片坐标用标准几何属性和矩阵节点表达，顺序及目标能力不得静默降级。"""

import json
from copy import deepcopy
from pathlib import Path

import MaterialX as mx
import pytest

from _scripts.native_fixture import fixture, renderer
from _tests.test_materialx_bridge import call
from iris_relay import RelayAPI
from iris_relay.model import ValidationError

ROOT = Path(__file__).resolve().parents[1]
SCALE = [2, 0, 0, 0, 0, 3, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
ROTATE_TRANSLATE = [0, 1, 0, 0, -1, 0, 0, 0, 0, 0, 1, 0, 4, -2, 0, 1]


def setup(coordinates):
    """使用真实字典构造仅变化坐标的材质，文件 URI 不依赖测试机器。"""
    profile = json.loads((ROOT / "dictionaries/blender-cycles.json").read_text("utf-8"))
    source = fixture(profile)
    source["textures"]["fixture"]["coordinates"] = deepcopy(coordinates)
    return RelayAPI.from_directory(ROOT / "dictionaries"), source


@pytest.mark.parametrize(
    "name,transforms", [("纹理UV", []), ("", [SCALE, ROTATE_TRANSLATE]), ("UVMap.001", [SCALE])]
)
def test_uv_coordinates_roundtrip_in_standard_nodes(name, transforms):
    coordinates = {"kind": "uv", "set": name}
    if transforms:
        coordinates["transforms"] = transforms
    api, source = setup(coordinates)
    original = deepcopy(source)
    exported = call(api, "materialx.export", material=source)
    assert exported["status"] == "warning", exported
    document = exported["result"]["document"]
    assert call(api, "materialx.validate", document=document)["status"] == "ok"
    doc = mx.createDocument()
    mx.readFromXmlString(doc, document)
    assert not doc.getNodeDefs()
    matrices = [n for n in doc.getNodes() if n.getCategory() == "transformmatrix"]
    assert len(matrices) == len(transforms)
    if name:
        uv = next(n for n in doc.getNodes() if n.getCategory() == "geompropvalue")
        assert uv.getInput("geomprop").getValueString() == name
    else:
        uv = next(n for n in doc.getNodes() if n.getCategory() == "texcoord")
        assert uv.getInput("index").getValue() == 0
    if transforms == [SCALE, ROTATE_TRANSLATE]:
        point = mx.Vector3(0.25, 0.5, 0)
        for node in matrices:
            point = node.getInput("mat").getValue().transformPoint(point)
        assert list(point) == pytest.approx([2.5, -1.5, 0])
    converted = call(api, "materialx.convert", document=document, target=source["renderer"])
    assert converted["status"] == "warning", converted
    restored = next(iter(converted["result"]["material"]["textures"].values()))
    assert restored["coordinates"] == coordinates
    assert not any(
        i["code"] == "MATERIALX_UNUSED_NODES" for i in converted["result"]["report"]["issues"]
    )
    assert source == original == exported["result"]["source"]


@pytest.mark.parametrize("engine", ["redshift", "arnold"])
def test_target_without_coordinate_capability_blocks_before_host_creation(engine):
    api, source = setup({"kind": "uv", "set": "UVMap.001", "transforms": [SCALE]})
    document = call(api, "materialx.export", material=source)["result"]["document"]
    profile = json.loads(next((ROOT / "dictionaries").glob(f"*{engine}*.json")).read_text("utf-8"))
    profile["texture_coordinates"] = {"named_uv": False, "transforms": "none"}
    result = call(
        RelayAPI([profile]), "materialx.convert", document=document, target=renderer(profile)
    )
    assert result["status"] == "error"
    assert result["result"]["material"] is None
    assert result["result"]["source"] == document
    assert "MATERIALX_RESOURCE_UNSUPPORTED" in {
        i["code"] for i in result["result"]["report"]["issues"]
    }


@pytest.mark.parametrize(
    "coordinates",
    [
        {"kind": "generated", "set": ""},
        {"kind": "uv", "set": 1},
        {"kind": "uv", "set": "", "transforms": [[1, 2]]},
        {"kind": "uv", "set": "", "offset": [1, 2]},
        {"kind": "uv", "set": "", "transforms": [[1, 0, 0, 1] * 4]},
    ],
)
def test_unhandled_coordinate_description_cannot_be_omitted(coordinates):
    api, source = setup(coordinates)
    result = call(api, "materialx.export", material=source)
    assert result["status"] == "error"
    assert result["result"]["document"] is None
    assert result["result"]["source"] == source


@pytest.mark.parametrize(
    "matrix",
    [
        [1, 1, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
        [0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
    ],
)
def test_shear_or_degenerate_matrix_is_storable_but_not_a_trs_target(matrix):
    api, source = setup({"kind": "uv", "set": "", "transforms": [matrix]})
    exported = call(api, "materialx.export", material=source)
    assert exported["status"] == "warning", exported
    result = call(
        api, "materialx.convert", document=exported["result"]["document"], target=source["renderer"]
    )
    assert result["status"] == "error"
    assert result["result"]["material"] is None


@pytest.mark.parametrize("transforms", [[], {}, True, "arbitrary"])
def test_invalid_dictionary_coordinate_capability_is_a_validation_error(transforms):
    profile = json.loads((ROOT / "dictionaries/blender-cycles.json").read_text("utf-8"))
    profile["texture_coordinates"]["transforms"] = transforms
    with pytest.raises(ValidationError):
        RelayAPI([profile])


@pytest.mark.parametrize(
    "change",
    ["index", "constant", "unknown_source", "dynamic_matrix", "missing_uv", "geomprop_default"],
)
def test_external_coordinate_variations_are_not_silently_ignored(change):
    api, source = setup(
        {
            "kind": "uv",
            "set": "纹理UV" if change == "geomprop_default" else "",
            "transforms": [SCALE],
        }
    )
    document = call(api, "materialx.export", material=source)["result"]["document"]
    doc = mx.createDocument()
    mx.readFromXmlString(doc, document)
    image = next(n for n in doc.getNodes() if n.getCategory() == "image")
    uv = next(n for n in doc.getNodes() if n.getCategory() in {"texcoord", "geompropvalue"})
    transform = next(n for n in doc.getNodes() if n.getCategory() == "transformmatrix")
    if change == "index":
        uv.getInput("index").setValue(1)
    elif change == "constant":
        image.getInput("texcoord").removeAttribute("nodename")
        image.getInput("texcoord").setValue(mx.Vector2(0.2, 0.4))
    elif change == "unknown_source":
        normal = doc.addNode("normal", "normal", "vector3")
        transform.getInput("in").setConnectedNode(normal)
    elif change == "dynamic_matrix":
        matrix = doc.addNode("constant", "dynamic", "matrix44")
        matrix.addInput("value", "matrix44").setValueString(", ".join(map(str, SCALE)))
        transform.getInput("mat").removeAttribute("value")
        transform.getInput("mat").setConnectedNode(matrix)
    elif change == "missing_uv":
        expanded = next(
            n for n in doc.getNodes() if n.getCategory() == "convert" and n.getType() == "vector3"
        )
        expanded.getInput("in").removeAttribute("nodename")
        expanded.getInput("in").setValue(mx.Vector2(0, 0))
    else:
        uv.addInput("default", "vector2").setValue(mx.Vector2(0.5, 0.5))
    changed = mx.writeToXmlString(doc)
    assert call(api, "materialx.validate", document=changed)["status"] == "ok"
    result = call(api, "materialx.convert", document=changed, target=source["renderer"])
    assert result["status"] == "error"
    assert result["result"]["material"] is None
    assert result["result"]["source"] == changed
