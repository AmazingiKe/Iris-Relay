"""既有原生字典与 MaterialX 的受限桥接；节点定义来自官方库。

导出入口 export_material：原生图转语义图 → 创建节点 → 连接节点 → 校验并返回 XML。
反向入口 convert_document：解析 MaterialX → 提取受支持语义 → 生成目标原生数据。
本模块只生成文档内容，文件路径检查和保存由插件 controller 负责。
"""

from copy import deepcopy
from dataclasses import dataclass, field

import MaterialX as mx

from ..conversion import issue, status_for, transcode
from ..model import Material, ValidationError
from .coordinates import (
    check_target_coordinates,
    export_coordinates,
    import_coordinates,
    prepare_source_coordinates,
    prepare_target_coordinates,
)
from .editor import (
    decorate_material,
    export_editor,
    finalize_node_names,
    get_editor,
    get_text_attribute,
    set_text_attribute,
)
from .extensions import install_extensions, load_extensions, preserved_extensions
from .functions import export_functions, import_functions
from .images import (
    IMAGE_TYPES,
    ImageGraphReader,
    adapt_target_image_graphs,
    add_image_outputs,
    alpha_description,
    alpha_warning,
    check_target_alpha,
)
from .lowerings import apply_lowerings
from .preserve import collect_deferred_details, read_deferred_details, write_deferred_details
from .recipes import prepare_recipes

# 这里只声明已核验的语义对应，不复制 MaterialX 的类型和默认值定义。
BINDINGS = {
    "surface.standard": (
        "ND_standard_surface_surfaceshader",
        {
            "base.color": "base_color",
            "metalness": "metalness",
            "reflection.roughness": "specular_roughness",
            "reflection.ior": "specular_IOR",
            "reflection.weight": "specular",
            "reflection.tint": "specular_color",
            "reflection.anisotropy": "specular_anisotropy",
            "reflection.rotation": "specular_rotation",
            "diffuse.roughness": "diffuse_roughness",
            "coat.weight": "coat",
            "coat.roughness": "coat_roughness",
            "coat.ior": "coat_IOR",
            "coat.tint": "coat_color",
            "coat.normal": "coat_normal",
            "normal": "normal",
            "emission.color": "emission_color",
            "emission.weight": "emission",
            "sheen.weight": "sheen",
            "sheen.roughness": "sheen_roughness",
            "sheen.tint": "sheen_color",
            "opacity": "opacity",
        },
        "surface",
    ),
    "texture.image": ("ND_image_color3", {"image": "file"}, "color"),
    "normal.tangent": (
        "ND_normalmap_float",
        {"normal.color": "in", "normal.strength": "scale"},
        "normal",
    ),
    "bump.height": (
        "ND_bump_vector3",
        {"height": "height", "height.scale": "scale", "normal": "normal"},
        "normal",
    ),
    "channel.red": ("ND_extract_color3", {"color": "in"}, "value"),
    "color.weighted_sum": ("ND_dotproduct_vector3", {"color": "in1", "weights": "in2"}, "value"),
}
COLOR_TO_MX = {"Raw": "raw", "sRGB": "srgb_texture"}
COLOR_FROM_MX = {v: k for k, v in COLOR_TO_MX.items()}


def bindings_for(core, dictionary, extensions=None):
    """合并旧桥接、官方定义和内联扩展绑定；端口及类型均由 SDK 校验。"""
    bindings = dict(BINDINGS)
    types = {
        "float": "float",
        "integer": "float",
        "color3": "color",
        "color4": "color4",
        "vector3": "vector",
        "boolean": "bool",
        "string": "string",
    }
    for rule in dictionary.nodes.values():
        semantic = rule["semantic"]
        if not semantic.startswith("materialx."):
            continue
        name = semantic.removeprefix("materialx.")
        definition = core.library.getNodeDef(name) or (
            extensions.getNodeDef(name) if extensions is not None else None
        )
        if definition is None:
            raise ValidationError(
                f"标准库与字典扩展均没有 NodeDef：{name}", "MATERIALX_BINDING_INVALID"
            )
        outputs = definition.getActiveOutputs()
        if not outputs or any(types.get(p.getType()) is None for p in outputs):
            raise ValidationError(
                f"直接绑定尚不支持该输出类型：{name}", "MATERIALX_BINDING_INVALID"
            )
        inputs = {}
        for direction, declared in (("inputs", definition.getActiveInputs()), ("outputs", outputs)):
            ports = {p.getName(): p for p in declared}
            supplied = set()
            for port in rule[direction].values():
                key = port["semantic"]
                if (
                    direction == "inputs"
                    and key.startswith("native.")
                    and port.get("omit_if_default")
                ):
                    continue
                datatype = "color" if port.get("transform") == "scalar_to_color" else port["type"]
                if key not in ports or types.get(ports[key].getType()) != datatype:
                    raise ValidationError(
                        f"原生绑定与官方端口不符：{name}.{key}", "MATERIALX_BINDING_INVALID"
                    )
                supplied.add(key)
                if direction == "inputs":
                    inputs[key] = key
            if direction == "outputs" and supplied != set(ports):
                raise ValidationError(f"直接绑定缺少输出：{name}", "MATERIALX_BINDING_INVALID")
        binding = (
            name,
            inputs,
            outputs[0].getName()
            if len(outputs) == 1
            else {p.getName(): p.getName() for p in outputs},
        )
        if semantic in bindings and bindings[semantic] != binding:
            raise ValidationError(
                f"同一标准节点的绑定端口不一致：{name}", "MATERIALX_BINDING_INVALID"
            )
        bindings[semantic] = binding
    return bindings


