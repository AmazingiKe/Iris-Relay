"""从实机接口生成 Blender 混合节点的标准计算子图；宿主不复制混合公式。"""

import json
from copy import deepcopy

import MaterialX as mx

MIX_MODES = (
    "mix",
    "add",
    "multiply",
    "subtract",
    "darken",
    "lighten",
    "screen",
    "overlay",
    "divide",
    "difference",
    "exclusion",
    "dodge",
    "burn",
    "soft_light",
    "linear_light",
)


def add_blender_mix(profile, catalog):
    """绑定已探测的 Mix／MixRGB 模式，保留因子钳制、结果钳制与完整原生配置。"""
    library = mx.createDocument()
    mx.loadLibraries(mx.getDefaultDataLibraryFolders(), mx.getDefaultDataSearchPath(), library)
    document = mx.createDocument()
    document.setDataLibrary(library)

    def generate(key, kind, mode, clamp_factor, clamp_result, native, inputs, output):
        """每个确定模式有唯一 NodeDef；RGB 运算用标准分量数学，避免目标私有公式。"""
        nd_name = "ND_iris_" + mx.createValidName(key)
        nd = document.addNodeDef(nd_name, kind, "iris_" + mx.createValidName(key))
        observed_ports = {p["id"]: p for p in catalog["nodes"][native["id"]]["inputs"]}
        defaults = {}
        for identifier, semantic in inputs:
            value = deepcopy(observed_ports[identifier]["default"])
            defaults[semantic] = value[:3] if isinstance(value, list) else value
        parameters = {}
        for name in ("factor", "a", "b"):
            port = nd.addInput(name, "float" if name == "factor" else kind)
            value = defaults[name]
            port.setValueString(
                ", ".join(map(str, value)) if isinstance(value, list) else str(value)
            )
            parameters[name] = port
        graph = document.addNodeGraph("NG_iris_" + mx.createValidName(key))
        graph.setNodeDefString(nd_name)

        def node(category, datatype="float", graph=graph, **values):
            standard = library.getNodeDef("ND_" + category + "_" + datatype)
            if standard is None:
                raise ValueError("标准定义不存在：" + category + "_" + datatype)
            result = graph.addNodeInstance(standard, graph.createValidChildName(category))
            for name, value in values.items():
                port = result.addInput(name, standard.getActiveInput(name).getType())
                if isinstance(value, mx.Node):
                    port.setConnectedNode(value)
                elif isinstance(value, mx.Input):
                    port.setInterfaceName(value.getName())
                else:
                    port.setValueString(str(value))
            return result

        factor = parameters["factor"]
        if clamp_factor:
            factor = node("clamp", **{"in": factor, "low": 0, "high": 1})
        a, b = parameters["a"], parameters["b"]
        direct = mode in {"dodge", "burn", "linear_light"}
        if mode != "mix":
            values = []
            for index in range(3):
                left = node("extract", kind, **{"in": a, "index": index})
                right = node("extract", kind, **{"in": b, "index": index})

                def sub(x, y):
                    return node("subtract", in1=x, in2=y)

                def mul(x, y):
                    return node("multiply", in1=x, in2=y)

                def add(x, y):
                    return node("add", in1=x, in2=y)

                def select(x, y, positive, other):
                    return node("ifgreater", value1=x, value2=y, in1=positive, in2=other)

                if mode in {"add", "multiply", "subtract"}:
                    value = node(mode, in1=left, in2=right)
                elif mode in {"darken", "lighten"}:
                    value = node("min" if mode == "darken" else "max", in1=left, in2=right)
                elif mode == "screen":
                    value = sub(1, mul(sub(1, left), sub(1, right)))
                elif mode == "overlay":
                    low = mul(2, mul(left, right))
                    high = sub(1, mul(2, mul(sub(1, left), sub(1, right))))
                    value = select(0.5, left, low, high)
                elif mode == "divide":
                    safe = node("ifequal", value1=right, value2=0, in1=1, in2=right)
                    divided = node("divide", in1=left, in2=safe)
                    value = node("ifequal", value1=right, value2=0, in1=left, in2=divided)
                elif mode == "difference":
                    difference = sub(left, right)
                    value = node("max", in1=difference, in2=sub(0, difference))
                elif mode == "exclusion":
                    value = sub(add(left, right), mul(2, mul(left, right)))
                elif mode == "soft_light":
                    screen = sub(1, mul(sub(1, right), sub(1, left)))
                    value = add(mul(mul(sub(1, left), right), left), mul(left, screen))
                elif mode == "linear_light":
                    value = add(left, mul(factor, sub(mul(2, right), 1)))
                else:
                    denominator = (
                        sub(1, mul(factor, right))
                        if mode == "dodge"
                        else add(sub(1, factor), mul(factor, right))
                    )
                    safe = select(denominator, 0, denominator, 1)
                    divided = node(
                        "divide", in1=left if mode == "dodge" else sub(1, left), in2=safe
                    )
                    if mode == "dodge":
                        bounded = node("min", in1=divided, in2=1)
                        value = select(denominator, 0, bounded, 1)
                        value = node("ifequal", value1=left, value2=0, in1=0, in2=value)
                    else:
                        bounded = node("clamp", **{"in": sub(1, divided), "low": 0, "high": 1})
                        value = select(denominator, 0, bounded, 0)
                values.append(value)
            b = node("combine3", kind, **{f"in{i + 1}": v for i, v in enumerate(values)})
        result = b if direct else node("mix", kind, fg=b, bg=a, mix=factor)
        if mode == "exclusion":
            result = node("max", "color3FA", in1=result, in2=0)
        if clamp_result:
            values = [
                node(
                    "clamp",
                    **{
                        "in": node("extract", kind, **{"in": result, "index": i}),
                        "low": 0,
                        "high": 1,
                    },
                )
                for i in range(3)
            ]
            result = node("combine3", kind, **{f"in{i + 1}": v for i, v in enumerate(values)})
        graph.addOutput("out", kind).setConnectedNode(result)
        rule = {
            "semantic": "materialx." + nd_name,
            "native": native,
            "inputs": {},
            "outputs": {
                output: {
                    "semantic": "out",
                    "type": "float" if kind == "float" else "color",
                    "native": {"id": output, "kind": "socket", "codec": "identity"},
                }
            },
        }
        for identifier, name in inputs:
            datatype = "float" if name == "factor" or kind == "float" else "color"
            rule["inputs"][identifier] = {
                "semantic": name,
                "type": datatype,
                "default": deepcopy(defaults[name]),
                "native": {
                    "id": identifier,
                    "kind": "socket",
                    "codec": "rgb" if datatype == "color" else "identity",
                },
            }
        profile["nodes"][key] = rule

    observed = catalog["nodes"]["ShaderNodeMix"]
    if observed["status"] != "instantiated":
        raise ValueError("Mix 缺少实机创建证据")
    ports = {p["id"] for p in observed["inputs"]}
    for datatype, kind, suffix in (("FLOAT", "float", "Float"), ("RGBA", "color3", "Color")):
        for cf in (False, True):
            for cr in (False, True) if datatype == "RGBA" else (False,):
                modes = MIX_MODES if datatype == "RGBA" else ("mix",)
                for mode in modes:
                    inputs = [
                        ("Factor_Float", "factor"),
                        ("A_" + suffix, "a"),
                        ("B_" + suffix, "b"),
                    ]
                    assert all(p in ports for p, _ in inputs)
                    generate(
                        f"ShaderNodeMix.{datatype.lower()}_{mode}_cf{int(cf)}_cr{int(cr)}",
                        kind,
                        mode,
                        cf,
                        cr,
                        {
                            "id": "ShaderNodeMix",
                            "properties": {
                                "data_type": datatype,
                                "factor_mode": "UNIFORM",
                                "blend_type": mode.upper(),
                                "clamp_factor": cf,
                                "clamp_result": cr,
                            },
                        },
                        inputs,
                        "Result_" + suffix,
                    )
    observed = catalog["nodes"]["ShaderNodeMixRGB"]
    if observed["status"] != "instantiated":
        raise ValueError("MixRGB 缺少实机创建证据")
    for mode in MIX_MODES:
        for clamp in (False, True):
            generate(
                f"ShaderNodeMixRGB.{mode}_c{int(clamp)}",
                "color3",
                mode,
                True,
                clamp,
                {
                    "id": "ShaderNodeMixRGB",
                    "properties": {
                        "blend_type": mode.upper(),
                        "use_alpha": False,
                        "use_clamp": clamp,
                    },
                },
                [("Fac", "factor"), ("Color1", "a"), ("Color2", "b")],
                "Color",
            )
    existing = mx.createDocument()
    if profile.get("materialx_extensions"):
        mx.readFromXmlString(existing, profile["materialx_extensions"])
    existing.importLibrary(document)
    profile["materialx_extensions"] = mx.writeToXmlString(existing)
    from iris_relay.materialx_recipes import DEFAULT_RAMP

    profile["materialx_recipes"] = {"ShaderNodeValToRGB": "blender.color_ramp"}
    profile["nodes"]["ShaderNodeValToRGB"] = {
        "semantic": "materialx.ND_iris_blender_color_ramp",
        "native": {"id": "ShaderNodeValToRGB", "properties": {}},
        "inputs": {
            "Fac": {
                "semantic": "factor",
                "type": "float",
                "default": 0.5,
                "native": {"id": "Fac", "kind": "socket", "codec": "identity"},
            },
            "color_ramp": {
                "semantic": "native.config",
                "type": "string",
                "default": json.dumps(DEFAULT_RAMP, sort_keys=True, separators=(",", ":")),
                "omit_if_default": True,
                "native": {"id": "color_ramp", "kind": "property", "codec": "color_ramp"},
            },
        },
        "outputs": {
            "Color": {
                "semantic": "color",
                "type": "color",
                "native": {"id": "Color", "kind": "socket", "codec": "identity"},
            },
            "Alpha": {
                "semantic": "alpha",
                "type": "float",
                "native": {"id": "Alpha", "kind": "socket", "codec": "identity"},
            },
        },
    }


