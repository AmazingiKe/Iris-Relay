"""统一命名须保持连接身份，原名备份不能重新参与命名或显示。"""

from copy import deepcopy
from xml.etree import ElementTree as ET

import MaterialX as mx
import pytest

from _scripts.native_fixture import fixture, renderer
from _tests.test_materialx_bridge import PROFILES, call
from iris_relay import RelayAPI
from iris_relay.materialx.editor import get_text_attribute
from iris_relay.tools.naming import node_name, normalize_names


@pytest.mark.parametrize(
    "source,expected",
    [
        ("123木纹", "mu_wen"),
        ("123wood_02", "wood_02"),
        ("１２３wood_02", "wood_02"),
        ("!!123wood", "wood"),
        ("wood123", "wood123"),
        ("木纹🔥颜色！", "mu_wen_yan_se"),
        ("wood- color", "woodcolor"),
        ("123", "image"),
        ("🔥！！", "image"),
        ("___", "image"),
        ("木纹wood02", "mu_wen_wood02"),
        ("текст", "stable_id"),
    ],
)
def test_name_rules(source, expected):
    assert node_name(source, "stable_id", "image") == expected


def test_invalid_fallbacks_are_safe_and_deterministic():
    for source, identity, kind in [("日本語かな", "123?!", "image"), ("🔥", "999", "!!!")]:
        name = node_name(source, identity, kind)
        assert name == node_name(source, identity, kind)
        assert name.isascii() and not name[0].isdigit()
        assert set(name) <= set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


def test_graph_names_keep_connections_and_are_order_independent():
    ids = ["木纹", "木纹!", "mu_wen", "123", "🔥", "a" * 100, "a" * 99 + "b"]
    source = {
        "nodes": [{"id": key, "type": "image", "metadata": {}} for key in ids],
        "links": [
            {"from": {"node": ids[0], "socket": "out"}, "to": {"node": ids[1], "socket": "in"}}
        ],
        "output": {"node": ids[1], "socket": "out"},
    }
    saved = deepcopy(source)
    result = normalize_names(source)
    assert source == saved
    mapping = {n["metadata"]["source_name"]: n["id"] for n in result["nodes"]}
    assert mapping["mu_wen"] == "mu_wen"
    assert len(set(mapping.values())) == len(ids)
    assert all(len(name) <= 63 for name in mapping.values())
    assert result["links"][0]["from"]["node"] == mapping[ids[0]]
    assert result["links"][0]["to"]["node"] == result["output"]["node"] == mapping[ids[1]]
    source["nodes"].reverse()
    assert {
        n["metadata"]["source_name"]: n["id"] for n in normalize_names(source)["nodes"]
    } == mapping
    assert normalize_names(result) == result


def test_backup_is_passive_and_not_copied_to_editor():
    source = {
        "nodes": [
            {
                "id": "current",
                "type": "image",
                "metadata": {"source_name": "原名", "editor": {"label": "current"}},
            }
        ],
        "links": [],
        "output": {"node": "current", "socket": "out"},
    }
    assert normalize_names(source) == source


def test_materialx_generated_nodes_and_inline_connections_keep_valid_names():
    from iris_relay.materialx.core import MaterialXCore
    from iris_relay.materialx.editor import finalize_node_names

    core = MaterialXCore()
    doc = core.read("""<materialx version="1.39">
      <nodegraph name="graph">
        <constant name="123source" type="float">
          <input name="value" type="float" value="0.3"/>
        </constant>
        <multiply name="456target" type="float">
          <input name="in1" type="float" nodename="123source"/>
          <input name="in2" type="float" value="2"/>
        </multiply>
        <output name="out" type="float" nodename="456target"/>
      </nodegraph>
    </materialx>""")
    finalize_node_names(doc)
    graph = doc.getNodeGraph("graph")
    assert graph.getNode("target").getInput("in1").getConnectedNode() == graph.getNode("source")
    assert graph.getOutput("out").getConnectedNode() == graph.getNode("target")
    assert get_text_attribute(graph.getNode("source"), "iris_source_name") == "123source"
    assert not any(i["severity"] == "error" for i in core.inspect(doc))
    first = core.serialize(doc)
    finalize_node_names(doc)
    assert core.serialize(doc) == first


def test_original_with_xml_control_characters_is_stored_once_and_recovers_exactly():
    from _tests.test_materialx_editor import material

    profile, source = material()
    original = '123木头<"&\x01🔥'
    source["nodes"][0]["id"] = original
    source["output"]["node"] = original
    api = RelayAPI([profile])
    exported = call(api, "materialx.export", material=source)
    assert exported["result"]["document"], exported
    converted = call(
        api, "materialx.convert", document=exported["result"]["document"], target=source["renderer"]
    )
    node = converted["result"]["material"]["nodes"][0]
    assert node["id"] == "mu_tou"
    assert node["metadata"]["source_name"] == original
    assert node["metadata"]["editor"]["label"] == "mu_tou"


