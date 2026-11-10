"""将实机节点探测结果整理为可审阅目录及受限的真实转换字典。"""

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _scripts.materialx_extension_library import blender_hsv_library  # noqa: E402

RS = "com.redshift3d.redshift4c4d.nodes.core."

COMMON = {
    "cycles": {
        "Base Color": "base.color",
        "Metallic": "metalness",
        "Roughness": "reflection.roughness",
        "IOR": "reflection.ior",
        "Diffuse Roughness": "diffuse.roughness",
        "Specular IOR Level": "reflection.weight",
        "Specular Tint": "reflection.tint",
        "Anisotropic": "reflection.anisotropy",
        "Anisotropic Rotation": "reflection.rotation",
        "Coat Weight": "coat.weight",
        "Coat Roughness": "coat.roughness",
        "Coat IOR": "coat.ior",
        "Coat Tint": "coat.tint",
        "Coat Normal": "coat.normal",
        "Normal": "normal",
        "Emission Color": "emission.color",
        "Emission Strength": "emission.weight",
        "Sheen Weight": "sheen.weight",
        "Sheen Roughness": "sheen.roughness",
        "Sheen Tint": "sheen.tint",
        "Alpha": "opacity",
    },
    "redshift": {
        "base_color": "base.color",
        "metalness": "metalness",
        "refl_roughness": "reflection.roughness",
        "refl_ior": "reflection.ior",
        "diffuse_roughness": "diffuse.roughness",
        "refl_weight": "reflection.weight",
        "refl_color": "reflection.tint",
        "refl_aniso": "reflection.anisotropy",
        "refl_aniso_rotation": "reflection.rotation",
        "coat_weight": "coat.weight",
        "coat_roughness": "coat.roughness",
        "coat_ior": "coat.ior",
        "coat_color": "coat.tint",
        "coat_bump_input": "coat.normal",
        "bump_input": "normal",
        "emission_color": "emission.color",
        "emission_weight": "emission.weight",
        "sheen_weight": "sheen.weight",
        "sheen_roughness": "sheen.roughness",
        "sheen_color": "sheen.tint",
        "opacity_color": "opacity",
    },
    "arnold": {
        "base_color": "base.color",
        "metalness": "metalness",
        "specular_roughness": "reflection.roughness",
        "specular_ior": "reflection.ior",
        "diffuse_roughness": "diffuse.roughness",
        "specular": "reflection.weight",
        "specular_color": "reflection.tint",
        "specular_anisotropy": "reflection.anisotropy",
        "specular_rotation": "reflection.rotation",
        "coat": "coat.weight",
        "coat_roughness": "coat.roughness",
        "coat_IOR": "coat.ior",
        "coat_ior": "coat.ior",
        "coat_color": "coat.tint",
        "coat_normal": "coat.normal",
        "normal": "normal",
        "emission_color": "emission.color",
        "emission": "emission.weight",
        "sheen": "sheen.weight",
        "sheen_roughness": "sheen.roughness",
        "sheen_color": "sheen.tint",
        "opacity": "opacity",
    },
}


