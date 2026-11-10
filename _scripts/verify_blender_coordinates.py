"""独立创建 UV／Mapping 夹具，检查标准文件中的矩阵含义和不支持情况的明确失败。"""

import argparse
import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path

import bpy
from mathutils import Euler, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "_scripts")]

from _scripts.verify_blender_batch import CachedService  # noqa: E402
from _scripts.verify_native import write_png  # noqa: E402
from _scripts.verify_native_host import equal  # noqa: E402
from plugins.blender import controller  # noqa: E402
from plugins.blender.adapter import BlenderAdapter, renderer_ref, source_snapshot  # noqa: E402


def native_point(point, steps):
    """按原生 POINT 的逐步缩放、XYZ 欧拉旋转、平移计算采样点，不使用导出矩阵。"""
    result = Vector(point)
    for location, rotation, scale in steps:
        result = Vector([a * b for a, b in zip(result, scale, strict=True)])
        result.rotate(Euler(rotation, "XYZ"))
        result += Vector(location)
    return list(result)[:2]


def document_point(point, coordinates):
    """按文档的行向量矩阵运算核对坐标；最后才丢弃 Z，避免错把多步链路当作二维。"""
    result = [*point, 1]
    for matrix in coordinates.get("transforms", []):
        result = [sum(result[row] * matrix[row * 4 + col] for row in range(4)) for col in range(4)]
    return result[:2]


def make_case(name, uv_name, steps, image_path):
    """手工建立有共享坐标输出的两张图片；中文 UV 名称和负缩放均保留。"""
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    material.use_fake_user = True
    tree = material.node_tree
    surface = next(n for n in tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
    uv = tree.nodes.new("ShaderNodeUVMap" if uv_name else "ShaderNodeTexCoord")
    if uv_name:
        uv.uv_map = uv_name
    output = uv.outputs["UV"]
    mappings = []
    for location, rotation, scale in steps:
        mapping = tree.nodes.new("ShaderNodeMapping")
        mapping.vector_type = "POINT"
        for key, value in zip(
            ("Location", "Rotation", "Scale"), (location, rotation, scale), strict=True
        ):
            mapping.inputs[key].default_value = value
        tree.links.new(output, mapping.inputs["Vector"])
        mappings.append(mapping)
        output = mapping.outputs["Vector"]
    reroute = tree.nodes.new("NodeReroute")
    tree.links.new(output, reroute.inputs[0])
    for port in ("Base Color", "Emission Color"):
        image = tree.nodes.new("ShaderNodeTexImage")
        image.image = bpy.data.images.load(str(image_path), check_existing=False)
        image.image.colorspace_settings.name = "Non-Color"
        tree.links.new(reroute.outputs[0], image.inputs["Vector"])
        tree.links.new(image.outputs["Color"], surface.inputs[port])
    return material, uv, mappings


def main():
    """导出夹具并保存报告；另一 Blender 进程仅从 .mtlx 和图片完成保存重开验证。"""
    # 1. 限制到独立后台进程，并创建本次唯一输出目录和图片。
    if not bpy.app.background or "--factory-startup" not in sys.argv:
        raise RuntimeError("需要独立后台 Blender 和 --factory-startup")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    args.out.mkdir(parents=True, exist_ok=False)
    image_path = args.out / "fixture.png"
    write_png(image_path)
    image_hash = hashlib.sha256(image_path.read_bytes()).hexdigest()
    service = CachedService(controller.create_service())
    cases = [
        ("named", "纹理UV.001", []),
        (
            "two_steps",
            "",
            [
                ([0.1, -0.7, 0.4], [0.2, 0.1, 0.6], [2, 3, 1]),
                ([4, -2, 0.5], [-0.4, 0.3, 1.1], [0.5, 2, 1.5]),
            ],
        ),
        ("negative", "UVMap.001", [([1, 2, -3], [0.2, -0.3, 0.5], [-2, 1.5, 0.4])]),
    ]
    # 2. 从手工原生图导出，核对文档坐标含义、失败边界及源数据保护。
    results = []
    for name, uv_name, steps in cases:
        material, uv, mappings = make_case(name, uv_name, steps, image_path)
        before = source_snapshot(material)
        adapter = BlenderAdapter(material, service)
        exported = controller.export_material(
            adapter, service, args.out / (name + ".mtlx"), accept_warnings=True
        )
        assert exported.saved_path, exported
        imported = controller.check_import(service, exported.saved_path, renderer_ref())
        assert imported.target_material, imported
        for resource in imported.target_material["textures"].values():
            coordinates = resource["coordinates"]
            assert coordinates["set"] == uv_name
            assert len(coordinates.get("transforms", [])) == len(steps)
            for point in ([0, 0, 0], [0.25, 0.5, 0], [-1.5, 2.4, 0]):
                assert equal(document_point(point, coordinates), native_point(point, steps))
        assert source_snapshot(material) == before
        # 明确失败的链路不得被误读为默认 UV。
        uv.from_instancer = True
        assert controller.check_material(adapter, service).status == "error"
        uv.from_instancer = False
        if mappings:
            value = material.node_tree.nodes.new("ShaderNodeValue")
            material.node_tree.links.new(value.outputs[0], mappings[0].inputs["Location"])
            assert controller.check_material(adapter, service).status == "error"
            material.node_tree.nodes.remove(value)
            mappings[0].vector_type = "TEXTURE"
            assert controller.check_material(adapter, service).status == "error"
            mappings[0].vector_type = "POINT"
        assert source_snapshot(material) == before
        # 直接宿主写入也检查不可分解矩阵，且完整清理失败时新增的材质及图片。
        invalid = deepcopy(imported.target_material)
        for resource in invalid["textures"].values():
            resource["coordinates"]["transforms"] = [
                [1, 1, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
            ]
        counts = (len(bpy.data.materials), len(bpy.data.images))
        try:
            BlenderAdapter(None, service).create_material(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError("剪切矩阵不应静默分解")
        assert counts == (len(bpy.data.materials), len(bpy.data.images))
        results.append(
            {
                "name": name,
                "uv": uv_name,
                "matrices": len(steps),
                "sampled_points_verified": True,
                "source_unchanged": True,
                "unsupported_chains_blocked": True,
                "cleanup_verified": True,
            }
        )
    # 3. 保存证据；夹具 .blend 不参与后续独立文档导入。
    assert image_hash == hashlib.sha256(image_path.read_bytes()).hexdigest()
    bpy.ops.wm.save_as_mainfile(filepath=str(args.out / "fixtures.blend"))
    report = {
        "status": "ok",
        "version": bpy.app.version_string,
        "cases": results,
        "appearance_verified": False,
    }
    (args.out / "summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), "utf-8"
    )
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
