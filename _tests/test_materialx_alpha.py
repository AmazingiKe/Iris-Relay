"""Alpha 的资源解释、独立输出与透明度连接必须在文档中有明确表示。"""

import json
from copy import deepcopy
from pathlib import Path

import MaterialX as mx
import pytest

from _scripts.native_fixture import fixture
from _tests.test_materialx_bridge import call
from iris_relay import RelayAPI

ROOT = Path(__file__).resolve().parents[1]


def setup(mode="STRAIGHT", channels=4, *, linked=True, space="sRGB"):
    """建立同一图片驱动颜色与透明度的原生图；不读取真实文件。"""
    profile = json.loads((ROOT / "dictionaries/blender-cycles-5.2.3.json").read_text("utf-8"))
    source = fixture(profile, "alpha.png")
    image = next(n for n in source["nodes"] if n["type"] == "ShaderNodeTexImage")
    surface = next(n for n in source["nodes"] if n["type"] == "ShaderNodeBsdfPrincipled")
    source["textures"]["fixture"]["metadata"]["image_alpha"] = {"mode": mode, "channels": channels}
    source["textures"]["fixture"]["color_space"] = space
    if linked:
        source["links"].append(
            {
                "from": {"node": image["id"], "socket": "Alpha"},
                "to": {"node": surface["id"], "socket": "Alpha"},
            }
        )
    return RelayAPI.from_directory(ROOT / "dictionaries"), source


@pytest.mark.parametrize("mode", ["STRAIGHT", "PREMUL", "CHANNEL_PACKED", "NONE"])
@pytest.mark.parametrize("channels", [1, 3, 4])
def test_alpha_standard_document_roundtrip_preserves_both_outputs(mode, channels):
    api, source = setup(mode, channels)
    before = deepcopy(source)
    exported = call(api, "materialx.export", material=source)
    assert exported["status"] == "warning", exported
    document = exported["result"]["document"]
    assert call(api, "materialx.validate", document=document)["status"] == "ok"
    doc = mx.createDocument()
    mx.readFromXmlString(doc, document)
    assert not doc.getNodeDefs()
    image = next(n for n in doc.getNodes() if n.getCategory() == "image")
    assert image.getType() == {1: "color3", 3: "color3", 4: "color4"}[channels]
    if channels == 1:
        gray_channel = next(
            n for n in doc.getNodes() if n.getAttribute("iris_image_role") == "gray"
        )
        assert gray_channel.getCategory() == "extract"
        assert gray_channel.getInput("index").getValue() == 0
        assert gray_channel.getInput("in").getConnectedNode() == image
    surface = next(n for n in doc.getNodes() if n.getCategory() == "standard_surface")
    gray = surface.getInput("opacity").getConnectedNode()
    assert gray.getCategory() == "convert" and gray.getType() == "color3"
    alpha = gray.getInput("in").getConnectedNode()
    if channels == 4 and mode != "NONE":
        assert alpha.getCategory() == "extract" and alpha.getInput("index").getValue() == 3
    else:
        assert alpha.getCategory() == "constant" and alpha.getInput("value").getValue() == 1
    converted = call(api, "materialx.convert", document=document, target=source["renderer"])
    assert converted["status"] == "warning", converted
    material = converted["result"]["material"]
    images = [n for n in material["nodes"] if n["type"] == "ShaderNodeTexImage"]
    assert len(images) == 1
    assert any(
        link["from"] == {"node": images[0]["id"], "socket": "Alpha"}
        and link["to"]["socket"] == "Alpha"
        for link in material["links"]
    )
    assert next(iter(material["textures"].values()))["metadata"]["image_alpha"] == {
        "mode": mode,
        "channels": channels,
    }
    assert source == before == exported["result"]["source"]
    assert not any(
        i["code"] == "MATERIALX_UNUSED_NODES" for i in converted["result"]["report"]["issues"]
    )


@pytest.mark.parametrize(
    "mode,linked,space,operator",
    [
        ("STRAIGHT", False, "sRGB", "premult"),
        ("STRAIGHT", True, "sRGB", None),
        ("PREMUL", True, "sRGB", "unpremult"),
        ("PREMUL", False, "sRGB", None),
        ("CHANNEL_PACKED", False, "sRGB", None),
        ("STRAIGHT", False, "Raw", None),
        ("PREMUL", True, "Raw", None),
        ("NONE", True, "sRGB", None),
    ],
)
def test_color_association_is_explicit_and_approximation_reported(mode, linked, space, operator):
    api, source = setup(mode, linked=linked, space=space)
    result = call(api, "materialx.export", material=source)
    assert result["status"] == "warning", result
    doc = mx.createDocument()
    mx.readFromXmlString(doc, result["result"]["document"])
    operations = [
        n.getCategory() for n in doc.getNodes() if n.getCategory() in {"premult", "unpremult"}
    ]
    assert operations == ([operator] if operator else [])
    if space == "sRGB" and mode in {"STRAIGHT", "PREMUL"}:
        assert any(
            i["code"] == "ALPHA_FILTERING_APPROXIMATION"
            for i in result["result"]["report"]["issues"]
        )


@pytest.mark.parametrize(
    "metadata",
    [
        {"mode": "unknown", "channels": 4},
        {"mode": "STRAIGHT", "channels": 2},
        {"mode": "STRAIGHT", "channels": True},
        {"mode": "STRAIGHT"},
    ],
)
def test_unknown_alpha_description_blocks_without_losing_source(metadata):
    api, source = setup()
    source["textures"]["fixture"]["metadata"]["image_alpha"] = metadata
    result = call(api, "materialx.export", material=source)
    assert result["status"] == "error" and result["result"]["document"] is None
    assert result["result"]["source"] == source


