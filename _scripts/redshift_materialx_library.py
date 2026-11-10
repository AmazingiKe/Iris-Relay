"""生成 Redshift 组合转换需要的内联标准子图；不覆盖官方 NodeDef。"""

import MaterialX as mx


def build_library():
    """声明精确的比较、钳制和分量展开；Sign 的零值按 Redshift 文档显式表达。"""
    library = mx.createDocument()
    mx.loadLibraries(mx.getDefaultDataLibraryFolders(), mx.getDefaultDataSearchPath(), library)
    doc = mx.createDocument()
    doc.setDataLibrary(library)
    lowerings = {}

    def create_definition(name, original, omit=()):
        """复用官方接口，只有已由规则匹配的常量选择输入可以省略。"""
        source = library.getNodeDef(original)
        nd = doc.addNodeDef(name, source.getType(), name.removeprefix("ND_"))
        for port in source.getActiveInputs():
            if port.getName() not in omit:
                nd.addInput(port.getName(), port.getType()).copyContentFrom(port)
        graph = doc.addNodeGraph(name.replace("ND_", "NG_", 1))
        graph.setNodeDefString(name)
        return nd, graph

    def node(graph, definition, **inputs):
        nd = doc.getNodeDef(definition) or library.getNodeDef(definition)
        if nd is None:
            raise ValueError("官方库中缺少定义：" + definition)
        created = graph.addNodeInstance(nd, graph.createValidChildName(nd.getNodeString()))
        for name, value in inputs.items():
            port = created.addInput(name, nd.getActiveInput(name).getType())
            if isinstance(value, mx.Node):
                port.setConnectedNode(value)
            elif isinstance(value, str):
                port.setInterfaceName(value)
            else:
                port.setValueString(str(value))
        return created

    def finish(nd, graph, output):
        graph.addOutput("out", nd.getType()).setConnectedNode(output)

    # 1. 原生 Sign 在零时返回 +1，与 MaterialX sign 不同，必须使用独立定义。
    nd, graph = create_definition("ND_iris_rs_sign_float", "ND_sign_float")
    finish(nd, graph, node(graph, "ND_ifgreatereq_float", value1="in", value2=0, in1=1, in2=-1))
    for index, channel in enumerate("rgb"):
        name = "ND_iris_rs_extract_" + channel
        nd, graph = create_definition(name, "ND_extract_color3", omit=("index",))
        finish(nd, graph, node(graph, "ND_extract_color3", **{"in": "in", "index": index}))
        if index:
            lowerings.setdefault("ND_extract_color3", []).append(
                {"when": {"index": index}, "replacement": name}
            )

    def lowering(original):
        replacement = original.replace("ND_", "ND_iris_rs_lower_", 1)
        lowerings[original] = [{"when": {}, "replacement": replacement}]
        return create_definition(replacement, original)

    # 2. 只用精确算术组合严格比较与相等；边界不使用窄坡道近似。
    for operation in ("ifequal", "ifgreater"):
        for dtype in ("float", "color3"):
            nd, graph = lowering("ND_" + operation + "_" + dtype)
            if operation == "ifgreater":
                difference = node(graph, "ND_subtract_float", in1="value2", in2="value1")
                signed = node(graph, "ND_iris_rs_sign_float", **{"in": difference})
                numerator = node(graph, "ND_subtract_float", in1=1, in2=signed)
            else:
                difference = node(graph, "ND_subtract_float", in1="value1", in2="value2")
                negative = node(graph, "ND_subtract_float", in1=0, in2=difference)
                positive_sign = node(graph, "ND_iris_rs_sign_float", **{"in": difference})
                negative_sign = node(graph, "ND_iris_rs_sign_float", **{"in": negative})
                numerator = node(graph, "ND_add_float", in1=positive_sign, in2=negative_sign)
            factor = node(graph, "ND_multiply_float", in1=numerator, in2=0.5)
            finish(nd, graph, node(graph, "ND_mix_" + dtype, fg="in1", bg="in2", mix=factor))
    nd, graph = lowering("ND_clamp_float")
    maximum = node(graph, "ND_max_float", in1="in", in2="low")
    finish(nd, graph, node(graph, "ND_min_float", in1=maximum, in2="high"))
    nd, graph = lowering("ND_convert_float_color3")
    finish(nd, graph, node(graph, "ND_combine3_color3", in1="in", in2="in", in3="in"))
    nd, graph = lowering("ND_max_color3FA")
    channels = []
    for channel in "rgb":
        component = node(graph, "ND_iris_rs_extract_" + channel, **{"in": "in1"})
        channels.append(node(graph, "ND_max_float", in1=component, in2="in2"))
    finish(
        nd,
        graph,
        node(graph, "ND_combine3_color3", in1=channels[0], in2=channels[1], in3=channels[2]),
    )
    return mx.writeToXmlString(doc), lowerings