def linear_channel(value):
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def infer_rule(port, engine, semantic=None):
    value = deepcopy(port.get("default"))
    if isinstance(value, dict) or port.get("compound") or port["id"] == "name":
        return None
    identifier = port["id"]
    native_id = port.get("code", identifier)
    codec = "identity"
    kind = "socket"
    if port.get("type") == "RGBA":
        value = value[:3]
        codec = "rgb"
        datatype = "color"
    elif port.get("type") == "SHADER":
        datatype = "shader"
    elif isinstance(value, bool):
        datatype = "bool"
    elif isinstance(value, (int, float)):
        datatype = "float"
    elif isinstance(value, str):
        datatype = "string"
    elif isinstance(value, list) and len(value) in (3, 4):
        datatype = (
            "color4"
            if len(value) == 4
            else "vector"
            if port.get("type") == "VECTOR" or port.get("datatype") == 23
            else "color"
        )
        if engine == "arnold" and datatype == "color":
            value = [linear_channel(channel) for channel in value]
            codec = "srgb"
    elif value is None:
        datatype = "vector"
    else:
        return None
    if semantic in {"normal", "coat.normal"}:
        datatype, value, codec = "vector", None, "normal"
    rule = {
        "semantic": semantic or f"native.{engine}.{identifier}",
        "type": datatype,
        "default": value,
        "native": {"id": native_id, "kind": kind, "codec": codec},
        "omit_if_default": semantic is None,
    }
    if value is None and datatype not in {"shader", "texture"}:
        rule["nullable"] = True
    if semantic and datatype in {"float", "color", "vector"} and value is not None:
        rule["adjustable"] = True
        if datatype == "float" and (
            semantic.endswith(("roughness", "weight", "anisotropy")) or semantic == "metalness"
        ):
            rule["range"] = [0, 1] if not semantic.startswith("emission") else [0, 100000]
        if semantic.endswith("ior"):
            rule["range"] = [1, 10]
    if engine == "cycles" and identifier == "Specular IOR Level":
        rule["transform"] = {"op": "scale", "factor": 2}
        rule["range"] = [0, 1]
    if engine == "cycles" and identifier == "Alpha":
        rule["transform"] = "scalar_to_color"
        rule["range"] = [0, 1]
    return rule


