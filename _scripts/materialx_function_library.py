"""生成通用功能接口及各目标声明；数学实现仍只保存在目标字典中。"""

import MaterialX as mx

COLOR_CORRECT = "ND_iris_colorcorrect_color3"
LEGACY_HSV = "ND_iris_blender_hsv_color3"


def color_correction_interface(document):
    """继承官方颜色校正接口，增加有明确含义的跨宿主细节，不包含实现图。"""
    definition = document.addNodeDef(COLOR_CORRECT, "color3", "iris_colorcorrect")
    definition.setInheritString("ND_colorcorrect_color3")
    definition.setAttribute("iris_function_version", "1")
    definition.setAttribute("doc", "颜色校正功能；附加参数须由目标字典解释")
    for name, value, description in (
        ("mix", 1.0, "原始颜色与校正颜色的混合权重，允许外插"),
        ("hue_center", 0.0, "色相控制的中性位置，有效偏移为 hue 减去此值"),
        ("saturation_max", 1.0, "HSV 调整后的饱和度上限"),
        ("output_min", 0.0, "混合完成后 RGB 各通道的下限"),
    ):
        port = definition.addInput(name, "float")
        port.setValue(value)
        port.setAttribute("doc", description)
    return definition


def add_color_correction_functions(profile, *, native=False):
    """为目标登记明确的功能实现；共享配方可以复用，是否启用由各目标字典决定。"""
    from _scripts.materialx_extension_library import blender_hsv_library

    document = mx.createDocument()
    if profile.get("materialx_extensions"):
        mx.readFromXmlString(document, profile["materialx_extensions"])
    if document.getNodeDef(COLOR_CORRECT) is None:
        color_correction_interface(document)
    if document.getNodeDef(LEGACY_HSV) is None:
        inputs = {
            name: {
                "semantic": name,
                "default": value,
                "type": "color" if name == "color" else "float",
            }
            for name, value in {
                "hue": 0.5,
                "saturation": 1.0,
                "value": 1.0,
                "factor": 1.0,
                "color": [0.800000011920929] * 3,
            }.items()
        }
        implementation = mx.createDocument()
        mx.readFromXmlString(implementation, blender_hsv_library(inputs))
        document.importLibrary(implementation)
    inputs = {
        "hue": "hue",
        "saturation": "saturation",
        "value": "gain",
        "factor": "mix",
        "color": "in",
    }
    if native:
        profile["materialx_function_exports"] = {
            LEGACY_HSV: {
                "definition": COLOR_CORRECT,
                "inputs": inputs,
                "constants": {"hue_center": 0.5, "saturation_max": 1.0, "output_min": 0.0},
            }
        }
    profile["materialx_function_implementations"] = {
        COLOR_CORRECT: [
            {
                "definition": LEGACY_HSV,
                "kind": "native" if native else "recipe",
                "inputs": {target: source for source, target in inputs.items()},
                "when": {"hue_center": 0.5, "saturation_max": 1.0, "output_min": 0.0},
                "evidence": "Cycles HSV 既有数值配方；目标仅核验结构，未证明跨渲染器外观一致",
            }
        ]
    }
    if native:
        fallback = dict(profile["materialx_function_implementations"][COLOR_CORRECT][0])
        fallback["kind"] = "recipe"
        profile["materialx_function_implementations"][COLOR_CORRECT].append(fallback)
    profile["materialx_extensions"] = mx.writeToXmlString(document)


