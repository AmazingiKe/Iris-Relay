"""图片 Alpha 的标准运算与受限原生还原；不读取图片像素或执行着色。"""

from copy import deepcopy

import MaterialX as mx

from .conversion import issue
from .model import ValidationError

MODES = {"STRAIGHT", "PREMUL", "CHANNEL_PACKED", "NONE"}
IMAGE_TYPES = {1: "color3", 3: "color3", 4: "color4"}


def alpha_description(resource):
    """读取显式通道与 Alpha 解释；缺省保留旧 RGB 协议，未知解释阻断。"""
    value = resource.get("metadata", {}).get("image_alpha")
    if value is None:
        return None
    if (
        not isinstance(value, dict)
        or not {"mode", "channels"} <= set(value) <= {"mode", "channels", "association"}
        or not isinstance(value["mode"], str)
        or value["mode"] not in MODES
        or type(value["channels"]) is not int
        or value["channels"] not in IMAGE_TYPES
        or ("association" in value and value["association"] not in {"premultiplied", "independent"})
    ):
        raise ValidationError(
            "图片 Alpha 模式或文件通道描述不受支持", "MATERIALX_RESOURCE_UNSUPPORTED"
        )
    return value


def color_association(description, used_alpha, color_space):
    """返回文件颜色至当前逻辑颜色的 Alpha 次数；原生固定行为优先于 Blender 的使用规则。"""
    if description["channels"] != 4:
        return 0
    if "association" in description:
        return 1 if description["association"] == "premultiplied" else 0
    if color_space == "Raw":
        return 0
    if description["mode"] == "STRAIGHT" and not used_alpha:
        return 1
    if description["mode"] == "PREMUL" and used_alpha:
        return -1
    return 0


