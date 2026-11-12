"""功能交换保持紧凑接口；实现选择只使用目标字典。"""

from copy import deepcopy

import pytest

from _scripts.native_fixture import renderer
from _tests.test_materialx_hsv import profile
from _tests.test_maya_materialx import hsv_document, maya_profile
from iris_relay import RelayAPI
from iris_relay.materialx.core import MaterialXCore


def convert(target, document):
    """通过公开 API 检查目标实际生成图，源文档仅作为输入。"""
    return RelayAPI([target]).handle(
        {
            "api_version": "1",
            "request_id": "functions",
            "operation": "materialx.convert",
            "payload": {"document": document, "target": renderer(target)},
        }
    )


def test_export_color_correction_contains_interface_without_math_implementation():
    core = MaterialXCore()
    doc = core.read(hsv_document())
    node = doc.getNode("hsv")
    definition = node.getNodeDef()
    assert definition.getInheritString() == "ND_colorcorrect_color3"
    assert node.getInput("gain").getValue() == pytest.approx(2.3)
    assert node.getInput("mix").getValue() == pytest.approx(0.37)
    assert node.getInput("hue_center").getValue() == 0.5
    assert not [g for g in doc.getNodeGraphs() if g.getDocument() == doc]
    assert "blender" not in definition.getName()
    assert doc.validate()[0]


def test_native_function_mapping_wins_over_math_recipe():
    response = convert(profile(), hsv_document())
    assert response["status"] != "error", response
    nodes = response["result"]["material"]["nodes"]
    assert next(n for n in nodes if n["id"] == "hsv")["type"] == "ShaderNodeHueSaturation"
    assert not any(n["type"].startswith("ShaderNodeMath") for n in nodes)


def test_missing_target_function_recipe_does_not_use_source_formula():
    target = deepcopy(maya_profile())
    target.pop("materialx_function_implementations", None)
    response = convert(target, hsv_document())
    assert response["status"] == "error"
    assert response["result"]["material"] is None
    assert any(
        i["code"] == "MATERIALX_FUNCTION_UNSUPPORTED"
        for i in response["result"]["report"]["issues"]
    )


def test_target_recipe_is_applied_only_in_conversion_working_graph():
    document = hsv_document()
    target = maya_profile()
    for name, choices in target["materialx_function_implementations"].items():
        target["materialx_function_implementations"][name] = [
            e for e in choices if e["kind"] == "recipe"
        ]
    response = convert(target, document)
    assert response["status"] != "error", response
    assert response["result"]["source"] == document
    assert any(
        i["code"] == "MATERIALX_FUNCTION_RECIPE" for i in response["result"]["report"]["issues"]
    )
    doc = MaterialXCore().read(document)
    assert not [g for g in doc.getNodeGraphs() if g.getDocument() == doc]


def test_arnold_prefers_native_color_correction_with_small_auxiliary_graph():
    response = convert(maya_profile(), hsv_document())
    assert response["status"] != "error", response
    nodes = response["result"]["material"]["nodes"]
    assert sum(n["type"] == "color_correct" for n in nodes) == 1
    assert not any(n["type"] == "floor_float" for n in nodes)
    assert len(nodes) < 12


def test_redshift_prefers_native_color_correction():
    from _tests.test_materialx_lowerings import rs_profile

    response = convert(rs_profile(), hsv_document())
    assert response["status"] != "error", response
    nodes = response["result"]["material"]["nodes"]
    assert sum(n["type"] == "ND_colorcorrect_color3" for n in nodes) == 1
    assert not any(n["type"] == "ND_floor_float" for n in nodes)
    assert len(nodes) < 35


def test_missing_native_binding_selects_target_math_fallback():
    target = maya_profile()
    target["nodes"].pop("color_correct")
    response = convert(target, hsv_document())
    assert response["status"] != "error", response
    assert any(n["type"] == "floor_float" for n in response["result"]["material"]["nodes"])
    assert any(
        i["code"] == "MATERIALX_FUNCTION_RECIPE" for i in response["result"]["report"]["issues"]
    )


@pytest.mark.parametrize("linked", [False, True, "unsupported_function"])
def test_unknown_detail_is_preserved_and_reported_without_blocking_base_function(linked):
    core = MaterialXCore()
    doc = core.read(hsv_document())
    node = doc.getNode("hsv")
    definition = node.getNodeDef()
    definition.addInput("detail_strength", "float").setValue(0.0)
    port = node.addInput("detail_strength", "float")
    if linked:
        if linked == "unsupported_function":
            definition = doc.addNodeDef("ND_future_detail", "float", "future_detail")
            definition.setAttribute("iris_function_version", "1")
            upstream = doc.addNodeInstance(definition, "detail_control")
        else:
            upstream = doc.addNodeInstance(
                core.library.getNodeDef("ND_constant_float"), "detail_control"
            )
            upstream.addInput("value", "float").setValue(0.7)
        port.setConnectedNode(upstream)
    else:
        port.setValue(0.7)
    document = core.serialize(doc)
    response = convert(profile(), document)
    assert response["status"] != "error", response
    assert response["result"]["source"] == document
    archive = response["result"]["material"]["metadata"]["deferred_details"]
    snapshot = next(iter(archive["documents"].values()))
    assert 'name="detail_strength"' in snapshot
    if linked:
        assert 'nodename="detail_control"' in snapshot
    assert any(
        i["code"] == "MATERIALX_DETAIL_UNAPPLIED" and i["port"] == "detail_strength"
        for i in response["result"]["report"]["issues"]
    )