def add_blender_normal_uv(profile):
    """命名 UV 作为实际可读写属性保留；是否能等价展开由核心和网格证据共同决定。"""
    normal = profile["nodes"]["ShaderNodeNormalMap"]
    normal["native"]["properties"].pop("uv_map")
    normal["inputs"]["uv_map"] = {
        "semantic": "native.uv_map",
        "type": "string",
        "default": "",
        "omit_if_default": True,
        "native": {"id": "uv_map", "kind": "property", "codec": "identity"},
    }
    profile["materialx_recipes"]["ShaderNodeNormalMap"] = "blender.normal_map"


def add_blender_rgb_outputs(profile, catalog):
    """Separate Color 保存全部 RGB 输出；旧红通道契约用已探测的 Separate XYZ.X 接入。"""
    old = profile["nodes"].pop("ShaderNodeSeparateColor")
    xyz = catalog["nodes"]["ShaderNodeSeparateXYZ"]
    assert xyz["status"] == "instantiated"
    projection = deepcopy(old)
    projection["native"] = {"id": "ShaderNodeSeparateXYZ", "properties": {}}
    port = next(iter(projection["inputs"].values()))
    port["native"].update(id="Vector", codec="identity")
    projection["inputs"] = {"Vector": port}
    output = next(iter(projection["outputs"].values()))
    output["native"]["id"] = "X"
    projection["outputs"] = {"X": output}
    profile["nodes"]["ShaderNodeSeparateXYZ.channel_red"] = projection
    original = deepcopy(old)
    original["semantic"] = "materialx.ND_separate3_color3"
    original["inputs"]["Color"]["semantic"] = "in"
    original["outputs"] = {
        name: {
            "semantic": out,
            "type": "float",
            "native": {"id": name, "kind": "socket", "codec": "identity"},
        }
        for name, out in zip(("Red", "Green", "Blue"), ("outr", "outg", "outb"), strict=True)
    }
    profile["nodes"]["ShaderNodeSeparateColor"] = original


