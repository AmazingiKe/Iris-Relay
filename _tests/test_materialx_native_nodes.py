"""原生数学绑定直接引用官方 NodeDef，不扩展手工维护的通用节点词汇。"""

import json
from copy import deepcopy
from pathlib import Path

import MaterialX as mx
import pytest

from _scripts.native_fixture import renderer
from _tests.test_materialx_link_casts import export, setup
from iris_relay import RelayAPI


def math_fixture(operation="multiply"):
    """建立两个数学节点和共享上游，验证符号、输入顺序与实际连接。"""
    profile, source = setup()
    profile["nodes"] = {
        k: n for k, n in profile["nodes"].items() if not n["semantic"].startswith("materialx.")
    }
    # 此夹具刻意只保留旧语义与一个数学节点，相应移除已经不在节点集合中的配方。
    profile.pop("materialx_recipes", None)
    profile.pop("materialx_function_recipes", None)
    profile.pop("materialx_function_exports", None)
    profile.pop("materialx_function_implementations", None)
    profile.pop("materialx_lowerings", None)
    source["links"] = []
    source["nodes"] = [n for n in source["nodes"] if n["type"] == "ShaderNodeBsdfPrincipled"]
    source["textures"] = {}
    profile["nodes"]["math"] = {
        "semantic": f"materialx.ND_{operation}_float",
        "native": {
            "id": "ShaderNodeMath",
            "properties": {"operation": operation.upper(), "use_clamp": False},
        },
        "inputs": {
            "Value": {"semantic": "in1", "type": "float", "default": 0.5},
            "Value_001": {"semantic": "in2", "type": "float", "default": 0.5},
        },
        "outputs": {"Value": {"semantic": "out", "type": "float"}},
    }
    source["nodes"].extend(
        [
            {
                "id": "first",
                "type": "math",
                "inputs": {"Value": -2.5, "Value_001": 4.0},
                "outputs": ["Value"],
            },
            {
                "id": "second",
                "type": "math",
                "inputs": {"Value": 0.0, "Value_001": 0.2},
                "outputs": ["Value"],
            },
        ]
    )
    source["links"] = [
        {"from": {"node": "first", "socket": "Value"}, "to": {"node": "second", "socket": "Value"}},
        {
            "from": {"node": "first", "socket": "Value"},
            "to": {"node": "surface_standard", "socket": "Metallic"},
        },
        {
            "from": {"node": "second", "socket": "Value"},
            "to": {"node": "surface_standard", "socket": "Roughness"},
        },
    ]
    for node in source["nodes"]:
        node.setdefault("metadata", {})
    return profile, source


def convert(profile, source, document):
    """只经公开 API 回读独立 XML，不携带原生源图。"""
    return RelayAPI([profile]).handle(
        {
            "api_version": "1",
            "request_id": "standard-binding",
            "operation": "materialx.convert",
            "payload": {"document": document, "target": source["renderer"]},
        }
    )


@pytest.mark.parametrize("operation", ["add", "subtract", "multiply"])
def test_direct_official_binding_preserves_values_and_shared_links(operation):
    profile, source = math_fixture(operation)
    before = deepcopy(source)
    exported = export(profile, source)
    assert exported["status"] == "warning", exported
    assert source == before == exported["result"]["source"]
    document = exported["result"]["document"]
    xml = mx.createDocument()
    mx.readFromXmlString(xml, document)
    assert not xml.getNodeDefs()
    assert xml.getNode("first").getNodeDefString() == f"ND_{operation}_float"
    assert xml.getNode("first").getInput("in1").getValue() == -2.5
    result = convert(profile, source, document)
    assert result["status"] == "warning", result
    target = result["result"]["material"]
    assert sorted(target["links"], key=str) == sorted(source["links"], key=str)
    for name in ("first", "second"):
        actual = next(n for n in target["nodes"] if n["id"] == name)
        expected = next(n for n in source["nodes"] if n["id"] == name)
        assert actual["inputs"] == pytest.approx(expected["inputs"])


@pytest.mark.parametrize(
    "change", ["unknown_definition", "wrong_input", "wrong_type", "wrong_output"]
)
def test_official_binding_must_match_sdk_contract(change):
    profile, source = math_fixture()
    rule = profile["nodes"]["math"]
    if change == "unknown_definition":
        rule["semantic"] = "materialx.ND_not_an_official_node"
    elif change == "wrong_input":
        rule["inputs"]["Value"]["semantic"] = "misspelled"
    elif change == "wrong_type":
        rule["inputs"]["Value"].update(type="color", default=[0, 0, 0])
        for node in source["nodes"][1:]:
            node["inputs"]["Value"] = [0, 0, 0]
    else:
        rule["outputs"]["Value"]["semantic"] = "wrong"
    result = export(profile, source)
    assert result["status"] == "error", result
    assert result["result"]["document"] is None
    assert result["result"]["source"] == source
    assert "MATERIALX_BINDING_INVALID" in {i["code"] for i in result["result"]["report"]["issues"]}


def test_target_without_official_binding_blocks_and_keeps_document():
    profile, source = math_fixture()
    document = export(profile, source)["result"]["document"]
    del profile["nodes"]["math"]
    result = convert(profile, source, document)
    assert result["status"] == "error"
    assert result["result"]["material"] is None
    assert result["result"]["source"] == document


@pytest.mark.parametrize("operation", ["add", "subtract", "multiply"])
def test_redshift_uses_scalar_math_and_keeps_input_order(operation):
    profile, source = math_fixture(operation)
    document = export(profile, source)["result"]["document"]
    target = json.loads(
        (Path(__file__).resolve().parents[1] / "dictionaries/cinema4d-redshift.json").read_text(
            "utf-8"
        )
    )
    source["renderer"] = renderer(target)
    result = convert(target, source, document)
    assert result["status"] == "warning", result
    first = next(n for n in result["result"]["material"]["nodes"] if n["id"] == "first")
    rule = target["nodes"][first["type"]]
    values = {rule["inputs"][key]["semantic"]: value for key, value in first["inputs"].items()}
    assert values == {"in1": -2.5, "in2": 4.0}
