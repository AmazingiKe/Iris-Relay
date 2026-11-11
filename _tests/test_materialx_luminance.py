"""亮度链回读：保留系数、常量与共享连接，并明确拒绝未支持的形式。"""

import json
from pathlib import Path

import MaterialX as mx
import pytest

from _scripts.native_fixture import renderer
from _tests.test_materialx_link_casts import export, setup
from iris_relay import RelayAPI

ROOT = Path(__file__).resolve().parents[1]


def convert_document(source, document, *, target=None):
    """用公开 API 转换 XML，返回含源文档的完整响应。"""
    return RelayAPI.from_directory(ROOT / "dictionaries").handle(
        {
            "api_version": "1",
            "request_id": "luminance",
            "operation": "materialx.convert",
            "payload": {"document": document, "target": target or source["renderer"]},
        }
    )


def editable_document():
    """取得独立可编辑文档，避免测试依赖实际木地板资源。"""
    profile, source = setup()
    document = mx.createDocument()
    mx.readFromXmlString(document, export(profile, source)["result"]["document"])
    luma = next(n for n in document.getNodes() if n.getCategory() == "luminance")
    return profile, source, document, luma


@pytest.mark.parametrize("coefficients", [[0.1, 0.2, 0.7], [-0.1, 0.8, 0.3], [2, 0, 0]])
def test_coefficients_survive_import_and_reexport(coefficients):
    profile, source, document, luma = editable_document()
    luma.getInput("lumacoeffs").setValueString(", ".join(map(str, coefficients)))
    response = convert_document(source, mx.writeToXmlString(document))
    assert response["status"] == "warning", response
    material = response["result"]["material"]
    weighted = next(n for n in material["nodes"] if n["type"].endswith(".dot_product"))
    assert weighted["inputs"]["Vector_001"] == pytest.approx(coefficients)
    exported = export(profile, material)
    assert exported["status"] == "warning", exported
    readback = convert_document(source, exported["result"]["document"])
    assert readback["status"] == "warning", readback
    again = next(
        n for n in readback["result"]["material"]["nodes"] if n["type"].endswith(".dot_product")
    )
    assert again["inputs"]["Vector_001"] == pytest.approx(coefficients)


def test_luminance_constant_and_official_default_coefficients():
    _, source, document, luma = editable_document()
    luma.getInput("in").removeAttribute("nodename")
    luma.getInput("in").setValueString("0.2, 0.4, 0.6")
    luma.removeInput("lumacoeffs")
    response = convert_document(source, mx.writeToXmlString(document))
    assert response["status"] == "warning", response
    weighted = next(
        n for n in response["result"]["material"]["nodes"] if n["type"].endswith(".dot_product")
    )
    library = mx.createDocument()
    mx.loadLibraries(mx.getDefaultDataLibraryFolders(), mx.getDefaultDataSearchPath(), library)
    expected = library.getNodeDef("ND_luminance_color3").getActiveInput("lumacoeffs").getValue()
    assert weighted["inputs"]["Vector"] == pytest.approx([0.2, 0.4, 0.6])
    assert weighted["inputs"]["Vector_001"] == pytest.approx(list(expected))


def test_shared_scalar_output_keeps_both_consumers():
    _, source, document, _ = editable_document()
    surface = next(n for n in document.getNodes() if n.getCategory() == "standard_surface")
    roughness = surface.getInput("specular_roughness")
    metalness = surface.getInput("metalness")
    metalness.removeAttribute("value")
    metalness.setNodeName(roughness.getNodeName())
    response = convert_document(source, mx.writeToXmlString(document))
    assert response["status"] == "warning", response
    material = response["result"]["material"]
    weighted = [n for n in material["nodes"] if n["type"].endswith(".dot_product")]
    assert len(weighted) == 1
    destinations = {
        link["to"]["socket"]
        for link in material["links"]
        if link["from"]["node"] == weighted[0]["id"]
    }
    assert destinations == {"Roughness", "Metallic"}


@pytest.mark.parametrize("change", ["dynamic_weights", "color_output", "wrong_index"])
def test_unsupported_forms_keep_source_and_block(change):
    _, source, document, luma = editable_document()
    if change == "dynamic_weights":
        port = luma.getInput("lumacoeffs")
        port.removeAttribute("value")
        port.setNodeName(luma.getInput("in").getNodeName())
    elif change == "color_output":
        surface = next(n for n in document.getNodes() if n.getCategory() == "standard_surface")
        surface.getInput("base_color").removeAttribute("value")
        surface.getInput("base_color").setNodeName(luma.getName())
    else:
        extract = next(n for n in document.getNodes() if n.getCategory() == "extract")
        extract.getInput("index").setValueString("1")
    xml = mx.writeToXmlString(document)
    response = convert_document(source, xml)
    assert response["status"] == "error"
    assert response["result"]["material"] is None
    assert response["result"]["source"] == xml