def test_missing_alpha_description_cannot_guess_file_channels():
    api, source = setup()
    source["textures"]["fixture"]["metadata"].clear()
    result = call(api, "materialx.export", material=source)
    assert result["status"] == "error"
    assert any(
        i["code"] == "MATERIALX_RESOURCE_UNSUPPORTED" for i in result["result"]["report"]["issues"]
    )


@pytest.mark.parametrize(
    "change", ["index", "mode", "owner", "operation", "extra", "unused_alpha", "direct_color4"]
)
def test_annotations_cannot_hide_modified_alpha_graph(change):
    api, source = setup()
    doc = mx.createDocument()
    mx.readFromXmlString(doc, call(api, "materialx.export", material=source)["result"]["document"])
    alpha = next(n for n in doc.getNodes() if n.getAttribute("iris_image_role") == "alpha")
    image = next(n for n in doc.getNodes() if n.getCategory() == "image")
    if change == "index":
        alpha.getInput("index").setValue(0)
    elif change == "mode":
        image.setAttribute("iris_alpha_mode", "NONE")
    elif change == "owner":
        alpha.setAttribute("iris_image_source", "missing")
    elif change == "operation":
        alpha.setNodeDefString("ND_extract_vector4")
    elif change == "extra":
        duplicate = doc.addNode("extract", "duplicate", "float")
        duplicate.copyContentFrom(alpha)
    elif change == "unused_alpha":
        surface = next(n for n in doc.getNodes() if n.getCategory() == "standard_surface")
        surface.removeInput("opacity")
    else:
        helper = next(n for n in doc.getNodes() if n.getAttribute("iris_image_role") == "color")
        helper.setAttribute("iris_image_source", "")
    document = mx.writeToXmlString(doc)
    result = call(api, "materialx.convert", document=document, target=source["renderer"])
    assert result["status"] == "error"
    assert result["result"]["material"] is None
    assert result["result"]["source"] == document


def test_unproven_target_alpha_modes_block_and_keep_document():
    from _scripts.native_fixture import renderer

    api, source = setup()
    document = call(api, "materialx.export", material=source)["result"]["document"]
    target = renderer(
        json.loads((ROOT / "dictionaries/cinema4d-redshift-2025.3.0.json").read_text("utf-8"))
    )
    result = call(api, "materialx.convert", document=document, target=target)
    assert result["status"] == "error" and result["result"]["source"] == document
    assert result["result"]["material"] is None


def test_alpha_only_image_retains_resource_and_original_output():
    api, source = setup("NONE", 3)
    source["links"] = [link for link in source["links"] if link["from"]["socket"] != "Color"]
    document = call(api, "materialx.export", material=source)["result"]["document"]
    result = call(api, "materialx.convert", document=document, target=source["renderer"])
    assert result["status"] == "warning", result
    assert next(iter(result["result"]["material"]["textures"].values()))["uri"] == "alpha.png"
    assert any(link["from"]["socket"] == "Alpha" for link in result["result"]["material"]["links"])


def test_resource_verifier_only_allows_previously_unspecified_alpha_information():
    from _scripts.verify_native_host import resource_equal

    _, source = setup()
    actual = source["textures"]["fixture"]
    expected = deepcopy(actual)
    assert resource_equal(actual, expected)
    expected["metadata"]["image_alpha"]["mode"] = "PREMUL"
    assert not resource_equal(actual, expected)
    expected["metadata"].clear()
    assert resource_equal(actual, expected)
    expected["color_space"] = "Raw"
    assert not resource_equal(actual, expected)


def test_gray_color_image_generates_standard_srgb_transform():
    """通过官方着色生成器核对颜色解释确实参与运算；仅 XML 属性存在不足以证明。"""
    import MaterialX.PyMaterialXGenGlsl as glsl
    import MaterialX.PyMaterialXGenShader as shader

    api, source = setup(channels=1)
    library = mx.createDocument()
    mx.loadLibraries(mx.getDefaultDataLibraryFolders(), mx.getDefaultDataSearchPath(), library)
    doc = mx.createDocument()
    doc.setDataLibrary(library)
    mx.readFromXmlString(doc, call(api, "materialx.export", material=source)["result"]["document"])
    generator = glsl.GlslShaderGenerator.create()
    colors = shader.DefaultColorManagementSystem.create(generator.getTarget())
    colors.loadLibrary(library)
    generator.setColorManagementSystem(colors)
    context = shader.GenContext(generator)
    context.registerSourceCodeSearchPath(mx.getDefaultDataSearchPath())
    context.getOptions().targetColorSpaceOverride = "lin_rec709"
    material = next(n for n in doc.getNodes() if n.getCategory() == "surfacematerial")
    code = generator.generate("gray_color", material, context).getSourceCode(shader.PIXEL_STAGE)
    # 包含函数定义和实际调用，防止 float 图片输出悄悄绕开颜色变换。
    assert code.count("NG_srgb_texture_to_lin_rec709_color3(") >= 2


def test_unused_ambiguous_target_function_does_not_block_alpha_import():
    """目标选择只约束实际使用的功能，不能因扫描透明度规则提前拒绝无关节点。"""
    api, source = setup()
    document = call(api, "materialx.export", material=source)["result"]["document"]
    profile = json.loads((ROOT / "dictionaries/blender-cycles-5.2.3.json").read_text("utf-8"))
    bump = next(rule for rule in profile["nodes"].values() if rule["semantic"] == "bump.height")
    bump.pop("target_default", None)
    profile["nodes"]["BumpAlias"] = deepcopy(bump)
    result = call(
        RelayAPI([profile]), "materialx.convert", document=document, target=source["renderer"]
    )
    assert result["status"] == "warning", result
