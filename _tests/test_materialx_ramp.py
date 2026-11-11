"""原生渐变的配置编译为标准计算；两个输出及原生可编辑配置可以往返。"""

import json
import struct

import pytest

from _tests.test_materialx_hsv import evaluate, profile
from _tests.test_materialx_link_casts import export
from _tests.test_materialx_native_nodes import convert, math_fixture
from iris_relay.materialx.core import MaterialXCore


def ramp_source(mode="LINEAR"):
    """包含非默认位置与 Alpha，确保颜色和 Alpha 不能相互冒充。"""
    data = profile()
    assert "ShaderNodeValToRGB" in data["nodes"]
    config = {
        "color_mode": "RGB",
        "hue_interpolation": "NEAR",
        "interpolation": mode,
        "elements": [
            {"position": 0.2, "color": [0.1, 0.2, 0.3, 0.4]},
            {"position": 0.8, "color": [0.9, 0.8, 0.7, 0.6]},
        ],
    }
    _, source = math_fixture()
    source["nodes"] = [
        source["nodes"][0],
        {
            "id": "ramp",
            "type": "ShaderNodeValToRGB",
            "inputs": {"Fac": 0.37, "color_ramp": json.dumps(config)},
            "outputs": ["Color", "Alpha"],
        },
    ]
    source["links"] = [
        {
            "from": {"node": "ramp", "socket": "Color"},
            "to": {"node": "surface_standard", "socket": "Base Color"},
        },
        {
            "from": {"node": "ramp", "socket": "Alpha"},
            "to": {"node": "surface_standard", "socket": "Roughness"},
        },
    ]
    return data, source, config


@pytest.mark.parametrize("mode", ["LINEAR", "EASE", "CONSTANT"])
def test_ramp_portable_math_matches_piecewise_rgba_and_native_roundtrip(mode):
    data, source, config = ramp_source(mode)
    result = export(data, source)
    assert result["result"]["document"], result
    core = MaterialXCore()
    doc = core.read(result["result"]["document"])
    graph = next(
        g for g in doc.getNodeGraphs() if g.getAttribute("iris_recipe") == "blender.color_ramp"
    )
    for factor in [-1, 0.2, 0.37, 0.5, 0.8, 1.5]:
        # Blender RNA、evaluate 与 MaterialX float 都使用 32 位数值，边界须在同一域比较。
        factor = struct.unpack("f", struct.pack("f", factor))[0]
        t = min(1, max(0, (factor - 0.2) / 0.6))
        t = (
            (0 if factor < 0.8 else 1)
            if mode == "CONSTANT"
            else t * t * (3 - 2 * t)
            if mode == "EASE"
            else t
        )
        expected = [
            a * (1 - t) + b * t
            for a, b in zip(
                config["elements"][0]["color"], config["elements"][1]["color"], strict=True
            )
        ]
        assert evaluate(graph, {"factor": factor}, output_name="color") == pytest.approx(
            expected[:3]
        )
        assert evaluate(graph, {"factor": factor}, output_name="alpha") == pytest.approx(
            expected[3]
        )
    restored = convert(data, source, result["result"]["document"])
    assert restored["result"]["material"], restored
    node = next(n for n in restored["result"]["material"]["nodes"] if n["id"] == "ramp")
    assert node["type"] == "ShaderNodeValToRGB"
    assert json.loads(node["inputs"]["color_ramp"]) == config
    assert sorted(restored["result"]["material"]["links"], key=str) == sorted(
        source["links"], key=str
    )


def test_tampered_compiled_ramp_cannot_restore_as_native_color_ramp():
    data, source, _ = ramp_source()
    result = export(data, source)
    assert result["result"]["document"], result
    document = result["result"]["document"].replace(
        'nodedef="ND_add_float"', 'nodedef="ND_multiply_float"'
    )
    restored = convert(data, source, document)
    assert restored["status"] == "error" and restored["result"]["material"] is None


def test_bspline_keeps_blender_endpoint_blending():
    from iris_relay.materialx.recipes import DEFAULT_RAMP, ramp_library

    config = dict(DEFAULT_RAMP, interpolation="B_SPLINE")
    doc, _, _, _ = ramp_library(MaterialXCore(), config)
    graph = next(g for g in doc.getNodeGraphs() if g.getAttribute("iris_recipe"))
    for factor in [-0.1, 0, 0.123, 0.5, 0.91, 1, 1.1]:
        t = min(1, max(0, factor))
        expected = (1 + 3 * t + 3 * t * t - 2 * t * t * t) / 6
        assert evaluate(graph, {"factor": factor}, output_name="color") == pytest.approx(
            [expected] * 3
        )
