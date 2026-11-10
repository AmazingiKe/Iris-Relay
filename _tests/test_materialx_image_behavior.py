"""文件通道、原生采样关联和连接使用方式分别记录，不能互相冒充。"""

from copy import deepcopy

import pytest

from iris_relay.dictionary import Dictionary
from iris_relay.materialx_images import alpha_description, prepare_target_images


def material(mode, linked, space="sRGB", association=None):
    """建立只含图片及出口引用的语义夹具，检查关联子图的实际数值。"""
    description = {"mode": mode, "channels": 4}
    if association:
        description["association"] = association
    return {
        "nodes": [
            {
                "id": "image",
                "type": "texture.image",
                "inputs": {"image": {"texture": "t"}},
                "outputs": ["color", "alpha"],
                "metadata": {},
            }
        ],
        "links": [
            {
                "from": {"node": "image", "socket": "color"},
                "to": {"node": "surface", "socket": "color"},
            }
        ]
        + (
            [
                {
                    "from": {"node": "image", "socket": "alpha"},
                    "to": {"node": "surface", "socket": "opacity"},
                }
            ]
            if linked
            else []
        ),
        "textures": {
            "t": {
                "uri": "rgba.png",
                "color_space": space,
                "coordinates": {"kind": "uv", "set": ""},
                "metadata": {"image_alpha": description},
            }
        },
    }


def evaluate(data, rgb, alpha):
    """独立解释目标语义链，不使用转换器中的指数或生成函数。"""
    records = {n["id"]: n for n in data["nodes"]}
    edges = {(e["to"]["node"], e["to"]["socket"]): e["from"] for e in data["links"]}

    def value(endpoint):
        if endpoint["node"] == "image":
            return rgb if endpoint["socket"] == "color" else alpha
        n = records[endpoint["node"]]

        def arg(name):
            key = (n["id"], name)
            return value(edges[key]) if key in edges else n["inputs"][name]

        category = n["type"].removeprefix("materialx.ND_")
        if category == "extract_color3":
            return arg("in")[int(arg("index"))]
        if category == "combine3_color3":
            return [arg("in1"), arg("in2"), arg("in3")]
        if category == "multiply_float":
            return arg("in1") * arg("in2")
        if category == "divide_float":
            return arg("in1") / arg("in2")
        if category == "ifequal_float":
            return arg("in1") if arg("value1") == arg("value2") else arg("in2")
        if category == "constant_float":
            return arg("value")
        raise AssertionError(category)

    return value(edges[("surface", "color")])


@pytest.mark.parametrize(
    "mode,linked,space,desired",
    [
        ("STRAIGHT", False, "sRGB", 1),
        ("STRAIGHT", True, "sRGB", 0),
        ("PREMUL", True, "sRGB", -1),
        ("PREMUL", False, "sRGB", 0),
        ("NONE", True, "Raw", 0),
        ("CHANNEL_PACKED", False, "Raw", 0),
    ],
)
def test_target_premultiplied_sampler_gets_explicit_joint_channel_chain(
    mode, linked, space, desired
):
    data = material(mode, linked, space)
    original = deepcopy(data)
    profile = Dictionary({"texture_alpha_behavior": "premultiplied"}, "test")
    issues = []
    result = prepare_target_images(data, profile, issues)
    assert data == original
    assert evaluate(result, [0.2 * 0.4, 0.6 * 0.4, 0.8 * 0.4], 0.4) == pytest.approx(
        [v * 0.4**desired for v in [0.2, 0.6, 0.8]]
    )
    assert evaluate(result, [0, 0, 0], 0) == pytest.approx([0, 0, 0])
    assert result["textures"]["t"]["metadata"]["image_alpha"] == {
        "mode": "CHANNEL_PACKED",
        "channels": 4,
        "association": "premultiplied",
    }


def test_native_association_survives_export_and_is_explicit_for_blender():
    data = material("CHANNEL_PACKED", True, association="premultiplied")
    assert alpha_description(data["textures"]["t"])["association"] == "premultiplied"
    result = prepare_target_images(data, Dictionary({}, "test"), [])
    assert evaluate(result, [0.2, 0.6, 0.8], 0.4) == pytest.approx([0.08, 0.24, 0.32])
    assert result["textures"]["t"]["metadata"]["image_alpha"] == {
        "mode": "CHANNEL_PACKED",
        "channels": 4,
    }


@pytest.mark.parametrize("host", ["maya", "cinema4d"])
@pytest.mark.parametrize(
    "mode,linked", [("STRAIGHT", False), ("STRAIGHT", True), ("PREMUL", True), ("NONE", True)]
)
def test_public_rgba_target_conversion_uses_native_alpha_and_explicit_math(host, mode, linked):
    import json
    from pathlib import Path

    from _scripts.build_native_dictionaries import build
    from _scripts.native_fixture import renderer
    from _tests.test_materialx_alpha import setup
    from _tests.test_materialx_bridge import call
    from iris_relay import RelayAPI

    root = Path(__file__).resolve().parents[1]
    api, source = setup(mode, linked=linked)
    document = call(api, "materialx.export", material=source)["result"]["document"]
    filename = "maya-arnold-7.2.5.3.json" if host == "maya" else "cinema4d-redshift-2025.3.0.json"
    profile = build(json.loads((root / "dictionaries/catalogs" / filename).read_text("utf-8")))
    api = RelayAPI([profile])
    response = call(api, "materialx.convert", document=document, target=renderer(profile))
    assert response["result"]["material"], response
    output = response["result"]["material"]
    assert any(v["metadata"]["image_alpha"]["channels"] == 4 for v in output["textures"].values())
    assert any(
        i["code"] == "IMAGE_NATIVE_ALPHA_EXPLICIT" for i in response["result"]["report"]["issues"]
    )