def value_string(value):
    """将 Python 标量、布尔值或向量编码为 MaterialX 属性文本。"""
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def python_value(port):
    """读取 MaterialX 端口常量；未声明值时返回 None。"""
    if not port.hasValueString():
        return None
    value = port.getValue()
    if isinstance(value, (str, float, int, bool)):
        return value
    if isinstance(value, (mx.Matrix33, mx.Matrix44)):
        return [
            value[row, column]
            for row in range(value.numRows())
            for column in range(value.numColumns())
        ]
    return list(value)


def add_value(node, name, value):
    """按官方 NodeDef 的类型创建输入；值为空时只创建端口，供随后接线。"""
    definition = node.getNodeDef().getActiveInput(name)
    port = node.addInput(name, definition.getType())
    if value is not None:
        if definition.getType() == "integer":
            if type(value) not in (int, float) or int(value) != value:
                raise ValidationError("MaterialX 整数输入必须为整数值", "MATERIALX_BINDING_INVALID")
            value = int(value)
        port.setValueString(value_string(value))
    return port


def connect_endpoint(port, endpoint):
    """保留多输出节点的显式端口；单输出继续使用标准默认连接。"""
    node, output = endpoint if isinstance(endpoint, tuple) else (endpoint, None)
    port.setConnectedNode(node)
    if output is not None:
        port.setOutputString(output)
    return port


def endpoint_output(port):
    """沿图输出引用读取最终选择；循环与未声明输出交由文档校验拒绝。"""
    seen = set()
    while port is not None:
        if port.getNamePath() in seen:
            raise ValidationError("输出引用包含循环", "MATERIALX_CYCLE")
        seen.add(port.getNamePath())
        connected = port.getConnectedOutput()
        if connected is None:
            return port.getOutputString()
        port = connected
    return ""


def report(issues):
    """建立默认阻断的桥接报告，只有完整操作成功后才修改状态。"""
    return {
        "status": "blocked",
        "issues": issues,
        "appearance_verified": False,
        "materialx_version": mx.getVersionString(),
    }


def explicit_color_casts(source, dictionary):
    """只接受字典声明的宿主隐式转换；系数须由宿主读取实际色彩上下文。"""
    # 1. 必须先有字典授权，再查找确实存在的颜色 → 标量连接。
    rule = dictionary.data.get("materialx_export", {}).get("color_to_float")
    if rule != "scene_linear_luminance":
        return set(), None
    nodes = {n["id"]: dictionary.nodes.get(n["type"], {}) for n in source["nodes"]}
    casts = set()
    for link in source["links"]:
        start, end = link["from"], link["to"]
        source_port = nodes[start["node"]].get("outputs", {}).get(start["socket"], {})
        target_port = nodes[end["node"]].get("inputs", {}).get(end["socket"], {})
        if source_port.get("type") == "color" and target_port.get("type") == "float":
            casts.add((end["node"], end["socket"]))
    if not casts:
        return casts, None
    # 2. 校验宿主提供的亮度上下文，不使用 MaterialX 默认系数替代。
    context = source.get("metadata", {}).get("color_to_float")
    coefficients = context.get("coefficients") if isinstance(context, dict) else None
    if (
        not isinstance(context, dict)
        or context.get("working_space") != "lin_rec709_scene"
        or not isinstance(coefficients, list)
        or len(coefficients) != 3
        or any(type(v) not in (int, float) or v < 0 for v in coefficients)
        or abs(sum(coefficients) - 1) > 0.001
    ):
        raise ValidationError(
            "颜色到标量连接需要已核验的 Linear Rec.709 上下文及宿主亮度系数",
            "MATERIALX_COLORSPACE_UNSUPPORTED",
        )
    return casts, coefficients