def prepare_target_images(material, dictionary, issues, library=None):
    """依据目标已验证的采样关联展开显式标准数学；原始材质与资源描述不变。"""
    result = deepcopy(material)
    behavior = dictionary.data.get("texture_alpha_behavior")
    used_ids = {n["id"] for n in result["nodes"]}
    if library is None:
        library = mx.createDocument()
        mx.loadLibraries(mx.getDefaultDataLibraryFolders(), mx.getDefaultDataSearchPath(), library)

    def node(owner, category, inputs):
        definition = library.getNodeDef("ND_" + category)
        name = owner + "__alpha_" + category
        while name in used_ids:
            name += "_"
        used_ids.add(name)
        values = {}
        for key, value in inputs.items():
            if isinstance(value, dict):
                result["links"].append({"from": value, "to": {"node": name, "socket": key}})
                default = definition.getActiveInput(key).getValue()
                values[key] = (
                    list(default) if isinstance(default, (mx.Color3, mx.Vector3)) else default
                )
            else:
                values[key] = value
        result["nodes"].append(
            {
                "id": name,
                "type": "materialx.ND_" + category,
                "inputs": values,
                "outputs": ["out"],
                "metadata": {"function_owner": owner, "role": "image_alpha"},
            }
        )
        return {"node": name, "socket": "out"}

    for record in list(result["nodes"]):
        if record["type"] != "texture.image":
            continue
        reference = record["inputs"].get("image")
        if reference is None:
            continue
        resource = result["textures"][reference["texture"]]
        description = alpha_description(resource)
        if description is None or description["channels"] != 4:
            continue
        if behavior is None and "association" not in description:
            continue
        resource_behavior = behavior
        if isinstance(behavior, dict):
            extension = resource["uri"].rsplit(".", 1)[-1].lower()
            image_format = resource.get("metadata", {}).get("image_format", extension)
            resource_behavior = behavior.get(image_format)
            if resource_behavior is None:
                raise ValidationError(
                    "目标尚未核验此 RGBA 文件格式的关联行为：" + image_format,
                    "MATERIALX_RESOURCE_UNSUPPORTED",
                )
        original_links = [e for e in result["links"] if e["from"]["node"] == record["id"]]
        alpha_links = [e for e in original_links if e["from"]["socket"] == "alpha"]
        color_links = [e for e in original_links if e["from"]["socket"] == "color"]
        desired = color_association(description, bool(alpha_links), resource["color_space"])
        actual = 1 if resource_behavior == "premultiplied" else 0
        normalized = {"mode": "CHANNEL_PACKED", "channels": 4}
        if actual:
            normalized["association"] = "premultiplied"
        resource["metadata"]["source_image_alpha"] = deepcopy(description)
        resource["metadata"]["image_alpha"] = normalized
        if "alpha" not in record["outputs"]:
            record["outputs"].append("alpha")
        alpha = {"node": record["id"], "socket": "alpha"}
        # NONE 的逻辑 Alpha 为 1，与采样器的实际第四通道分别表达。
        if description["mode"] == "NONE" and alpha_links:
            one = node(record["id"], "constant_float", {"value": 1.0})
            for edge in alpha_links:
                edge["from"] = deepcopy(one)
        difference = desired - actual
        if difference and color_links:
            safe_alpha = (
                node(
                    record["id"],
                    "ifequal_float",
                    {
                        "value1": alpha,
                        "value2": 0.0,
                        "in1": 1.0,
                        "in2": alpha,
                    },
                )
                if difference < 0
                else alpha
            )
            channels = []
            for index in range(3):
                endpoint = node(
                    record["id"],
                    "extract_color3",
                    {
                        "in": {"node": record["id"], "socket": "color"},
                        "index": index,
                    },
                )
                for _ in range(abs(difference)):
                    endpoint = node(
                        record["id"],
                        "multiply_float" if difference > 0 else "divide_float",
                        {
                            "in1": endpoint,
                            "in2": safe_alpha,
                        },
                    )
                channels.append(endpoint)
            color = node(
                record["id"],
                "combine3_color3",
                {
                    "in1": channels[0],
                    "in2": channels[1],
                    "in3": channels[2],
                },
            )
            for edge in color_links:
                edge["from"] = deepcopy(color)
        issues.append(
            issue(
                "IMAGE_NATIVE_ALPHA_EXPLICIT",
                "已按目标原生采样关联保留独立 Alpha 与显式颜色数学；"
                "解码量化和透明边缘过滤仍可能有差异，预乘解码已丢失的零 Alpha 隐藏 RGB 无法恢复",
                record["id"],
                severity="warning",
            )
        )
    return result


def alpha_warning(description, color_space, issues, name):
    """记录加载、颜色解释和过滤顺序的边界，不把运算节点当作渲染等价证明。"""
    if (
        description["channels"] == 4
        and description["mode"] in {"STRAIGHT", "PREMUL"}
        and color_space != "Raw"
    ):
        issues.append(
            issue(
                "ALPHA_FILTERING_APPROXIMATION",
                "标准图显式表达 Alpha 关联；宿主的预乘、颜色转换与过滤次序可能不同，"
                "透明边缘尚无外观等价证明",
                name,
                severity="warning",
            )
        )