def test_invalid_backup_is_reported_without_a_partial_export():
    from _tests.test_materialx_editor import material

    profile, source = material()
    source["nodes"][0]["metadata"]["source_name"] = 123
    result = call(RelayAPI([profile]), "materialx.export", material=source)
    assert result["status"] == "error" and result["result"]["document"] is None
    assert result["result"]["source"] == source


def test_preserved_native_graph_has_one_backup_and_still_verifiable():
    from _scripts.verify_preserved_documents import verify_native
    from _tests.test_materialx_preserve import preserve, snapshot

    source = snapshot()
    result = preserve(source)
    root = ET.fromstring(result["result"]["document"])
    assert verify_native(root, source) == {"nodes": 2, "links": 1}
    for node in (n for n in root if n.get("iris_snapshot_index") is not None):
        assert node.get("iris_source_name")
        assert node.get("iris_native_id") is None


def test_preservation_uses_the_same_stable_collision_rules():
    from _tests.test_materialx_preserve import preserve, snapshot

    source = snapshot()
    source["nodes"][1]["id"] = "图片!"
    source["links"][0]["to"]["node"] = "图片!"
    results = []
    for _ in range(2):
        result = preserve(source)
        assert result["result"]["document"], result
        root = ET.fromstring(result["result"]["document"])
        results.append(
            {n.get("iris_source_name"): n.get("name") for n in root if n.get("iris_source_name")}
        )
        source["nodes"].reverse()
    assert results[0] == results[1]


def test_inline_numeric_names_can_export_and_restore_without_weakening_computation_checks():
    from _tests.test_materialx_extensions import fixture as extension_fixture
    from _tests.test_materialx_link_casts import export
    from _tests.test_materialx_native_nodes import convert

    profile, source = extension_fixture()
    profile["materialx_extensions"] = profile["materialx_extensions"].replace(
        "calculation", "123calculation"
    )
    exported = export(profile, source)
    document = exported["result"]["document"]
    assert document, exported
    assert ET.fromstring(document).find('.//*[@name="123calculation"]') is None
    restored = convert(profile, source, document)
    assert restored["result"]["material"], restored
    # 旧文档的内部实例名也能继续读取，常量被篡改则仍须阻断。
    doc = mx.createDocument()
    mx.readFromXmlString(doc, document)
    graph = doc.getNodeGraphs()[0]
    original = graph.getNode("calculation")
    original.setName("123calculation")
    for port in graph.getOutputs():
        if port.getNodeName() == "calculation":
            port.setNodeName("123calculation")
    legacy = convert(profile, source, mx.writeToXmlString(doc))
    assert legacy["result"]["material"], legacy
    original.getInput("in2").setValueString("0.9")
    rejected = convert(profile, source, mx.writeToXmlString(doc))
    assert rejected["result"]["material"] is None


@pytest.mark.parametrize("profile", PROFILES, ids=lambda p: p["id"])
def test_materialx_names_and_one_backup_survive_repeated_conversion(profile):
    api = RelayAPI(PROFILES)
    source = fixture(profile, "textures/test.png")
    originals = ["123木纹🔥", "木纹!", "!!!", "текст"]
    mapping = {n["id"]: originals[i] for i, n in enumerate(source["nodes"])}
    for node in source["nodes"]:
        node["id"] = mapping[node["id"]]
        node["metadata"]["editor"] = {
            "label": node["id"],
            "source_id": node["id"],
            "source_label": node["id"],
        }
    for edge in source["links"]:
        for endpoint in edge.values():
            endpoint["node"] = mapping[endpoint["node"]]
    source["output"]["node"] = mapping[source["output"]["node"]]
    saved = deepcopy(source)
    for cycle in range(3):
        exported = call(api, "materialx.export", material=source)
        document = exported["result"]["document"]
        assert document, exported
        doc = mx.createDocument()
        mx.readFromXmlString(doc, document)
        for node in (n for n in doc.traverseTree() if isinstance(n, mx.Node)):
            assert node.hasAttribute("iris_source_name")
            assert not node.getName()[0].isdigit() and node.getName().isascii()
        decoded = [get_text_attribute(n, "iris_source_name") for n in doc.getNodes()]
        assert set(mapping.values()) <= set(decoded)
        root = ET.fromstring(document)
        for element in root:
            if element.get("iris_source_name") in mapping.values():
                assert element.get("uiname") not in mapping.values()
        converted = call(api, "materialx.convert", document=document, target=renderer(profile))
        source = converted["result"]["material"]
        assert source, converted
        assert len(source["links"]) == len(saved["links"])
        for node in source["nodes"]:
            metadata = node["metadata"]
            assert metadata["source_name"] in mapping.values()
            assert metadata["editor"].get("source_id") != metadata["source_name"]
            assert metadata["editor"].get("source_label") != metadata["source_name"]
            assert metadata["editor"]["label"] == node["id"]
        if cycle == 0:
            first_names = {n["id"] for n in source["nodes"]}
        assert {n["id"] for n in source["nodes"]} == first_names
