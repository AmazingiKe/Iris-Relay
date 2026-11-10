"""图片 Alpha 的标准运算与受限原生还原；不读取图片像素或执行着色。"""

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
        or set(value) != {"mode", "channels"}
        or not isinstance(value["mode"], str)
        or value["mode"] not in MODES
        or type(value["channels"]) is not int
        or value["channels"] not in IMAGE_TYPES
    ):
        raise ValidationError(
            "图片 Alpha 模式或文件通道描述不受支持", "MATERIALX_RESOURCE_UNSUPPORTED"
        )
    return value


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
    association = None
    if channels == 4 and color_space != "Raw":
        if mode == "STRAIGHT" and not used_alpha:
            association = "premult"
        elif mode == "PREMUL" and used_alpha:
            association = "unpremult"
    if association:
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
        if description is not None and description["mode"] not in supported:
            raise ValidationError(
                "目标未声明该图片 Alpha 模式的原生读写能力", "MATERIALX_RESOURCE_UNSUPPORTED"
            )
