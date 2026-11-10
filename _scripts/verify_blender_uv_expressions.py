"""实机核验组内 UV 加乘、常量角度表达式和五种旋转，再交给独立进程还原。"""

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Euler, Quaternion, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from _scripts.verify_blender_alpha import write_image  # noqa: E402
from _scripts.verify_blender_batch import CachedService, write_json  # noqa: E402
from _scripts.verify_blender_coordinates import document_point  # noqa: E402
from _scripts.verify_blender_groups import snapshot  # noqa: E402
from _scripts.verify_native_host import equal  # noqa: E402
from plugins.blender import controller  # noqa: E402
from plugins.blender.adapter import BlenderAdapter, renderer_ref  # noqa: E402


def definition(name, mode, invert):
    """按实际原生节点建组，名称刻意不同于 A1；组名不应决定功能。"""
    tree = bpy.data.node_groups.new(name, "ShaderNodeTree")
    for label, kind in (
        ("UV", "Vector"),
        ("Scale", "Float"),
        ("Stretch", "Float"),
        ("OffsetX", "Float"),
        ("OffsetY", "Float"),
        ("Degrees", "Float"),
        ("Center", "Vector"),
        ("Axis", "Vector"),
        ("Rotation", "Vector"),
    ):
        tree.interface.new_socket(name=label, in_out="INPUT", socket_type="NodeSocket" + kind)
    tree.interface.new_socket(name="坐标", in_out="OUTPUT", socket_type="NodeSocketVector")
    entry, output = tree.nodes.new("NodeGroupInput"), tree.nodes.new("NodeGroupOutput")
    scale, stretch, offset = [tree.nodes.new("ShaderNodeVectorMath") for _ in range(3)]
    scale.operation = stretch.operation = "MULTIPLY"
    offset.operation = "ADD"
    stretch_value, offset_value = [tree.nodes.new("ShaderNodeCombineXYZ") for _ in range(2)]
    stretch_value.inputs["X"].default_value = stretch_value.inputs["Z"].default_value = 1
    radians = tree.nodes.new("ShaderNodeMath")
    radians.operation = "RADIANS"
    rotate = tree.nodes.new("ShaderNodeVectorRotate")
    rotate.rotation_type, rotate.invert = mode, invert
    for source, destination in (
        (entry.outputs["UV"], scale.inputs[0]),
        (entry.outputs["Scale"], scale.inputs[1]),
        (scale.outputs["Vector"], stretch.inputs[0]),
        (entry.outputs["Stretch"], stretch_value.inputs["Y"]),
        (stretch_value.outputs[0], stretch.inputs[1]),
        (stretch.outputs["Vector"], offset.inputs[0]),
        (entry.outputs["OffsetX"], offset_value.inputs["X"]),
        (entry.outputs["OffsetY"], offset_value.inputs["Y"]),
        (offset_value.outputs[0], offset.inputs[1]),
        (entry.outputs["Degrees"], radians.inputs[0]),
        (offset.outputs["Vector"], rotate.inputs["Vector"]),
        (entry.outputs["Center"], rotate.inputs["Center"]),
        (rotate.outputs[0], output.inputs[0]),
    ):
        tree.links.new(source, destination)
    if mode == "EULER_XYZ":
        tree.links.new(entry.outputs["Rotation"], rotate.inputs["Rotation"])
    else:
        tree.links.new(radians.outputs[0], rotate.inputs["Angle"])
        if mode == "AXIS_ANGLE":
            tree.links.new(entry.outputs["Axis"], rotate.inputs["Axis"])
    return tree


