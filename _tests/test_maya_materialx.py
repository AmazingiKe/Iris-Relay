"""Maya 2022 的标准节点映射由核心完成，宿主只读取公开目标图。"""

import json
from pathlib import Path

import pytest

from _scripts.native_fixture import renderer
from _tests.test_materialx_hsv import profile
from _tests.test_materialx_link_casts import export, setup
from iris_relay import RelayAPI


def maya_profile():
    from _scripts.build_maya_dictionary import build

    return build(
        json.loads(Path("dictionaries/catalogs/maya-arnold-7.2.5.3.json").read_text("utf-8"))
    )


def hsv_document():
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
    surface = next(
        n["id"] for n in source["nodes"] if n["type"].startswith("ShaderNodeBsdfPrincipled")
    )
    source["links"] = [e for e in source["links"] if e["to"]["socket"] != "Base Color"]
    source["links"].append(
        {
            "from": {"node": "hsv", "socket": "Color"},
            "to": {"node": surface, "socket": "Base Color"},
        }
    )
    return export(profile(), source)["result"]["document"]


def test_hsv_math_fallback_keeps_editable_maya_nodes_without_source_snapshot():
    target = maya_profile()
    for name, choices in target["materialx_function_implementations"].items():
        target["materialx_function_implementations"][name] = [
            e for e in choices if e["kind"] == "recipe"
        ]
    response = RelayAPI([target]).handle(
        {
            "api_version": "1",
            "request_id": "maya",
            "operation": "materialx.convert",
            "payload": {"document": hsv_document(), "target": renderer(target)},
        }
    )
    assert response["status"] == "warning", response
    graph = response["result"]["material"]
    assert len(graph["nodes"]) > 50
    assert any(n["type"] == "floor_float" for n in graph["nodes"])
    assert {n["inputs"]["index"] for n in graph["nodes"] if n["type"] == "extract_color3"} == {
        0,
        1,
        2,
    }
    assert all("source_snapshot" not in n for n in graph["nodes"])


@pytest.mark.parametrize("index", [0, 1, 2])
def test_integer_index_is_preserved_in_public_conversion(index):
    target = maya_profile()
    xml = f'''<materialx version="1.39"><constant name="color" type="color3">
    <input name="value" type="color3" value="0.2,0.4,0.8"/>
    </constant>
    <extract name="channel" type="float">
    <input name="in" type="color3" nodename="color"/>
    <input name="index" type="integer" value="{index}"/>
    </extract>
    <standard_surface name="surface" type="surfaceshader">
    <input name="specular_roughness" type="float" nodename="channel"/>
    </standard_surface>
    <surfacematerial name="material" type="material">
    <input name="surfaceshader" type="surfaceshader" nodename="surface"/>
    </surfacematerial></materialx>'''
    response = RelayAPI([target]).handle(
        {
            "api_version": "1",
            "request_id": "index",
            "operation": "materialx.convert",
            "payload": {"document": xml, "target": renderer(target)},
        }
    )
    assert response["status"] == "warning", response
    assert (
        next(n for n in response["result"]["material"]["nodes"] if n["id"] == "channel")["inputs"][
            "index"
        ]
        == index
    )


def test_scalar_color_conversion_keeps_explicit_broadcast():
    target = maya_profile()
    xml = """<materialx version="1.39">
    <convert name="gray" type="color3">
      <input name="in" type="float" value="0.3"/>
    </convert>
    <standard_surface name="surface" type="surfaceshader">
      <input name="base_color" type="color3" nodename="gray"/>
    </standard_surface>
    <surfacematerial name="material" type="material">
      <input name="surfaceshader" type="surfaceshader" nodename="surface"/>
    </surfacematerial></materialx>"""
    response = RelayAPI([target]).handle(
        {
            "api_version": "1",
            "request_id": "broadcast",
            "operation": "materialx.convert",
            "payload": {"document": xml, "target": renderer(target)},
        }
    )
    assert response["status"] == "warning", response
    gray = next(n for n in response["result"]["material"]["nodes"] if n["id"] == "gray")
    assert gray["inputs"]["in"] == pytest.approx(0.3)
    assert target["nodes"][gray["type"]]["inputs"]["in"]["native"]["codec"] == "gray"


def test_maya_specular_limit_is_reported_before_host_creation():
    target = maya_profile()
    document = hsv_document().replace(
        'name="specular" type="float" value="1"', 'name="specular" type="float" value="1.5"'
    )
    import MaterialX as mx

    xml = mx.createDocument()
    mx.readFromXmlString(xml, document)
    surface = next(n for n in xml.getNodes() if n.getCategory() == "standard_surface")
    (surface.getInput("specular") or surface.addInput("specular", "float")).setValueString("1.5")
    response = RelayAPI([target]).handle(
        {
            "api_version": "1",
            "request_id": "range",
            "operation": "materialx.convert",
            "payload": {"document": mx.writeToXmlString(xml), "target": renderer(target)},
        }
    )
    assert response["status"] == "error", response
    assert response["result"]["material"] is None


def test_maya_unmapped_native_settings_are_guarded_by_live_evidence():
    target = maya_profile()
    # 这些输入影响着色，却没有对应的已实现语义；读取时必须保持已核验默认值。
    assert target["nodes"]["standard_surface"]["native"]["properties"]["subsurface"] == 0
    assert target["nodes"]["image"]["native"]["properties"]["sscale"] == 1
    assert "outColor" not in target["nodes"]["standard_surface"]["native"]["properties"]
    assert (
        target["nodes"]["standard_surface"]["inputs"]["coatNormal"]["native"]["codec"] == "normal"
    )


def test_maya_geometry_normal_default_uses_actual_port_evidence():
    from iris_relay.native import decode_value

    inputs = maya_profile()["nodes"]["standard_surface"]["inputs"]
    assert decode_value([1, 1, 1], inputs["normalCamera"]) is None
    assert decode_value([0, 0, 0], inputs["coatNormal"]) is None
    assert decode_value([0, 0, 0], inputs["normalCamera"]) == [0, 0, 0]


def test_unverified_rgba_is_blocked_by_core_target_capability():
    from _tests.test_materialx_alpha import setup
    from _tests.test_materialx_bridge import call
    from _tests.test_materialx_lowerings import rs_profile

    api, source = setup(channels=4, linked=False)
    document = call(api, "materialx.export", material=source)["result"]["document"]
    for target in (maya_profile(), rs_profile()):
        target["texture_image_channels"] = [1, 3]
        response = call(
            RelayAPI([target]), "materialx.convert", document=document, target=renderer(target)
        )
        assert response["status"] == "error", response
        assert response["result"]["material"] is None