def build(catalog):
    if catalog["host"]["id"] == "maya":
        from _scripts.build_maya_dictionary import build as build_maya

        return build_maya(catalog)
    engine = catalog["renderer"]["id"]
    host = catalog["host"]
    profile = {
        "schema_version": "1",
        "id": f"{host['id']}.{engine}.native",
        "version": "0.3.0",
        "semantic_schema_version": "1",
        "description": (
            "真实原生绑定；标准表面、图片、切线法线、高度凹凸与显式 R 通道提取。"
            "其余节点见探测目录。"
        ),
        "renderer": {"id": engine, "versions": [catalog["renderer"]["version"]]},
        "host": {
            "id": host["id"],
            "versions": [host["version"]],
            "node_system": catalog["node_system"],
        },
        "evidence": {"kind": catalog["evidence"], "appearance_verified": False},
        "nodes": {},
    }
    if "color_context" in catalog:
        profile["evidence"]["color_context"] = catalog["color_context"]
    if engine == "cycles":
        profile["version"] = "0.3.6"
        profile["texture_alpha_modes"] = ["STRAIGHT", "PREMUL", "CHANNEL_PACKED", "NONE"]
        profile["texture_coordinates"] = {"named_uv": True, "transforms": "trs"}
        profile["materialx_export"] = {"color_to_float": "scene_linear_luminance"}
        profile["evidence"]["implicit_color_to_float"] = (
            "https://github.com/blender/blender/blob/v5.2.3/intern/cycles/kernel/svm/convert.h"
        )
    shader_key = {
        "cycles": "ShaderNodeBsdfPrincipled",
        "redshift": RS + "standardmaterial",
        "arnold": "standard_surface",
    }[engine]
    image_key = {
        "cycles": "ShaderNodeTexImage",
        "redshift": RS + "texturesampler",
        "arnold": "image",
    }[engine]
    normal_key = {
        "cycles": "ShaderNodeNormalMap",
        "redshift": RS + "bumpmap",
        "arnold": "normal_map",
    }[engine]
    bump_key = {"cycles": "ShaderNodeBump", "redshift": RS + "bumpmap", "arnold": "bump2d"}[engine]
    channel_key = {
        "cycles": "ShaderNodeSeparateColor",
        "redshift": RS + "rscolorsplitter",
        "arnold": "rgb_to_float",
    }[engine]
    for key, semantic in [
        (shader_key, "surface.standard"),
        (image_key, "texture.image"),
        (normal_key, "normal.tangent"),
        (bump_key, "bump.height"),
        (channel_key, "channel.red"),
    ]:
        observed = catalog["nodes"][key]
        if observed["status"] != "instantiated":
            raise ValueError(f"节点未经实机创建：{key}")
        native = {
            "id": observed.get("code", key),
            "label": observed.get("label", key),
            "properties": {},
        }
        rule = {"semantic": semantic, "native": native, "inputs": {}, "outputs": {}}
        if semantic == "surface.standard":
            rule["approximation"] = (
                "不同渲染器的 BSDF、能量分配及各层响应不保证一致；数值映射需渲染对照。"
            )
            if engine == "cycles":
                native["properties"] = {
                    "distribution": "MULTI_GGX",
                    "subsurface_method": "RANDOM_WALK",
                }
        if semantic == "normal.tangent":
            rule["approximation"] = (
                "切线空间法线需要相同 UV、切线约定及几何体；首版只支持默认方向约定。"
            )
            if engine == "redshift":
                native["properties"][RS + "bumpmap.inputtype"] = 1
            elif engine == "cycles":
                native["properties"] = {
                    "space": "TANGENT",
                    "uv_map": "",
                    "convention": "OPENGL",
                    "base": "DISPLACED",
                }
        if semantic == "bump.height":
            rule["approximation"] = (
                "高度幅度、单位、滤波与空间导数依赖宿主及几何体；当前只验证参数和连接，不保证外观等价。"
            )
            if engine == "redshift":
                native["properties"][RS + "bumpmap.inputtype"] = 0
            elif engine == "cycles":
                native["properties"]["invert"] = False
        if semantic == "channel.red":
            if engine == "cycles":
                native["properties"]["mode"] = "RGB"
            elif engine == "arnold":
                mode = next(p for p in observed["inputs"] if p["id"] == "mode")
                native["properties"][str(mode["code"])] = int(
                    next(k for k, v in mode["items"].items() if v == "r")
                )
        if engine == "cycles" and semantic == "texture.image":
            native["properties"] = {
                "interpolation": "Linear",
                "projection": "FLAT",
                "projection_blend": 0.0,
                "extension": "REPEAT",
            }
        for port in observed["inputs"]:
            identifier = port["id"]
            short = identifier.rsplit(".", 1)[-1]
            if (
                identifier in native["properties"]
                or str(port.get("code", identifier)) in native["properties"]
                or short in {"inputtype"}
                and semantic == "normal.tangent"
            ):
                continue
            if semantic == "surface.standard":
                mapped = COMMON[engine].get(short if engine != "cycles" else identifier)
            elif semantic == "normal.tangent":
                mapped = {
                    "Strength": "normal.strength",
                    "Color": "normal.color",
                    "scale": "normal.strength",
                    "strength": "normal.strength",
                    "input": "normal.color",
                }.get(short)
            elif semantic == "bump.height":
                mapped = {
                    "Height": "height",
                    "input": "height",
                    "bump_map": "height",
                    "Distance": "height.scale",
                    "scale": "height.scale",
                    "bump_height": "height.scale",
                    "Normal": "normal",
                    "normal": "normal",
                }.get(short)
            elif semantic == "channel.red":
                mapped = "color" if short in {"Color", "input"} else None
            else:
                mapped = None
            input_rule = infer_rule(port, engine, mapped)
            if input_rule is None:
                continue
            if semantic == "normal.tangent" and mapped == "normal.color":
                input_rule.update(type="color", default=[0.5, 0.5, 1.0])
                input_rule.pop("nullable", None)
            if semantic == "bump.height" and mapped == "height":
                input_rule.update(type="float", default=0.0)
                input_rule.pop("nullable", None)
                if engine == "redshift":
                    input_rule["native"]["codec"] = "gray"
            if semantic == "bump.height" and mapped == "normal":
                input_rule["omit_if_default"] = True
            if semantic == "channel.red" and mapped == "color":
                input_rule["type"] = "color"
                if engine == "redshift":
                    input_rule["default"] = input_rule["default"][:3]
                    input_rule["native"]["codec"] = "rgb"
            rule["inputs"][identifier] = input_rule
        if semantic == "texture.image":
            if engine == "cycles":
                identifier = "@image"
                binding = {"id": "image", "kind": "resource", "codec": "texture"}
            elif engine == "redshift":
                identifier = RS + "texturesampler.tex0/path"
                binding = {
                    "id": identifier,
                    "kind": "resource",
                    "codec": "texture",
                    "colorspace_id": RS + "texturesampler.tex0/colorspace",
                    "uv_id": RS + "texturesampler.tspace_id",
                }
                rule["inputs"].pop(binding["colorspace_id"], None)
                rule["inputs"].pop(binding["uv_id"], None)
            else:
                identifier = "filename"
                observed_inputs = {item["id"]: item for item in observed["inputs"]}
                binding = {
                    "id": observed_inputs["filename"]["code"],
                    "kind": "resource",
                    "codec": "texture",
                    "colorspace_id": observed_inputs["color_space"]["code"],
                    "uv_id": observed_inputs["uvset"]["code"],
                }
                rule["inputs"].pop("color_space", None)
                rule["inputs"].pop("uvset", None)
            rule["inputs"][identifier] = {
                "semantic": "image",
                "type": "texture",
                "default": None,
                "native": binding,
            }
        for index, port in enumerate(observed["outputs"]):
            if semantic == "channel.red":
                red_output = {
                    "cycles": "Red",
                    "redshift": RS + "rscolorsplitter.outr",
                    "arnold": "0",
                }[engine]
                if port["id"] != red_output:
                    continue
                port_semantic, datatype = "value", "float"
            elif semantic == "surface.standard":
                port_semantic, datatype = "surface", "shader"
            elif semantic in {"normal.tangent", "bump.height"}:
                port_semantic, datatype = "normal", "vector"
            else:
                port_semantic, datatype = ("color", "color") if index == 0 else ("alpha", "float")
            rule["outputs"][port["id"]] = {
                "semantic": port_semantic,
                "type": datatype,
                "native": {
                    "id": port.get("code", port["id"]),
                    "kind": "socket",
                    "codec": "identity",
                },
            }
        implementation = (
            key + ".height" if engine == "redshift" and semantic == "bump.height" else key
        )
        profile["nodes"][implementation] = rule
    if engine == "cycles":
        # 5.2.3 目录列出的枚举，并由木地板实文件读取核验；不扩大 SSS 输入范围。
        base = profile["nodes"][shader_key]
        for name in (
            "Subsurface Radius",
            "Subsurface Scale",
            "Subsurface IOR",
            "Subsurface Anisotropy",
        ):
            base["inputs"][name]["inactive_when"] = {"input": "Subsurface Weight", "equals": 0}
        base["target_default"] = True
        legacy = deepcopy(base)
        legacy.pop("target_default")
        legacy["native"]["properties"] = {"distribution": "GGX", "subsurface_method": "BURLEY"}
        legacy["approximation"] = (
            "旧 Principled GGX / BURLEY 配置近似映射到标准表面；不支持非默认 SSS，外观尚未验证。"
        )
        profile["nodes"][shader_key + ".ggx_burley"] = legacy
        add_blender_principled_variants(profile, catalog, shader_key)
        add_blender_weighted_sum(profile, catalog)
        add_blender_standard_math(profile, catalog)
        add_blender_invert(profile, catalog)
        add_blender_hsv(profile, catalog)
    else:
        profile["version"] = "0.3.1"
        add_c4d_weighted_sum(profile, catalog)
        if engine == "redshift":
            profile["version"] = "0.3.2"
            add_redshift_standard_math(profile, catalog)
    return profile


