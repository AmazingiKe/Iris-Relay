"""用独立 Cycles 公式核对导出的标准节点子图，并检查 HSV 参数及共享连接往返。"""

import json
import math
import random
from pathlib import Path

import pytest

from _scripts.build_native_dictionaries import build
from _tests.test_materialx_link_casts import export, setup
from _tests.test_materialx_native_nodes import convert
from iris_relay.materialx_core import MaterialXCore


def profile():
    """测试实际字典生成器，避免夹具绕过产品中的原生端口声明。"""
    catalog = (
        Path(__file__).resolve().parents[1] / "dictionaries/catalogs/blender-cycles-5.2.3.json"
    )
    return build(json.loads(catalog.read_text("utf-8")))


def cycles_hsv(color, hue, saturation, value, factor):
    """按 Cycles 5.2.3 的分支公式独立求值，覆盖负颜色、近灰及非单位混合。"""
    maximum, minimum = max(color), min(color)
    delta = maximum - minimum
    sat = delta / maximum if maximum != 0 else 0
    h = 0
    if sat != 0:
        c = [(maximum - channel) / delta for channel in color]
        h = (
            c[2] - c[1]
            if color[0] == maximum
            else (2 + c[0] - c[2] if color[1] == maximum else 4 + c[1] - c[0])
        )
        h /= 6
        if h < 0:
            h += 1
    h = (h + hue + 0.5) % 1
    sat = min(1, max(0, sat * saturation))
    v = maximum * value
    if sat == 0:
        adjusted = [v] * 3
    else:
        sector = math.floor(h * 6)
        f = h * 6 - sector
        p, q, t = v * (1 - sat), v * (1 - sat * f), v * (1 - sat * (1 - f))
        adjusted = [(v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q)][sector]
    return [max(0, factor * a + (1 - factor) * c) for a, c in zip(adjusted, color, strict=True)]


def evaluate(graph, parameters):
    """按标准运算解释实际 XML 子图；不调用字典生成器中的计算实现。"""
    cache = {}

    def input_value(port):
        if port.getInterfaceName():
            return parameters[port.getInterfaceName()]
        if port.getNodeName():
            return node_value(graph.getNode(port.getNodeName()))
        value = port.getValue()
        return value if isinstance(value, (float, int)) else list(value)

    def binary(a, b, operation):
        if isinstance(a, list) or isinstance(b, list):
            a = a if isinstance(a, list) else [a] * 3
            b = b if isinstance(b, list) else [b] * 3
            return [operation(x, y) for x, y in zip(a, b, strict=True)]
        return operation(a, b)

    def node_value(node):
        if node.getName() in cache:
            return cache[node.getName()]
        ports = {
            p.getName(): input_value(node.getInput(p.getName()) or p)
            for p in node.getNodeDef().getActiveInputs()
        }
        category = node.getCategory()
        operations = {
            "add": lambda a, b: a + b,
            "subtract": lambda a, b: a - b,
            "multiply": lambda a, b: a * b,
            "divide": lambda a, b: a / b,
            "max": max,
            "min": min,
        }
        if category in operations:
            result = binary(ports["in1"], ports["in2"], operations[category])
        elif category == "extract":
            result = ports["in"][ports["index"]]
        elif category == "floor":
            result = math.floor(ports["in"])
        elif category == "clamp":
            result = min(ports["high"], max(ports["low"], ports["in"]))
        elif category in {"ifequal", "ifgreater"}:
            condition = (
                ports["value1"] == ports["value2"]
                if category == "ifequal"
                else ports["value1"] > ports["value2"]
            )
            result = ports["in1"] if condition else ports["in2"]
        elif category == "combine3":
            result = [ports[f"in{i}"] for i in (1, 2, 3)]
        elif category == "mix":
            result = [
                f * ports["mix"] + b * (1 - ports["mix"])
                for f, b in zip(ports["fg"], ports["bg"], strict=True)
            ]
        else:
            raise AssertionError(f"未经独立解释的运算：{category}")
        cache[node.getName()] = result
        return result

    return node_value(graph.getNode(graph.getOutput("out").getNodeName()))


def test_hsv_standard_graph_matches_cycles_over_boundaries_and_random_inputs():
    core = MaterialXCore()
    document = core.read(profile()["materialx_extensions"])
    graph = document.getNodeGraph("NG_iris_blender_hsv_color3")
    assert graph is not None
    rng = random.Random(192)
    colors = [[0, 0, 0], [1, 1, 1], [1, 1 - 1e-6, 1], [0, -1, -2], [-1, -2, -3], [10, 0.1, 2]]
    parameters = [
        (c, h, s, v, f)
        for c in colors
        for h, s, v, f in [(0.5, 1, 1, 1), (1, 2, 3, 0.4), (-0.2, -2, -1, 1.5), (0, 0, 2, 0)]
    ]
    parameters.extend(
        (
            [rng.uniform(-3, 5) for _ in range(3)],
            rng.uniform(-2, 2),
            rng.uniform(-2, 3),
            rng.uniform(-1, 4),
            rng.uniform(-1, 2),
        )
        for _ in range(500)
    )
    for color, hue, saturation, value, factor in parameters:
        actual = evaluate(
            graph, dict(color=color, hue=hue, saturation=saturation, value=value, factor=factor)
        )
        assert actual == pytest.approx(
            cycles_hsv(color, hue, saturation, value, factor), abs=2e-6, rel=2e-6
        )


def test_hsv_shared_color_and_linked_factor_roundtrip_without_source_snapshot():
    _, source = setup()
    source["nodes"].append(
        {
            "id": "hsv",
            "type": "ShaderNodeHueSaturation",
            "inputs": {
                "Hue": 0.27,
                "Saturation": 1.7,
                "Value": 2.3,
                "Fac": 0.37,
                "Color": [0.13, 0.47, 0.81],
            },
            "outputs": ["Color"],
        }
    )
    edge = next(e for e in source["links"] if e["to"]["socket"] == "Roughness")
    upstream, surface = edge["from"], edge["to"]["node"]
    edge["from"] = {"node": "hsv", "socket": "Color"}
    source["links"].extend(
        [
            {"from": upstream, "to": {"node": "hsv", "socket": "Color"}},
            {
                "from": {"node": "hsv", "socket": "Color"},
                "to": {"node": surface, "socket": "Emission Color"},
            },
            {"from": {"node": "factor", "socket": "Value"}, "to": {"node": "hsv", "socket": "Fac"}},
        ]
    )
    source["nodes"].append(
        {
            "id": "factor",
            "type": "ShaderNodeMath.multiply",
            "inputs": {"Value": 0.5, "Value_001": 0.7},
            "outputs": ["Value"],
        }
    )
    source["nodes"][-1]["metadata"] = {}
    source["nodes"][-2]["metadata"] = {}
    dictionary = profile()
    result = export(dictionary, source)
    assert result["status"] == "warning", result
    restored = convert(dictionary, source, result["result"]["document"])
    assert restored["status"] == "warning", restored
    actual = next(n for n in restored["result"]["material"]["nodes"] if n["id"] == "hsv")
    assert actual["type"] == "ShaderNodeHueSaturation"
    assert {k: actual["inputs"][k] for k in ("Hue", "Saturation", "Value")} == pytest.approx(
        {"Hue": 0.27, "Saturation": 1.7, "Value": 2.3}
    )
    links = restored["result"]["material"]["links"]
    assert {
        "from": {"node": "factor", "socket": "Value"},
        "to": {"node": "hsv", "socket": "Fac"},
    } in links
    assert sum(e["from"] == {"node": "hsv", "socket": "Color"} for e in links) == 2
