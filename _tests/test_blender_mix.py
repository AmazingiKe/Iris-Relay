"""混合节点的模式、钳制和共享连线须经过真实字典与独立数学对照。"""

import pytest

from _tests.test_materialx_hsv import evaluate, profile
from _tests.test_materialx_link_casts import export
from _tests.test_materialx_native_nodes import convert, math_fixture
from iris_relay.materialx.core import MaterialXCore


def test_additional_mix_modes_match_cycles_branch_boundaries():
    """负颜色、零除数和因子越界按独立参考计算；Dodge 因子不能放到最后混合。"""
    data = profile()
    doc = MaterialXCore().read(data["materialx_extensions"])
    modes = (
        "darken",
        "lighten",
        "screen",
        "overlay",
        "divide",
        "difference",
        "exclusion",
        "dodge",
        "burn",
        "soft_light",
        "linear_light",
    )
    for mode in modes:
        key = f"ShaderNodeMix.rgba_{mode}_cf0_cr0"
        assert key in data["nodes"], mode
        graph = next(
            g
            for g in doc.getNodeGraphs()
            if g.getNodeDefString() == data["nodes"][key]["semantic"].removeprefix("materialx.")
        )
        for factor in (-0.4, 0, 0.3, 1, 1.7):
            for a, b in (([-0.2, 0, 0.5], [0, 1, 2]), ([0.9, 1.4, 0.2], [0.3, -0.4, 0])):
                expected = []
                for x, y in zip(a, b, strict=True):
                    if mode == "dodge":
                        d = 1 - factor * y
                        z = 0 if x == 0 else 1 if d <= 0 else min(x / d, 1)
                    elif mode == "burn":
                        d = 1 - factor + factor * y
                        z = 0 if d <= 0 else min(1, max(0, 1 - (1 - x) / d))
                    elif mode == "linear_light":
                        z = x + factor * (2 * y - 1)
                    else:
                        changed = {
                            "darken": min(x, y),
                            "lighten": max(x, y),
                            "screen": 1 - (1 - x) * (1 - y),
                            "overlay": 2 * x * y if x < 0.5 else 1 - 2 * (1 - x) * (1 - y),
                            "divide": x / y if y != 0 else x,
                            "difference": abs(x - y),
                            "exclusion": x + y - 2 * x * y,
                            "soft_light": (1 - x) * y * x + x * (1 - (1 - y) * (1 - x)),
                        }[mode]
                        z = x * (1 - factor) + factor * changed
                        if mode == "exclusion":
                            z = max(0, z)
                    expected.append(z)
                assert evaluate(graph, {"factor": factor, "a": a, "b": b}) == pytest.approx(
                    expected, abs=1e-6
                ), (mode, factor, a, b)


@pytest.mark.parametrize("factor", [-0.4, 0.0, 0.35, 1.0, 1.7])
@pytest.mark.parametrize("clamp_factor", [False, True])
def test_mix_float_keeps_factor_policy_and_editable_interface(factor, clamp_factor):
    data = profile()
    key = f"ShaderNodeMix.float_mix_cf{int(clamp_factor)}_cr0"
    rule = data["nodes"][key]
    core = MaterialXCore()
    extensions = core.read(data["materialx_extensions"])
    name = rule["semantic"].removeprefix("materialx.")
    graph = next(g for g in extensions.getNodeGraphs() if g.getNodeDefString() == name)
    result = evaluate(graph, {"factor": factor, "a": -0.2, "b": 1.4})
    effective = min(1, max(0, factor)) if clamp_factor else factor
    assert result == pytest.approx(-0.2 * (1 - effective) + 1.4 * effective)


@pytest.mark.parametrize("mode", ["mix", "add", "multiply", "subtract"])
def test_mixrgb_modes_are_portable_and_can_restore_native_node(mode):
    data = profile()
    key = f"ShaderNodeMixRGB.{mode}_c0"
    assert data["nodes"][key]["native"]["properties"]["blend_type"] == mode.upper()
    _, source = math_fixture()
    source["nodes"] = [source["nodes"][0]]
    source["nodes"].append(
        {
            "id": "mix",
            "type": key,
            "inputs": {"Fac": 0.3, "Color1": [0.2, 0.4, 0.6], "Color2": [0.8, 0.3, 0.1]},
            "outputs": ["Color"],
        }
    )
    source["links"] = [
        {
            "from": {"node": "mix", "socket": "Color"},
            "to": {"node": source["nodes"][0]["id"], "socket": "Base Color"},
        }
    ]
    result = export(data, source)
    assert result["result"]["document"], result
    document = MaterialXCore().read(result["result"]["document"])
    assert len([d for d in document.getNodeDefs() if d.getDocument() == document]) == 1
    restored = convert(data, source, result["result"]["document"])
    assert restored["result"]["material"], restored
    actual = next(n for n in restored["result"]["material"]["nodes"] if n["id"] == "mix")
    assert actual["type"] == f"ShaderNodeMix.rgba_{mode}_cf1_cr0"
    assert actual["inputs"]["Factor_Float"] == pytest.approx(0.3)
    assert actual["inputs"]["A_Color"] == pytest.approx([0.2, 0.4, 0.6])
    assert actual["inputs"]["B_Color"] == pytest.approx([0.8, 0.3, 0.1])
