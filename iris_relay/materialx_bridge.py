"""既有原生字典与 MaterialX 的受限桥接；节点定义来自官方库。"""

from copy import deepcopy

import MaterialX as mx

from .conversion import issue, status_for, transcode
from .model import Material, ValidationError

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
}
COLOR_TO_MX = {"Raw": "raw", "sRGB": "srgb_texture"}
COLOR_FROM_MX = {v: k for k, v in COLOR_TO_MX.items()}


def value_string(value):
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def python_value(port):
    if not port.hasValueString():
        return None
    value = port.getValue()
    if isinstance(value, (str, float, int, bool)):
        return value
    return list(value)


def add_value(node, name, value):
    definition = node.getNodeDef().getActiveInput(name)
    port = node.addInput(name, definition.getType())
    if value is not None:
        port.setValueString(value_string(value))
    return port


def report(issues):
    return {
        "status": "blocked",
        "issues": issues,
        "appearance_verified": False,
        "materialx_version": mx.getVersionString(),
    }


def explicit_color_casts(source, dictionary):
    """只接受字典声明的宿主隐式转换；系数须由宿主读取实际色彩上下文。"""
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


def export_material(core, source, dictionary):
    issues = []
    result = {"source": source.to_dict(), "document": None, "report": report(issues)}
    result["report"]["source_dictionary_fingerprint"] = dictionary.fingerprint
    try:
        casts, coefficients = explicit_color_casts(source.data, dictionary)
    except ValidationError as exc:
        issues.append(issue(exc.code, str(exc)))
        return result
    semantic = transcode(
        source.to_dict(),
        dictionary,
        encode=False,
        renderer={"id": "iris.semantic", "version": "1"},
        issues=issues,
        explicit_casts=casts,
    )
    if status_for(issues) == "error":
        return result
    try:
        doc = mx.createDocument()
        doc.setDataLibrary(core.library)
        doc.setAttribute("iris_material_name", source.data["name"])
        nodes, input_ports, output_ports = {}, {}, {}
        used = {(link["from"]["node"], link["from"]["socket"]) for link in semantic["links"]}
        used.add((semantic["output"]["node"], semantic["output"]["socket"]))
        for record in semantic["nodes"]:
            binding = BINDINGS.get(record["type"])
            if binding is None:
                raise ValidationError(
                    f"尚无 MaterialX 对应：{record['type']}", "MATERIALX_NODE_UNSUPPORTED"
                )
            definition, inputs, output = binding
            name = doc.createValidChildName(mx.createValidName(record["id"]))
            node = doc.addNodeInstance(core.library.getNodeDef(definition), name)
            node.setAttribute("iris_source_name", record["id"])
            nodes[record["id"]] = node
            output_ports[(record["id"], output)] = node
            for port in record["outputs"]:
                if port != output and (record["id"], port) in used:
                    raise ValidationError(
                        f"尚不支持输出：{record['id']}.{port}", "MATERIALX_OUTPUT_UNSUPPORTED"
                    )
            for port, value in record["inputs"].items():
                if port not in inputs:
                    raise ValidationError(
                        f"尚不支持输入：{record['id']}.{port}", "MATERIALX_INPUT_UNSUPPORTED"
                    )
                if record["type"] == "texture.image":
                    if value is None:
                        raise ValidationError("图片节点缺少资源", "MATERIALX_RESOURCE_UNSUPPORTED")
                    resource = semantic["textures"][value["texture"]]
                    if resource["coordinates"] != {"kind": "uv", "set": ""}:
                        raise ValidationError(
                            "首版桥接仅支持默认 UV", "MATERIALX_RESOURCE_UNSUPPORTED"
                        )
                    color = COLOR_TO_MX.get(resource["color_space"])
                    if color is None:
                        raise ValidationError(
                            "尚未核验该贴图色彩空间", "MATERIALX_COLORSPACE_UNSUPPORTED"
                        )
                    target = add_value(node, "file", resource["uri"])
                    target.setColorSpace(color)
                    input_ports[(record["id"], port)] = target
                elif record["type"] == "normal.tangent" and port == "normal.color":
                    helper = doc.addNodeInstance(
                        core.library.getNodeDef("ND_convert_color3_vector3"),
                        doc.createValidChildName(name + "_color_to_vector"),
                    )
                    input_ports[(record["id"], port)] = add_value(helper, "in", value)
                    add_value(node, "in", None).setConnectedNode(helper)
                elif value is not None:
                    input_ports[(record["id"], port)] = add_value(node, inputs[port], value)
                else:
                    # 缺省几何法线不写成零向量，保留 MaterialX 的几何默认语义。
                    input_ports[(record["id"], port)] = None
        for original, link in zip(source.data["links"], semantic["links"], strict=True):
            target_key = (link["to"]["node"], link["to"]["socket"])
            port = input_ports[target_key]
            if port is None:
                record = next(n for n in semantic["nodes"] if n["id"] == target_key[0])
                port = add_value(
                    nodes[target_key[0]], BINDINGS[record["type"]][1][target_key[1]], None
                )
            port.removeAttribute("value")
            upstream = output_ports[(link["from"]["node"], link["from"]["socket"])]
            if (original["to"]["node"], original["to"]["socket"]) in casts:
                luma = doc.addNodeInstance(
                    core.library.getNodeDef("ND_luminance_color3"),
                    doc.createValidChildName("color_to_luminance"),
                )
                add_value(luma, "in", None).setConnectedNode(upstream)
                add_value(luma, "lumacoeffs", coefficients)
                upstream = doc.addNodeInstance(
                    core.library.getNodeDef("ND_extract_color3"),
                    doc.createValidChildName("luminance_to_float"),
                )
                add_value(upstream, "in", None).setConnectedNode(luma)
                add_value(upstream, "index", 0)
                issues.append(
                    issue(
                        "IMPLICIT_CAST_EXPLICIT",
                        "已将宿主颜色转标量连接展开为显式亮度计算",
                        original["to"]["node"],
                        original["to"]["socket"],
                        "info",
                    )
                )
            port.setConnectedNode(upstream)
        material = doc.addNodeInstance(
            core.library.getNodeDef("ND_surfacematerial"), doc.createValidChildName("material")
        )
        add_value(material, "surfaceshader", None).setConnectedNode(
            output_ports[(semantic["output"]["node"], semantic["output"]["socket"])]
        )
        issues.extend(core.inspect(doc))
        if status_for(issues) != "error":
            result["document"] = mx.writeToXmlString(doc)
            result["report"]["status"] = "exported"
    except (ValidationError, mx.Exception) as exc:
        issues.append(issue(getattr(exc, "code", "MATERIALX_INVALID"), str(exc)))
    return result


