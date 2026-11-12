"""从当前 Redshift 实机目录补充标准数学图绑定，组合规则留在核心字典中。"""

import MaterialX as mx

from _scripts.redshift_materialx_library import build_library

RS = "com.redshift3d.redshift4c4d.nodes.core."


def extend(profile, catalog):
    """核对每个资产及端口，声明标准节点和带完整实现的目标特有原语。"""
    xml, lowerings = build_library()
    library = mx.createDocument()
    mx.loadLibraries(mx.getDefaultDataLibraryFolders(), mx.getDefaultDataSearchPath(), library)
    mx.readFromXmlString(library, xml)
    types = {"float": "float", "integer": "float", "color3": "color"}

    def bind(suffix, definition, mapping, output="out", fixed=None):
        key = RS + suffix
        observed = catalog["nodes"][key]
        if observed["status"] != "instantiated":
            raise ValueError("节点缺少实机证据：" + key)
        ports = {p["id"]: p for p in observed["inputs"]}
        outputs = {p["id"]: p for p in observed["outputs"]}
        nd = library.getNodeDef(definition)
        inputs = {}
        for semantic, name in mapping.items():
            native_id = key + "." + name
            assert native_id in ports, native_id
            declared = nd.getActiveInput(semantic)
            value = declared.getValue()
            if declared.getType() == "color3":
                value = list(value)
            inputs[semantic] = {
                "semantic": semantic,
                "type": types[declared.getType()],
                "default": value,
                "native": {
                    "id": native_id,
                    "kind": "socket",
                    "codec": "rgb" if declared.getType() == "color3" else "identity",
                },
            }
        properties = {p["id"]: p["default"] for p in ports.values() if p["id"].endswith(".math_op")}
        properties.update({key + "." + name: value for name, value in (fixed or {}).items()})
        assert set(properties) <= ports.keys()
        assert key + "." + output in outputs
        profile["nodes"][definition] = {
            "semantic": "materialx." + definition,
            "native": {"id": key, "properties": properties},
            "inputs": inputs,
            "outputs": {
                "out": {
                    "semantic": "out",
                    "type": types[nd.getType()],
                    "native": {"id": key + "." + output, "kind": "socket", "codec": "identity"},
                }
            },
        }

    # 原有三个实现继续作为唯一目标默认，避免重复功能产生新的歧义。
    for op, suffix in (("divide", "rsmathdiv"), ("min", "rsmathmin"), ("max", "rsmathmax")):
        bind(suffix, "ND_" + op + "_float", {"in1": "input1", "in2": "input2"})
    bind("rsmathfloor", "ND_floor_float", {"in": "input"})
    bind("rsmathpow", "ND_power_float", {"in1": "base", "in2": "exponent"})
    bind("rsmathsign", "ND_iris_rs_sign_float", {"in": "input"})
    bind("rsmathmix", "ND_mix_float", {"fg": "input2", "bg": "input1", "mix": "mixamount"})
    bind(
        "rscolormix",
        "ND_mix_color3",
        {"fg": "input2", "bg": "input1", "mix": "mixamount"},
        "outcolor",
    )
    bind(
        "rscolormaker",
        "ND_combine3_color3",
        {"in1": "red", "in2": "green", "in3": "blue"},
        "outcolor",
        {"alpha": 1},
    )
    bind("rscolorconstant", "ND_constant_color3", {"value": "color"}, "outcolor")
    bind("rsmathadd", "ND_constant_float", {"value": "input1"}, fixed={"input2": 0})
    bind(
        "rscolorcorrection",
        "ND_colorcorrect_color3",
        {
            "in": "input",
            "hue": "hue",
            "saturation": "saturation",
            "gamma": "gamma",
            "gain": "level",
        },
        "outcolor",
        fixed={"contrast": 0.5, "updatedmode": True, "xsimode": False},
    )
    profile["nodes"]["ND_colorcorrect_color3"]["approximation"] = (
        "Redshift 颜色校正采用实机端口与官方 Hue/Level 说明；对比度固定中性值，"
        "饱和度及 Gamma 的跨渲染器外观未验证"
    )
    profile.setdefault("evidence", {})["color_correct"] = (
        "https://help.maxon.net/c4d/en-us/Content/html/"
        "COM_REDSHIFT3D_REDSHIFT4C4D_NODES_CORE_RSCOLORCORRECTION-NET_MAXON_NODE_BASE_GROUP_INPUTS.html"
    )
    profile["nodes"][RS + "rsmathadd"]["source_default"] = True
    for channel in "rgb":
        bind("rscolorsplitter", "ND_iris_rs_extract_" + channel, {"in": "input"}, "out" + channel)
    profile["materialx_extensions"] = xml
    profile["materialx_lowerings"] = lowerings
    profile["texture_alpha_modes"] = ["STRAIGHT", "PREMUL", "CHANNEL_PACKED", "NONE"]
    profile["texture_image_channels"] = [1, 3, 4]
    profile["texture_alpha_behavior"] = {"png": "independent", "exr": "independent"}
    profile["version"] = "0.5.0"
    image = next(rule for rule in profile["nodes"].values() if rule["semantic"] == "texture.image")
    splitter = RS + "rscolorsplitter"
    observed = catalog["nodes"][splitter]
    assert observed["status"] == "instantiated"
    assert any(p["id"] == splitter + ".outa" for p in observed["outputs"])
    image["outputs"]["alpha"] = {
        "semantic": "alpha",
        "type": "float",
        "native": {
            "id": splitter + ".outa",
            "kind": "socket",
            "codec": "identity",
            "properties": {
                "output_projection": {
                    "asset": splitter,
                    "input": splitter + ".input",
                    "source": next(
                        v["native"]["id"]
                        for v in image["outputs"].values()
                        if v["semantic"] == "color"
                    ),
                }
            },
        },
    }
    resource = next(p for p in image["inputs"].values() if p["type"] == "texture")
    resource["native"]["properties"] = {
        "color_spaces": {
            "Raw": "RS_INPUT_COLORSPACE_RAW",
            "sRGB": "RS_INPUT_COLORSPACE_SRGB",
        }
    }
    sampler = {
        "scale": RS + "texturesampler.scale",
        "offset": RS + "texturesampler.offset",
        "rotation": RS + "texturesampler.rotate",
    }
    resource["native"]["properties"]["uv_sampler"] = sampler
    profile["texture_coordinates"] = {
        "named_uv": True,
        "transforms": "trs2d",
        "sampler": {
            "rotation_unit": "degrees",
            "pivot": [0.5, 0.5],
            "order": "scale_rotate_translate",
            "verified": False,
        },
    }
    for identifier in sampler.values():
        image["inputs"].pop(identifier, None)
        image["native"]["properties"].pop(identifier, None)
    # 实机默认含镜像与亮度 Alpha，不能作为 Blender 重复采样的无损默认值。
    for name in ("mirroru", "mirrorv", "alpha_is_luminance", "tone_map_enable"):
        identifier = RS + "texturesampler." + name
        assert identifier in image["inputs"]
        image["inputs"].pop(identifier)
        image["native"]["properties"][identifier] = False
    profile["evidence"]["standard_graph_lowering"] = (
        "C4D 2025.3.2 / Redshift 2025.3.0 实机资产与端口；比较边界按 Sign(0)=1 展开。"
        "https://help.maxon.net/r3d/cinema/en-us/Content/html/Math%2BScalar%2BShaders.html"
    )
