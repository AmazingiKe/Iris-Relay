"""未知原生节点可以保存结构，但保存结果不能被误报为目标可转换。"""

from copy import deepcopy

from iris_relay import RelayAPI
from iris_relay.materialx.core import MaterialXCore


def snapshot():
    return {
        "name": "未知混合材质",
        "host": {"id": "test", "version": "1"},
        "nodes": [
            {
                "id": "图片",
                "type": "native_image",
                "inputs": [],
                "outputs": [{"id": "Color", "type": "color3"}],
                "properties": {"path": "source.png"},
            },
            {
                "id": "未知节点",
                "type": "vendor_shader",
                "inputs": [{"id": "Base Color", "type": "color3", "value": [0.2, 0.4, 0.6]}],
                "outputs": [{"id": "Surface", "type": "surfaceshader"}],
                "properties": {"mode": "special"},
            },
        ],
        "links": [
            {
                "from": {"node": "图片", "socket": "Color"},
                "to": {"node": "未知节点", "socket": "Base Color"},
            }
        ],
        "metadata": {"groups": {"nested": {"native_data": [1, 2, 3]}}},
    }


def preserve(value):
    return RelayAPI([]).handle(
        {
            "api_version": "1",
            "request_id": "preserve",
            "operation": "materialx.preserve",
            "payload": {"snapshot": value},
        }
    )


def test_native_unknown_graph_is_saved_with_explicit_nonconvertible_status():
    source = snapshot()
    response = preserve(source)
    assert response["status"] == "warning", response
    result = response["result"]
    assert result["conversion_supported"] is False and result["appearance_verified"] is False
    core = MaterialXCore()
    doc = core.read(result["document"])
    assert not any(i["severity"] == "error" for i in core.inspect(doc))
    assert len(doc.getNodes()) == 2
    assert result["snapshot"] == source
    import base64
    import json

    assert "vendor_shader" in result["document"]
    assert json.loads(base64.b64decode(doc.getAttribute("iris_native_snapshot"))) == source


def test_host_implicit_cast_remains_explicit_and_unimplemented():
    source = snapshot()
    source["nodes"][1]["inputs"][0].update(type="float", value=0.4)
    response = preserve(source)
    assert response["status"] == "warning", response
    assert "iris_native_cast" in response["result"]["document"]
    assert len(MaterialXCore().read(response["result"]["document"]).getNodes()) == 3


def test_invalid_native_connection_never_produces_success_document():
    source = deepcopy(snapshot())
    source["links"][0]["from"]["socket"] = "missing"
    response = preserve(source)
    assert response["status"] == "error"
    assert response["result"]["document"] is None


def test_unconnected_native_shader_socket_is_preserved_without_invalid_empty_binding():
    source = snapshot()
    source["nodes"][1]["inputs"].append({"id": "Volume", "type": "surfaceshader", "value": None})
    response = preserve(source)
    assert response["status"] == "warning", response
    assert "inputs_Volume" in response["result"]["document"]


def test_preservation_document_is_explicitly_blocked_at_target_conversion():
    from _scripts.native_fixture import renderer
    from _tests.test_maya_materialx import maya_profile

    document = preserve(snapshot())["result"]["document"]
    profile = maya_profile()
    response = RelayAPI([profile]).handle(
        {
            "api_version": "1",
            "request_id": "blocked",
            "operation": "materialx.convert",
            "payload": {"document": document, "target": renderer(profile)},
        }
    )
    assert response["status"] == "error"
    assert response["result"]["source"] == document
    assert response["result"]["material"] is None
    assert any(
        i["code"] == "MATERIALX_PRESERVED_ONLY" for i in response["result"]["report"]["issues"]
    )


def test_native_string_and_labels_with_xml_characters_remain_recoverable():
    source = snapshot()
    source["metadata"]["label"] = 'World <---> Object & "noise"'
    source["nodes"][1]["inputs"].append(
        {"id": "string <socket>", "type": "string", "value": "x < y\x01"}
    )
    response = preserve(source)
    assert response["status"] == "warning", response
    assert "base64-utf8-json" in response["result"]["document"]


def test_blender_nonfinite_native_values_are_tagged_without_weakening_api_numbers():
    from plugins.blender.preservation import preservation_snapshot

    material = {
        "name": "infinite",
        "nodes": [
            {
                "name": "node",
                "type": "vendor",
                "properties": {},
                "inputs": [{"id": "input", "type": "VALUE", "value": float("inf")}],
                "outputs": [],
            }
        ],
        "links": [],
    }
    captured = preservation_snapshot(material, {"blender_version": "test"})
    assert captured["nodes"][0]["inputs"][0]["value"] is None
    assert preserve(captured)["status"] == "warning"
