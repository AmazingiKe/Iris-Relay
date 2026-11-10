"""生成原生操作的内联标准子图；复用 SDK NodeDef，不复制标准端口定义。"""

import MaterialX as mx


def blender_hsv_library(inputs):
    """按 Cycles 5.2.3 公式表达完整 HSV，包括负颜色、饱和度钳制与混合后下限。"""
    library = mx.createDocument()
    mx.loadLibraries(mx.getDefaultDataLibraryFolders(), mx.getDefaultDataSearchPath(), library)
    document = mx.createDocument()
    document.setDataLibrary(library)
    definition = document.addNodeDef("ND_iris_blender_hsv_color3", "color3", "iris_blender_hsv")
    parameters = {}
    for rule in inputs.values():
        port = definition.addInput(
            rule["semantic"], "color3" if rule["type"] == "color" else "float"
        )
        value = rule["default"]
        port.setValueString(
            ", ".join(str(v) for v in value) if isinstance(value, list) else str(value)
        )
        parameters[rule["semantic"]] = port
    graph = document.addNodeGraph("NG_iris_blender_hsv_color3")
    graph.setNodeDefString(definition.getName())

    def node(name, nodedef, **values):
        """从官方定义读取输入类型，建立常量、子图接口或标准节点连接。"""
        standard = library.getNodeDef(nodedef)
        if standard is None:
            raise ValueError(f"当前 MaterialX 缺少标准定义：{nodedef}")
        result = graph.addNodeInstance(standard, name)
        for key, value in values.items():
            port = result.addInput(key, standard.getActiveInput(key).getType())
            if isinstance(value, mx.Node):
                port.setConnectedNode(value)
            elif isinstance(value, mx.Input):
                port.setInterfaceName(value.getName())
            else:
                port.setValueString(str(value))
        return result

    def binary(name, operation, a, b):
        """建立两个浮点输入的标准运算，保持源公式的输入顺序。"""
        return node(name, f"ND_{operation}_float", in1=a, in2=b)

    def choose(name, a, b, yes, no, datatype="float"):
        """标准相等选择；分支中的除法另用非零分母，避免后端提前求值产生零除。"""
        return node(name, f"ND_ifequal_{datatype}", value1=a, value2=b, in1=yes, in2=no)

    # 1. RGB→HSV 按 Cycles 的 max!=0、s!=0 分支展开，保留负颜色与近灰行为。
    rgb = [
        node(label, "ND_extract_color3", **{"in": parameters["color"], "index": i})
        for i, label in enumerate(("red", "green", "blue"))
    ]
    maximum = binary("maximum", "max", rgb[0], binary("maximum_gb", "max", rgb[1], rgb[2]))
    minimum = binary("minimum", "min", rgb[0], binary("minimum_gb", "min", rgb[1], rgb[2]))
    delta = binary("chroma", "subtract", maximum, minimum)
    safe_maximum = choose("nonzero_maximum", maximum, 0.0, 1.0, maximum)
    saturation = choose(
        "source_saturation",
        maximum,
        0.0,
        0.0,
        binary("saturation_ratio", "divide", delta, safe_maximum),
    )
    safe_delta = choose("nonzero_chroma", delta, 0.0, 1.0, delta)
    differences = [
        binary(
            f"relative_{i}",
            "divide",
            binary(f"difference_{i}", "subtract", maximum, channel),
            safe_delta,
        )
        for i, channel in enumerate(rgb)
    ]
    r, g, b = differences
    red_hue = binary("red_hue", "subtract", b, g)
    green_hue = binary("green_hue", "subtract", binary("green_offset", "add", 2.0, r), b)
    blue_hue = binary("blue_hue", "subtract", binary("blue_offset", "add", 4.0, g), r)
    sector = choose(
        "hue_sector",
        rgb[0],
        maximum,
        red_hue,
        choose("hue_green_or_blue", rgb[1], maximum, green_hue, blue_hue),
    )
    hue = binary("hue_fraction", "divide", sector, 6.0)
    positive = node(
        "positive_hue",
        "ND_ifgreater_float",
        value1=0.0,
        value2=hue,
        in1=binary("hue_plus_one", "add", hue, 1.0),
        in2=hue,
    )
    hue = choose("source_hue", saturation, 0.0, 0.0, positive)

    # 2. 原生 Hue 的中点是 0.5；饱和度乘积钳制到 [0,1]，Value 保持乘法。
    shifted = binary(
        "hue_shift", "add", binary("hue_parameter", "add", hue, parameters["hue"]), 0.5
    )
    hue = binary(
        "hue_wrap", "subtract", shifted, node("hue_floor", "ND_floor_float", **{"in": shifted})
    )
    saturation = node(
        "saturation_clamp",
        "ND_clamp_float",
        **{
            "in": binary("saturation_parameter", "multiply", saturation, parameters["saturation"]),
            "low": 0.0,
            "high": 1.0,
        },
    )
    value = binary("value_parameter", "multiply", maximum, parameters["value"])

    # 3. HSV→RGB 不使用其他实现的近灰阈值；六个扇区按原生分支组合。
    expanded_hue = binary("hue_six", "multiply", hue, 6.0)
    index = node("sector_index", "ND_floor_float", **{"in": expanded_hue})
    fraction = binary("sector_fraction", "subtract", expanded_hue, index)
    p = binary("rgb_p", "multiply", value, binary("one_minus_s", "subtract", 1.0, saturation))
    q = binary(
        "rgb_q",
        "multiply",
        value,
        binary("one_minus_sf", "subtract", 1.0, binary("sf", "multiply", saturation, fraction)),
    )
    t = binary(
        "rgb_t",
        "multiply",
        value,
        binary(
            "one_minus_s_one_minus_f",
            "subtract",
            1.0,
            binary(
                "s_one_minus_f",
                "multiply",
                saturation,
                binary("one_minus_f", "subtract", 1.0, fraction),
            ),
        ),
    )
    combinations = [
        (value, t, p),
        (q, value, p),
        (p, value, t),
        (p, q, value),
        (t, p, value),
        (value, p, q),
    ]
    colors = [
        node(
            f"sector_color_{i}",
            "ND_combine3_color3",
            **{f"in{j}": v for j, v in enumerate(channels, 1)},
        )
        for i, channels in enumerate(combinations)
    ]
    adjusted = colors[-1]
    for i in reversed(range(5)):
        adjusted = choose(f"select_sector_{i}", index, float(i), colors[i], adjusted, "color3")
    # 4. Fac 可由连接驱动；源公式不钳制 Fac，混合后只限制负颜色。
    mixed = node(
        "factor_mix", "ND_mix_color3", fg=adjusted, bg=parameters["color"], mix=parameters["factor"]
    )
    final = node("nonnegative", "ND_max_color3FA", in1=mixed, in2=0.0)
    graph.addOutput("out", "color3").setConnectedNode(final)
    valid, message = document.validate()
    if not valid:
        raise ValueError(f"HSV 标准子图未通过 SDK 校验：{message}")
    return mx.writeToXmlString(document)
