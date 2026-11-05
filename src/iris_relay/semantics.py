"""版本化的通用节点功能契约；不依赖宿主、不含原生 ID 或着色实现。"""

from copy import deepcopy

from .model import ValidationError


def port(kind, description, *, required=False, nullable=False):
    return {"type": kind, "description": description, "required": required, "nullable": nullable}


_NODES = {
    "channel.red": {
        "description": "显式提取 RGB 的 R 分量，供标量高度输入使用。",
        "inputs": {"color": port("color", "待提取的 RGB 数据。", required=True)},
        "outputs": {"value": port("float", "原样输出 R 分量，不计算亮度。", required=True)},
        "limitations": ["当前仅支持 R 通道；其他通道连接须报告，不猜测替代。"],
        "standard_references": ["MaterialX extract，index=0"],
    },
    "texture.image": {
        "description": "从图片资源采样；用途由图连接决定，不根据文件名猜测。",
        "inputs": {
            "image": port("texture", "资源引用，包含 URI、色彩解释及坐标描述。", required=True)
        },
        "outputs": {
            "color": port("color", "采样颜色或三通道数据。", required=True),
            "alpha": port("float", "图片 Alpha，不等于亮度或高度。"),
        },
        "limitations": [
            "当前原生实现限定外部单张图片与默认 UV。",
            "数据贴图需保留 Raw 等色彩解释，不修改场景色彩管理。",
        ],
        "standard_references": ["MaterialX image", "USD UsdUVTexture"],
    },
    "normal.tangent": {
        "description": "将切线空间编码的三通道法线数据解码为着色法线。",
        "inputs": {
            "normal.color": port("color", "默认 OpenGL 方向约定的编码法线。", required=True),
            "normal.strength": port("float", "原生法线强度；跨实现响应不保证相同。", required=True),
        },
        "outputs": {"normal": port("vector", "着色法线，非图片颜色。", required=True)},
        "limitations": ["依赖 UV、切线基与几何体；强度及方向需要渲染对照。"],
        "standard_references": ["MaterialX normalmap"],
    },
    "bump.height": {
        "description": "根据标量高度场的空间变化扰动法线，不改变几何体。",
        "inputs": {
            "height": port("float", "标量高度信号；常量不能证明空间凹凸效果。", required=True),
            "height.scale": port(
                "float", "原生高度幅度，单位与导数实现需单独核验。", required=True
            ),
            "normal": port("vector", "可选基础法线；空值表示宿主隐式法线。", nullable=True),
        },
        "outputs": {"normal": port("vector", "扰动后的着色法线。", required=True)},
        "limitations": [
            "高度幅度只做带警告的基础映射，不宣称物理单位或外观等价。",
            "图片颜色到标量高度需要明确的通道提取，不隐式取平均。",
        ],
        "standard_references": ["MaterialX bump"],
    },
    "surface.standard": {
        "description": "既有标准表面基础语义，跨 BSDF 仅近似对应。",
        "inputs": {},
        "outputs": {"surface": port("shader", "表面着色闭包。", required=True)},
        "limitations": ["不同 BSDF 及层间能量分配不能通过同名参数证明等价。"],
        "standard_references": [],
    },
}

for _name, _kind in {
    "base.color": "color",
    "metalness": "float",
    "reflection.roughness": "float",
    "reflection.ior": "float",
    "reflection.weight": "float",
    "reflection.tint": "color",
    "reflection.anisotropy": "float",
    "reflection.rotation": "float",
    "diffuse.roughness": "float",
    "coat.weight": "float",
    "coat.roughness": "float",
    "coat.ior": "float",
    "coat.tint": "color",
    "coat.normal": "vector",
    "normal": "vector",
    "emission.color": "color",
    "emission.weight": "float",
    "sheen.weight": "float",
    "sheen.roughness": "float",
    "sheen.tint": "color",
    "opacity": "color",
}.items():
    _NODES["surface.standard"]["inputs"][_name] = port(
        _kind, "标准表面基础属性：" + _name, nullable=_kind == "vector"
    )


def describe_semantics():
    return {
        "version": "1",
        "nodes": deepcopy(_NODES),
        "standard_compatibility": "参考标准的功能划分，尚未实现 USD/MaterialX 文件导入导出。",
    }


def validate_contract(node):
    semantic = node["semantic"]
    if semantic not in _NODES:
        raise ValidationError(f"未定义的通用功能：{semantic}", "UNKNOWN_SEMANTIC")
    definition = _NODES[semantic]
    for direction in ("inputs", "outputs"):
        supplied = set()
        for rule in node[direction].values():
            name = rule["semantic"]
            if name.startswith("native.") and direction == "inputs":
                if not rule.get("omit_if_default"):
                    raise ValidationError("原生专用参数必须明确默认省略边界")
                continue
            expected = definition[direction].get(name)
            if expected is None:
                raise ValidationError(f"通用功能 {semantic} 未定义端口：{name}")
            kind = "color" if rule.get("transform") == "scalar_to_color" else rule["type"]
            if kind != expected["type"]:
                raise ValidationError(f"通用端口类型不一致：{semantic}.{name}")
            if rule.get("nullable") and not expected["nullable"]:
                raise ValidationError(f"通用端口不允许空值：{semantic}.{name}")
            supplied.add(name)
        missing = {k for k, v in definition[direction].items() if v["required"]} - supplied
        if missing:
            raise ValidationError(f"通用功能 {semantic} 缺少必要端口：{sorted(missing)}")
