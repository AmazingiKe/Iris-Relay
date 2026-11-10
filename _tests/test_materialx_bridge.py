"""原生协议与标准 MaterialX 之间的实际转换，不依赖 DCC 运行时。"""

import json
from copy import deepcopy
from pathlib import Path

import MaterialX as mx
import pytest

from _scripts.native_fixture import fixture, renderer
from iris_relay import RelayAPI

ROOT = Path(__file__).resolve().parents[1]
PROFILES = [
    json.loads(p.read_text("utf-8")) for p in sorted((ROOT / "dictionaries").glob("*.json"))
]


def call(api, operation, **payload):
    return api.handle(
        {"api_version": "1", "request_id": "转换", "operation": operation, "payload": payload}
    )


@pytest.mark.parametrize("profile", PROFILES, ids=lambda p: p["id"])
@pytest.mark.parametrize("normal", ["normal.tangent", "bump.height"])
def test_native_to_materialx_then_all_targets(profile, normal):
    api = RelayAPI(PROFILES)
    source = fixture(profile, "textures/高度.png", normal_semantic=normal)
    original = deepcopy(source)
    exported = call(api, "materialx.export", material=source)
    assert exported["status"] == "warning", exported
    assert source == original
    assert exported["result"]["source"] == original
    document = exported["result"]["document"]
    assert document
    assert call(api, "materialx.validate", document=document)["status"] == "ok"
    for target in PROFILES:
        converted = call(api, "materialx.convert", document=document, target=renderer(target))
        assert converted["status"] == "warning", converted
        result = converted["result"]
        assert result["source"] == document
        assert result["material"]["renderer"] == renderer(target)
        assert len(result["material"]["links"]) == len(source["links"])
        assert next(iter(result["material"]["textures"].values()))["color_space"] == "Raw"
        assert result["report"]["appearance_verified"] is False
        expected = call(api, "convert", material=source, target=renderer(target))["result"][
            "material"
        ]
        linked = {(link["to"]["node"], link["to"]["socket"]) for link in expected["links"]}
        for node in expected["nodes"]:
            actual = next(n for n in result["material"]["nodes"] if n["id"] == node["id"])
            for port, value in node["inputs"].items():
                if (node["id"], port) in linked:
                    continue
                if isinstance(value, dict):
                    assert (
                        result["material"]["textures"][actual["inputs"][port]["texture"]]
                        == expected["textures"][value["texture"]]
                    )
                elif isinstance(value, (int, float, list)) and value is not None:
                    assert actual["inputs"][port] == pytest.approx(value)
                else:
                    assert actual["inputs"][port] == value


def test_independent_standard_document_can_convert_and_honors_standard_defaults():
    api = RelayAPI(PROFILES)
    document = """<materialx version="1.39">
      <image name="image" type="color3">
      <input name="file" type="filename" value="albedo.png" colorspace="srgb_texture" />
      </image>
      <standard_surface name="shader" type="surfaceshader">
        <input name="base_color" type="color3" nodename="image" />
      </standard_surface>
      <surfacematerial name="mat" type="material">
      <input name="surfaceshader" type="surfaceshader" nodename="shader" />
      </surfacematerial>
    </materialx>"""
    target = next(p for p in PROFILES if p["renderer"]["id"] == "cycles")
    response = call(api, "materialx.convert", document=document, target=renderer(target))
    assert response["status"] == "warning", response
    material = response["result"]["material"]
    shader = next(n for n in material["nodes"] if n["id"] == "shader")
    assert shader["inputs"]["Roughness"] == pytest.approx(0.2)
    assert shader["inputs"]["Base Color"] == pytest.approx([0.8, 0.8, 0.8])
    assert len(material["links"]) == 1


@pytest.mark.parametrize(
    "extra",
    [
        '<input name="transmission" type="float" value="0.8" />',
        '<input name="base" type="float" value="0.5" />',
        '<input name="thin_walled" type="boolean" value="true" />',
    ],
)
def test_unrepresentable_standard_input_blocks_and_keeps_source(extra):
    document = f"""<materialx version="1.39">
      <standard_surface name="s" type="surfaceshader">{extra}</standard_surface>
      <surfacematerial name="m" type="material">
        <input name="surfaceshader" type="surfaceshader" nodename="s" />
      </surfacematerial>
    </materialx>"""
    response = call(
        RelayAPI(PROFILES), "materialx.convert", document=document, target=renderer(PROFILES[0])
    )
    assert response["status"] == "error", response
    assert response["result"]["material"] is None
    assert response["result"]["source"] == document
    assert any(
        i["code"] == "MATERIALX_INPUT_UNSUPPORTED" for i in response["result"]["report"]["issues"]
    )


def test_opaque_custom_node_remains_preserved_but_cannot_convert():
    document = """<materialx version="1.39">
      <nodedef name="ND_vendor_surface" node="vendor_surface">
      <output name="out" type="surfaceshader" />
      </nodedef>
      <vendor_surface name="s" type="surfaceshader" nodedef="ND_vendor_surface" />
      <surfacematerial name="m" type="material">
      <input name="surfaceshader" type="surfaceshader" nodename="s" />
      </surfacematerial>
    </materialx>"""
    response = call(
        RelayAPI(PROFILES), "materialx.convert", document=document, target=renderer(PROFILES[0])
    )
    assert response["status"] == "error"
    assert response["result"]["source"] == document
    assert response["result"]["material"] is None


