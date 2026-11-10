"""独立验证 HSV 常量、图片、动态参数及节点组，导出文件再交给独立进程还原。"""

import argparse
import hashlib
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
from plugins.blender import controller  # noqa: E402
from plugins.blender.adapter import BlenderAdapter, renderer_ref  # noqa: E402


def make_case(name, mode, path):
    """构造共享 HSV 输出；连接参数由普通数学节点提供，不冻结为默认值。"""
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    root = material.node_tree
    surface = next(n for n in root.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
    tree = root
    if mode == "group":
        tree = bpy.data.node_groups.new(name + "_definition", "ShaderNodeTree")
        for port in ("Hue", "Saturation", "Value", "Fac", "Color"):
            tree.interface.new_socket(
                name=port,
                in_out="INPUT",
                socket_type="NodeSocketColor" if port == "Color" else "NodeSocketFloat",
            )
        tree.interface.new_socket(name="Color", in_out="OUTPUT", socket_type="NodeSocketColor")
    hsv = tree.nodes.new("ShaderNodeHueSaturation")
    hsv.name = "hsv"
    values = {
        "Hue": 0.27,
        "Saturation": 1.7,
        "Value": 2.3,
        "Fac": 0.37,
        "Color": [0.13, 0.47, 0.81, 1],
    }
    if mode == "near_gray":
        values.update(Hue=1.0, Saturation=2.0, Value=3.0, Fac=1.0, Color=[1, 0.999999, 1, 1])
    owner = hsv
    output = hsv.outputs["Color"]
    if mode == "group":
        entry, end = tree.nodes.new("NodeGroupInput"), tree.nodes.new("NodeGroupOutput")
        for port in values:
            tree.links.new(entry.outputs[port], hsv.inputs[port])
        tree.links.new(hsv.outputs[0], end.inputs[0])
        owner = root.nodes.new("ShaderNodeGroup")
        owner.node_tree = tree
        output = owner.outputs[0]
    for port, value in values.items():
        owner.inputs[port].default_value = value
    if mode in {"image", "linked", "group"}:
        image = root.nodes.new("ShaderNodeTexImage")
        image.image = bpy.data.images.load(str(path), check_existing=False)
        image.image.colorspace_settings.name = "Non-Color"
        root.links.new(image.outputs["Color"], owner.inputs["Color"])
    if mode == "linked":
        for port in ("Hue", "Saturation", "Value", "Fac"):
            math = root.nodes.new("ShaderNodeMath")
            math.operation = "ADD"
            math.inputs[0].default_value = 0.1
            math.inputs[1].default_value = values[port] - 0.1
            root.links.new(math.outputs[0], owner.inputs[port])
    for port in ("Base Color", "Roughness"):
        root.links.new(output, surface.inputs[port])
    return material


def main():
    """核对真实宿主导出与源保护；修改内联实现必须使原生回导失败。"""
    if not bpy.app.background or "--factory-startup" not in sys.argv:
        raise RuntimeError("需要独立后台 Blender 和 --factory-startup")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    args.out.mkdir(parents=True, exist_ok=False)
    path = args.out / "hsv.png"
    write_image(path, 3)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    service = CachedService(controller.create_service())
    cases = []
    for mode in ("constant", "near_gray", "image", "linked", "group"):
        name = "hsv_" + mode
        material = make_case(name, mode, path)
        before = snapshot(material)
        result = controller.export_material(
            BlenderAdapter(material, service),
            service,
            args.out / (name + ".mtlx"),
            accept_warnings=True,
        )
        assert result.saved_path, result
        xml = ET.fromstring(Path(result.saved_path).read_text("utf-8"))
        graph = xml.find("nodegraph[@name='NG_iris_blender_hsv_color3']")
        assert graph is not None and xml.find("iris_blender_hsv") is not None
        graph.find("clamp[@name='saturation_clamp']/input[@name='high']").set("value", "2")
        rejected = controller.request(
            service,
            "materialx.convert",
            document=ET.tostring(xml, encoding="unicode"),
            target=renderer_ref(),
        )
        assert rejected["status"] == "error" and rejected["result"]["material"] is None
        assert snapshot(material) == before
        cases.append(
            {"name": name, "source_unchanged": True, "modified_implementation_blocked": True}
        )
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    write_json(
        args.out / "summary.json",
        {
            "status": "ok",
            "version": bpy.app.version_string,
            "cases": cases,
            "appearance_verified": False,
        },
    )
    print(json.dumps({"status": "ok", "cases": len(cases)}), flush=True)


if __name__ == "__main__":
    main()