@dataclass
class _ExportNodes:
    """语义 ID/端口到 MaterialX 对象的索引，供节点创建和连接两个阶段共用。"""

    nodes: dict = field(default_factory=dict)
    inputs: dict = field(default_factory=dict)
    outputs: dict = field(default_factory=dict)
    bindings: dict = field(default_factory=dict)


def export_material(core, source, dictionary):
    """将原生材质转为 MaterialX 文本；失败返回源图和诊断，不写磁盘。"""
    # 1. 保留来源与字典指纹，确定哪些隐式连接需要显式转换节点。
    issues = []
    result = {"source": source.to_dict(), "document": None, "report": report(issues)}
    result["report"]["source_dictionary_fingerprint"] = dictionary.fingerprint
    try:
        from ..tools.naming import normalize_names

        prepared, dictionary, compiled = prepare_recipes(
            core, dictionary, source=normalize_names(source.data)
        )
        prepared = prepare_source_coordinates(prepared, dictionary, issues)
        for record in prepared["nodes"]:
            if record["type"] in compiled and record["inputs"].get("uv_map"):
                issues.append(
                    issue(
                        "NORMAL_UV_ALIAS_VERIFIED",
                        "所有已读网格的命名法线 UV 与渲染默认集一致；目标几何须使用对应默认 UV",
                        record["id"],
                        "uv_map",
                        "warning",
                    )
                )
        source = Material.parse(prepared)
        extensions = load_extensions(core, dictionary)
        bindings = bindings_for(core, dictionary, extensions)
        casts, coefficients = explicit_color_casts(source.data, dictionary)
        transforms = {
            (node["id"], port)
            for node in source.data["nodes"]
            for port, rule in dictionary.nodes.get(node["type"], {}).get("inputs", {}).items()
            if rule.get("transform") == "scalar_to_color"
        }
    except ValidationError as exc:
        issues.append(issue(exc.code, str(exc)))
        return result
    # 2. 由统一字典转换为语义图；已有错误时停止，不生成部分成功文档。
    semantic = transcode(
        source.to_dict(),
        dictionary,
        encode=False,
        renderer={"id": "iris.semantic", "version": "1"},
        issues=issues,
        explicit_casts=casts,
        explicit_transforms=transforms,
    )
    if status_for(issues) == "error":
        return result
    try:
        # 3. 建立空文档，创建标准节点及输入，再恢复连接和隐式转换。
        doc = mx.createDocument()
        doc.setDataLibrary(core.library)
        install_extensions(doc, extensions, bindings, semantic)
        set_text_attribute(doc, "iris_material_name", source.data["name"])
        index = _create_export_nodes(core, doc, semantic, bindings, issues)
        _connect_export_links(
            core, doc, source.data, semantic, index, casts, coefficients, issues, transforms
        )

        # 4. 增加材质根节点，把选定的表面连接到 surfaceshader。
        material = doc.addNodeInstance(
            core.library.getNodeDef("ND_surfacematerial"), doc.createValidChildName("material")
        )
        add_value(material, "surfaceshader", None).setConnectedNode(
            index.outputs[(semantic["output"]["node"], semantic["output"]["socket"])]
        )

        # 5. 整图通过校验后才序列化；这里返回 XML 字符串，尚未保存文件。
        export_functions(core, doc, dictionary, extensions)
        write_deferred_details(doc, source.data.get("metadata", {}).get("deferred_details"), issues)
        finalize_node_names(doc)
        issues.extend(core.inspect(doc))
        if status_for(issues) != "error":
            result["document"] = core.serialize(doc)
            result["report"]["status"] = "exported"
    except (ValidationError, mx.Exception) as exc:
        issues.append(issue(getattr(exc, "code", "MATERIALX_INVALID"), str(exc)))
    return result


