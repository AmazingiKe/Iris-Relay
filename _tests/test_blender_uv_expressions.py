"""独立数值核对静态 UV 表达式；动态量、非仿射图与常量坐标不能冒充 UV。"""

import math
from types import SimpleNamespace as NS

import pytest

from _tests.test_blender_groups import link, node
from iris_relay.model import ValidationError
from plugins.blender.coordinates import read_uv_coordinates
from plugins.blender.groups import ExpandedGraph


def vector_node(tree, name, kind, inputs, **properties):
    """建立向量节点替身，端口类型和值不依赖被测解析器。"""
    value = node(tree, name, kind, inputs=tuple(inputs), outputs=("Vector",))
    for port, default in inputs.items():
        socket = value.inputs[port]
        socket.type = "VECTOR" if isinstance(default, list) else "VALUE"
        socket.default_value = default
    value.outputs[0].type = "VECTOR"
    for key, item in properties.items():
        setattr(value, key, item)
    return value


def fixture():
    """非对称缩放、偏移与绕非原点旋转，用于发现顺序或中心错误。"""
    tree = NS(name="root", nodes=[], animation_data=None)
    uv = node(tree, "UV", "ShaderNodeUVMap", outputs=("UV",))
    uv.from_instancer, uv.uv_map, uv.outputs[0].type = False, "测试UV", "VECTOR"
    scale = vector_node(
        tree,
        "Scale",
        "ShaderNodeVectorMath",
        {"Vector": [0, 0, 0], "Vector_001": [2, 3, 1]},
        operation="MULTIPLY",
    )
    offset = vector_node(
        tree,
        "Offset",
        "ShaderNodeVectorMath",
        {"Vector": [0, 0, 0], "Vector_001": [1, -2, 0]},
        operation="ADD",
    )
    rotate = vector_node(
        tree,
        "Rotate",
        "ShaderNodeVectorRotate",
        {
            "Vector": [0, 0, 0],
            "Center": [1, 0, 0],
            "Axis": [0, 0, 7],
            "Angle": math.pi / 2,
            "Rotation": [0, 0, math.pi / 2],
        },
        rotation_type="AXIS_ANGLE",
        invert=False,
    )
    target = vector_node(tree, "Image", "ShaderNodeTexImage", {"Vector": [0, 0, 0]})
    for source, destination in ((uv, scale), (scale, offset), (offset, rotate), (rotate, target)):
        link(source.outputs[0], destination.inputs[0])
    return tree, target, uv, scale, offset, rotate


def read(tree, target):
    """通过实际组视图读取，结果再用行向量逐步作用到三个维度。"""
    return read_uv_coordinates(ExpandedGraph(tree).node(target).inputs[0], set())


def point(description, value):
    value = [*value, 1]
    for matrix in description.get("transforms", []):
        value = [sum(value[i] * matrix[i * 4 + j] for i in range(4)) for j in range(4)]
    return value[:3]


@pytest.mark.parametrize("mode", ["AXIS_ANGLE", "Z_AXIS", "EULER_XYZ"])
@pytest.mark.parametrize("invert", [False, True])
def test_scale_offset_rotation_order_and_pivot(mode, invert):
    tree, target, _, _, _, rotate = fixture()
    rotate.rotation_type, rotate.invert = mode, invert
    result = read(tree, target)
    assert result["set"] == "测试UV"
    assert len(result["transforms"]) == 3
    assert point(result, [0.25, 0.5, 0]) == pytest.approx(
        [0.5, -0.5, 0] if invert else [1.5, 0.5, 0]
    )


def test_degree_constant_expression_and_scalar_broadcast():
    tree, target, _, scale, _, rotate = fixture()
    combine = node(
        tree, "Combine", "ShaderNodeCombineXYZ", inputs=("X", "Y", "Z"), outputs=("Vector",)
    )
    combine.outputs[0].type = "VECTOR"
    for p, v in zip(combine.inputs, (2, 3, 1), strict=True):
        p.default_value = v
    link(combine.outputs[0], scale.inputs[1])
    radians = node(tree, "Radians", inputs=("Value",))
    radians.operation, radians.use_clamp = "RADIANS", False
    radians.inputs[0].default_value = 90
    link(radians.outputs[0], rotate.inputs["Angle"])
    assert point(read(tree, target), [0.25, 0.5, 0]) == pytest.approx([1.5, 0.5, 0])
    scalar = node(tree, "Value", "ShaderNodeValue")
    scalar.outputs[0].default_value = 2
    scale.inputs[1].links.clear()
    link(scalar.outputs[0], scale.inputs[1])
    assert point(read(tree, target), [0.25, 0.5, 0]) == pytest.approx([2, 0.5, 0])


