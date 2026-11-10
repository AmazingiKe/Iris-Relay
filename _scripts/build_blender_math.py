"""补齐外部标准数学图的 Blender 原生绑定，公式与组合继续保存在字典。"""

import MaterialX as mx


def extend(profile, catalog):
    """用当前实机端口和官方接口生成直接绑定、比较原语及有限组合。"""
    library = mx.createDocument()
    mx.loadLibraries(mx.getDefaultDataLibraryFolders(), mx.getDefaultDataSearchPath(), library)
    document = mx.createDocument()
    document.setDataLibrary(library)
    mx.readFromXmlString(document, profile["materialx_extensions"])
    lowerings = {}
    types = {"float": "float", "integer": "float", "color3": "color"}

    def node(graph, definition, **values):
        declared = document.getNodeDef(definition) or library.getNodeDef(definition)
        result = graph.addNodeInstance(
            declared, graph.createValidChildName(declared.getNodeString())
        )
        for key, value in values.items():
            port = result.addInput(key, declared.getActiveInput(key).getType())
            if isinstance(value, tuple):
                port.setConnectedNode(value[0])
                port.setOutputString(value[1])
            elif isinstance(value, mx.Node):
                port.setConnectedNode(value)
            elif isinstance(value, str):
                port.setInterfaceName(value)
            else:
                port.setValueString(
                    ", ".join(map(str, value)) if isinstance(value, list) else str(value)
                )
        return result

    def bind(key, native, definition, mapping, output, properties=None, output_value=False):
        observed = catalog["nodes"][native]
        assert observed["status"] == "instantiated"
        inputs = {p["id"] for p in observed["inputs"]}
        outputs = {p["id"] for p in observed["outputs"]}
        declared = document.getNodeDef(definition) or library.getNodeDef(definition)
        rule = {
            "semantic": "materialx." + definition,
            "native": {"id": native, "properties": properties or {}},
            "inputs": {},
            "outputs": {
                output: {
                    "semantic": "out",
                    "type": types[declared.getType()],
                    "native": {"id": output, "kind": "socket", "codec": "identity"},
                },
            },
        }
        assert output in outputs
        for semantic, identifier in mapping.items():
            assert identifier in (outputs if output_value else inputs)
            port = declared.getActiveInput(semantic)
            default = port.getValue()
            if port.getType() == "color3":
                default = list(default)
            binding = {
                "id": "default_value" if output_value else identifier,
                "kind": "property" if output_value else "socket",
                "codec": "rgb" if port.getType() == "color3" else "identity",
            }
            if output_value:
                binding["properties"] = {"output_socket": identifier}
            rule["inputs"][semantic] = {
                "semantic": semantic,
                "type": types[port.getType()],
                "default": default,
                "native": binding,
            }
        profile["nodes"][key] = rule
        return rule

    operations = {
        "divide": "DIVIDE",
        "min": "MINIMUM",
        "max": "MAXIMUM",
        "floor": "FLOOR",
        "sign": "SIGN",
        "power": "POWER",
    }
    for operation, native in operations.items():
        assert native in catalog["nodes"]["ShaderNodeMath"]["properties"]["operation"]["items"]
        mapping = (
            {"in": "Value"}
            if operation in {"floor", "sign"}
            else {"in1": "Value", "in2": "Value_001"}
        )
        rule = bind(
            "ShaderNodeMath." + operation,
            "ShaderNodeMath",
            "ND_" + operation + "_float",
            mapping,
            "Value",
            {"operation": native, "use_clamp": False},
        )
        if operation in {"divide", "power"}:
            rule["approximation"] = (
                "保留有限有效域的标准运算；除零、非法幂及宿主浮点极值行为可能不同"
            )
    bind(
        "ShaderNodeValue",
        "ShaderNodeValue",
        "ND_constant_float",
        {"value": "Value"},
        "Value",
        output_value=True,
    )
    bind(
        "ShaderNodeRGB",
        "ShaderNodeRGB",
        "ND_constant_color3",
        {"value": "Color"},
        "Color",
        output_value=True,
    )
    bind(
        "ShaderNodeCombineColor",
        "ShaderNodeCombineColor",
        "ND_combine3_color3",
        {"in1": "Red", "in2": "Green", "in3": "Blue"},
        "Color",
        {"mode": "RGB"},
    )

    def definition(original, suffix="", omit=()):
        source = library.getNodeDef(original)
        name = "ND_iris_blender_lower_" + original.removeprefix("ND_") + suffix
        nd = document.addNodeDef(name, source.getType(), name.removeprefix("ND_"))
        for port in source.getActiveInputs():
            if port.getName() not in omit:
                nd.addInput(port.getName(), port.getType()).copyContentFrom(port)
        graph = document.addNodeGraph(name.replace("ND_", "NG_", 1))
        graph.setNodeDefString(name)
        return nd, graph

    def finish(nd, graph, output):
        port = graph.addOutput("out", nd.getType())
        if isinstance(output, tuple):
            port.setConnectedNode(output[0])
            port.setOutputString(output[1])
        else:
            port.setConnectedNode(output)

    def lower(original):
        nd, graph = definition(original)
        lowerings[original] = [{"when": {}, "replacement": nd.getName()}]
        return nd, graph

    # 1. 严格比较作为已核验原生模式，避免 Blender COMPARE 的容差改变相等语义。
    nd, graph = definition("ND_ifgreater_float", "_native_greater", omit=("value1", "value2"))
    for name in ("in1", "in2"):
        nd.getInput(name).setValue(0.0)
    finish(nd, graph, node(graph, "ND_ifgreater_float", value1="in1", value2="in2", in1=1, in2=0))
    greater = nd.getName()
    bind(
        "ShaderNodeMath.greater_than",
        "ShaderNodeMath",
        greater,
        {"in1": "Value", "in2": "Value_001"},
        "Value",
        {"operation": "GREATER_THAN", "use_clamp": False},
    )
    for datatype, suffix in (("float", "float_mix_cf0_cr0"), ("color3", "rgba_mix_cf0_cr0")):
        original = "ND_mix_" + datatype
        nd, graph = lower(original)
        native = profile["nodes"]["ShaderNodeMix." + suffix]["semantic"].removeprefix("materialx.")
        finish(nd, graph, node(graph, native, factor="mix", a="bg", b="fg"))
    for operation in ("ifequal", "ifgreater", "ifgreatereq"):
        for datatype in ("float", "color3"):
            nd, graph = lower("ND_" + operation + "_" + datatype)
            forward = (
                node(graph, greater, in1="value1", in2="value2")
                if operation != "ifgreatereq"
                else None
            )
            if operation == "ifgreater":
                factor = forward
            else:
                reverse = node(graph, greater, in1="value2", in2="value1")
                both = (
                    node(graph, "ND_add_float", in1=forward, in2=reverse)
                    if operation == "ifequal"
                    else reverse
                )
                factor = node(graph, "ND_subtract_float", in1=1, in2=both)
            finish(nd, graph, node(graph, "ND_mix_" + datatype, fg="in1", bg="in2", mix=factor))
    # 2. 类型广播与颜色数学只拆分已知分量，共享上游仍保持一份节点身份。
    nd, graph = lower("ND_convert_float_color3")
    finish(nd, graph, node(graph, "ND_combine3_color3", in1="in", in2="in", in3="in"))
    nd, graph = lower("ND_clamp_float")
    finish(
        nd,
        graph,
        node(
            graph, "ND_min_float", in1=node(graph, "ND_max_float", in1="in", in2="low"), in2="high"
        ),
    )
    for operation in ("add", "subtract", "multiply", "divide", "max"):
        original = "ND_max_color3FA" if operation == "max" else "ND_" + operation + "_color3"
        nd, graph = lower(original)
        channels = []
        for index in range(3):
            a = node(graph, "ND_extract_color3", **{"in": "in1", "index": index})
            b = (
                "in2"
                if operation == "max"
                else node(graph, "ND_extract_color3", **{"in": "in2", "index": index})
            )
            channels.append(node(graph, "ND_" + operation + "_float", in1=a, in2=b))
        finish(
            nd,
            graph,
            node(graph, "ND_combine3_color3", in1=channels[0], in2=channels[1], in3=channels[2]),
        )
    for index, output in ((1, "outg"), (2, "outb")):
        nd, graph = definition("ND_extract_color3", "_" + str(index), omit=("index",))
        separated = node(graph, "ND_separate3_color3", **{"in": "in"})
        # SDK 1.39.5 展开图出口直接引用多输出节点时会丢失 output 选择；
        # 单输出恒等端点保留内部显式通道，避免按节点名字猜回缺失信息。
        selected = node(graph, "ND_add_float", in1=(separated, output), in2=0)
        finish(nd, graph, selected)
        lowerings.setdefault("ND_extract_color3", []).append(
            {"when": {"index": index}, "replacement": nd.getName()}
        )
    profile["materialx_extensions"] = mx.writeToXmlString(document)
    profile["materialx_lowerings"] = lowerings