def _create_export_nodes(core, doc, semantic, bindings, issues):
    """创建所有标准节点及输入，返回端口索引；未知节点或被使用的未知输出阻断。"""
    index = _ExportNodes(bindings=bindings)
    used = {(link["from"]["node"], link["from"]["socket"]) for link in semantic["links"]}
    used.add((semantic["output"]["node"], semantic["output"]["socket"]))
    for record in semantic["nodes"]:
        binding = bindings.get(record["type"])
        if binding is None:
            raise ValidationError(
                f"尚无 MaterialX 对应：{record['type']}", "MATERIALX_NODE_UNSUPPORTED"
            )
        definition, inputs, output = binding
        description = None
        used_alpha = (record["id"], "alpha") in used
        if record["type"] == "texture.image":
            reference = record["inputs"].get("image")
            resource = semantic["textures"].get(reference["texture"]) if reference else None
            description = alpha_description(resource) if resource else None
            if used_alpha and description is None:
                raise ValidationError(
                    "使用 Alpha 输出需要明确文件通道和 Alpha 模式", "MATERIALX_RESOURCE_UNSUPPORTED"
                )
            if description is not None:
                definition = "ND_image_" + IMAGE_TYPES[description["channels"]]
        name = doc.createValidChildName(record["id"])
        node = doc.addNodeInstance(
            doc.getNodeDef(definition) or core.library.getNodeDef(definition), name
        )
        set_text_attribute(node, "iris_source_name", record["metadata"]["source_name"])
        export_editor(node, record)
        index.nodes[record["id"]] = node
        if isinstance(output, dict):
            index.outputs.update(
                {(record["id"], semantic): (node, name) for name, semantic in output.items()}
            )
        else:
            index.outputs[(record["id"], output)] = node
        if description is not None:
            outputs = add_image_outputs(
                core, doc, node, description, used_alpha, resource["color_space"], add_value
            )
            index.outputs.update({(record["id"], key): value for key, value in outputs.items()})
            alpha_warning(description, resource["color_space"], issues, record["id"])
        for port in record["outputs"]:
            if (record["id"], port) not in index.outputs and (record["id"], port) in used:
                raise ValidationError(
                    f"尚不支持输出：{record['id']}.{port}", "MATERIALX_OUTPUT_UNSUPPORTED"
                )
        for port, value in record["inputs"].items():
            if port not in inputs:
                raise ValidationError(
                    f"尚不支持输入：{record['id']}.{port}", "MATERIALX_INPUT_UNSUPPORTED"
                )
            index.inputs[(record["id"], port)] = _create_export_input(
                core, doc, node, record["type"], port, inputs[port], value, semantic["textures"]
            )
    return index


def _create_export_input(core, doc, node, semantic_type, port, mx_port, value, textures):
    """写入单个输入，返回实际接线端口；图片和法线颜色需要额外处理。"""
    # 图片：验证资源解释，保留路径和色彩空间；不加载或修改图片文件。
    if semantic_type == "texture.image":
        if value is None:
            raise ValidationError("图片节点缺少资源", "MATERIALX_RESOURCE_UNSUPPORTED")
        resource = textures[value["texture"]]
        export_coordinates(core, doc, node, resource["coordinates"], add_value)
        color = COLOR_TO_MX.get(resource["color_space"])
        if color is None:
            raise ValidationError("尚未核验该贴图色彩空间", "MATERIALX_COLORSPACE_UNSUPPORTED")
        target = add_value(node, "file", resource["uri"])
        target.setColorSpace(color)
        return target

    # 法线或加权求和的颜色：加上 color3 → vector3，后续连线接到辅助节点输入。
    if (semantic_type, port) in {
        ("normal.tangent", "normal.color"),
        ("color.weighted_sum", "color"),
    }:
        helper = doc.addNodeInstance(
            core.library.getNodeDef("ND_convert_color3_vector3"),
            doc.createValidChildName(node.getName() + "_color_to_vector"),
        )
        target = add_value(helper, "in", value)
        add_value(node, mx_port, None).setConnectedNode(helper)
        return target

    # 普通值直接写入；空的几何法线保留 MaterialX 默认含义，不写成零向量。
    return add_value(node, mx_port, value) if value is not None else None


def _connect_export_links(
    core, doc, source, semantic, index, casts, coefficients, issues, transforms
):
    """按语义图恢复连接；按已核验规则补出颜色转亮度和标量转灰度颜色。"""
    for original, link in zip(source["links"], semantic["links"], strict=True):
        target_key = (link["to"]["node"], link["to"]["socket"])
        port = index.inputs[target_key]
        if port is None:
            # 原来省略的几何默认输入现在有连接，需要先创建空端口。
            record = next(n for n in semantic["nodes"] if n["id"] == target_key[0])
            port = add_value(
                index.nodes[target_key[0]], index.bindings[record["type"]][1][target_key[1]], None
            )
        port.removeAttribute("value")
        upstream = index.outputs[(link["from"]["node"], link["from"]["socket"])]
        if (original["to"]["node"], original["to"]["socket"]) in casts:
            upstream = _add_luminance_cast(core, doc, upstream, coefficients)
            issues.append(
                issue(
                    "IMPLICIT_CAST_EXPLICIT",
                    "已将宿主颜色转标量连接展开为显式亮度计算",
                    original["to"]["node"],
                    original["to"]["socket"],
                    "info",
                )
            )
        if (original["to"]["node"], original["to"]["socket"]) in transforms:
            gray = doc.addNodeInstance(
                core.library.getNodeDef("ND_convert_float_color3"),
                doc.createValidChildName("scalar_to_color"),
            )
            connect_endpoint(add_value(gray, "in", None), upstream)
            upstream = gray
        connect_endpoint(port, upstream)