def test_export_rejects_unknown_nodes_and_retains_native_source():
    source = fixture(PROFILES[0])
    source["nodes"][0]["type"] = "not_registered"
    response = call(RelayAPI(PROFILES), "materialx.export", material=source)
    assert response["status"] == "error", response
    assert response["result"]["source"] == source
    assert response["result"]["document"] is None


def test_native_names_do_not_have_to_be_valid_materialx_names():
    source = fixture(PROFILES[0])
    old = source["nodes"][0]["id"]
    source["nodes"][0]["id"] = "命名空间:材质.001"
    for link in source["links"]:
        for key in ("from", "to"):
            if link[key]["node"] == old:
                link[key]["node"] = source["nodes"][0]["id"]
    if source["output"]["node"] == old:
        source["output"]["node"] = source["nodes"][0]["id"]
    response = call(RelayAPI(PROFILES), "materialx.export", material=source)
    assert response["status"] == "warning", response
    doc = mx.createDocument()
    mx.readFromXmlString(doc, response["result"]["document"])
    assert any(n.getAttribute("iris_source_name") == "命名空间:材质.001" for n in doc.getNodes())


@pytest.mark.parametrize(
    "attribute",
    [
        'fileprefix="elsewhere/"',
        'colorspace="srgb_texture"',
        'unit="centimeter" unittype="distance"',
    ],
)
def test_unhandled_interpretation_metadata_blocks_conversion(attribute):
    document = f"""<materialx version="1.39" {attribute}>
      <standard_surface name="s" type="surfaceshader" />
      <surfacematerial name="m" type="material">
        <input name="surfaceshader" type="surfaceshader" nodename="s" />
      </surfacematerial>
    </materialx>"""
    response = call(
        RelayAPI(PROFILES), "materialx.convert", document=document, target=renderer(PROFILES[0])
    )
    assert response["status"] == "error", response
    assert response["result"]["source"] == document


def test_nodegraph_output_connection_and_material_selection():
    document = """<materialx version="1.39">
      <nodegraph name="NG">
        <image name="img" type="color3">
      <input name="file" type="filename" value="albedo.png" colorspace="srgb_texture" />
      </image>
        <output name="color" type="color3" nodename="img" />
      </nodegraph>
      <standard_surface name="s" type="surfaceshader">
      <input name="base_color" type="color3" nodegraph="NG" output="color" />
      </standard_surface>
      <surfacematerial name="first" type="material">
      <input name="surfaceshader" type="surfaceshader" nodename="s" />
      </surfacematerial>
      <surfacematerial name="second" type="material">
      <input name="surfaceshader" type="surfaceshader" nodename="s" />
      </surfacematerial>
    </materialx>"""
    api = RelayAPI(PROFILES)
    assert (
        call(api, "materialx.convert", document=document, target=renderer(PROFILES[0]))["status"]
        == "error"
    )
    response = call(
        api,
        "materialx.convert",
        document=document,
        target=renderer(PROFILES[0]),
        material_name="first",
    )
    assert response["status"] == "warning", response
    assert response["result"]["material"]["links"][0]["from"]["node"] == "NG/img"


def test_custom_surface_graph_expands_to_supported_nodes():
    document = """<materialx version="1.39">
      <nodedef name="ND_iris_surface" node="iris_surface">
      <input name="roughness" type="float" value="0.42" />
      <output name="out" type="surfaceshader" />
      </nodedef>
      <nodegraph name="NG_iris_surface" nodedef="ND_iris_surface">
        <standard_surface name="s" type="surfaceshader">
      <input name="specular_roughness" type="float" interfacename="roughness" />
      </standard_surface>
        <output name="out" type="surfaceshader" nodename="s" />
      </nodegraph>
      <iris_surface name="custom" type="surfaceshader" nodedef="ND_iris_surface" />
      <surfacematerial name="m" type="material">
      <input name="surfaceshader" type="surfaceshader" nodename="custom" />
      </surfacematerial>
    </materialx>"""
    response = call(
        RelayAPI(PROFILES), "materialx.convert", document=document, target=renderer(PROFILES[0])
    )
    assert response["status"] == "warning", response
    material = response["result"]["material"]
    surface = material["output"]["node"]
    edge = next(e for e in material["links"] if e["to"] == {"node": surface, "socket": "Roughness"})
    control = next(n for n in material["nodes"] if n["id"] == edge["from"]["node"])
    assert control["metadata"]["editor"]["control"] == "roughness"
    assert control["inputs"]["value"] == pytest.approx(0.42)


def test_deep_target_graph_does_not_depend_on_python_recursion_limit():
    nodes = ['<bump name="b0" type="vector3" />']
    for i in range(1, 1100):
        nodes.append(
            f'<bump name="b{i}" type="vector3">'
            f'<input name="normal" type="vector3" nodename="b{i - 1}" /></bump>'
        )
    document = (
        '<materialx version="1.39">'
        + "".join(nodes)
        + '<standard_surface name="s" type="surfaceshader">'
        '<input name="normal" type="vector3" nodename="b1099" /></standard_surface>'
        '<surfacematerial name="m" type="material">'
        '<input name="surfaceshader" type="surfaceshader" nodename="s" />'
        "</surfacematerial></materialx>"
    )
    response = call(
        RelayAPI(PROFILES), "materialx.convert", document=document, target=renderer(PROFILES[0])
    )
    assert response["status"] == "warning", response
    assert len(response["result"]["material"]["nodes"]) == 1101
