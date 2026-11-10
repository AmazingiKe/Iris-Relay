"""图片 UV 的标准节点表示及目标能力检查；不依赖宿主，也不改变几何体。"""

import math
from copy import deepcopy

from .model import ValidationError


def parse_coordinates(value):
    """检查默认／命名 UV 和按应用顺序排列的 MaterialX 行向量矩阵，拒绝未知解释。"""
    if (
        not isinstance(value, dict)
        or set(value) - {"kind", "set", "transforms"}
        or value.get("kind") != "uv"
        or not isinstance(value.get("set"), str)
    ):
        raise ValidationError("尚不支持该坐标描述", "MATERIALX_RESOURCE_UNSUPPORTED")
    matrices = value.get("transforms", [])
    if not isinstance(matrices, list) or len(matrices) > 64:
        raise ValidationError("坐标矩阵须为至多 64 项的列表", "MATERIALX_RESOURCE_UNSUPPORTED")
    for matrix in matrices:
        if (
            not isinstance(matrix, list)
            or len(matrix) != 16
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in matrix)
            or any(matrix[i] != 0 for i in (3, 7, 11))
            or matrix[15] != 1
        ):
            raise ValidationError(
                "仅支持有限的仿射 matrix44 坐标", "MATERIALX_RESOURCE_UNSUPPORTED"
            )
    result = {"kind": "uv", "set": value["set"]}
    if matrices:
        result["transforms"] = deepcopy(matrices)
    return result


def check_target_coordinates(data, dictionary):
    """目标须明确声明命名 UV 和非退化 TRS 能力；未声明时仅允许原有默认 UV。"""
    capability = dictionary.data.get("texture_coordinates", {})
    for resource in data["textures"].values():
        coordinates = parse_coordinates(resource["coordinates"])
        if coordinates["set"] and not capability.get("named_uv", False):
            raise ValidationError("目标尚未支持命名 UV 集", "MATERIALX_RESOURCE_UNSUPPORTED")
        matrices = coordinates.get("transforms", [])
        if matrices and capability.get("transforms", "none") != "trs":
            raise ValidationError("目标尚未支持贴图坐标矩阵", "MATERIALX_RESOURCE_UNSUPPORTED")
        for matrix in matrices:
            axes = [matrix[i : i + 3] for i in (0, 4, 8)]
            lengths = [math.sqrt(sum(v * v for v in axis)) for axis in axes]
            if any(length < 1e-12 for length in lengths) or any(
                abs(sum(a * b for a, b in zip(axes[i], axes[j], strict=True)))
                > 2e-5 * lengths[i] * lengths[j]
                for i, j in ((0, 1), (0, 2), (1, 2))
            ):
                raise ValidationError(
                    "目标 TRS 不接受剪切或退化矩阵", "MATERIALX_RESOURCE_UNSUPPORTED"
                )


def export_coordinates(core, doc, image, coordinates, add_value):
    """将 UV 来源和逐步矩阵写为官方节点；默认 UV 保留图片隐式坐标以兼容旧文件。"""
    coordinates = parse_coordinates(coordinates)
    if coordinates == {"kind": "uv", "set": ""}:
        return

    def create(definition, suffix):
        return doc.addNodeInstance(
            core.library.getNodeDef(definition), doc.createValidChildName(image.getName() + suffix)
        )

    # 1. 命名几何属性保持名称；只有默认 UV 才使用 index=0。
    named = bool(coordinates["set"])
    node = create("ND_geompropvalue_vector2" if named else "ND_texcoord_vector2", "_uv")
    add_value(node, "geomprop" if named else "index", coordinates["set"] if named else 0)
    matrices = coordinates.get("transforms", [])
    # 2. 每个矩阵保留为一个节点，避免多次非均匀变换合并后引入不可还原的剪切。
    if matrices:
        expanded = create("ND_convert_vector2_vector3", "_uv3")
        add_value(expanded, "in", None).setConnectedNode(node)
        node = expanded
        for matrix in matrices:
            transformed = create("ND_transformmatrix_vector3M4", "_mapping")
            add_value(transformed, "in", None).setConnectedNode(node)
            add_value(transformed, "mat", matrix)
            node = transformed
        reduced = create("ND_convert_vector3_vector2", "_uv2")
        add_value(reduced, "in", None).setConnectedNode(node)
        node = reduced
    add_value(image, "texcoord", None).setConnectedNode(node)


def import_coordinates(image, visited, read_input, require_defaults):
    """读取显式 UV／静态矩阵链；动态矩阵、未知来源和其他 UV 索引仍阻断。"""
    value, node = read_input(image, "texcoord")
    if node is None:
        if value is not None:
            raise ValidationError("尚不支持常量图片坐标", "MATERIALX_RESOURCE_UNSUPPORTED")
        return {"kind": "uv", "set": ""}
    matrices = []

    def name(current):
        definition = current.getNodeDef() if current is not None else None
        return definition.getName() if definition else ""

    def consume(current, ports):
        require_defaults(current, ports)
        visited.add(current.getNamePath())

    # 1. 仅把明确的 2D → 3D → 矩阵序列 → 2D 解释为图片坐标变换。
    if name(node) == "ND_convert_vector3_vector2":
        consume(node, {"in"})
        _, node = read_input(node, "in")
        while name(node) == "ND_transformmatrix_vector3M4":
            consume(node, {"in", "mat"})
            matrix, dynamic = read_input(node, "mat")
            if dynamic is not None or len(matrices) >= 64:
                raise ValidationError(
                    "坐标矩阵必须为有限层数的常量", "MATERIALX_RESOURCE_UNSUPPORTED"
                )
            matrices.append(matrix)
            _, node = read_input(node, "in")
        if name(node) != "ND_convert_vector2_vector3":
            raise ValidationError("坐标矩阵缺少明确的 UV 来源", "MATERIALX_RESOURCE_UNSUPPORTED")
        consume(node, {"in"})
        _, node = read_input(node, "in")
    # 2. 命名属性的回退值必须保持官方默认；不把非零索引猜成某个宿主名称。
    if name(node) == "ND_geompropvalue_vector2":
        consume(node, {"geomprop"})
        uv_name, dynamic = read_input(node, "geomprop")
        if dynamic is not None or not uv_name:
            raise ValidationError("命名 UV 需要非空常量名称", "MATERIALX_RESOURCE_UNSUPPORTED")
    elif name(node) == "ND_texcoord_vector2":
        consume(node, set())
        uv_name = ""
    else:
        raise ValidationError("尚不支持该图片坐标节点链", "MATERIALX_RESOURCE_UNSUPPORTED")
    result = {"kind": "uv", "set": uv_name}
    if matrices:
        result["transforms"] = list(reversed(matrices))
    return parse_coordinates(result)