def add_blender_principled_variants(profile, catalog, shader_key):
    """补入 A1 实文件读取到的历史 Principled 模式，沿用活动 SSS 阻断规则。"""
    # 1. 只增加实际样本中出现的组合，并核对该版本实机目录中的枚举。
    observed = catalog["nodes"][shader_key]["properties"]
    variants = (
        ("MULTI_GGX", "RANDOM_WALK_LEGACY"),
        ("GGX", "RANDOM_WALK_SKIN"),
        ("MULTI_GGX", "BURLEY"),
        ("MULTI_GGX", "RANDOM_WALK_SKIN"),
        ("GGX", "RANDOM_WALK_LEGACY"),
    )
    for distribution, method in variants:
        properties = {"distribution": distribution, "subsurface_method": method}
        if any(value not in observed[key]["items"] for key, value in properties.items()):
            raise ValueError("Principled 历史模式缺少当前版本宿主证据")
        # 2. 来源模式可识别；目标默认保持唯一，未实现的 SSS 不因扩展模式而放行。
        variant = deepcopy(profile["nodes"][shader_key])
        variant.pop("target_default")
        variant["native"]["properties"] = properties
        variant["approximation"] = (
            f"Principled {distribution} / {method} 近似映射到标准表面；"
            "活动 SSS 尚不支持，渲染外观未验证。"
        )
        profile["nodes"][f"{shader_key}.{distribution.lower()}_{method.lower()}"] = variant
    profile["evidence"]["principled_variants"] = (
        "Blender 5.2.3 对 A1.blend 全部 1803 个材质的原生读取；"
        "新增五种 distribution / subsurface_method 组合，样本中 SSS 权重均为零。"
    )