def add_image_outputs(core, doc, node, description, used_alpha, color_space, add_value):
    """为图片建立标准颜色和 Alpha 出口；附加属性只用于严格核验后的原生合并。"""
    mode, channels = description["mode"], description["channels"]
    node.setAttribute("iris_alpha_mode", mode)
    node.setAttribute("iris_image_channels", str(channels))
    node.setAttribute("iris_alpha_used", "true" if used_alpha else "false")
    if "association" in description:
        node.setAttribute("iris_native_association", description["association"])
    else:
        node.removeAttribute("iris_native_association")

    def helper(definition, role, upstream=None):
        child = doc.addNodeInstance(
            core.library.getNodeDef(definition),
            doc.createValidChildName(node.getName() + "_" + role),
        )
        child.setAttribute("iris_image_source", node.getNamePath())
        child.setAttribute("iris_image_role", role)
        if upstream is not None:
            add_value(child, "in", None).setConnectedNode(upstream)
        return child

    # 1. 文件通道和原生 Alpha 模式共同决定颜色；不能把缺失 Alpha 当零。
    color = node
    exponent = color_association(description, used_alpha, color_space)
    association = "premult" if exponent > 0 else "unpremult" if exponent < 0 else None
    if association:
        if exponent < 0:
            # Cycles 在 Alpha=0 时保留颜色；标准 unpremult 直接除零，需要显式保护分母。
            alpha = helper("ND_extract_color4", "association_alpha", node)
            add_value(alpha, "index", 3)
            safe = helper("ND_ifequal_float", "association_safe_alpha")
            add_value(safe, "value1", None).setConnectedNode(alpha)
            add_value(safe, "value2", 0)
            add_value(safe, "in1", 1)
            add_value(safe, "in2", None).setConnectedNode(alpha)
            components = []
            for index in range(3):
                component = helper("ND_extract_color4", "association_channel_" + str(index), node)
                add_value(component, "index", index)
                components.append(component)
            protected = helper("ND_combine4_color4", "association_protected")
            for index, component in enumerate(components + [safe]):
                add_value(protected, "in" + str(index + 1), None).setConnectedNode(component)
            color = protected
        color = helper("ND_" + association + "_color4", "association", color)
    if channels == 1:
        # 颜色类型才能接受标准颜色变换；先取灰度的 R，再显式复制到 RGB。
        color = helper("ND_extract_color3", "gray", color)
        add_value(color, "index", 0)
        color = helper("ND_convert_float_color3", "color", color)
    elif channels == 4:
        color = helper("ND_convert_color4_color3", "color", color)
    outputs = {"color": color}
    # 2. 有效第四通道显式提取；忽略 Alpha 或无 Alpha 的文件显式输出 1。
    if used_alpha:
        if channels == 4 and mode != "NONE":
            alpha = helper("ND_extract_color4", "alpha", node)
            add_value(alpha, "index", 3)
        else:
            alpha = helper("ND_constant_float", "alpha")
            add_value(alpha, "value", 1.0)
        outputs["alpha"] = alpha
    return outputs