def _add_luminance_cast(core, doc, upstream, coefficients):
    """创建颜色 → 亮度 → 标量链，返回可连接到标量输入的 extract 节点。"""
    # 先用源宿主的系数计算灰度颜色，再取其相同的任一分量得到标量。
    luma = doc.addNodeInstance(
        core.library.getNodeDef("ND_luminance_color3"),
        doc.createValidChildName("color_to_luminance"),
    )
    connect_endpoint(add_value(luma, "in", None), upstream)
    add_value(luma, "lumacoeffs", coefficients)
    scalar = doc.addNodeInstance(
        core.library.getNodeDef("ND_extract_color3"),
        doc.createValidChildName("luminance_to_float"),
    )
    add_value(scalar, "in", None).setConnectedNode(luma)
    add_value(scalar, "index", 0)
    return scalar


def connected_node(port):
    """沿 MaterialX 输出引用找到真正上游节点；输出引用循环时阻断。"""
    seen = set()
    while port is not None:
        if port.getNamePath() in seen:
            raise ValidationError("输出包含循环引用", "MATERIALX_CYCLE")
        seen.add(port.getNamePath())
        output = port.getConnectedOutput()
        if output is None:
            return port.getConnectedNode()
        port = output
    return None


def require_defaults(node, supported):
    """未覆盖端口只允许官方默认值；连接不能被忽略。"""
    definition = node.getNodeDef()
    for port in node.getInputs():
        if port.getName() in supported:
            continue
        default = definition.getActiveInput(port.getName())
        if (
            connected_node(port) is not None
            or default is None
            or python_value(port) != python_value(default)
        ):
            raise ValidationError(
                f"目标桥接尚不能表达：{node.getNamePath()}.{port.getName()}",
                "MATERIALX_INPUT_UNSUPPORTED",
            )


def explicit_default_basis(node, visited, issues):
    """只合并官方世界空间第零基底；其他索引、空间或动态来源不能变成默认法线。"""
    supported = set()
    for name in ("tangent", "bitangent"):
        port = node.getInput(name)
        upstream = connected_node(port)
        definition = upstream.getNodeDef() if upstream is not None else None
        if definition is None or definition.getName() != "ND_" + name + "_vector3":
            continue
        space, space_source = _read_input(upstream, "space")
        index, index_source = _read_input(upstream, "index")
        if space != "world" or index != 0 or space_source is not None or index_source is not None:
            continue
        require_defaults(upstream, {"space", "index"})
        supported.add(name)
        visited.add(upstream.getNamePath())
    if supported:
        issues.append(
            issue(
                "NORMAL_DEFAULT_BASIS_EXPLICIT",
                "显式世界空间第零 UV 基底映射为目标默认基底；目标几何须使用对应默认 UV",
                node.getNamePath(),
                severity="warning",
            )
        )
    return supported


def scalar_broadcast(node):
    """只合并明确的标量复制；三个通道须来自同一端口或完全相同的常量。"""
    definition = node.getNodeDef()
    if definition is None:
        return None
    if definition.getName() == "ND_convert_float_color3":
        require_defaults(node, {"in"})
        value, upstream = _read_input(node, "in")
        return value, upstream, node.getInput("in")
    if definition.getName() == "ND_combine3_color3":
        require_defaults(node, {"in1", "in2", "in3"})
        values = []
        for key in ("in1", "in2", "in3"):
            value, upstream = _read_input(node, key)
            port = node.getInput(key)
            values.append((value, upstream, port))
        identities = [
            (
                value if upstream is None else None,
                upstream.getNamePath() if upstream else None,
                endpoint_output(port),
            )
            for value, upstream, port in values
        ]
        if identities[0] == identities[1] == identities[2]:
            return values[0]
    return None


def _luminance_source(node):
    """识别 extract(luminance) 结构，返回亮度节点；数值和额外端口在访问时校验。"""
    definition = node.getNodeDef()
    if definition is None or definition.getName() != "ND_extract_color3":
        return None
    upstream = connected_node(node.getInput("in"))
    upstream_definition = upstream.getNodeDef() if upstream is not None else None
    if upstream_definition and upstream_definition.getName() == "ND_luminance_color3":
        return upstream
    return None