def add_native_color_correction_recipe(profile):
    """按目标声明的原生颜色校正端口生成辅助配方；不为不同宿主复制流程。"""
    document = mx.createDocument()
    mx.readFromXmlString(document, profile["materialx_extensions"])
    library = mx.createDocument()
    mx.loadLibraries(mx.getDefaultDataLibraryFolders(), mx.getDefaultDataSearchPath(), library)
    document.setDataLibrary(library)
    function = document.getNodeDef(COLOR_CORRECT)
    category = "iris_" + profile["renderer"]["id"] + "_colorcorrect"
    name = "ND_" + category + "_color3"
    definition = document.addNodeDef(name, "color3", category)
    native = next(
        n for n in profile["nodes"].values() if n["semantic"] == "materialx.ND_colorcorrect_color3"
    )
    native_inputs = {p["semantic"] for p in native["inputs"].values()}
    handled = native_inputs | {"mix", "hue_center", "output_min"}
    mapping = {}
    for source in function.getActiveInputs():
        if source.getName() not in handled:
            continue
        definition.addInput(source.getName(), source.getType()).copyContentFrom(source)
        mapping[source.getName()] = source.getName()
    graph = document.addNodeGraph("NG_" + category + "_color3")
    graph.setNodeDefString(name)

    def node(definition_name, identifier, inputs):
        """组合仅引用官方接口和已核验的目标绑定，不增加宿主侧业务公式。"""
        nd = library.getNodeDef(definition_name)
        created = graph.addNodeInstance(nd, identifier)
        for port, value in inputs.items():
            p = created.addInput(port, nd.getActiveInput(port).getType())
            if isinstance(value, str):
                p.setInterfaceName(value)
            else:
                p.setConnectedNode(value)
        return created

    hue = node("ND_subtract_float", "hue_offset", {"in1": "hue", "in2": "hue_center"})
    corrected = node(
        "ND_colorcorrect_color3",
        "color_correct",
        {
            **{p: p for p in sorted(native_inputs - {"hue"})},
            "hue": hue,
        },
    )
    mixed = node("ND_mix_color3", "factor_mix", {"fg": corrected, "bg": "in", "mix": "mix"})
    final = node("ND_max_color3FA", "output_minimum", {"in1": mixed, "in2": "output_min"})
    graph.addOutput("out", "color3").setConnectedNode(final)
    profile["materialx_function_implementations"][COLOR_CORRECT].insert(
        0,
        {
            "definition": name,
            "kind": "hybrid",
            "inputs": mapping,
            "when": {},
            "evidence": (
                "使用目标已声明的原生颜色校正与色相、混合、下限辅助节点；"
                "HSV 饱和度上限未应用，跨渲染器外观尚未验证"
            ),
        },
    )
    profile["materialx_extensions"] = mx.writeToXmlString(document)


def blend_implementation(
    document, library, nd_name, kind, mode, clamp_factor, clamp_result, defaults
):
    """生成已核验的混合配方；端口默认值由调用方给定，不读取宿主或探测目录。"""
    nd = document.addNodeDef(nd_name, kind, nd_name.removeprefix("ND_"))
    parameters = {}
    for name in ("factor", "a", "b"):
        port = nd.addInput(name, "float" if name == "factor" else kind)
        value = defaults[name]
        port.setValueString(", ".join(map(str, value)) if isinstance(value, list) else str(value))
        parameters[name] = port
    graph = document.addNodeGraph(nd_name.replace("ND_", "NG_", 1))
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
                divided = node("divide", in1=left if mode == "dodge" else sub(1, left), in2=safe)
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
    return nd


def color_adjustment_implementation(library, graph, parameters, operation):
    """构造 Gamma 或线性亮度对比度的精确配方，供明确声明的目标复用。"""

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


