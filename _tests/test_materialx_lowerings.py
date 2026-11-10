"""目标声明组合实现时仍保留计算连接；非恒定通道和篡改扩展不能放行。"""

import json
from pathlib import Path

import pytest

from _scripts.build_native_dictionaries import build
from _scripts.native_fixture import renderer
from _tests.test_maya_materialx import hsv_document
from iris_relay import RelayAPI


def rs_profile():
    return build(
        json.loads(Path("dictionaries/catalogs/cinema4d-redshift-2025.3.0.json").read_text("utf-8"))
    )


def test_complete_hsv_lowers_to_editable_redshift_math_graph():
    target = rs_profile()
    response = RelayAPI([target]).handle(
        {
            "api_version": "1",
            "request_id": "rs",
            "operation": "materialx.convert",
            "payload": {"document": hsv_document(), "target": renderer(target)},
        }
    )
    assert response["status"] == "warning", response
    graph = response["result"]["material"]
    assert len(graph["nodes"]) > 80
    assert len(graph["links"]) > 100
    assert any("rsmathsign" in target["nodes"][n["type"]]["native"]["id"] for n in graph["nodes"])
    assert any(i["code"] == "MATERIALX_LOWERED" for i in response["result"]["report"]["issues"])


def test_malformed_target_lowering_is_rejected():
    profile = rs_profile()
    profile["materialx_lowerings"] = {
        "ND_extract_color3": [{"when": {}, "replacement": "ND_missing"}]
    }
    response = RelayAPI([profile]).handle(
        {
            "api_version": "1",
            "request_id": "bad",
            "operation": "materialx.convert",
            "payload": {"document": hsv_document(), "target": renderer(profile)},
        }
    )
    assert response["status"] == "error"
    assert response["result"]["material"] is None


def test_target_lowering_does_not_rewrite_verified_gray_image_helpers():
    from _tests.test_materialx_alpha import setup
    from _tests.test_materialx_bridge import call

    api, source = setup(channels=1, linked=False)
    document = call(api, "materialx.export", material=source)["result"]["document"]
    target = rs_profile()
    response = call(
        RelayAPI([target]), "materialx.convert", document=document, target=renderer(target)
    )
    assert response["status"] == "warning", response
    assert response["result"]["material"] is not None


def test_target_lowering_definition_collision_is_not_silently_overridden():
    import MaterialX as mx

    from _tests.test_materialx_bridge import call
    from iris_relay.materialx_core import MaterialXCore

    target = rs_profile()
    core = MaterialXCore()
    doc = core.read(hsv_document())
    extensions = core.read(target["materialx_extensions"])
    modified = (
        extensions.getNodeGraph("NG_iris_rs_lower_ifgreater_float").getNodes()[0].getInput("in1")
    )
    modified.removeAttribute("interfacename")
    modified.setValueString("123")
    doc.importLibrary(extensions)
    response = call(
        RelayAPI([target]),
        "materialx.convert",
        document=mx.writeToXmlString(doc),
        target=renderer(target),
    )
    assert response["status"] == "error"
    assert any(
        i["code"] == "MATERIALX_BINDING_INVALID" for i in response["result"]["report"]["issues"]
    )


@pytest.mark.parametrize("operation", ["ifequal", "ifgreater"])
@pytest.mark.parametrize("dtype", ["float", "color3"])
def test_comparison_lowering_keeps_equality_and_zero_boundaries(operation, dtype):
    from _tests.test_materialx_hsv import evaluate
    from iris_relay.materialx_core import MaterialXCore

    core = MaterialXCore()
    doc = core.read(rs_profile()["materialx_extensions"])
    core.flatten(doc)
    graph = doc.getNodeGraph("NG_iris_rs_lower_" + operation + "_" + dtype)
    a, b = ([0.2, 0.4, 0.7], [0.9, 0.1, 0.3]) if dtype == "color3" else (0.37, 0.91)
    for first, second in [(0, 0), (0.0, -0.0), (-1, 0), (1, 0), (1e-20, 0), (-1e-20, 0), (2, 2)]:
        expected = a if (first == second if operation == "ifequal" else first > second) else b
        assert evaluate(graph, dict(value1=first, value2=second, in1=a, in2=b)) == pytest.approx(
            expected
        )
