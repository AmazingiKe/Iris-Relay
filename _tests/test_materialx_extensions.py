"""字典内联标准子图可往返为原生节点；定义变化不能仅凭名称获得原生支持。"""

from copy import deepcopy

import MaterialX as mx
import pytest

from _tests.test_materialx_link_casts import export, setup
from _tests.test_materialx_native_nodes import convert, math_fixture
from iris_relay import RelayAPI

EXTENSION = """<materialx version="1.39">
  <nodedef name="ND_iris_test_math_float" node="iris_test_math">
    <input name="in1" type="float" value="0.5" />
    <input name="in2" type="float" value="0.5" />
    <output name="out" type="float" />
  </nodedef>
  <nodegraph name="NG_iris_test_math_float" nodedef="ND_iris_test_math_float">
    <multiply name="calculation" type="float" nodedef="ND_multiply_float">
      <input name="in1" type="float" interfacename="in1" />
      <input name="in2" type="float" interfacename="in2" />
    </multiply>
    <output name="out" type="float" nodename="calculation" />
  </nodegraph>
</materialx>"""


def fixture():
    """为已有原生数学配置增加明确内联实现，原生协议和端口保持不变。"""
    profile, source = math_fixture()
    profile["materialx_extensions"] = EXTENSION
    profile["nodes"]["math"]["semantic"] = "materialx.ND_iris_test_math_float"
    return profile, source


def test_extension_roundtrip_keeps_original_ports_values_and_shared_links():
    profile, source = fixture()
    before = deepcopy(source)
    result = export(profile, source)
    assert result["status"] == "warning", result
    document = result["result"]["document"]
    xml = mx.createDocument()
    mx.readFromXmlString(xml, document)
    assert len(xml.getNodeDefs()) == len(xml.getNodeGraphs()) == 1
    assert xml.getNode("first").getNodeDefString() == "ND_iris_test_math_float"
    restored = convert(profile, source, document)
    assert restored["status"] == "warning", restored
    target = restored["result"]["material"]
    assert sorted(target["links"], key=str) == sorted(source["links"], key=str)
    for name in ("first", "second"):
        actual = next(n for n in target["nodes"] if n["id"] == name)["inputs"]
        expected = next(n for n in source["nodes"] if n["id"] == name)["inputs"]
        connected = {e["to"]["socket"] for e in source["links"] if e["to"]["node"] == name}
        assert {k: v for k, v in actual.items() if k not in connected} == pytest.approx(
            {k: v for k, v in expected.items() if k not in connected}
        )
    assert source == before == result["result"]["source"]


def test_unused_extensions_do_not_change_existing_export_documents():
    profile, source = setup()
    original = export(profile, source)["result"]["document"]
    extended, _ = fixture()
    library, extra = mx.createDocument(), mx.createDocument()
    if "materialx_extensions" in profile:
        mx.readFromXmlString(library, profile["materialx_extensions"])
    mx.readFromXmlString(extra, EXTENSION)
    library.importLibrary(extra)
    profile["materialx_extensions"] = mx.writeToXmlString(library)
    profile["nodes"]["math"] = extended["nodes"]["math"]
    assert export(profile, source)["result"]["document"] == original


@pytest.mark.parametrize(
    "change", ["operation", "default", "extra_implementation", "missing_graph"]
)
def test_changed_extension_never_impersonates_native_function(change):
    profile, source = fixture()
    result = export(profile, source)
    assert result["status"] == "warning", result
    xml = mx.createDocument()
    mx.readFromXmlString(xml, result["result"]["document"])
    if change == "operation":
        node = xml.getNodeGraph("NG_iris_test_math_float").getNode("calculation")
        node.setCategory("subtract")
        node.setNodeDefString("ND_subtract_float")
    elif change == "default":
        xml.getNodeDef("ND_iris_test_math_float").getInput("in1").setValueString("0.7")
    elif change == "extra_implementation":
        implementation = xml.addImplementation("IM_other")
        implementation.setNodeDefString("ND_iris_test_math_float")
        implementation.setTarget("genglsl")
        implementation.setAttribute("sourcecode", "0.0")
    else:
        xml.removeNodeGraph("NG_iris_test_math_float")
    document = mx.writeToXmlString(xml)
    result = convert(profile, source, document)
    assert result["status"] == "error" and result["result"]["material"] is None
    assert result["result"]["source"] == document


def test_target_without_custom_binding_can_use_supported_standard_subgraph():
    profile, source = fixture()
    result = export(profile, source)
    assert result["status"] == "warning", result
    target, _ = math_fixture()
    restored = convert(target, source, result["result"]["document"])
    assert restored["status"] == "warning", restored
    nodes = [n for n in restored["result"]["material"]["nodes"] if n["type"] == "math"]
    assert len(nodes) == 2
    assert sorted(n["inputs"]["Value_001"] for n in nodes) == pytest.approx([0.2, 4.0])


@pytest.mark.parametrize("bad", ["recursive", "no_implementation", "not_library"])
def test_invalid_dictionary_extensions_are_rejected(bad):
    profile, source = fixture()
    if bad == "recursive":
        profile["materialx_extensions"] = EXTENSION.replace(
            '<multiply name="calculation" type="float" nodedef="ND_multiply_float">',
            '<iris_test_math name="calculation" type="float" nodedef="ND_iris_test_math_float">',
        ).replace("</multiply>", "</iris_test_math>")
    elif bad == "no_implementation":
        profile["materialx_extensions"] = (
            EXTENSION[: EXTENSION.index("  <nodegraph")] + "</materialx>"
        )
    else:
        profile["materialx_extensions"] = EXTENSION.replace(
            "</materialx>", '<constant name="extra" type="float" /></materialx>'
        )
    result = export(profile, source)
    assert result["status"] == "error" and result["result"]["document"] is None


def test_extension_is_request_local_and_normalization_can_expand_it():
    profile, source = fixture()
    exported = export(profile, source)
    assert exported["status"] == "warning", exported
    service = RelayAPI([profile])
    response = service.handle(
        {
            "api_version": "1",
            "request_id": "flatten",
            "operation": "materialx.normalize",
            "payload": {"document": exported["result"]["document"], "flatten": True},
        }
    )
    assert response["status"] != "error", response
    xml = mx.createDocument()
    mx.readFromXmlString(xml, response["result"]["document"])
    assert not any(n.getCategory() == "iris_test_math" for n in xml.getNodes())
    definitions = service.handle(
        {
            "api_version": "1",
            "request_id": "definitions",
            "operation": "materialx.definitions",
            "payload": {},
        }
    )
    assert "ND_iris_test_math_float" not in {
        n["name"] for n in definitions["result"]["definitions"]
    }