def make_case(name, mode, invert, image_path):
    """同组两个实例共享 UV，参数不同；各自驱动独立图片。"""
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    tree = material.node_tree
    surface = next(n for n in tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
    uv = tree.nodes.new("ShaderNodeUVMap")
    uv.uv_map = "实例UV.001"
    group = definition(name + "_坐标运算", mode, invert)
    parameters = []
    instances = []
    for index, port in enumerate(("Base Color", "Emission Color")):
        instance = tree.nodes.new("ShaderNodeGroup")
        instance.node_tree = group
        instance.name = f"UV实例{index}"
        values = {
            "Scale": -1.3 if index else 2.1,
            "Stretch": 0.7 if index else 1.6,
            "OffsetX": 0.43,
            "OffsetY": -0.27,
            "Degrees": -37.0 if index else 73.0,
            "Center": [0.41, -0.23, 0.31],
            "Axis": [2, -1, 3],
            "Rotation": [0.31, -0.63, 1.12],
        }
        for key, value in values.items():
            instance.inputs[key].default_value = value
        # 预期数值取宿主实际存储的 float32，求值算法使用独立 mathutils 实现。
        parameters.append(
            {
                key: list(instance.inputs[key].default_value)
                if isinstance(value, list)
                else instance.inputs[key].default_value
                for key, value in values.items()
            }
        )
        image = tree.nodes.new("ShaderNodeTexImage")
        image.name = f"image_{index}"
        image.image = bpy.data.images.load(str(image_path), check_existing=False)
        image.image.colorspace_settings.name = "Non-Color"
        tree.links.new(uv.outputs[0], instance.inputs["UV"])
        tree.links.new(instance.outputs[0], image.inputs["Vector"])
        tree.links.new(image.outputs["Color"], surface.inputs[port])
        instances.append(instance)
    return material, instances, parameters


def expected_point(point, values, mode, invert):
    """直接按原生逐分量运算和 mathutils 旋转计算，独立于矩阵导出代码。"""
    value = Vector(point) * values["Scale"]
    value.y *= values["Stretch"]
    value += Vector([values["OffsetX"], values["OffsetY"], 0])
    center = Vector(values["Center"])
    value -= center
    if mode == "EULER_XYZ":
        rotation = Euler(values["Rotation"], "XYZ").to_matrix()
        value = (rotation.inverted() if invert else rotation) @ value
    else:
        axes = {"X_AXIS": [1, 0, 0], "Y_AXIS": [0, 1, 0], "Z_AXIS": [0, 0, 1]}
        axis = Vector(values["Axis"] if mode == "AXIS_ANGLE" else axes[mode])
        value.rotate(
            Quaternion(axis.normalized(), math.radians(values["Degrees"]) * (-1 if invert else 1))
        )
    return list(value + center)[:2]


def main():
    """核对每种旋转、每个实例的三个采样点，另检查动画和非仿射输入阻断。"""
    if not bpy.app.background or "--factory-startup" not in sys.argv:
        raise RuntimeError("需要独立后台 Blender 和 --factory-startup")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    args.out.mkdir(parents=True, exist_ok=False)
    image_path = args.out / "fixture.png"
    write_image(image_path, 3)
    image_hash = hashlib.sha256(image_path.read_bytes()).hexdigest()
    service = CachedService(controller.create_service())
    cases = []
    for mode in ("AXIS_ANGLE", "X_AXIS", "Y_AXIS", "Z_AXIS", "EULER_XYZ"):
        for invert in (False, True):
            name = f"uv_{mode}_{int(invert)}"
            material, instances, values = make_case(name, mode, invert, image_path)
            before = snapshot(material)
            exported = controller.export_material(
                BlenderAdapter(material, service),
                service,
                args.out / (name + ".mtlx"),
                accept_warnings=True,
            )
            assert exported.saved_path, exported
            checked = controller.check_import(service, exported.saved_path, renderer_ref())
            assert checked.target_material, checked
            expected = checked.target_material
            for index in range(2):
                image = next(n for n in expected["nodes"] if n["id"] == f"image_{index}")
                coordinates = expected["textures"][image["inputs"]["@image"]["texture"]][
                    "coordinates"
                ]
                assert coordinates["set"] == "实例UV.001"
                for sample in ([0, 0, 0], [0.25, 0.5, 0], [-1.5, 2.4, 0]):
                    assert equal(
                        document_point(sample, coordinates),
                        expected_point(sample, values[index], mode, invert),
                    ), (mode, invert, index, sample, coordinates)
            assert snapshot(material) == before
            # 组实例默认值一旦带动画，读取不能冻结当前帧；测试后清理仅本夹具的动画。
            instances[0].inputs["Degrees"].keyframe_insert(data_path="default_value", frame=1)
            rejected = controller.check_material(BlenderAdapter(material, service), service)
            assert rejected.status == "error" and any(
                i["code"] == "UV_DYNAMIC_UNSUPPORTED" for i in rejected.issues
            )
            material.node_tree.animation_data_clear()
            assert snapshot(material) == before
            cases.append(
                {
                    "name": name,
                    "instances": 2,
                    "checked_points": 6,
                    "source_unchanged": True,
                    "animation_blocked": True,
                }
            )
    # 同一 Math 同时驱动 UV 和粗糙度，不能将 UV 一侧冻结后破坏共享连接。
    before = snapshot(material)
    tree = material.node_tree
    shared = tree.nodes.new("ShaderNodeMath")
    shared.operation = "ADD"
    surface = next(n for n in tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
    tree.links.new(shared.outputs[0], instances[0].inputs["Scale"])
    tree.links.new(shared.outputs[0], surface.inputs["Roughness"])
    rejected = controller.check_material(BlenderAdapter(material, service), service)
    assert rejected.status == "error" and any(
        i["code"] == "UV_SHARED_EXPRESSION_UNSUPPORTED" for i in rejected.issues
    )
    tree.nodes.remove(shared)
    assert snapshot(material) == before
    assert hashlib.sha256(image_path.read_bytes()).hexdigest() == image_hash
    write_json(
        args.out / "summary.json",
        {
            "status": "ok",
            "version": bpy.app.version_string,
            "cases": cases,
            "appearance_verified": False,
        },
    )
    print(json.dumps({"cases": len(cases), "status": "ok"}), flush=True)


if __name__ == "__main__":
    main()
