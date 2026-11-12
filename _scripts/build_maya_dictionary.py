"""依据 Maya 2022 / MtoA 5.3.5.3 实机目录生成可审阅的目标字典。"""

import argparse
import json
from pathlib import Path

import MaterialX as mx


def build(catalog):
    """将已核验原生端口绑定标准功能；缺少节点或端口证据立即失败。"""
    library = mx.createDocument()
    mx.loadLibraries(mx.getDefaultDataLibraryFolders(), mx.getDefaultDataSearchPath(), library)
    profile = {
        "schema_version": "1",
        "semantic_schema_version": "1",
        "id": "maya.arnold.native",
        "version": "0.2.0",
        "description": "Maya 可编辑 Arnold 材质交换；验证环境单列，不代表外观等价。",
        "renderer": {"id": "arnold"},
        "host": {"id": "maya", "node_system": "mtoa"},
        "evidence": {
            "kind": catalog["evidence"],
            "plugin_version": catalog["renderer"]["plugin_version"],
            "appearance_verified": False,
            "verified_environments": [
                {
                    "id": "arnold",
                    "version": catalog["renderer"]["version"],
                    "host": "maya",
                    "host_version": catalog["host"]["version"],
                    "node_system": "mtoa",
                }
            ],
        },
        "texture_coordinates": {"named_uv": True, "transforms": "trs"},
        "texture_alpha_modes": ["STRAIGHT", "PREMUL", "CHANNEL_PACKED", "NONE"],
        "texture_image_channels": [1, 3, 4],
        "texture_alpha_behavior": {"png": "premultiplied", "exr": "independent"},
        "nodes": {},
    }

    def node(key, native, semantic, inputs, output, properties=None):
        """核对属性存在后保存声明；不同配置用独立实现键区分。"""
        attrs = catalog["nodes"][native]["attributes"]
        assert catalog["nodes"][native]["actual_type"] == native
        for port in list(inputs.values()) + [output]:
            assert port["native"]["id"] in attrs, (native, port)
        for name in properties or {}:
            assert name in attrs, (native, name)
        output.pop("default", None)
        guarded = dict(properties or {})
        # 未映射的着色输入必须仍为实机默认值；否则读回不能冒充完整支持。
        covered = {p["native"]["id"] for p in inputs.values()} | set(guarded)
        for p in inputs.values():
            covered.update(p["native"].get(k) for k in ("colorspace_id", "uv_id"))
        parents = {
            attrs[name].get("parent", [None])[0]
            for name in covered
            if name in attrs and attrs[name].get("parent")
        }
        ignored = {
            "caching",
            "frozen",
            "isHistoricallyInteresting",
            "nodeState",
            "binMembership",
            "workingSpace",
            "colorManagementEnabled",
            "colorManagementConfigFileEnabled",
            "colorManagementConfigFilePath",
            "colorProfile",
            "hardwareColor",
        }
        for name, observed in attrs.items():
            parent = observed.get("parent") or []
            if (
                name in covered
                or name in ignored
                or not observed.get("writable", False)
                or any(p in covered for p in parent)
                or name in parents
            ):
                continue
            value = observed["value"]
            if isinstance(value, list) and len(value) == 1 and isinstance(value[0], list):
                value = value[0]
            if value is None or (isinstance(value, list) and len(value) not in (2, 3)):
                continue
            if parent and not any(p in parents for p in parent):
                # 未覆盖的完整复合端口在父项保存，避免同时覆盖父项和子项。
                continue
            guarded[name] = value
        rule = {
            "semantic": semantic,
            "native": {"id": native, "properties": guarded},
            "inputs": inputs,
            "outputs": {output["semantic"]: output},
        }
        profile["nodes"][key] = rule
        if semantic in {"surface.standard", "normal.tangent", "bump.height"}:
            rule["approximation"] = "已核验原生端口；BSDF、切线与高度响应尚无渲染等价证据"
        if key == "floor_float":
            rule["approximation"] = "Arnold floor 使用 32 位整数输出，超出整数范围的计算尚未验证"

    def port(semantic, datatype, native, default=None, **extras):
        """构造公开原生端口，不在宿主复制语义规则。"""
        result = {
            "semantic": semantic,
            "type": datatype,
            "native": {"id": native, "kind": "socket", "codec": "identity"},
        }
        if default is not None or datatype in {"vector", "texture", "shader"}:
            result["default"] = default
        result.update(extras)
        return result

    # 1. 基础材质功能使用统一语义，不套用 C4DtoA 的整数 code 或颜色编码。
    surface = {
        "baseColor": ("base.color", "color"),
        "metalness": ("metalness", "float"),
        "specularRoughness": ("reflection.roughness", "float"),
        "specularIOR": ("reflection.ior", "float"),
        "specular": ("reflection.weight", "float"),
        "specularColor": ("reflection.tint", "color"),
        "specularAnisotropy": ("reflection.anisotropy", "float"),
        "specularRotation": ("reflection.rotation", "float"),
        "diffuseRoughness": ("diffuse.roughness", "float"),
        "coat": ("coat.weight", "float"),
        "coatColor": ("coat.tint", "color"),
        "coatRoughness": ("coat.roughness", "float"),
        "coatIOR": ("coat.ior", "float"),
        "coatNormal": ("coat.normal", "vector"),
        "normalCamera": ("normal", "vector"),
        "emission": ("emission.weight", "float"),
        "emissionColor": ("emission.color", "color"),
        "sheen": ("sheen.weight", "float"),
        "sheenColor": ("sheen.tint", "color"),
        "sheenRoughness": ("sheen.roughness", "float"),
        "opacity": ("opacity", "color"),
    }
    inputs = {}
    for native, (semantic, datatype) in surface.items():
        default = catalog["nodes"]["aiStandardSurface"]["attributes"][native]["value"]
        if datatype == "color":
            default = default[0]
        if datatype == "vector":
            default = None
        inputs[native] = port(semantic, datatype, native, default)
        if native == "specular":
            # Maya 2022 的 setAttr 实测拒绝大于 1，不通过钳制掩盖源反射权重。
            inputs[native]["range"] = [0, 1]
        if native in {"specularRoughness", "specular"}:
            inputs[native]["adjustable"] = True
            inputs[native]["adjustment_range"] = (
                [0.05, 0.95] if native == "specularRoughness" else [0, 1]
            )
        if datatype == "vector":
            inputs[native]["nullable"] = True
            inputs[native]["native"]["codec"] = "normal"
            inputs[native]["native"]["properties"] = {
                "implicit_value": catalog["nodes"]["aiStandardSurface"]["attributes"][native][
                    "value"
                ][0]
            }
    node(
        "standard_surface",
        "aiStandardSurface",
        "surface.standard",
        inputs,
        port("surface", "shader", "outColor"),
    )
    image = port("image", "texture", "filename")
    image["native"].update(kind="resource", colorspace_id="colorSpace", uv_id="uvset")
    uv = catalog["nodes"]["aiUvTransform"]
    assert uv["status"] == "instantiated"
    excluded = {
        "passthrough",
        "passthroughA",
        "uvwMatrix",
        "uvset",
        "caching",
        "frozen",
        "isHistoricallyInteresting",
        "binMembership",
    }
    fixed = {}
    for name, observed in uv["attributes"].items():
        if name in excluded or observed.get("parent") or not observed["writable"]:
            continue
        value = observed["value"]
        if isinstance(value, list) and len(value) == 1 and isinstance(value[0], list):
            value = value[0]
        if value is not None:
            fixed[name] = value
    image["native"]["properties"] = {
        "coordinate_wrapper": {
            "asset": "aiUvTransform",
            "matrix": "uvwMatrix",
            "uv": "uvset",
            "inputs": {"color": "passthrough", "alpha": "passthroughA"},
            "outputs": {"color": "outColor", "alpha": "outAlpha"},
            "fixed": fixed,
        }
    }
    node(
        "image",
        "aiImage",
        "texture.image",
        {"image": image},
        port("color", "color", "outColor"),
        {"autoTx": False, "ignoreColorSpaceFileRules": True, "singleChannel": False},
    )
    profile["nodes"]["image"]["outputs"]["alpha"] = port("alpha", "float", "outAlpha")
    node(
        "normalmap",
        "aiNormalMap",
        "normal.tangent",
        {
            "input": port("normal.color", "color", "input", [0.5, 0.5, 1]),
            "strength": port("normal.strength", "float", "strength", 1),
        },
        port("normal", "vector", "outValue"),
        {"tangentSpace": True, "colorToSigned": True},
    )
    node(
        "bump",
        "aiBump2d",
        "bump.height",
        {
            "height": port("height", "float", "bumpMap", 0),
            "scale": port("height.scale", "float", "bumpHeight", 1),
        },
        port("normal", "vector", "outValue"),
    )
    node(
        "red",
        "aiColorToFloat",
        "channel.red",
        {"color": port("color", "color", "input", [0, 0, 0])},
        port("value", "float", "outValue"),
        {"mode": 5},
    )
    node(
        "weighted_sum",
        "aiDot",
        "color.weighted_sum",
        {
            "color": port("color", "color", "input1", [0, 0, 0]),
            "weights": port("weights", "vector", "input2", [0.2126, 0.7152, 0.0722]),
        },
        port("value", "float", "outValue"),
    )

    def standard(key, native, definition, mapping, output, properties=None, gray=()):
        """复用官方输入类型和默认值，原生复合端口的标量广播是声明式编码。"""
        nd = library.getNodeDef(definition)
        types = {"float": "float", "integer": "float", "color3": "color"}
        inputs = {}
        for semantic, attr in mapping.items():
            mx_port = nd.getActiveInput(semantic)
            value = mx_port.getValue()
            if mx_port.getType() == "color3":
                value = list(value)
            inputs[semantic] = port(semantic, types[mx_port.getType()], attr, value)
            if semantic in gray:
                inputs[semantic]["native"]["codec"] = "gray"
        node(
            key,
            native,
            "materialx." + definition,
            inputs,
            port("out", types[nd.getType()], output),
            properties,
        )

    # 2. HSV 展开的每一步保留为可编辑原生节点；整数枚举按端口编码验证。
    standard(
        "color_correct",
        "aiColorCorrect",
        "ND_colorcorrect_color3",
        {
            "in": "input",
            "hue": "hueShift",
            "saturation": "saturation",
            "gamma": "gamma",
            "gain": "multiply",
            "contrast": "contrast",
            "contrastpivot": "contrastPivot",
            "exposure": "exposure",
        },
        "outColor",
        gray=("gain",),
    )
    profile["nodes"]["color_correct"]["approximation"] = (
        "Arnold 原生颜色校正端口已实机核验；与 MaterialX 的运算顺序及边界尚未完成渲染等价验证"
    )
    profile["evidence"]["color_correct"] = catalog["nodes"]["aiColorCorrect"]["evidence"]
    for operation, native in {
        "add": "aiAdd",
        "subtract": "aiSubtract",
        "multiply": "aiMultiply",
        "divide": "aiDivide",
        "min": "aiMin",
        "max": "aiMax",
    }.items():
        standard(
            operation + "_float",
            native,
            "ND_" + operation + "_float",
            {"in1": "input1R", "in2": "input2R"},
            "outColorR",
        )
        standard(
            operation + "_color3",
            native,
            "ND_" + operation + "_color3",
            {"in1": "input1", "in2": "input2"},
            "outColor",
        )
        profile["nodes"][operation + "_color3"]["source_default"] = True
    standard(
        "floor_float", "aiFloatToInt", "ND_floor_float", {"in": "input"}, "outValue", {"mode": 2}
    )
    standard(
        "power_float",
        "aiPow",
        "ND_power_float",
        {"in1": "base", "in2": "exponent"},
        "outColorR",
        gray=("in1", "in2"),
    )
    standard(
        "clamp_float",
        "aiClamp",
        "ND_clamp_float",
        {"in": "inputR", "low": "min", "high": "max"},
        "outColorR",
        {"mode": 0},
    )
    for operation, mode in [("ifequal", 0), ("ifgreater", 2)]:
        for datatype in ("float", "color3"):
            suffix = "R" if datatype == "float" else ""
            standard(
                operation + "_" + datatype,
                "condition",
                "ND_" + operation + "_" + datatype,
                {
                    "value1": "firstTerm",
                    "value2": "secondTerm",
                    "in1": "colorIfTrue" + suffix,
                    "in2": "colorIfFalse" + suffix,
                },
                "outColor" + suffix,
                {"operation": mode},
            )
            if datatype == "color3":
                profile["nodes"][operation + "_color3"]["source_default"] = True
    standard(
        "combine3",
        "aiFloatToRgba",
        "ND_combine3_color3",
        {"in1": "r", "in2": "g", "in3": "b"},
        "outColor",
    )
    standard(
        "mix_color3",
        "blendColors",
        "ND_mix_color3",
        {"fg": "color1", "bg": "color2", "mix": "blender"},
        "output",
    )
    standard(
        "max_color3FA",
        "aiMax",
        "ND_max_color3FA",
        {"in1": "input1", "in2": "input2"},
        "outColor",
        gray=("in2",),
    )
    standard(
        "extract_color3",
        "aiColorToFloat",
        "ND_extract_color3",
        {"in": "input", "index": "mode"},
        "outValue",
    )
    profile["nodes"]["extract_color3"]["inputs"]["index"]["native"]["properties"] = {
        "enum_values": [5, 6, 7]
    }
    profile["nodes"]["extract_color3"]["inputs"]["index"]["range"] = [0, 2]
    profile["nodes"]["extract_color3"]["source_default"] = True
    standard(
        "constant_color3",
        "aiAdd",
        "ND_constant_color3",
        {"value": "input1"},
        "outColor",
        {"input2": [0, 0, 0]},
    )
    standard(
        "constant_float",
        "aiAdd",
        "ND_constant_float",
        {"value": "input1R"},
        "outColorR",
        {"input2": [0, 0, 0]},
    )
    standard(
        "float_color3",
        "aiAdd",
        "ND_convert_float_color3",
        {"in": "input1"},
        "outColor",
        {"input2": [0, 0, 0]},
        gray=("in",),
    )
    from _scripts.materialx_extension_library import rgb_separation_library

    profile["materialx_extensions"] = rgb_separation_library()
    profile["materialx_lowerings"] = {
        "ND_separate3_color3": [{"when": {}, "replacement": "ND_iris_separate3_color3"}]
    }
    from _scripts.materialx_function_library import (
        add_color_correction_functions,
        add_native_color_correction_recipe,
    )

    add_color_correction_functions(profile)
    add_native_color_correction_recipe(profile)
    from _scripts.materialx_function_library import add_static_functions

    add_static_functions(profile)
    profile["version"] = "0.7.0"
    return profile


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    with args.out.open("x", encoding="utf-8") as stream:
        json.dump(
            build(json.loads(args.catalog.read_text("utf-8"))), stream, ensure_ascii=False, indent=2
        )
        stream.write("\n")
