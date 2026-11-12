"""可变配置也交换功能接口；目标字典明确声明是否生成替代配方。"""

from copy import deepcopy

import pytest

from _tests.test_materialx_functions import convert
from _tests.test_materialx_link_casts import export
from _tests.test_materialx_lowerings import rs_profile
from _tests.test_materialx_ramp import ramp_source
from _tests.test_maya_materialx import maya_profile
from _tests.test_normal_uv_context import source as normal_source
from iris_relay.materialx.core import MaterialXCore


@pytest.mark.parametrize("family", ["ramp", "normal"])
def test_variable_configurations_export_no_implementation_graph(family):
    source, material = ramp_source()[:2] if family == "ramp" else normal_source()
    result = export(source, material)
    assert result["result"]["document"], result
    core = MaterialXCore()
    doc = core.read(result["result"]["document"])
    assert not [g for g in doc.getNodeGraphs() if g.getDocument() == doc]
    for target in (source, maya_profile(), rs_profile()):
        converted = convert(target, result["result"]["document"])
        assert converted["result"]["material"], converted
    target = deepcopy(maya_profile())
    target.pop("materialx_function_recipes", None)
    rejected = convert(target, result["result"]["document"])
    assert rejected["status"] == "error"
    assert rejected["result"]["source"] == result["result"]["document"]


def test_two_ramp_configurations_share_interface_and_restore_distinct_parameters():
    source, material, config = ramp_source()
    second = deepcopy(material["nodes"][-1])
    second["id"] = "ramp_two"
    second["inputs"]["color_ramp"] = second["inputs"]["color_ramp"].replace("LINEAR", "EASE")
    material["nodes"].append(second)
    material["links"][1]["from"]["node"] = "ramp_two"
    document = export(source, material)["result"]["document"]
    assert document
    doc = MaterialXCore().read(document)
    assert doc.getNode("ramp").getNodeDefString() == doc.getNode("ramp_two").getNodeDefString()
    result = convert(source, document)
    assert result["result"]["material"], result
    nodes = {n["id"]: n for n in result["result"]["material"]["nodes"]}
    assert "LINEAR" in nodes["ramp"]["inputs"]["color_ramp"]
    assert "EASE" in nodes["ramp_two"]["inputs"]["color_ramp"]


@pytest.mark.parametrize("shared", [False, True])
def test_unsupported_recipe_only_blocks_when_required_by_supported_inputs(shared):
    """细节独占的未知配置可以归档；共用该支路的主要输入仍须阻断。"""
    import json

    from _tests.test_materialx_functions import hsv_document

    target, material, _ = ramp_source()
    core = MaterialXCore()
    ramp_doc = core.read(export(target, material)["result"]["document"])
    doc = core.read(hsv_document())
    definition = ramp_doc.getNodeDef("ND_iris_color_ramp")
    doc.addChildOfCategory("nodedef", definition.getName()).copyContentFrom(definition)
    ramp = doc.addNodeInstance(doc.getNodeDef(definition.getName()), "future_ramp")
    config = json.loads(ramp_doc.getNode("ramp").getInput("configuration").getValueString())
    config["interpolation"] = "FUTURE_MODE"
    ramp.addInput("configuration", "string").setValueString(json.dumps(config))
    hsv = doc.getNode("hsv")
    hsv.getNodeDef().addInput("detail_strength", "float").setValue(0.0)
    port = hsv.addInput("detail_strength", "float")
    port.setConnectedNode(ramp)
    port.setOutputString("alpha")
    if shared:
        port = hsv.getInput("mix")
        port.removeAttribute("value")
        port.setConnectedNode(ramp)
        port.setOutputString("alpha")
    document = core.serialize(doc)
    result = convert(target, document)
    assert result["result"]["source"] == document
    if shared:
        assert result["result"]["material"] is None
        assert any(
            i["code"] == "MATERIALX_RECIPE_UNSUPPORTED"
            for i in result["result"]["report"]["issues"]
        )
    else:
        assert result["result"]["material"], result
        archive = result["result"]["material"]["metadata"]["deferred_details"]
        assert any("FUTURE_MODE" in snapshot for snapshot in archive["documents"].values())


def test_native_recipe_requires_matching_configuration_binding():
    """存在同名原生节点还不够，配置编译器也须与其绑定一致。"""
    from iris_relay.dictionary import Dictionary
    from iris_relay.model import ValidationError

    target, _, _ = ramp_source()
    target["materialx_function_recipes"]["ND_iris_color_ramp"]["native"] = "ShaderNodeNormalMap"
    with pytest.raises(ValidationError, match="配方.*绑定"):
        Dictionary.parse(target)
