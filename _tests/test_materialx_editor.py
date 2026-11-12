"""编辑元数据保留源标签，展开后的函数参数必须仍由共享可编辑控制节点驱动。"""

import json
from pathlib import Path

import pytest

from _scripts.build_native_dictionaries import build
from _scripts.native_fixture import renderer
from _tests.test_materialx_bridge import call
from _tests.test_materialx_hsv import profile
from _tests.test_materialx_native_nodes import math_fixture
from iris_relay import RelayAPI


def material():
    data = profile()
    _, source = math_fixture()
    source["nodes"] = source["nodes"][:1]
    source["links"] = []
    source["textures"] = {}
    return data, source


def test_special_material_and_node_labels_make_valid_document_and_survive_import():
    data, source = material()
    source["name"] = '材质 <木头 & 金属> "A"'
    source["nodes"][0]["metadata"]["editor"] = {
        "label": "底色 <输入 & 输出>",
        "source_position": [32, 96],
    }
    exported = call(RelayAPI([data]), "materialx.export", material=source)
    assert exported["result"]["document"], exported
    validation = call(RelayAPI(), "materialx.validate", document=exported["result"]["document"])
    assert validation["status"] == "ok", validation
    converted = call(
        RelayAPI([data]),
        "materialx.convert",
        document=exported["result"]["document"],
        target=source["renderer"],
    )
    assert converted["result"]["material"]["name"] == source["name"]
    record = converted["result"]["material"]["nodes"][0]
    assert record["metadata"]["editor"]["label"] == "底色 <输入 & 输出>"
    assert record["metadata"]["editor"]["source_position"] == [32, 96]


def test_expanded_hsv_parameters_become_shared_controls_and_function_group():
    data, source = material()
    root = source["nodes"][0]["id"]
    source["nodes"].append(
        {
            "id": "source_hsv",
            "type": "ShaderNodeHueSaturation",
            "inputs": {
                "Hue": 0.27,
                "Saturation": 1.3,
                "Value": 1.1,
                "Fac": 0.6,
                "Color": [0.2, 0.4, 0.8],
            },
            "outputs": ["Color"],
            "metadata": {"editor": {"label": "色相调整"}},
        }
    )
    source["links"] = [
        {
            "from": {"node": "source_hsv", "socket": "Color"},
            "to": {"node": root, "socket": "Base Color"},
        }
    ]
    document = call(RelayAPI([data]), "materialx.export", material=source)["result"]["document"]
    path = Path(__file__).resolve().parents[1] / "dictionaries/catalogs/maya-arnold-7.2.5.3.json"
    maya = build(json.loads(path.read_text("utf-8")))
    for name, choices in maya["materialx_function_implementations"].items():
        maya["materialx_function_implementations"][name] = [
            e for e in choices if e["kind"] == "recipe"
        ]
    response = call(RelayAPI([maya]), "materialx.convert", document=document, target=renderer(maya))
    assert response["result"]["material"], response
    output = response["result"]["material"]
    controls = [
        n for n in output["nodes"] if n["metadata"].get("editor", {}).get("control") == "hue"
    ]
    assert len(controls) == 1
    assert controls[0]["inputs"]["value"] == pytest.approx(0.27)
    assert controls[0]["metadata"]["editor"]["function_path"][0] == "source_hsv"
    assert any(
        controls[0]["id"] in group["nodes"] for group in output["metadata"]["editor"]["groups"]
    )
