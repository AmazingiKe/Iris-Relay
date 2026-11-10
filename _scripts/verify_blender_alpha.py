"""独立建立图片 Alpha 模式夹具，导出标准图并核对源资源保护和失败清理。"""

import argparse
import hashlib
import json
import struct
import sys
import zlib
from pathlib import Path
from xml.etree import ElementTree as ET

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from _scripts.verify_blender_batch import CachedService, write_json  # noqa: E402
from plugins.blender import controller  # noqa: E402
from plugins.blender.adapter import BlenderAdapter, renderer_ref, source_snapshot  # noqa: E402


def write_image(path, channels):
    """写入 2×2 非对称 PNG，含零、中间和全不透明 Alpha；不依赖源资产。"""

    def chunk(kind, value):
        return (
            struct.pack(">I", len(value))
            + kind
            + value
            + struct.pack(">I", zlib.crc32(kind + value))
        )

    colors = [(24, 128, 200, 0), (201, 75, 47, 64), (31, 223, 90, 192), (240, 15, 164, 255)]
    pixels = b"".join(
        b"\x00" + b"".join(bytes(p[:channels]) for p in colors[row : row + 2]) for row in (0, 2)
    )
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, {1: 0, 3: 2, 4: 6}[channels], 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(pixels))
        + chunk(b"IEND", b"")
    )


def main():
    """先创建原生图，再检查导出语义；独立回导由 verify_blender_batch 执行。"""
    if not bpy.app.background or "--factory-startup" not in sys.argv:
        raise RuntimeError("需要独立后台 Blender 和 --factory-startup")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    args.out.mkdir(parents=True, exist_ok=False)
    service = CachedService(controller.create_service())
    cases = []
    for channels in (1, 3, 4):
        path = args.out / f"channels_{channels}.png"
        write_image(path, channels)
        original_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        for mode in ("STRAIGHT", "PREMUL", "CHANNEL_PACKED", "NONE"):
            for space in ("sRGB", "Non-Color"):
                for linked in (False, True):
                    name = f"c{channels}_{mode}_{space}_{int(linked)}"
                    material = bpy.data.materials.new(name)
                    material.use_nodes = True
                    tree = material.node_tree
                    surface = next(
                        n for n in tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled"
                    )
                    image = tree.nodes.new("ShaderNodeTexImage")
                    image.image = bpy.data.images.load(str(path), check_existing=False)
                    image.image.colorspace_settings.name = space
                    image.image.alpha_mode = mode
                    tree.links.new(image.outputs["Color"], surface.inputs["Base Color"])
                    tree.links.new(image.outputs["Color"], surface.inputs["Emission Color"])
                    if linked:
                        tree.links.new(image.outputs["Alpha"], surface.inputs["Alpha"])
                        tree.links.new(image.outputs["Alpha"], surface.inputs["Roughness"])
                    before = source_snapshot(material)
                    count, loaded = len(bpy.data.images), image.image.has_data
                    result = controller.export_material(
                        BlenderAdapter(material, service),
                        service,
                        args.out / (name + ".mtlx"),
                        accept_warnings=True,
                    )
                    assert result.saved_path, result
                    assert source_snapshot(material) == before
                    assert len(bpy.data.images) == count and image.image.has_data == loaded
                    assert hashlib.sha256(path.read_bytes()).hexdigest() == original_hash
                    document = ET.fromstring(Path(result.saved_path).read_text("utf-8"))
                    exported = document.find("image")
                    assert (
                        exported.attrib["type"] == {1: "color3", 3: "color3", 4: "color4"}[channels]
                    )
                    assert exported.attrib["iris_alpha_mode"] == mode
                    assert exported.attrib["iris_alpha_used"] == str(linked).lower()
                    checked = controller.check_import(service, result.saved_path, renderer_ref())
                    assert checked.target_material, checked
                    target = checked.target_material
                    resource = next(iter(target["textures"].values()))
                    assert resource["metadata"]["image_alpha"] == {
                        "mode": mode,
                        "channels": channels,
                    }
                    # 错误通道必须在新建目标时清理全部新增数据，不能留下半成品。
                    resource["metadata"]["image_alpha"]["channels"] = 4 if channels != 4 else 3
                    material_count = len(bpy.data.materials)
                    try:
                        BlenderAdapter(None, service).create_material(target)
                    except Exception as exc:
                        assert "通道" in str(exc), exc
                    else:
                        raise AssertionError("应拒绝实际文件与声明通道不符")
                    assert (
                        len(bpy.data.materials) == material_count and len(bpy.data.images) == count
                    )
                    cases.append(
                        {
                            "name": name,
                            "channels": channels,
                            "mode": mode,
                            "space": space,
                            "alpha_linked": linked,
                            "source_unchanged": True,
                            "failure_cleanup": True,
                        }
                    )
    write_json(
        args.out / "summary.json",
        {"version": bpy.app.version_string, "cases": cases, "appearance_verified": False},
    )
    print(json.dumps({"cases": len(cases), "status": "ok"}), flush=True)


if __name__ == "__main__":
    main()