def add_blender_weighted_sum(profile, catalog):
    """用已探测的 Vector Math 端口声明 RGB 加权求和；仅开放 DOT_PRODUCT 模式。"""
    # 1. 验证类型和运算枚举来自宿主目录，避免将未核验名称写入运行时字典。
    observed = catalog["nodes"]["ShaderNodeVectorMath"]
    if (
        observed["status"] != "instantiated"
        or "DOT_PRODUCT" not in observed["properties"]["operation"]["items"]
    ):
        raise ValueError("Vector Math DOT_PRODUCT 缺少宿主证据")
    ports = {p["id"]: p for p in observed["inputs"]}
    rule = {
        "semantic": "color.weighted_sum",
        "native": {
            "id": observed["id"],
            "label": observed["label"],
            "properties": {"operation": "DOT_PRODUCT"},
        },
        "inputs": {},
        "outputs": {
            "Value": {
                "semantic": "value",
                "type": "float",
                "native": {"id": "Value", "kind": "socket", "codec": "identity"},
            }
        },
    }
    # 2. RGB 和向量按三个分量原样传递；系数是文档数据，不设为校正器可调整项。
    for identifier, semantic, kind in (
        ("Vector", "color", "color"),
        ("Vector_001", "weights", "vector"),
    ):
        port = infer_rule(ports[identifier], "cycles", semantic)
        port["type"] = kind
        port.pop("adjustable", None)
        rule["inputs"][identifier] = port
    profile["nodes"]["ShaderNodeVectorMath.dot_product"] = rule
    profile["evidence"]["weighted_sum"] = (
        "Blender 5.2.3 DOT_PRODUCT 实机端口核验；"
        "https://github.com/blender/blender/blob/v5.2.3/"
        "source/blender/nodes/shader/nodes/node_shader_vector_math.cc"
    )


def add_blender_standard_math(profile, catalog):
    """把有直接对应的三个未钳制数学模式绑定到官方 NodeDef，其他模式仍显式阻断。"""
    observed = catalog["nodes"]["ShaderNodeMath"]
    ports = {p["id"]: p for p in observed["inputs"]}
    output = next(p for p in observed["outputs"] if p["id"] == "Value")
    for operation in ("ADD", "SUBTRACT", "MULTIPLY"):
        if (
            observed["status"] != "instantiated"
            or operation not in observed["properties"]["operation"]["items"]
        ):
            raise ValueError(f"数学模式缺少实机证据：{operation}")
        rule = {
            "semantic": f"materialx.ND_{operation.lower()}_float",
            "native": {
                "id": observed["id"],
                "label": observed["label"],
                "properties": {"operation": operation, "use_clamp": False},
            },
            "inputs": {},
            "outputs": {
                "Value": {
                    "semantic": "out",
                    "type": "float",
                    "native": {"id": output["id"], "kind": "socket", "codec": "identity"},
                }
            },
        }
        for identifier, semantic in (("Value", "in1"), ("Value_001", "in2")):
            rule["inputs"][identifier] = infer_rule(ports[identifier], "cycles", semantic)
            rule["inputs"][identifier].pop("adjustable", None)
        profile["nodes"][f"ShaderNodeMath.{operation.lower()}"] = rule
    profile["evidence"]["standard_math"] = (
        "本版本 ShaderNodeMath 实机枚举与端口；ADD、SUBTRACT、MULTIPLY 且 use_clamp=false。"
        "对应官方 float 数学 NodeDef，未声明其他模式或外观验证。"
    )


