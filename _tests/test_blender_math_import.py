"""外部标准数学图须能在 Blender 中编辑；不依赖原始 Blender HSV 的折叠标记。"""

import MaterialX as mx
import pytest

from _scripts.native_fixture import renderer
from _tests.test_materialx_bridge import call
from _tests.test_materialx_hsv import profile
from iris_relay import RelayAPI
from iris_relay.materialx_core import MaterialXCore


@pytest.fixture(scope="module")
def target():
    data = profile()
    return RelayAPI([data]), renderer(data)


@pytest.mark.parametrize(
    "definition",
    [
        "ND_divide_float",
        "ND_min_float",
        "ND_max_float",
        "ND_floor_float",
        "ND_sign_float",
        "ND_power_float",
        "ND_constant_float",
        "ND_constant_color3",
        "ND_combine3_color3",
        "ND_ifequal_float",
        "ND_ifgreater_float",
        "ND_ifgreatereq_float",
        "ND_ifequal_color3",
        "ND_ifgreater_color3",
        "ND_ifgreatereq_color3",
        "ND_mix_float",
        "ND_mix_color3",
        "ND_convert_float_color3",
        "ND_max_color3FA",
        "ND_add_color3",
        "ND_multiply_color3",
        "ND_subtract_color3",
        "ND_divide_color3",
    ],
)
def test_standard_math_definition_is_convertible_without_source_annotations(target, definition):
    api, ref = target
    core = MaterialXCore()
    doc = mx.createDocument()
    doc.setDataLibrary(core.library)
    nd = core.library.getNodeDef(definition)
    node = doc.addNodeInstance(nd, "external_operation")
    surface = doc.addNodeInstance(
        core.library.getNodeDef("ND_standard_surface_surfaceshader"), "surface"
    )
    surface.addInput(
        "base_color" if nd.getType() == "color3" else "specular_roughness", nd.getType()
    ).setConnectedNode(node)
    root = doc.addNodeInstance(core.library.getNodeDef("ND_surfacematerial"), "material")
    root.addInput("surfaceshader", "surfaceshader").setConnectedNode(surface)
    result = call(api, "materialx.convert", document=mx.writeToXmlString(doc), target=ref)
    assert result["result"]["material"], result


@pytest.mark.parametrize("index", [0, 1, 2])
def test_external_extract_preserves_selected_channel(target, index):
    api, ref = target
    document = f'''<materialx version="1.39">
<extract name="channel" type="float" nodedef="ND_extract_color3">
<input name="in" type="color3" value=".2,.4,.8"/>
<input name="index" type="integer" value="{index}"/></extract>
<standard_surface name="surface" type="surfaceshader" nodedef="ND_standard_surface_surfaceshader">
<input name="specular_roughness" type="float" nodename="channel"/></standard_surface>
<surfacematerial name="material" type="material" nodedef="ND_surfacematerial">
<input name="surfaceshader" type="surfaceshader" nodename="surface"/></surfacematerial>
</materialx>'''
    result = call(api, "materialx.convert", document=document, target=ref)
    assert result["result"]["material"], result


def test_shared_rgb_combine_broadcast_restores_scalar_opacity_without_losing_color_user(target):
    api, ref = target
    document = """<materialx version="1.39">
<constant name="alpha" type="float" nodedef="ND_constant_float">
<input name="value" type="float" value=".4"/></constant>
<combine3 name="gray" type="color3" nodedef="ND_combine3_color3">
<input name="in1" type="float" nodename="alpha"/>
<input name="in2" type="float" nodename="alpha"/>
<input name="in3" type="float" nodename="alpha"/></combine3>
<standard_surface name="surface" type="surfaceshader" nodedef="ND_standard_surface_surfaceshader">
<input name="opacity" type="color3" nodename="gray"/>
<input name="base_color" type="color3" nodename="gray"/></standard_surface>
<surfacematerial name="material" type="material" nodedef="ND_surfacematerial">
<input name="surfaceshader" type="surfaceshader" nodename="surface"/>
</surfacematerial></materialx>"""
    result = call(api, "materialx.convert", document=document, target=ref)
    assert result["result"]["material"], result
    converted = result["result"]["material"]
    assert any(
        e["from"]["node"] == "gray" and e["to"]["socket"] == "Base Color"
        for e in converted["links"]
    )
    assert any(
        e["from"]["node"] == "alpha" and e["to"]["socket"] == "Alpha" for e in converted["links"]
    )