def adapt_target_image_graphs(
    core, doc, dictionary, read_input, require_defaults, add_value, issues
):
    """先严格核验图片组合，再建立目标采样组合及显式数学；后续统一组合展开覆盖新增节点。"""
    reader = ImageGraphReader(core, doc, read_input, require_defaults, add_value)
    changed = False
    for image in list(doc.getNodes()):
        if image.getCategory() != "image":
            continue
        information = reader.read(image)
        if information is None or information["description"]["channels"] != 4:
            continue
        if (
            not dictionary.data.get("texture_alpha_behavior")
            and "association" not in information["description"]
        ):
            continue
        name = image.getNamePath()
        # 计划只包含此图片和虚拟出口；全部数学仍使用与普通转换相同的标准语义。
        file = image.getInput("file") or image.getNodeDef().getActiveInput("file")
        space = "Raw" if file.getActiveColorSpace() == "raw" else "sRGB"
        links = [
            {"from": {"node": name, "socket": "color"}, "to": {"node": "__sink", "socket": "color"}}
        ]
        if information["used_alpha"]:
            links.append(
                {
                    "from": {"node": name, "socket": "alpha"},
                    "to": {"node": "__sink", "socket": "alpha"},
                }
            )
        plan = prepare_target_images(
            {
                "nodes": [
                    {
                        "id": name,
                        "type": "texture.image",
                        "inputs": {"image": {"texture": "image"}},
                        "outputs": list(information["outputs"]),
                        "metadata": {},
                    }
                ],
                "links": links,
                "textures": {
                    "image": {
                        "uri": file.getValueString(),
                        "color_space": space,
                        "metadata": {"image_alpha": information["description"]},
                    }
                },
            },
            dictionary,
            issues,
            core.library,
        )
        old_helpers = information["helpers"]
        old_endpoints = {
            endpoint.getNamePath(): key for key, endpoint in information["outputs"].items()
        }
        external_ports = []
        for element in doc.traverseTree():
            if not isinstance(element, (mx.Input, mx.Output)):
                continue
            parent = element.getParent()
            if parent.getNamePath() in old_helpers or parent == image:
                continue
            upstream = element.getConnectedNode()
            if upstream is not None and upstream.getNamePath() in old_endpoints:
                external_ports.append((element, old_endpoints[upstream.getNamePath()]))
        # 1. 目标原生采样行为作为可核验的图片组合保留，新增补偿在组合之外。
        used_alpha = any(e["from"] == {"node": name, "socket": "alpha"} for e in plan["links"])
        normalized = plan["textures"]["image"]["metadata"]["image_alpha"]
        native_outputs = add_image_outputs(
            core, doc, image, normalized, used_alpha, space, add_value
        )
        endpoints = {(name, key): value for key, value in native_outputs.items()}
        for record in plan["nodes"][1:]:
            definition = core.library.getNodeDef(record["type"].removeprefix("materialx."))
            created = doc.addNodeInstance(definition, doc.createValidChildName(record["id"]))
            for key, value in record["inputs"].items():
                add_value(created, key, value)
            endpoints[(record["id"], "out")] = created
        sinks = {}
        for edge in plan["links"]:
            origin = endpoints[(edge["from"]["node"], edge["from"]["socket"])]
            if edge["to"]["node"] == "__sink":
                sinks[edge["to"]["socket"]] = origin
            else:
                destination = endpoints[(edge["to"]["node"], "out")]
                port = destination.getInput(edge["to"]["socket"])
                port.removeAttribute("value")
                port.setConnectedNode(origin)
        # 2. 所有实际外部使用者都重连，包括共享分支；随后才删除旧的已核验组合。
        for port, key in external_ports:
            port.removeAttribute("value")
            port.setConnectedNode(sinks[key])
        for helper in old_helpers:
            doc.removeNode(helper)
        changed = True
    return changed