def add_static_functions(profile):
    """登记混合、颜色调整和数学原语的功能接口；配方由目标明确启用。"""
    from _scripts.blender_materialx_nodes import MIX_MODES

    library = mx.createDocument()
    mx.loadLibraries(mx.getDefaultDataLibraryFolders(), mx.getDefaultDataSearchPath(), library)
    doc = mx.createDocument()
    doc.setDataLibrary(library)
    mx.readFromXmlString(doc, profile["materialx_extensions"])
    exports = profile.setdefault("materialx_function_exports", {})
    implementations = profile.setdefault("materialx_function_implementations", {})
    native_source = profile["host"]["id"] == "blender"

    def interface(name, original, details):
        """继承官方接口，只新增当前功能需要的通用细节。"""
        nd = doc.addNodeDef(name, library.getNodeDef(original).getType(), name.removeprefix("ND_"))
        nd.setInheritString(original)
        nd.setAttribute("iris_function_version", "1")
        for key, datatype, value, description in details:
            port = nd.addInput(key, datatype)
            port.setValueString(str(value).lower() if isinstance(value, bool) else str(value))
            port.setAttribute("doc", description)
        return nd

    def choice(function, definition, inputs, when, native=False, kind=None):
        """原生候选和数学后备分别声明，不按源软件标签猜测目标实现。"""
        implementations.setdefault(function, []).append(
            {
                "definition": definition,
                "inputs": inputs,
                "when": when,
                "kind": kind or ("native" if native else "recipe"),
                "evidence": (
                    "原生接口或已独立数值核验的公共配方；目标运算由各自字典绑定，外观未验证"
                ),
            }
        )

    def blends():
        """登记混合模式和钳制条件，共用原生接口布局及已核验配方。"""
        # 1. Mix 与旧颜色混合共用功能接口；目标原生布局统一选用已核验的现代混合节点。
        for dtype in ("float", "color3"):
            function = "ND_iris_blend_" + dtype
            interface(
                function,
                "ND_mix_" + dtype,
                (
                    ("blend_mode", "string", "mix", "两路输入的混合模式；mix 为线性插值"),
                    ("clamp_factor", "boolean", False, "运算前将混合因子限制在零到一"),
                    ("clamp_result", "boolean", False, "运算后将结果各分量限制在零到一"),
                ),
            )
            for mode in MIX_MODES if dtype == "color3" else ("mix",):
                for cf in (False, True):
                    for cr in (False, True) if dtype == "color3" else (False,):
                        conditions = {"blend_mode": mode, "clamp_factor": cf, "clamp_result": cr}
                        inputs = {"mix": "factor", "bg": "a", "fg": "b"}
                        suffix = f"{dtype}_{mode}_cf{int(cf)}_cr{int(cr)}"
                        if native_source:
                            native_dtype = "float" if dtype == "float" else "rgba"
                            key = f"ShaderNodeMix.{native_dtype}_{mode}_cf{int(cf)}_cr{int(cr)}"
                            name = profile["nodes"][key]["semantic"].removeprefix("materialx.")
                            choice(function, name, inputs, conditions, native=True)
                            exports[name] = {
                                "definition": function,
                                "inputs": {v: k for k, v in inputs.items()},
                                "constants": conditions,
                            }
                        else:
                            name = "ND_iris_blend_recipe_" + suffix
                            default = 0.0 if dtype == "float" else [0.0, 0.0, 0.0]
                            blend_implementation(
                                doc,
                                library,
                                name,
                                dtype,
                                mode,
                                cf,
                                cr,
                                {"factor": 0.0, "a": default, "b": default},
                            )
                            if mode == "mix":
                                if not cf and not cr:
                                    choice(
                                        function,
                                        "ND_mix_" + dtype,
                                        {"fg": "fg", "bg": "bg", "mix": "mix"},
                                        conditions,
                                        native=True,
                                    )
                                else:
                                    choice(function, name, inputs, conditions, kind="hybrid")
                        choice(function, name, inputs, conditions)
            if native_source and dtype == "color3":
                for key, rule in profile["nodes"].items():
                    if key.startswith("ShaderNodeMixRGB."):
                        properties = rule["native"]["properties"]
                        exports[rule["semantic"].removeprefix("materialx.")] = {
                            "definition": function,
                            "inputs": {"factor": "mix", "a": "bg", "b": "fg"},
                            "constants": {
                                "blend_mode": properties["blend_type"].lower(),
                                "clamp_factor": True,
                                "clamp_result": properties["use_clamp"],
                            },
                        }

    def adjustments():
        """登记 Gamma 和亮度对比度的边界契约及目标实现。"""
        # 2. 颜色调整保留官方端口；边界行为通过有类型的额外参数声明。
        specifications = (
            (
                "gamma",
                "ND_power_color3FA",
                "ShaderNodeGamma",
                {"in1": "color", "in2": "gamma"},
                {
                    "nonpositive_mode": "preserve",
                    "zero_exponent_value": 1.0,
                },
                (
                    ("nonpositive_mode", "string", "preserve", "非正底数的处理：保留输入"),
                    ("zero_exponent_value", "float", 1.0, "指数为零时各分量的结果"),
                ),
            ),
            (
                "brightcontrast",
                "ND_contrast_color3FA",
                "ShaderNodeBrightContrast",
                {"in": "color", "amount": "contrast", "brightness": "bright"},
                {
                    "amount_offset": 1.0,
                    "pivot": 0.5,
                    "output_min": 0.0,
                },
                (
                    ("amount_offset", "float", 1.0, "有效线性斜率为 amount 加此偏移"),
                    ("brightness", "float", 0.0, "围绕 pivot 线性调整后增加的亮度"),
                    ("output_min", "float", 0.0, "输出各分量的下限"),
                ),
            ),
        )
        for operation, original, native_key, inputs, conditions, details in specifications:
            function = "ND_iris_" + operation + "_color3"
            interface(function, original, details)
            if native_source:
                name = profile["nodes"][native_key]["semantic"].removeprefix("materialx.")
                choice(function, name, inputs, conditions, native=True)
                exports[name] = {
                    "definition": function,
                    "inputs": {v: k for k, v in inputs.items()},
                    "constants": conditions,
                }
            else:
                name = "ND_iris_" + operation + "_recipe_color3"
                nd = doc.addNodeDef(name, "color3", name.removeprefix("ND_"))
                parameters = {}
                for key in inputs.values():
                    port = nd.addInput(key, "color3" if key == "color" else "float")
                    port.setValueString(
                        "0,0,0" if key == "color" else "1" if key == "gamma" else "0"
                    )
                    parameters[key] = port
                graph = doc.addNodeGraph(name.replace("ND_", "NG_", 1))
                graph.setNodeDefString(name)
                color_adjustment_implementation(library, graph, parameters, operation)
            choice(function, name, inputs, conditions)

    def primitives():
        """把比较和分量投影回官方接口，单独声明 Sign 零值差异。"""
        # 3. 原生大于比较导出为官方条件节点，固定的真假值由声明给出。
        if native_source:
            name = profile["nodes"]["ShaderNodeMath.greater_than"]["semantic"].removeprefix(
                "materialx."
            )
            exports[name] = {
                "definition": "ND_ifgreater_float",
                "inputs": {"in1": "value1", "in2": "value2"},
                "constants": {"in1": 1.0, "in2": 0.0},
            }
            choice(
                "ND_ifgreater_float",
                name,
                {"value1": "in1", "value2": "in2"},
                {"in1": 1.0, "in2": 0.0},
                native=True,
            )

        # 4. 分量提取直接回到官方节点；Sign 的零值差异作为通用细节保留。
        function = "ND_iris_sign_float"
        interface(
            function, "ND_sign_float", (("zero_value", "float", 1.0, "输入恰好为零时的返回值"),)
        )
        if profile["renderer"]["id"] == "redshift":
            name = "ND_iris_rs_sign_float"
            exports[name] = {
                "definition": function,
                "inputs": {"in": "in"},
                "constants": {"zero_value": 1.0},
            }
            choice(function, name, {"in": "in"}, {"zero_value": 1.0}, native=True)
            for index, channel in enumerate("rgb"):
                exports["ND_iris_rs_extract_" + channel] = {
                    "definition": "ND_extract_color3",
                    "inputs": {"in": "in"},
                    "constants": {"index": index},
                }
        else:
            name = "ND_iris_sign_positive_zero_float"
            nd = doc.addNodeDef(name, "float", "iris_sign_positive_zero")
            nd.addInput("in", "float").setValue(0.0)
            graph = doc.addNodeGraph("NG_iris_sign_positive_zero_float")
            graph.setNodeDefString(name)
            comparison = graph.addNodeInstance(
                library.getNodeDef("ND_ifgreatereq_float"), "compare"
            )
            comparison.addInput("value1", "float").setInterfaceName("in")
            for key, value in {"value2": 0.0, "in1": 1.0, "in2": -1.0}.items():
                comparison.addInput(key, "float").setValue(value)
            graph.addOutput("out", "float").setConnectedNode(comparison)
        choice(function, name, {"in": "in"}, {"zero_value": 1.0})

    # 按功能族登记接口，再由本目标写出唯一扩展库。
    blends()
    adjustments()
    primitives()
    from iris_relay.materialx.recipes import FUNCTION_RECIPES, recipe_interfaces

    doc.importLibrary(recipe_interfaces())
    profile["materialx_function_recipes"] = {
        function: {
            "recipe": recipe,
            **(
                {
                    "native": "ShaderNodeValToRGB"
                    if recipe == "color_ramp"
                    else "ShaderNodeNormalMap"
                }
                if native_source
                else {}
            ),
        }
        for recipe, function in FUNCTION_RECIPES.items()
    }
    profile["materialx_extensions"] = mx.writeToXmlString(doc)
