"""创建真实 RGBA、命名 UV 与多步静态 Mapping 夹具，保存重开核对可编辑链路。"""

import argparse
import json
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from _scripts.probe_maya_texture import write_fixture  # noqa: E402
from _scripts.verify_blender_batch import compare_target_graph  # noqa: E402
from _scripts.verify_native import write_png  # noqa: E402
from plugins.blender.adapter import BlenderAdapter  # noqa: E402
from plugins.core import create_service, request  # noqa: E402


def main():
    """只操作全新后台场景；全部原生读取、创建和资源校验均沿公开 API。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    args.out.mkdir(parents=True, exist_ok=False)
    images = args.out / "images"
    images.mkdir()
    documents = args.out / "documents"
    documents.mkdir()
    coordinates = args.out / "coordinates"
    coordinates.mkdir()
    write_fixture(images / "rgba.png")
    write_png(images / "rgb.png")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    cases = []
    with create_service(ROOT) as service:
        for mode, linked in [
            ("STRAIGHT", False),
            ("STRAIGHT", True),
            ("PREMUL", True),
            ("CHANNEL_PACKED", True),
            ("NONE", True),
            ("UV", True),
        ]:
            name = mode + ("_alpha" if linked else "_color")
            material = bpy.data.materials.new(name)
            material.use_nodes = True
            tree = material.node_tree
            surface = next(n for n in tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
            image = bpy.data.images.load(
                str(images / ("rgb.png" if mode == "UV" else "rgba.png")), check_existing=False
            )
            image.alpha_mode = "STRAIGHT" if mode == "UV" else mode
            image.colorspace_settings.name = "Non-Color" if mode == "CHANNEL_PACKED" else "sRGB"
            node = tree.nodes.new("ShaderNodeTexImage")
            node.image = image
            tree.links.new(node.outputs["Color"], surface.inputs["Base Color"])
            if linked and mode != "UV":
                tree.links.new(node.outputs["Alpha"], surface.inputs["Alpha"])
            if mode == "UV":
                uv = tree.nodes.new("ShaderNodeUVMap")
                uv.uv_map = "DetailUV"
                first = tree.nodes.new("ShaderNodeMapping")
                second = tree.nodes.new("ShaderNodeMapping")
                first.inputs["Scale"].default_value = (2, 0.75, 1)
                first.inputs["Location"].default_value = (0.125, -0.25, 0)
                second.inputs["Rotation"].default_value = (0, 0, 0.35)
                second.inputs["Location"].default_value = (0.2, 0.15, 0)
                tree.links.new(uv.outputs["UV"], first.inputs["Vector"])
                tree.links.new(first.outputs["Vector"], second.inputs["Vector"])
                tree.links.new(second.outputs["Vector"], node.inputs["Vector"])
            source = BlenderAdapter(material, service).read_material()
            exported = request(service, "materialx.export", material=source)
            assert exported["result"]["document"], exported
            directory = coordinates if mode == "UV" else documents
            (directory / (name + ".mtlx")).write_text(
                exported["result"]["document"], encoding="utf-8"
            )
            converted = request(
                service,
                "materialx.convert",
                document=exported["result"]["document"],
                target=source["renderer"],
            )
            expected = converted["result"]["material"]
            assert expected, converted
            restored = BlenderAdapter(None, service).create_material(expected)
            cases.append(
                {"id": name, "material": restored.name, "expected": expected, "mode": mode}
            )
        path = args.out / "restored.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(path))
        bpy.ops.wm.open_mainfile(filepath=str(path))
        for case in cases:
            actual = BlenderAdapter(bpy.data.materials[case["material"]], service).read_material()
            case.update(compare_target_graph(case.pop("expected"), actual), reopened=True)
            (args.out / (case["id"] + "-native.json")).write_text(
                json.dumps(actual, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    (args.out / "summary.json").write_text(
        json.dumps(
            {"successful": len(cases), "cases": cases, "appearance_verified": False},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps({"successful": len(cases)}), flush=True)


if __name__ == "__main__":
    main()
