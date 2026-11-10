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
        "version": "0.1.0",
        "description": "Maya 2022 可编辑 Arnold 材质导入；结构核验，不代表外观等价。",
        "renderer": {"id": "arnold", "versions": [catalog["renderer"]["version"]]},
        "host": {"id": "maya", "versions": [catalog["host"]["version"]], "node_system": "mtoa"},
        "evidence": {
            "kind": catalog["evidence"],
            "plugin_version": catalog["renderer"]["plugin_version"],
            "appearance_verified": False,
        },
        "texture_coordinates": {"named_uv": True, "transforms": "none"},
        "texture_alpha_modes": ["STRAIGHT", "PREMUL", "CHANNEL_PACKED", "NONE"],
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
        rule = {
            "semantic": semantic,
            "native": {"id": native, "properties": properties or {}},
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
        if datatype == "vector":
            inputs[native]["nullable"] = True
    node(
        "standard_surface",
        "aiStandardSurface",
        "surface.standard",
        inputs,
        port("surface", "shader", "outColor"),
    )
    image = port("image", "texture", "filename")
    image["native"].update(kind="resource", colorspace_id="colorSpace", uv_id="uvset")
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
        "floor_float", "aiFloatToInt", "ND_floor_float", {"in": "input"}, "outValue", {"mode": 2}
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
    standard(
        "constant_color3",
        "aiAdd",
        "ND_constant_color3",
        {"value": "input1"},
        "outColor",
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