def add_blender_invert(profile, catalog):
    """将 Fac=1 的颜色反相绑定官方 invert；部分系数和系数连接仍显式阻断。"""
    observed = catalog["nodes"]["ShaderNodeInvert"]
    ports = {p["id"]: p for p in observed["inputs"]}
    output = next(p for p in observed["outputs"] if p["id"] == "Color")
    if observed["status"] != "instantiated" or ports["Fac"]["default"] != 1.0:
        raise ValueError("颜色反相缺少本版本实机默认值证据")
    color = infer_rule(ports["Color"], "cycles", "in")
    color.pop("adjustable", None)
    profile["nodes"]["ShaderNodeInvert"] = {
        "semantic": "materialx.ND_invert_color3",
        "native": {"id": observed["id"], "label": observed["label"], "properties": {}},
        "inputs": {"Color": color, "Fac": infer_rule(ports["Fac"], "cycles")},
        "outputs": {
            "Color": {
                "semantic": "out",
                "type": "color",
                "native": {"id": output["id"], "kind": "socket", "codec": "rgb"},
            }
        },
    }
    profile["version"] = "0.3.7"
    profile["evidence"]["full_invert"] = (
        "Blender 5.2.3 ShaderNodeInvert 实机端口；仅 Fac=1 且未连接，RGB 为 1-color。"
        "对应 MaterialX ND_invert_color3 的 amount 默认值；不把 amount 误映射成 Fac。"
        "https://github.com/blender/blender/blob/v5.2.3/source/blender/gpu/shaders/material/gpu_shader_material_invert.glsl"
    )


def add_blender_hsv(profile, catalog):
    """将 HSV 原生端口绑定内联标准子图，保持各参数及连接，不误用 hsvadjust。"""
    observed = catalog["nodes"]["ShaderNodeHueSaturation"]
    if observed["status"] != "instantiated":
        raise ValueError("HSV 缺少本版本原生端口证据")
    semantics = {
        "Hue": "hue",
        "Saturation": "saturation",
        "Value": "value",
        "Fac": "factor",
        "Color": "color",
    }
    inputs = {p["id"]: infer_rule(p, "cycles", semantics[p["id"]]) for p in observed["inputs"]}
    for rule in inputs.values():
        rule.pop("adjustable", None)
    profile["nodes"]["ShaderNodeHueSaturation"] = {
        "semantic": "materialx.ND_iris_blender_hsv_color3",
        "native": {"id": observed["id"], "label": observed["label"], "properties": {}},
        "inputs": inputs,
        "outputs": {
            "Color": {
                "semantic": "out",
                "type": "color",
                "native": {"id": "Color", "kind": "socket", "codec": "rgb"},
            }
        },
    }
    profile["materialx_extensions"] = blender_hsv_library(inputs)
    profile["version"] = "0.3.8"
    profile["evidence"]["hsv"] = (
        "Cycles 5.2.3 HSV：色相环绕、饱和度钳制、Value 乘法、Fac 混合及非负输出。"
        "内联标准数学子图，回导须核对实际定义与实现；不采用存在边界差异的 hsvadjust。"
        "https://github.com/blender/blender/blob/v5.2.3/intern/cycles/kernel/svm/hsv.h "
        "https://github.com/blender/blender/blob/v5.2.3/intern/cycles/util/color.h"
    )