class ImageGraphReader:
    """核验带解释属性的图片组合；任何端口、连线或运算变更都不能被标记掩盖。"""

    def __init__(self, core, doc, read_input, require_defaults, add_value):
        self.core, self.doc = core, doc
        self.read_input, self.require_defaults, self.add_value = (
            read_input,
            require_defaults,
            add_value,
        )
        self.cache = {}

    def read(self, node):
        """返回图片主节点及其出口、辅助节点和解释；未声明 Alpha 的旧图返回 None。"""
        name = node.getNamePath()
        if name in self.cache:
            return self.cache[name]
        if not any(
            node.hasAttribute(a)
            for a in ("iris_alpha_mode", "iris_image_channels", "iris_alpha_used")
        ):
            return None
        raw = node.getAttribute("iris_image_channels")
        description = alpha_description(
            {
                "metadata": {
                    "image_alpha": {
                        "mode": node.getAttribute("iris_alpha_mode"),
                        "channels": int(raw) if raw in {"1", "3", "4"} else 0,
                    }
                }
            }
        )
        if node.hasAttribute("iris_native_association"):
            description["association"] = node.getAttribute("iris_native_association")
            alpha_description({"metadata": {"image_alpha": description}})
        used = node.getAttribute("iris_alpha_used")
        definition = node.getNodeDef()
        if (
            used not in {"true", "false"}
            or definition is None
            or definition.getName() != "ND_image_" + IMAGE_TYPES[description["channels"]]
        ):
            raise ValidationError("图片类型或 Alpha 使用标记不符", "MATERIALX_RESOURCE_UNSUPPORTED")
        file = node.getInput("file")
        color_space = "Raw" if file is not None and file.getActiveColorSpace() == "raw" else "sRGB"
        # 1. 按声明生成仅供比较的标准组合，实际值、连接仍从输入文档读取。
        expected = mx.createDocument()
        expected.setDataLibrary(self.core.library)
        origin = expected.addNodeInstance(definition, "image")
        outputs = add_image_outputs(
            self.core, expected, origin, description, used == "true", color_space, self.add_value
        )
        helpers = [n for n in self.doc.getNodes() if n.getAttribute("iris_image_source") == name]
        roles = {n.getAttribute("iris_image_role"): n for n in helpers}
        expected_helpers = [n for n in expected.getNodes() if n != origin]
        if len(roles) != len(helpers) or set(roles) != {
            n.getAttribute("iris_image_role") for n in expected_helpers
        }:
            raise ValidationError("图片辅助节点缺失、重复或多余", "MATERIALX_RESOURCE_UNSUPPORTED")
        for template in expected_helpers:
            actual = roles[template.getAttribute("iris_image_role")]
            if actual.getNodeDef() != template.getNodeDef():
                raise ValidationError("图片辅助节点的运算不符", "MATERIALX_RESOURCE_UNSUPPORTED")
            self.require_defaults(actual, {p.getName() for p in template.getInputs()})
            for port in template.getInputs():
                value, upstream = self.read_input(actual, port.getName())
                expected_upstream = port.getConnectedNode()
                if expected_upstream is not None:
                    target = (
                        node
                        if expected_upstream == origin
                        else roles[expected_upstream.getAttribute("iris_image_role")]
                    )
                    valid = upstream == target
                else:
                    valid = (
                        upstream is None and value == self.read_input(template, port.getName())[0]
                    )
                if not valid:
                    raise ValidationError(
                        "图片辅助节点的参数或连接不符", "MATERIALX_RESOURCE_UNSUPPORTED"
                    )
        result = {
            "description": description,
            "used_alpha": used == "true",
            "helpers": {n.getNamePath() for n in helpers},
            "outputs": {
                key: node if value == origin else roles[value.getAttribute("iris_image_role")]
                for key, value in outputs.items()
            },
        }
        self.cache[name] = result
        return result

    def resolve(self, node):
        """把已核验的颜色／Alpha 出口还原为同一图片；中间运算不能冒充出口。"""
        owner = node.getAttribute("iris_image_source")
        primary = self.doc.getDescendant(owner) if owner else node
        if not isinstance(primary, mx.Node):
            raise ValidationError("图片辅助节点引用的来源不存在", "MATERIALX_RESOURCE_UNSUPPORTED")
        result = self.read(primary)
        if result is None:
            if owner:
                raise ValidationError("图片来源缺少 Alpha 解释", "MATERIALX_RESOURCE_UNSUPPORTED")
            return node, None
        for output, endpoint in result["outputs"].items():
            if node == endpoint:
                return primary, output
        raise ValidationError("图片内部运算不能替代原生输出", "MATERIALX_OUTPUT_UNSUPPORTED")


def check_target_alpha(material, dictionary):
    """目标必须声明图片模式读写能力；没有证据时不静默忽略资源解释。"""
    supported = dictionary.data.get("texture_alpha_modes", [])
    for resource in material["textures"].values():
        description = alpha_description(resource)
        channels = dictionary.data.get("texture_image_channels")
        if (
            description is not None
            and channels is not None
            and description["channels"] not in channels
        ):
            raise ValidationError(
                "目标尚未核验图片通道数：" + str(description["channels"]),
                "MATERIALX_RESOURCE_UNSUPPORTED",
            )
        if description is not None and description["mode"] not in supported:
            raise ValidationError(
                "目标未声明该图片 Alpha 模式的原生读写能力", "MATERIALX_RESOURCE_UNSUPPORTED"
            )