@pytest.mark.parametrize("engine", ["arnold", "redshift"])
@pytest.mark.parametrize("coefficients", [[0.2126, 0.7152, 0.0722], [-0.1, 0.8, 0.3]])
def test_c4d_targets_preserve_weighted_sum_and_reexport(engine, coefficients):
    """C4D 目标使用显式点积；文档权重和贴图连接经过再导出仍可回到 Blender。"""
    _, source, document, luma = editable_document()
    luma.getInput("lumacoeffs").setValueString(", ".join(map(str, coefficients)))
    target_profile = next(
        json.loads(path.read_text("utf-8"))
        for path in (ROOT / "dictionaries").glob("*.json")
        if engine in path.name
    )
    response = convert_document(
        source, mx.writeToXmlString(document), target=renderer(target_profile)
    )
    assert response["status"] == "warning", response
    material = response["result"]["material"]
    weighted = next(
        n
        for n in material["nodes"]
        if target_profile["nodes"][n["type"]]["semantic"] == "color.weighted_sum"
    )
    rule = target_profile["nodes"][weighted["type"]]
    weights = next(k for k, p in rule["inputs"].items() if p["semantic"] == "weights")
    color = next(k for k, p in rule["inputs"].items() if p["semantic"] == "color")
    assert weighted["inputs"][weights] == pytest.approx(coefficients)
    assert rule["inputs"][weights]["native"]["codec"] == "identity"
    assert rule["inputs"][color]["native"]["codec"] == "identity"
    assert any(
        link["to"] == {"node": weighted["id"], "socket": color} for link in material["links"]
    )
    exported = export(target_profile, material)
    assert exported["status"] == "warning", exported
    returned = convert_document(source, exported["result"]["document"])
    assert returned["status"] == "warning", returned
    restored = next(
        n for n in returned["result"]["material"]["nodes"] if n["type"].endswith(".dot_product")
    )
    assert restored["inputs"]["Vector_001"] == pytest.approx(coefficients)


def test_target_without_weighted_sum_binding_is_blocked():
    _, source, document, _ = editable_document()
    target_profile = json.loads((ROOT / "dictionaries/cinema4d-redshift.json").read_text("utf-8"))
    target_profile["nodes"] = {
        k: n for k, n in target_profile["nodes"].items() if n["semantic"] != "color.weighted_sum"
    }
    response = RelayAPI([target_profile]).handle(
        {
            "api_version": "1",
            "request_id": "missing-binding",
            "operation": "materialx.convert",
            "payload": {
                "document": mx.writeToXmlString(document),
                "target": renderer(target_profile),
            },
        }
    )
    assert response["status"] == "error"
    assert response["result"]["material"] is None


@pytest.mark.parametrize("change", ["constant_color", "dynamic_weights", "unhandled_vector"])
def test_canonical_dotproduct_input_boundaries(change):
    """再次导出的点积仍检查输入含义，不能借向量端口接纳任意计算节点。"""
    profile, source, document, _ = editable_document()
    native = convert_document(source, mx.writeToXmlString(document))["result"]["material"]
    canonical = mx.createDocument()
    mx.readFromXmlString(canonical, export(profile, native)["result"]["document"])
    dot = next(n for n in canonical.getNodes() if n.getCategory() == "dotproduct")
    if change == "constant_color":
        dot.getInput("in1").removeAttribute("nodename")
        dot.getInput("in1").setValueString("0.2, 0.4, 0.6")
    elif change == "dynamic_weights":
        dot.getInput("in2").removeAttribute("value")
        dot.getInput("in2").setNodeName(dot.getInput("in1").getNodeName())
    else:
        vector = canonical.addNode("constant", "unhandled_vector", "vector3")
        vector.addInput("value", "vector3").setValueString("0.2, 0.4, 0.6")
        dot.getInput("in1").setNodeName(vector.getName())
    xml = mx.writeToXmlString(canonical)
    response = convert_document(source, xml)
    assert response["result"]["source"] == xml
    if change == "constant_color":
        assert response["status"] == "warning", response
        weighted = next(
            n for n in response["result"]["material"]["nodes"] if n["type"].endswith(".dot_product")
        )
        assert weighted["inputs"]["Vector"] == pytest.approx([0.2, 0.4, 0.6])
    else:
        assert response["status"] == "error"
        assert response["result"]["material"] is None
        expected = (
            "MATERIALX_INPUT_UNSUPPORTED"
            if change == "dynamic_weights"
            else "MATERIALX_NODE_UNSUPPORTED"
        )
        assert expected in {i["code"] for i in response["result"]["report"]["issues"]}