def add_redshift_standard_math(profile, catalog):
    """绑定实机标量加减乘节点；保留运算模式，不将向量或颜色数学节点当成标量。"""
    for operation, suffix in (
        ("add", "rsmathadd"),
        ("subtract", "rsmathsub"),
        ("multiply", "rsmathmul"),
    ):
        key = RS + suffix
        observed = catalog["nodes"][key]
        if observed["status"] != "instantiated":
            raise ValueError(f"标量数学节点缺少宿主证据：{key}")
        ports = {p["id"].rsplit(".", 1)[-1]: p for p in observed["inputs"]}
        output = next(p for p in observed["outputs"] if p["id"] == key + ".out")
        mode = ports["math_op"]
        rule = {
            "semantic": f"materialx.ND_{operation}_float",
            "native": {
                "id": key,
                "label": observed["label"],
                "properties": {mode["id"]: mode["default"]},
            },
            "inputs": {},
            "outputs": {
                output["id"]: {
                    "semantic": "out",
                    "type": "float",
                    "native": {"id": output["id"], "kind": "socket", "codec": "identity"},
                }
            },
        }
        for name, semantic in (("input1", "in1"), ("input2", "in2")):
            port = infer_rule(ports[name], "redshift", semantic)
            if port["type"] != "float":
                raise ValueError(f"数学端口不是标量：{key}.{name}")
            port.pop("adjustable", None)
            rule["inputs"][ports[name]["id"]] = port
        profile["nodes"][key] = rule


def add_c4d_weighted_sum(profile, catalog):
    """将实机点积端口绑定到已有 RGB 加权求和契约，系数按向量数据原样保留。"""
    engine = catalog["renderer"]["id"]
    key = RS + "rsmathdotvector" if engine == "redshift" else "dot"
    observed = catalog["nodes"][key]
    if observed["status"] != "instantiated":
        raise ValueError(f"点积节点缺少宿主创建证据：{key}")
    ports = {p["id"].rsplit(".", 1)[-1]: p for p in observed["inputs"]}
    outputs = {p["id"].rsplit(".", 1)[-1]: p for p in observed["outputs"]}
    output = outputs["out" if engine == "redshift" else "0"]
    rule = {
        "semantic": "color.weighted_sum",
        "native": {
            "id": observed.get("code", key),
            "label": observed["label"],
            "properties": {},
        },
        "inputs": {},
        "outputs": {
            output["id"]: {
                "semantic": "value",
                "type": "float",
                "native": {
                    "id": output.get("code", output["id"]),
                    "kind": "socket",
                    "codec": "identity",
                },
            }
        },
    }
    if engine == "redshift":
        operation = ports["math_op"]
        rule["native"]["properties"][operation["id"]] = operation["default"]
    for name, semantic, datatype in (("input1", "color", "color"), ("input2", "weights", "vector")):
        observed_port = ports[name]
        port = infer_rule(observed_port, engine, semantic)
        # 端口实机类型为 vector；RGB 仅作分量解释，不能套用颜色 UI 的 sRGB 编码。
        if port["native"]["codec"] != "identity":
            raise ValueError(f"点积端口不是原始向量：{key}.{name}")
        port["type"] = datatype
        port.pop("adjustable", None)
        rule["inputs"][observed_port["id"]] = port
    profile["nodes"][key] = rule
    profile["evidence"]["weighted_sum"] = (
        "本版本 C4D 实机点积节点及输入输出端口核验；"
        "RGB 与权重按原始向量分量写入，输出为未归一化点积。"
    )


def main():
    parser = argparse.ArgumentParser(description="从实机目录生成真实材质绑定字典")
    parser.add_argument("--blender", type=Path, required=True)
    parser.add_argument("--cinema4d", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    catalogs = [
        json.loads(args.blender.read_text(encoding="utf-8")),
        *json.loads(args.cinema4d.read_text(encoding="utf-8")).values(),
    ]
    args.out.mkdir(exist_ok=True)
    catalog_dir = args.out / "catalogs"
    catalog_dir.mkdir(exist_ok=True)
    for catalog in catalogs:
        for node in catalog["nodes"].values():
            for port in node.get("inputs", []) + node.get("outputs", []):
                if port.get("label") in (None, "None", ""):
                    port["label"] = port["id"].rsplit(".", 1)[-1]
        profile = build(catalog)
        filename = (
            f"{catalog['host']['id']}-{catalog['renderer']['id']}-"
            f"{catalog['renderer']['version']}.json"
        )
        for path, data in [(args.out / filename, profile), (catalog_dir / filename, catalog)]:
            with path.open("x", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)


if __name__ == "__main__":
    main()