def add_blender_color_adjustments(profile, catalog):
    """把 Cycles Gamma／亮度对比度编译为官方数学图，保留零指数和负颜色分支。"""
    library = mx.createDocument()
    mx.loadLibraries(mx.getDefaultDataLibraryFolders(), mx.getDefaultDataSearchPath(), library)
    document = mx.createDocument()
    document.setDataLibrary(library)
    specifications = (
        ("ShaderNodeGamma", "gamma", [("Color", "color"), ("Gamma", "gamma")]),
        (
            "ShaderNodeBrightContrast",
            "brightcontrast",
            [("Color", "color"), ("Bright", "bright"), ("Contrast", "contrast")],
        ),
    )
    for native, operation, ports in specifications:
        observed = catalog["nodes"][native]
        assert observed["status"] == "instantiated"
        observed_inputs = {p["id"]: p for p in observed["inputs"]}
        name = "iris_blender_" + operation + "_color3"
        nd = document.addNodeDef("ND_" + name, "color3", name)
        graph = document.addNodeGraph("NG_" + name)
        graph.setNodeDefString(nd.getName())
        parameters, inputs = {}, {}
        for identifier, semantic in ports:
            datatype = "color3" if semantic == "color" else "float"
            default = deepcopy(observed_inputs[identifier]["default"])
            if datatype == "color3":
                default = default[:3]
            port = nd.addInput(semantic, datatype)
            port.setValueString(
                ", ".join(map(str, default)) if isinstance(default, list) else str(default)
            )
            parameters[semantic] = port
            inputs[identifier] = {
                "semantic": semantic,
                "type": "color" if datatype == "color3" else "float",
                "default": default,
                "native": {
                    "id": identifier,
                    "kind": "socket",
                    "codec": "rgb" if datatype == "color3" else "identity",
                },
            }

        def node(category, datatype="float", graph=graph, **values):
            """类型和端口来自 SDK；无公式下放到宿主。"""
            definition = library.getNodeDef("ND_" + category + "_" + datatype)
            result = graph.addNodeInstance(definition, graph.createValidChildName(category))
            for key, value in values.items():
                port = result.addInput(key, definition.getActiveInput(key).getType())
                if isinstance(value, mx.Node):
                    port.setConnectedNode(value)
                elif isinstance(value, mx.Input):
                    port.setInterfaceName(value.getName())
                else:
                    port.setValueString(str(value))
            return result

        values = []
        for index in range(3):
            channel = node("extract", "color3", **{"in": parameters["color"], "index": index})
            if operation == "gamma":
                # 两个条件分支可能同时求值，先保护 pow 的底数，再选择 Cycles 的输出分支。
                positive = node("ifgreater", value1=channel, value2=0, in1=channel, in2=1)
                power = node("power", in1=positive, in2=parameters["gamma"])
                selected = node("ifgreater", value1=channel, value2=0, in1=power, in2=channel)
                result = node("ifequal", value1=parameters["gamma"], value2=0, in1=1, in2=selected)
            else:
                slope = node("add", in1=1, in2=parameters["contrast"])
                half = node("multiply", in1=parameters["contrast"], in2=0.5)
                offset = node("subtract", in1=parameters["bright"], in2=half)
                scaled = node("multiply", in1=channel, in2=slope)
                result = node("max", in1=node("add", in1=scaled, in2=offset), in2=0)
            values.append(result)
        combined = node("combine3", "color3", **{f"in{i + 1}": v for i, v in enumerate(values)})
        graph.addOutput("out", "color3").setConnectedNode(combined)
        profile["nodes"][native] = {
            "semantic": "materialx." + nd.getName(),
            "native": {"id": native, "properties": {}},
            "inputs": inputs,
            "outputs": {
                "Color": {
                    "semantic": "out",
                    "type": "color",
                    "native": {"id": "Color", "kind": "socket", "codec": "identity"},
                }
            },
        }
    existing = mx.createDocument()
    mx.readFromXmlString(existing, profile["materialx_extensions"])
    existing.importLibrary(document)
    profile["materialx_extensions"] = mx.writeToXmlString(existing)