@pytest.mark.parametrize("mode,expected", [("X_AXIS", [1.5, 0, -0.5]), ("Y_AXIS", [1, -0.5, -0.5])])
def test_axis_modes_do_not_use_unused_axis_parameter(mode, expected):
    tree, target, _, _, _, rotate = fixture()
    rotate.rotation_type = mode
    rotate.inputs["Axis"].default_value = [0, 0, 0]
    assert point(read(tree, target), [0.25, 0.5, 0]) == pytest.approx(expected)


def test_zero_axis_is_identity_and_constant_coordinates_are_rejected():
    tree, target, _, _, _, rotate = fixture()
    rotate.inputs["Axis"].default_value = [0, 0, 0]
    assert point(read(tree, target), [0.25, 0.5, 0]) == pytest.approx([1.5, -0.5, 0])
    target.inputs[0].links.clear()
    with pytest.raises(ValidationError):
        read(tree, target)


@pytest.mark.parametrize(
    "problem", ["animation", "driver", "non_affine", "unknown", "instance_uv", "cycle", "nonfinite"]
)
def test_unsafe_coordinate_evaluation_is_not_frozen(problem):
    tree, target, uv, scale, _, rotate = fixture()
    if problem in {"animation", "driver"}:
        tree.animation_data = NS(
            action=object() if problem == "animation" else None,
            drivers=[1] if problem == "driver" else [],
            nla_tracks=[],
        )
    elif problem == "non_affine":
        link(uv.outputs[0], scale.inputs[1])
    elif problem == "unknown":
        scale.operation = "NORMALIZE"
    elif problem == "instance_uv":
        uv.from_instancer = True
    elif problem == "cycle":
        scale.inputs[0].links.clear()
        link(rotate.outputs[0], scale.inputs[0])
    else:
        rotate.inputs["Angle"].default_value = float("nan")
    with pytest.raises(ValidationError):
        read(tree, target)


@pytest.mark.parametrize(
    "operation,angle",
    [
        ("ADD", 2.5),
        ("SUBTRACT", 1.5),
        ("MULTIPLY", 1.0),
        ("RADIANS", math.pi / 90),
        ("DEGREES", 360 / math.pi),
    ],
)
@pytest.mark.parametrize("clamp", [False, True])
def test_constant_math_modes_and_clamp_keep_rotation_amount(operation, angle, clamp):
    tree, target, _, _, _, rotate = fixture()
    constant = node(tree, "Constant", inputs=("Value", "Value_001"))
    constant.operation, constant.use_clamp = operation, clamp
    constant.inputs[0].default_value, constant.inputs[1].default_value = 2, 0.5
    link(constant.outputs[0], rotate.inputs["Angle"])
    angle = min(1, max(0, angle)) if clamp else angle
    expected = [
        1 + 0.5 * math.cos(angle) + 0.5 * math.sin(angle),
        0.5 * math.sin(angle) - 0.5 * math.cos(angle),
        0,
    ]
    assert point(read(tree, target), [0.25, 0.5, 0]) == pytest.approx(expected)


@pytest.mark.parametrize("reverse,expected", [(False, [-2.5, -1.5, 0]), (True, [4.5, -0.5, 0])])
def test_vector_subtract_preserves_operand_order(reverse, expected):
    tree, target, _, scale, offset, _ = fixture()
    offset.operation = "SUBTRACT"
    if reverse:
        offset.inputs[0].links.clear()
        offset.inputs[0].default_value = [1, -2, 0]
        link(scale.outputs[0], offset.inputs[1])
    assert point(read(tree, target), [0.25, 0.5, 0]) == pytest.approx(expected)


def test_vector_scale_and_static_vector_expression():
    tree, target, _, scale, offset, _ = fixture()
    scale.operation = "SCALE"
    extra = NS(
        node=scale, identifier="Scale", name="Scale", type="VALUE", default_value=2, links=[]
    )
    scale.inputs.append(extra)
    assert point(read(tree, target), [0.25, 0.5, 0]) == pytest.approx([2, 0.5, 0])
    constant = vector_node(
        tree,
        "Offset sum",
        "ShaderNodeVectorMath",
        {"Vector": [1, 2, 3], "Vector_001": [4, 5, 6]},
        operation="ADD",
    )
    link(constant.outputs[0], offset.inputs[1])
    assert point(read(tree, target), [0.25, 0.5, 0]) == pytest.approx([-7, 4.5, 9])
