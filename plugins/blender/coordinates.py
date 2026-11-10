"""读取 Blender 静态 UV 表达式为逐步仿射变换；不冻结动画或求值任意着色图。"""

import math
from dataclasses import dataclass

from iris_relay.model import ValidationError

IDENTITY = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]


def scalar(value):
    """只接受有限数值常量，不把向量或颜色猜作标量。"""
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValidationError("坐标参数需要有限标量常量", "UV_UNSUPPORTED")
    return value


def vector(value):
    """按已核验的原生 float→vector 广播；其他隐式转换仍须显式处理。"""
    if type(value) in (int, float):
        return [scalar(value)] * 3
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValidationError("坐标参数需要三分量向量常量", "UV_UNSUPPORTED")
    return [scalar(v) for v in value]


def linear(scale, offset=(0, 0, 0)):
    """生成行向量约定的逐分量缩放与平移，不合并相邻步骤。"""
    result = list(IDENTITY)
    for index in range(3):
        result[index * 5] = scale[index]
        result[12 + index] = offset[index]
    return result


def axis_rotation(axis, angle):
    """按原生轴角含义计算旋转；零轴保持输入不变，非单位轴先归一化。"""
    length = math.hypot(*axis)
    if not length:
        return list(IDENTITY)
    x, y, z = [v / length for v in axis]
    c, s, t = math.cos(angle), math.sin(angle), 1 - math.cos(angle)
    return [
        t * x * x + c,
        t * x * y + s * z,
        t * x * z - s * y,
        0,
        t * x * y - s * z,
        t * y * y + c,
        t * y * z + s * x,
        0,
        t * x * z + s * y,
        t * y * z - s * x,
        t * z * z + c,
        0,
        0,
        0,
        0,
        1,
    ]


def multiply(left, right):
    """只组合一个原生旋转节点的内部欧拉步骤，保持外部 UV 操作顺序。"""
    return [
        sum(left[row * 4 + k] * right[k * 4 + col] for k in range(4))
        for row in range(4)
        for col in range(4)
    ]


@dataclass
class _UV:
    name: str
    transforms: list