def _read_binding(node, reverse):
    """查询单节点绑定，或将亮度加 R 分量提取解释为一个显式加权求和。"""
    if _luminance_source(node) is not None:
        return "color.weighted_sum", {"color": "in", "weights": "lumacoeffs"}, "value"
    definition = node.getNodeDef()
    return reverse.get(definition.getName()) if definition else None


def _read_input(node, name):
    """读取输入常量及上游节点；省略常量时使用官方 NodeDef 默认值。"""
    actual = node.getInput(name)
    declared = node.getNodeDef().getActiveInput(name)
    value = (
        python_value(actual)
        if actual is not None and actual.hasValueString()
        else python_value(declared)
    )
    return value, connected_node(actual)


def _unwrap_color_vector(upstream, visited):
    """还原显式 color3 → vector3 辅助节点，返回原始颜色值和连接来源。"""
    helper = upstream.getNodeDef()
    if helper is None or helper.getName() != "ND_convert_color3_vector3":
        raise ValidationError(
            "颜色输入需要显式 color3 → vector3 转换", "MATERIALX_NODE_UNSUPPORTED"
        )
    require_defaults(upstream, {"in"})
    visited.add(upstream.getNamePath())
    return _read_input(upstream, "in")


def to_semantic(core, doc, material_name, issues, bindings, scalar_inputs, transforms, preserve=()):
    """反向读取 MaterialX 可达图，返回既有字典能处理的语义数据；未知含义阻断。"""
    # 1. 拒绝尚不能解释的单位、前缀和色彩上下文。
    for element in doc.traverseTree():
        for attribute in ("fileprefix", "geomprefix", "unit", "unittype", "channels"):
            if element.getAttribute(attribute):
                raise ValidationError(
                    f"尚未处理的解释属性：{element.getNamePath()}.{attribute}",
                    "MATERIALX_CONTEXT_UNSUPPORTED",
                )
        if element.getAttribute("colorspace") not in {"", "lin_rec709"} and not (
            isinstance(element, mx.Input) and element.getType() == "filename"
        ):
            raise ValidationError(
                "仅接受线性 Rec.709 的计算上下文及图片文件上的显式色彩解释",
                "MATERIALX_CONTEXT_UNSUPPORTED",
            )
    # 2. 展开自定义子图并重新校验，然后选定唯一表面材质。
    core.flatten(doc, preserve=preserve)
    images = ImageGraphReader(core, doc, _read_input, require_defaults, add_value)
    expanded_issues = core.inspect(doc)
    if status_for(expanded_issues) == "error":
        raise ValidationError("子图展开后未通过校验", "MATERIALX_INVALID")
    materials = [n for n in doc.getNodes() if n.getCategory() == "surfacematerial"]
    if material_name is not None:
        materials = [n for n in materials if n.getName() == material_name]
    if len(materials) != 1:
        raise ValidationError("需要选择唯一的 surfacematerial", "MATERIALX_MATERIAL_SELECTION")
    material = materials[0]
    require_defaults(material, {"surfaceshader"})
    surface = connected_node(material.getInput("surfaceshader"))
    if surface is None:
        raise ValidationError("材质缺少表面连接", "MATERIALX_MATERIAL_SELECTION")
    data = {
        "schema_version": "1",
        "name": get_text_attribute(doc, "iris_material_name") or material.getName(),
        "renderer": {"id": "iris.semantic", "version": "1"},
        "nodes": [],
        "links": [],
        "textures": {},
        "metadata": {},
        "output": {"node": surface.getNamePath(), "socket": "surface"},
    }
    visited, pending = set(), [surface]
    reverse = {
        binding[0]: (semantic, binding[1], binding[2]) for semantic, binding in bindings.items()
    }

    def visit(node):
        """读取一个标准节点，将上游依赖加入 pending 队列供后续遍历。"""
        # 1. 识别受支持的节点或亮度链；共享上游只读取一次。
        name = node.getNamePath()
        if name in visited:
            return
        visited.add(name)
        image_info = images.read(node)
        binding = (
            ("texture.image", {"image": "file"}, "color")
            if image_info
            else _read_binding(node, reverse)
        )
        if binding is None:
            raise ValidationError(
                f"目标桥接尚不支持节点：{name} ({node.getCategory()})", "MATERIALX_NODE_UNSUPPORTED"
            )
        semantic, ports, output = binding
        # 2. 亮度链从 luminance 读系数，ID 沿用标量出口；只接受 index=0。
        input_node = _luminance_source(node)
        if input_node is not None:
            require_defaults(node, {"in"})
            visited.add(input_node.getNamePath())
        else:
            input_node = node
        supported = set(ports.values()) | ({"texcoord"} if semantic == "texture.image" else set())
        if semantic == "normal.tangent":
            supported |= explicit_default_basis(input_node, visited, issues)
        require_defaults(input_node, supported)
        record = {
            "id": name,
            "type": semantic,
            "inputs": {},
            "outputs": list(output.values()) if isinstance(output, dict) else [output],
            "metadata": {
                "editor": get_editor(input_node),
                "source_name": get_text_attribute(input_node, "iris_source_name")
                if input_node.hasAttribute("iris_source_name")
                else input_node.getName(),
            },
        }
        if image_info:
            record["outputs"] = list(image_info["outputs"])
            visited.update(image_info["helpers"])
        data["nodes"].append(record)
        # 3. 逐项读取常量、图片与连接，显式还原颜色转向量的辅助节点。
        for port_name, mx_name in ports.items():
            value, upstream = _read_input(input_node, mx_name)
            source_port = input_node.getInput(mx_name)
            if semantic == "texture.image":
                if upstream is not None or not value:
                    raise ValidationError(
                        "图片文件名必须为非空常量", "MATERIALX_RESOURCE_UNSUPPORTED"
                    )
                # 使用有效属性，包含继承的色彩解释；不猜测缺失的色彩空间。
                actual = input_node.getInput(mx_name)
                color = actual.getActiveColorSpace() if actual is not None else ""
                if color not in COLOR_FROM_MX:
                    raise ValidationError(
                        "贴图需要明确且已核验的色彩空间", "MATERIALX_COLORSPACE_UNSUPPORTED"
                    )
                resource_id = "image_" + str(len(data["textures"]))
                data["textures"][resource_id] = {
                    "uri": value,
                    "color_space": COLOR_FROM_MX[color],
                    "coordinates": import_coordinates(
                        input_node, visited, _read_input, require_defaults
                    ),
                    "metadata": {"image_alpha": image_info["description"]} if image_info else {},
                }
                if image_info:
                    alpha_warning(image_info["description"], COLOR_FROM_MX[color], issues, name)
                value = {"texture": resource_id}
            needs_color_vector = (semantic, port_name) == ("normal.tangent", "normal.color") or (
                (semantic, port_name) == ("color.weighted_sum", "color") and input_node == node
            )
            if needs_color_vector and upstream is not None:
                source_port = upstream.getInput("in")
                value, upstream = _unwrap_color_vector(upstream, visited)
            if semantic == "color.weighted_sum" and port_name == "weights" and upstream is not None:
                raise ValidationError("加权求和暂只支持常量系数", "MATERIALX_INPUT_UNSUPPORTED")
            if (semantic, port_name) in scalar_inputs and upstream is not None:
                broadcast = scalar_broadcast(upstream)
                if broadcast is not None:
                    visited.add(upstream.getNamePath())
                    scalar, upstream, source_port = broadcast
                    value = [scalar] * 3
                    if upstream is not None:
                        transforms.add((name, port_name))
            if value is None and upstream is None:
                continue
            record["inputs"][port_name] = value
            if upstream is not None:
                upstream, image_output = images.resolve(upstream)
                pending.append(upstream)
                upstream_binding = (
                    ("texture.image", {}, image_output)
                    if image_output
                    else _read_binding(upstream, reverse)
                )
                if upstream_binding is None:
                    raise ValidationError(
                        f"目标桥接尚不支持节点：{upstream.getNamePath()}",
                        "MATERIALX_NODE_UNSUPPORTED",
                    )
                output_socket = upstream_binding[2]
                if isinstance(output_socket, dict):
                    selected = endpoint_output(source_port)
                    if selected not in output_socket:
                        raise ValidationError(
                            "多输出节点必须选择实际输出端口", "MATERIALX_OUTPUT_UNSUPPORTED"
                        )
                    output_socket = output_socket[selected]
                data["links"].append(
                    {
                        "from": {"node": upstream.getNamePath(), "socket": output_socket},
                        "to": {"node": name, "socket": port_name},
                    }
                )
        # 4. 结构可以转换仍不代表着色外观一致，保留近似诊断。
        if semantic in {"surface.standard", "normal.tangent", "bump.height"}:
            issues.append(
                issue(
                    "SHADING_MODEL_APPROXIMATION",
                    "MaterialX 与原生实现的 BSDF、切线和高度响应尚无外观等价证明",
                    name,
                    severity="warning",
                )
            )

    # 3. 只遍历选中表面的上游节点；其余节点保留源文档并报告未使用。
    while pending:
        visit(pending.pop())
    for name, information in images.cache.items():
        if name in visited and information["used_alpha"] != any(
            link["from"] == {"node": name, "socket": "alpha"} for link in data["links"]
        ):
            raise ValidationError(
                "图片 Alpha 使用标记与可达连接不符", "MATERIALX_RESOURCE_UNSUPPORTED"
            )
    unused = [
        n.getNamePath() for n in doc.getNodes() if n != material and n.getNamePath() not in visited
    ]
    if unused:
        issues.append(
            issue(
                "MATERIALX_UNUSED_NODES",
                "未选用节点仅保留于源文档：" + ", ".join(unused),
                severity="info",
            )
        )
    return Material.parse(data).to_dict()


