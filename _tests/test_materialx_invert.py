"""完整颜色反相直接复用官方 invert；非默认系数与连接不得冒充全量反相。"""

import json
from copy import deepcopy
from pathlib import Path

import MaterialX as mx
import pytest

from _scripts.build_native_dictionaries import build
from _tests.test_materialx_link_casts import export, setup
from _tests.test_materialx_native_nodes import convert


def fixture():
    """在图片与粗糙度之间插入反相，并共享结果给颜色输入。"""
    _, source = setup()
    catalog = (
        Path(__file__).resolve().parents[1] / "dictionaries/catalogs/blender-cycles-5.2.3.json"
    )
    profile = build(json.loads(catalog.read_text("utf-8")))
    edge = next(e for e in source["links"] if e["to"]["socket"] == "Roughness")
    upstream, surface = edge["from"], edge["to"]["node"]
    edge["from"] = {"node": "invert", "socket": "Color"}
    source["links"].extend(
        [
            {"from": upstream, "to": {"node": "invert", "socket": "Color"}},
            {
                "from": {"node": "invert", "socket": "Color"},
                "to": {"node": surface, "socket": "Emission Color"},
            },
        ]
    )
    source["nodes"].append(
        {
            "id": "invert",
            "type": "ShaderNodeInvert",
            "inputs": {"Fac": 1.0, "Color": [0.13, 0.47, 0.81]},
            "outputs": ["Color"],
            "metadata": {},
        }
    )
    return profile, source


def test_full_invert_preserves_input_shared_output_and_luminance_chain():
    profile, source = fixture()
    before = deepcopy(source)
    response = export(profile, source)
    assert response["status"] == "warning", response
    document = response["result"]["document"]
    xml = mx.createDocument()
    mx.readFromXmlString(xml, document)
    invert = xml.getNode("invert")
    assert invert.getNodeDefString() == "ND_invert_color3"
    assert invert.getInput("in").getNodeName()
    result = convert(profile, source, document)
    assert result["status"] == "warning", result
    restored = next(n for n in result["result"]["material"]["nodes"] if n["id"] == "invert")
    assert restored["type"] == "ShaderNodeInvert"
    assert restored["inputs"]["Fac"] == 1.0
    assert sum(e["from"]["node"] == "invert" for e in result["result"]["material"]["links"]) == 2
    assert source == before == response["result"]["source"]


@pytest.mark.parametrize("factor", [0.0, 0.37, 1.5])
def test_partial_invert_does_not_drop_factor(factor):
    profile, source = fixture()
    source["nodes"][-1]["inputs"]["Fac"] = factor
    response = export(profile, source)
    assert response["status"] == "error"
    assert response["result"]["document"] is None
    assert response["result"]["source"] == source


def test_connected_default_factor_requires_explicit_blending():
    profile, source = fixture()
    source["nodes"].append(
        {
            "id": "factor",
            "type": "ShaderNodeMath.add",
            "inputs": {"Value": 0.4, "Value_001": 0.6},
            "outputs": ["Value"],
        }
    )
    source["links"].append(
        {"from": {"node": "factor", "socket": "Value"}, "to": {"node": "invert", "socket": "Fac"}}
    )
    result = export(profile, source)
    assert result["status"] == "error" and result["result"]["document"] is None


def test_nondefault_materialx_amount_is_not_a_blender_factor():
    profile, source = fixture()
    response = export(profile, source)
    assert response["status"] == "warning", response
    xml = mx.createDocument()
    mx.readFromXmlString(xml, response["result"]["document"])
    xml.getNode("invert").addInput("amount", "color3").setValueString("0.5, 0.7, 1.3")
    document = mx.writeToXmlString(xml)
    result = convert(profile, source, document)
    assert result["status"] == "error" and result["result"]["material"] is None
    assert result["result"]["source"] == document
