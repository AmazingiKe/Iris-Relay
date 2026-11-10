"""实机验证完整颜色反相、图片与标量链；可用隔离字典测试尚未发布的绑定。"""

import argparse
import json
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from _scripts.verify_blender_alpha import write_image  # noqa: E402
from _scripts.verify_blender_batch import CachedService, write_json  # noqa: E402
from _scripts.verify_blender_groups import snapshot  # noqa: E402
from iris_relay.plugin import JsonCommandService  # noqa: E402
from plugins.blender import controller  # noqa: E402
from plugins.blender.adapter import BlenderAdapter  # noqa: E402


def make_case(name, mode, image_path):
    """构造常量、图片及组内反相，输出同时驱动颜色与粗糙度。"""
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    root = material.node_tree
    surface = next(n for n in root.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
    tree = root
    group = None
    if mode == "group":
        tree = bpy.data.node_groups.new(name + "_definition", "ShaderNodeTree")
        tree.interface.new_socket(name="Color", in_out="OUTPUT", socket_type="NodeSocketColor")
        group = root.nodes.new("ShaderNodeGroup")
        group.node_tree = tree
    invert = tree.nodes.new("ShaderNodeInvert")
    invert.inputs[0].default_value = 1.0
    invert.inputs[1].default_value = [0.13, 0.47, 0.81, 1]
    if mode != "constant":
        texture = tree.nodes.new("ShaderNodeTexImage")
        texture.image = bpy.data.images.load(str(image_path), check_existing=False)
        texture.image.colorspace_settings.name = "Non-Color"
        tree.links.new(texture.outputs["Color"], invert.inputs[1])
    output = invert.outputs[0]
    if group:
        end = tree.nodes.new("NodeGroupOutput")
        tree.links.new(output, end.inputs[0])
        output = group.outputs[0]
    for socket in ("Base Color", "Roughness"):
        root.links.new(output, surface.inputs[socket])
    return material, invert


def main():
    """导出并核对标准反相与亮度链，再检查部分系数和连接系数明确失败。"""
    if not bpy.app.background or "--factory-startup" not in sys.argv:
        raise RuntimeError("需要独立后台 Blender 和 --factory-startup")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--dictionaries", type=Path)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    args.out.mkdir(parents=True, exist_ok=False)
    service = (
        controller.create_service()
        if not args.dictionaries
        else JsonCommandService(
            [
                str(ROOT / ".venv/Scripts/python.exe"),
                "-m",
                "iris_relay",
                "--dictionaries",
                str(args.dictionaries),
            ],
            cwd=ROOT,
        )
    )
    service = CachedService(service)
    image_path = args.out / "invert.png"
    write_image(image_path, 3)
    original_image = image_path.read_bytes()
    cases = []
    for mode in ("constant", "image", "group"):
        name = "invert_" + mode
        material, invert = make_case(name, mode, image_path)
        before = snapshot(material)
        adapter = BlenderAdapter(material, service)
        exported = controller.export_material(
            adapter, service, args.out / (name + ".mtlx"), accept_warnings=True
        )
        assert exported.saved_path, exported
        xml = ET.fromstring(Path(exported.saved_path).read_text("utf-8"))
        assert len(xml.findall("invert")) == 1 and len(xml.findall("luminance")) == 1
        assert xml.find("invert").attrib["nodedef"] == "ND_invert_color3"
        assert snapshot(material) == before
        invert.inputs[0].default_value = 0.37
        assert controller.check_material(adapter, service).status == "error"
        invert.inputs[0].default_value = 1.0
        source = invert.id_data.nodes.new("ShaderNodeMath")
        source.operation = "ADD"
        source.inputs[0].default_value = 0.4
        source.inputs[1].default_value = 0.6
        invert.id_data.links.new(source.outputs[0], invert.inputs[0])
        assert controller.check_material(adapter, service).status == "error"
        invert.id_data.nodes.remove(source)
        assert snapshot(material) == before
        cases.append({"name": name, "source_unchanged": True, "factor_boundaries_verified": True})
    assert original_image == image_path.read_bytes()
    write_json(
        args.out / "summary.json",
        {"version": bpy.app.version_string, "cases": cases, "appearance_verified": False},
    )
    print(json.dumps({"cases": len(cases), "status": "ok"}), flush=True)


if __name__ == "__main__":
    main()
