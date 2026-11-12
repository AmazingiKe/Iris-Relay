"""功能族交换只保留接口，原生选择和完整数学实现由各目标声明。"""

from copy import deepcopy

import pytest

from _tests.test_materialx_functions import convert
from _tests.test_materialx_hsv import profile
from _tests.test_materialx_link_casts import export
from _tests.test_materialx_lowerings import rs_profile
from _tests.test_materialx_native_nodes import math_fixture
from _tests.test_maya_materialx import maya_profile
from iris_relay.materialx.core import MaterialXCore


@pytest.fixture(scope="module")
def profiles():
    return [profile(), maya_profile(), rs_profile()]


def material_with_node(profile, key):
    """用实际字典默认值构造最小表面链；测试不复制端口命名规则。"""
    _, material = math_fixture()
    surface = material["nodes"][0]
    rule = profile["nodes"][key]
    output = next(iter(rule["outputs"]))
    material["nodes"] = [
        surface,
        {
            "id": "function",
            "type": key,
            "inputs": {k: deepcopy(p["default"]) for k, p in rule["inputs"].items()},
            "outputs": [output],
        },
    ]
    material["links"] = [
        {
            "from": {"node": "function", "socket": output},
            "to": {
                "node": surface["id"],
                "socket": "Base Color"
                if rule["outputs"][output]["type"] == "color"
                else "Roughness",
            },
        }
    ]
    return material


@pytest.mark.parametrize(
    "key",
    [
        "ShaderNodeGamma",
        "ShaderNodeBrightContrast",
        "ShaderNodeMix.rgba_add_cf1_cr0",
        "ShaderNodeMixRGB.overlay_c1",
        "ShaderNodeMath.greater_than",
    ],
)
def test_function_families_export_interfaces_and_use_target_owned_implementations(profiles, key):
    source = material_with_node(profiles[0], key)
    result = export(profiles[0], source)
    assert result["result"]["document"], result
    doc = MaterialXCore().read(result["result"]["document"])
    assert not [g for g in doc.getNodeGraphs() if g.getDocument() == doc]
    assert all("blender" not in d.getName().lower() for d in doc.getNodeDefs())
    for target in profiles:
        actual = convert(target, result["result"]["document"])
        assert actual["result"]["material"], actual
        assert actual["result"]["source"] == result["result"]["document"]


def test_mix_uses_one_common_interface_for_native_layout_variants(profiles):
    core = MaterialXCore()
    names = []
    for key in ("ShaderNodeMix.rgba_overlay_cf1_cr1", "ShaderNodeMixRGB.overlay_c1"):
        document = export(profiles[0], material_with_node(profiles[0], key))["result"]["document"]
        names.append(core.read(document).getNode("function").getNodeDefString())
    assert names[0] == names[1]


def test_plain_mix_prefers_available_native_node_in_each_target(profiles):
    document = export(
        profiles[0], material_with_node(profiles[0], "ShaderNodeMix.rgba_mix_cf0_cr0")
    )["result"]["document"]
    for target in profiles[1:]:
        result = convert(target, document)
        assert result["result"]["material"], result
        assert len(result["result"]["material"]["nodes"]) == 2
        assert any(
            i["code"] == "MATERIALX_FUNCTION_NATIVE" for i in result["result"]["report"]["issues"]
        )


def test_target_cannot_borrow_blend_formula_from_source(profiles):
    source = material_with_node(profiles[0], "ShaderNodeMix.rgba_add_cf1_cr0")
    document = export(profiles[0], source)["result"]["document"]
    target = deepcopy(profiles[1])
    target["materialx_function_implementations"].pop("ND_iris_blend_color3", None)
    result = convert(target, document)
    assert result["status"] == "error"
    assert result["result"]["source"] == document


def test_static_native_extensions_all_have_function_export_rules(profiles):
    for target in profiles:
        for key, rule in target["nodes"].items():
            name = rule["semantic"].removeprefix("materialx.")
            if name.startswith("ND_iris_") and key != "ShaderNodeValToRGB":
                assert name in target["materialx_function_exports"], (target["id"], key)


@pytest.mark.parametrize("key", ["ShaderNodeGamma", "ShaderNodeMix.rgba_overlay_cf1_cr1"])
def test_math_fallback_reexports_no_private_implementation_graphs(profiles, key):
    core = MaterialXCore()
    document = export(profiles[0], material_with_node(profiles[0], key))["result"]["document"]
    target = profiles[2]
    material = convert(target, document)["result"]["material"]
    assert material
    result = export(target, material)
    assert result["result"]["document"], result
    doc = core.read(result["result"]["document"])
    assert not [g for g in doc.getNodeGraphs() if g.getDocument() == doc]
    restored = convert(profiles[0], result["result"]["document"])
    assert restored["result"]["material"], restored