def convert_document(core, source, dictionary, target, material_name=None):
    """反向转换入口：解析和校验文档 → 提取语义图 → 编码目标原生材质。"""
    issues = []
    result = {"source": source, "intermediate": None, "material": None, "report": report(issues)}
    result["report"]["target_dictionary_fingerprint"] = dictionary.fingerprint
    result["report"]["dictionary_versions"] = {
        "source": "MaterialX/1.39.5",
        "target": dictionary.data["version"],
    }
    result["report"]["calibration"] = {"status": "not_requested", "model_id": None, "changes": []}
    try:
        # 1. 保存完整来源，检查文档结构，拒绝带阻断问题的输入。
        doc = core.read(source)
        archived_details = read_deferred_details(doc)
        if archived_details["records"]:
            issues.append(
                issue(
                    "MATERIALX_DETAILS_ARCHIVED",
                    "来源携带未应用细节存档；保留但不自动参与着色",
                    severity="warning",
                )
            )
        issues.extend(core.inspect(doc))
        if status_for(issues) == "error":
            return result
        if doc.getAttribute("iris_preservation_only") == "true":
            issues.append(
                issue(
                    "MATERIALX_PRESERVED_ONLY", "文档仅保存原生结构；须补齐语义和目标规则后才能转换"
                )
            )
            return result
        # 2. 提取可转换部分，再按目标字典生成原生参数与连接。
        recipe_errors = {}
        _, dictionary, recipe_types = prepare_recipes(
            core, dictionary, document=doc, deferred_errors=recipe_errors
        )
        extensions = load_extensions(core, dictionary)
        bindings = bindings_for(core, dictionary, extensions)
        import_functions(
            core, doc, dictionary, extensions, bindings, issues, material_name, recipe_errors
        )
        preserve = preserved_extensions(doc, extensions, bindings)
        preserve = apply_lowerings(core, doc, dictionary, extensions, bindings, preserve, issues)
        if adapt_target_image_graphs(
            core, doc, dictionary, _read_input, require_defaults, add_value, issues
        ):
            preserve = apply_lowerings(
                core, doc, dictionary, extensions, bindings, preserve, issues
            )
        scalar_inputs = set()
        for semantic_type in {rule["semantic"] for rule in dictionary.nodes.values()}:
            try:
                _, rule = dictionary.target_node(semantic_type)
            except ValidationError:
                # 未使用功能的歧义不应阻断当前图；实际用到时由 transcode 明确报告。
                continue
            scalar_inputs.update(
                (semantic_type, port["semantic"])
                for port in rule["inputs"].values()
                if port.get("transform") == "scalar_to_color"
            )
        transforms = set()
        semantic = to_semantic(
            core, doc, material_name, issues, bindings, scalar_inputs, transforms, preserve
        )
        check_target_coordinates(semantic, dictionary)
        semantic = prepare_target_coordinates(semantic, dictionary, issues)
        result["intermediate"] = deepcopy(semantic)
        check_target_alpha(semantic, dictionary)
        candidate = transcode(
            semantic,
            dictionary,
            encode=True,
            renderer=deepcopy(target),
            issues=issues,
            explicit_casts=transforms,
            explicit_transforms=transforms,
        )
        # 3. 最终协议通过校验后才标记为 converted；并不自动写入 DCC。
        if status_for(issues) != "error":
            for record in candidate["nodes"]:
                record["type"] = recipe_types.get(record["type"], record["type"])
            from ..tools.naming import normalize_names

            archived_details = collect_deferred_details(core, source, archived_details, issues)
            if archived_details["records"]:
                candidate.setdefault("metadata", {})["deferred_details"] = archived_details
            result["material"] = Material.parse(
                decorate_material(normalize_names(candidate), dictionary)
            ).to_dict()
            result["report"]["status"] = "converted"
    except (ValidationError, mx.Exception) as exc:
        issues.append(issue(getattr(exc, "code", "MATERIALX_INVALID"), str(exc)))
    return result