def test_function_declaration_cannot_hide_a_modified_implementation():
    core = MaterialXCore()
    doc = core.read(hsv_document())
    graph = doc.addNodeGraph("alternate")
    graph.setNodeDefString(doc.getNode("hsv").getNodeDefString())
    constant = graph.addNodeInstance(core.library.getNodeDef("ND_constant_color3"), "changed")
    constant.addInput("value", "color3").setValueString("0,0,0")
    graph.addOutput("out", "color3").setConnectedNode(constant)
    response = convert(profile(), core.serialize(doc))
    assert response["status"] == "error"
    assert response["result"]["material"] is None


def test_integer_detail_is_archived_without_requiring_target_support():
    """计数类细节同样允许保留，不能因目标暂不支持整数参数阻断主功能。"""
    core = MaterialXCore()
    doc = core.read(hsv_document())
    node = doc.getNode("hsv")
    node.getNodeDef().addInput("iteration_count", "integer").setValue(0)
    node.addInput("iteration_count", "integer").setValue(3)
    result = convert(profile(), core.serialize(doc))
    assert result["result"]["material"], result
    archive = result["result"]["material"]["metadata"]["deferred_details"]
    assert archive["records"][0]["ports"] == ["iteration_count"]


def test_unsupported_detail_branch_still_blocks_when_required_by_main_function():
    core = MaterialXCore()
    doc = core.read(hsv_document())
    node = doc.getNode("hsv")
    node.getNodeDef().addInput("detail_strength", "float").setValue(0.0)
    future = doc.addNodeDef("ND_future_detail", "float", "future_detail")
    future.setAttribute("iris_function_version", "1")
    upstream = doc.addNodeInstance(future, "shared_control")
    node.addInput("detail_strength", "float").setConnectedNode(upstream)
    node.getInput("mix").removeAttribute("value")
    node.getInput("mix").setConnectedNode(upstream)
    document = core.serialize(doc)
    result = convert(profile(), document)
    assert result["status"] == "error"
    assert result["result"]["source"] == document
    assert result["result"]["material"] is None
    assert any(
        i["code"] == "MATERIALX_FUNCTION_UNSUPPORTED" for i in result["result"]["report"]["issues"]
    )


def test_unapplied_details_survive_reexport_without_replaying_supported_inputs():
    from _tests.test_materialx_link_casts import export

    core = MaterialXCore()
    doc = core.read(hsv_document())
    node = doc.getNode("hsv")
    node.getNodeDef().addInput("detail_strength", "float").setValue(0.0)
    node.addInput("detail_strength", "float").setValue(0.7)
    target = profile()
    first = convert(target, core.serialize(doc))
    material = first["result"]["material"]
    archive = material["metadata"]["deferred_details"]
    assert archive["records"][0]["ports"] == ["detail_strength"]
    assert 'name="detail_strength"' in archive["documents"][archive["records"][0]["document"]]
    # 修改真实目标输入；存档不能把用户编辑还原成最初的值。
    hsv = next(n for n in material["nodes"] if n["type"] == "ShaderNodeHueSaturation")
    hsv["inputs"]["Value"] = 0.83
    document = export(target, material)["result"]["document"]
    second = convert(target, document)
    actual = second["result"]["material"]
    assert actual["metadata"]["deferred_details"] == archive
    assert next(n for n in actual["nodes"] if n["type"] == "ShaderNodeHueSaturation")["inputs"][
        "Value"
    ] == pytest.approx(0.83)
    assert export(target, actual)["result"]["document"].count("iris_deferred_details=") == 1


def test_legacy_inline_hsv_remains_readable_and_is_not_replaced_by_labels():
    from _tests.test_materialx_link_casts import export

    # 用原导出契约生成历史结构，验证旧图仍按其完整实现核验。
    target = profile()
    legacy = deepcopy(target)
    legacy.pop("materialx_function_exports")
    core = MaterialXCore()
    doc = core.read(hsv_document())
    response = convert(target, core.serialize(doc))
    material = response["result"]["material"]
    old = export(legacy, material)["result"]["document"]
    assert "NG_iris_blender_hsv_color3" in old
    assert convert(target, old)["status"] != "error"
    altered = core.read(old)
    altered.getNodeGraph("NG_iris_blender_hsv_color3").getNode("nonnegative").getInput(
        "in2"
    ).setValue(0.9)
    assert convert(target, core.serialize(altered))["status"] == "error"


def test_export_rule_cannot_replace_a_connected_input_with_a_constant():
    from iris_relay.materialx.functions import validate_function_rules
    from iris_relay.model import ValidationError

    rules = {
        "materialx_function_exports": {
            "ND_source": {
                "definition": "ND_function",
                "inputs": {"factor": "mix"},
                "constants": {"mix": 1.0},
            }
        }
    }
    with pytest.raises(ValidationError, match="常量.*映射"):
        validate_function_rules(rules)


@pytest.mark.parametrize("damage", ["version", "digest", "reference", "ports"])
def test_deferred_details_reject_corrupt_archive(damage):
    from hashlib import sha256

    from iris_relay.materialx.preserve import deferred_details
    from iris_relay.model import ValidationError

    document = '<materialx version="1.39" />'
    digest = sha256(document.encode("utf-8")).hexdigest()
    archive = {
        "version": "1",
        "documents": {digest: document},
        "records": [{"node": "correction", "ports": ["detail_strength"], "document": digest}],
    }
    if damage == "version":
        archive["version"] = "2"
    elif damage == "digest":
        archive["documents"][digest] += "changed"
    elif damage == "reference":
        archive["records"][0]["document"] = "missing"
    else:
        archive["records"][0]["ports"] = []
    with pytest.raises(ValidationError):
        deferred_details(archive)