class _Reader:
    """缓存已解析的静态分支，检测计算环与规模；仅支持明确列出的坐标运算。"""

    def __init__(self, structural):
        self.structural, self.cache, self.active = structural, {}, set()

    @staticmethod
    def static(node):
        """坐标参数所在树及组实例祖先有动画、驱动或 NLA 时不冻结当前帧。"""
        scope = node.scope
        while scope:
            animation = getattr(scope.tree, "animation_data", None)
            if animation and (animation.action or animation.drivers or animation.nla_tracks):
                raise ValidationError(
                    f"坐标所在节点树含动画或驱动：{scope.tree.name}", "UV_DYNAMIC_UNSUPPORTED"
                )
            scope = scope.parent.scope if scope.parent else None

    def input(self, socket):
        """取得字面常量或显式 UV 表达式；向量字面量始终保留为常量。"""
        self.static(socket.node)
        source, value = socket.node.graph.resolve(socket, literal_defaults=True)
        result = self.output(source) if source else value
        if isinstance(result, _UV):
            if socket.type != "VECTOR":
                raise ValidationError("不能将 UV 动态场折叠成标量", "UV_UNSUPPORTED")
            return result
        return vector(result) if socket.type == "VECTOR" else scalar(result)

    def output(self, socket):
        """按实际节点与输出解析，限制递归深度及总量，未知节点不能被旁路。"""
        node, key = socket.node, (socket.node.name, socket.identifier)
        if key in self.active:
            raise ValidationError("坐标表达式包含循环", "INVALID_LINK")
        if key in self.cache:
            return self.cache[key]
        if len(self.active) >= 64 or len(self.cache) >= 20000:
            raise ValidationError("坐标表达式超过深度或节点数量限制", "UV_UNSUPPORTED")
        self.static(node)
        self.active.add(key)
        self.structural.add(node.name)
        try:
            value = self.calculate(node, socket)
            self.cache[key] = value
            return value
        finally:
            self.active.remove(key)

    def calculate(self, node, socket):
        """识别已核验 UV 来源、常量表达式和仿射节点，不按组名推断功能。"""
        kind, port = node.bl_idname, socket.identifier
        if kind in {"ShaderNodeTexCoord", "ShaderNodeUVMap"} and port == "UV":
            if node.from_instancer:
                raise ValidationError("尚不支持实例 UV", "UV_UNSUPPORTED")
            return _UV(getattr(node, "uv_map", ""), [])
        if kind == "ShaderNodeValue" and port == "Value":
            return scalar(socket.default_value)
        if kind == "ShaderNodeCombineXYZ" and port == "Vector":
            return [scalar(self.input(node.inputs[p])) for p in ("X", "Y", "Z")]
        if kind == "ShaderNodeMath" and port == "Value":
            return self.math(node)
        if kind == "ShaderNodeVectorMath" and port == "Vector":
            return self.vector_math(node)
        if kind == "ShaderNodeVectorRotate" and port == "Vector":
            return self.rotate(node)
        if kind == "ShaderNodeMapping" and port == "Vector" and node.vector_type == "POINT":
            return self.mapping(node)
        raise ValidationError(f"坐标链暂不支持节点或变换：{node.diagnostic_name}", "UV_UNSUPPORTED")

    def math(self, node):
        """求值有限常量数学分支；动态输入仍被拒绝，钳制按原生开关执行。"""
        operation = node.operation
        if operation not in {"ADD", "SUBTRACT", "MULTIPLY", "RADIANS", "DEGREES"}:
            raise ValidationError(f"坐标常量运算尚不支持：{operation}", "UV_UNSUPPORTED")
        a = scalar(self.input(node.inputs["Value"]))
        if operation in {"RADIANS", "DEGREES"}:
            result = math.radians(a) if operation == "RADIANS" else math.degrees(a)
        else:
            b = scalar(self.input(node.inputs["Value_001"]))
            result = a + b if operation == "ADD" else a - b if operation == "SUBTRACT" else a * b
        result = scalar(result)
        return min(1.0, max(0.0, result)) if node.use_clamp else result

    def vector_math(self, node):
        """仅允许一个 UV 场与静态向量加减乘或缩放，两个 UV 场不能当常量合并。"""
        operation = node.operation
        if operation not in {"ADD", "SUBTRACT", "MULTIPLY", "SCALE"}:
            raise ValidationError(f"坐标向量运算尚不支持：{operation}", "UV_UNSUPPORTED")
        a = self.input(node.inputs["Vector"])
        b = self.input(node.inputs["Scale" if operation == "SCALE" else "Vector_001"])
        if isinstance(a, _UV) and isinstance(b, _UV):
            raise ValidationError("两个动态 UV 分支尚无仿射合并规则", "UV_UNSUPPORTED")
        if not isinstance(a, _UV) and not isinstance(b, _UV):
            a, b = vector(a), vector(b)
            return vector(
                [
                    x + y if operation == "ADD" else x - y if operation == "SUBTRACT" else x * y
                    for x, y in zip(a, b, strict=True)
                ]
            )
        uv, constant = (a, vector(b)) if isinstance(a, _UV) else (b, vector(a))
        if operation in {"MULTIPLY", "SCALE"}:
            matrix = linear(constant)
        elif operation == "ADD":
            matrix = linear([1, 1, 1], constant)
        else:
            matrix = (
                linear([1, 1, 1], [-v for v in constant])
                if isinstance(a, _UV)
                else linear([-1, -1, -1], constant)
            )
        return self.append(uv, matrix)

    def rotate(self, node):
        """支持五种原生旋转模式和反向旋转，保持中心及 XYZ 顺序。"""
        value = self.input(node.inputs["Vector"])
        center = vector(self.input(node.inputs["Center"]))
        mode = node.rotation_type
        axes = {"X_AXIS": [1, 0, 0], "Y_AXIS": [0, 1, 0], "Z_AXIS": [0, 0, 1]}
        if mode == "EULER_XYZ":
            rotation = vector(self.input(node.inputs["Rotation"]))
            pairs = list(zip(axes.values(), rotation, strict=True))
            if node.invert:
                pairs = [(axis, -angle) for axis, angle in reversed(pairs)]
            matrix = list(IDENTITY)
            for axis, angle in pairs:
                matrix = multiply(matrix, axis_rotation(axis, angle))
        elif mode == "AXIS_ANGLE" or mode in axes:
            axis = vector(self.input(node.inputs["Axis"])) if mode == "AXIS_ANGLE" else axes[mode]
            angle = scalar(self.input(node.inputs["Angle"])) * (-1 if node.invert else 1)
            matrix = axis_rotation(axis, angle)
        else:
            raise ValidationError("未知坐标旋转模式", "UV_UNSUPPORTED")
        matrix[12:15] = [
            center[j] - sum(center[i] * matrix[i * 4 + j] for i in range(3)) for j in range(3)
        ]
        return self.append(value, matrix)

    def mapping(self, node):
        """沿用宿主 POINT 矩阵构建，允许明确的静态常量分支，保留已有数值约定。"""
        from mathutils import Euler, Matrix, Vector

        value = self.input(node.inputs["Vector"])
        location, rotation, scale = [
            vector(self.input(node.inputs[p])) for p in ("Location", "Rotation", "Scale")
        ]
        matrix = Matrix.LocRotScale(Vector(location), Euler(rotation, "XYZ"), Vector(scale))
        return self.append(value, [matrix[col][row] for row in range(4) for col in range(4)])

    @staticmethod
    def append(value, matrix):
        """追加一步矩阵；纯常量向量可求值，但最终图片坐标仍须追溯到 UV。"""
        if any(not math.isfinite(v) for v in matrix):
            raise ValidationError("坐标矩阵出现非有限数值", "UV_UNSUPPORTED")
        if not isinstance(value, _UV):
            values = [*vector(value), 1]
            return vector([sum(values[i] * matrix[i * 4 + j] for i in range(4)) for j in range(3)])
        transforms = value.transforms + ([matrix] if matrix != IDENTITY else [])
        if len(transforms) > 64:
            raise ValidationError("坐标变换链超过 64 层", "UV_UNSUPPORTED")
        return _UV(value.name, transforms)


def read_uv_coordinates(socket, structural):
    """从图片输入追踪 UV 和静态仿射表达式；动态场、常量坐标及未知来源明确失败。"""
    value = _Reader(structural).input(socket)
    if not isinstance(value, _UV):
        raise ValidationError("图片坐标没有受支持的 UV 来源", "UV_UNSUPPORTED")
    result = {"kind": "uv", "set": value.name}
    if value.transforms:
        result["transforms"] = value.transforms
    return result