def connected_node(port):
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


def to_semantic(core, doc, material_name, issues):
    for element in doc.traverseTree():
        for attribute in ("fileprefix", "geomprefix", "unit", "unittype", "channels"):
            if element.getAttribute(attribute):
                raise ValidationError(
                    f"尚未处理的解释属性：{element.getNamePath()}.{attribute}",
                    "MATERIALX_CONTEXT_UNSUPPORTED",
                )
        if element.getAttribute("colorspace") and not (
            isinstance(element, mx.Input) and element.getType() == "filename"
        ):
            raise ValidationError(
                "首版桥接仅接受图片文件输入上的色彩空间", "MATERIALX_CONTEXT_UNSUPPORTED"
            )
    core.flatten(doc)
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
        "name": doc.getAttribute("iris_material_name") or material.getName(),
        "renderer": {"id": "iris.semantic", "version": "1"},
        "nodes": [],
        "links": [],
        "textures": {},
        "metadata": {},
        "output": {"node": surface.getNamePath(), "socket": "surface"},
    }
    visited, pending = set(), [surface]
    reverse = {
        binding[0]: (semantic, binding[1], binding[2]) for semantic, binding in BINDINGS.items()
    }

    def visit(node):
        name = node.getNamePath()
        if name in visited:
            return
        visited.add(name)
        definition = node.getNodeDef()
        binding = reverse.get(definition.getName()) if definition else None
        if binding is None:
            raise ValidationError(
                f"目标桥接尚不支持节点：{name} ({node.getCategory()})", "MATERIALX_NODE_UNSUPPORTED"
            )
        semantic, ports, output = binding
        require_defaults(node, set(ports.values()))
        record = {"id": name, "type": semantic, "inputs": {}, "outputs": [output], "metadata": {}}
        data["nodes"].append(record)
        for port_name, mx_name in ports.items():
            declared = definition.getActiveInput(mx_name)
            actual = node.getInput(mx_name)
            value = (
                python_value(actual)
                if actual is not None and actual.hasValueString()
                else python_value(declared)
            )
            upstream = connected_node(actual)
            if semantic == "texture.image":
                if upstream is not None or not value:
                    raise ValidationError(
                        "图片文件名必须为非空常量", "MATERIALX_RESOURCE_UNSUPPORTED"
                    )
                # 使用有效属性，包含继承的色彩解释；不猜测缺失的色彩空间。
                color = actual.getActiveColorSpace() if actual is not None else ""
                if color not in COLOR_FROM_MX:
                    raise ValidationError(
                        "贴图需要明确且已核验的色彩空间", "MATERIALX_COLORSPACE_UNSUPPORTED"
                    )
                resource_id = "image_" + str(len(data["textures"]))
                data["textures"][resource_id] = {
                    "uri": value,
                    "color_space": COLOR_FROM_MX[color],
                    "coordinates": {"kind": "uv", "set": ""},
                    "metadata": {},
                }
                value = {"texture": resource_id}
            if (
                semantic == "normal.tangent"
                and port_name == "normal.color"
                and upstream is not None
            ):
                helper = upstream.getNodeDef()
                if helper is None or helper.getName() != "ND_convert_color3_vector3":
                    raise ValidationError(
                        "法线颜色需要显式 color3 → vector3 转换", "MATERIALX_NODE_UNSUPPORTED"
                    )
                visited.add(upstream.getNamePath())
                require_defaults(upstream, {"in"})
                actual = upstream.getInput("in")
                declared = helper.getActiveInput("in")
                value = (
                    python_value(actual)
                    if actual is not None and actual.hasValueString()
                    else python_value(declared)
                )
                upstream = connected_node(actual)
            if value is None and upstream is None:
                continue
            record["inputs"][port_name] = value
            if upstream is not None:
                pending.append(upstream)
                upstream_definition = upstream.getNodeDef()
                upstream_binding = (
                    reverse.get(upstream_definition.getName()) if upstream_definition else None
                )
                if upstream_binding is None:
                    raise ValidationError(
                        f"目标桥接尚不支持节点：{upstream.getNamePath()}",
                        "MATERIALX_NODE_UNSUPPORTED",
                    )
                data["links"].append(
                    {
                        "from": {"node": upstream.getNamePath(), "socket": upstream_binding[2]},
                        "to": {"node": name, "socket": port_name},
                    }
                )
        if semantic in {"surface.standard", "normal.tangent", "bump.height"}:
            issues.append(
                issue(
                    "SHADING_MODEL_APPROXIMATION",
                    "MaterialX 与原生实现的 BSDF、切线和高度响应尚无外观等价证明",
                    name,
                    severity="warning",
                )
            )

    while pending:
        visit(pending.pop())
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
    issues = []
    result = {"source": source, "material": None, "report": report(issues)}
    result["report"]["target_dictionary_fingerprint"] = dictionary.fingerprint
    try:
        doc = core.read(source)
        issues.extend(core.inspect(doc))
        if status_for(issues) == "error":
            return result
        semantic = to_semantic(core, doc, material_name, issues)
        candidate = transcode(
            semantic, dictionary, encode=True, renderer=deepcopy(target), issues=issues
        )
        if status_for(issues) != "error":
            result["material"] = Material.parse(candidate).to_dict()
            result["report"]["status"] = "converted"
    except (ValidationError, mx.Exception) as exc:
        issues.append(issue(getattr(exc, "code", "MATERIALX_INVALID"), str(exc)))
    return result
