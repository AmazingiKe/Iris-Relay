"""实机核验组接口的一次颜色转标量留在核心连接中，多次转换不能被消除。"""

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
from _scripts.verify_blender_groups import group_tree, snapshot  # noqa: E402
from _scripts.verify_native_host import equal  # noqa: E402
from plugins.blender import controller  # noqa: E402
from plugins.blender.adapter import BlenderAdapter  # noqa: E402


def make_case(name, mode, image_path):
    """颜色在组输入或输出转换为标量；嵌套情况仅增加同类型接口。"""
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    root = material.node_tree
    surface = next(n for n in root.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
    inner, entry, output = group_tree(name + "_inner")
    group = root.nodes.new("ShaderNodeGroup")
    group.node_tree = inner
    image_tree = root if mode == "input" else inner
    image = image_tree.nodes.new("ShaderNodeTexImage")
    image.image = bpy.data.images.load(str(image_path), check_existing=False)
    image.image.colorspace_settings.name = "Non-Color"
    if mode == "input":
        root.links.new(image.outputs["Color"], group.inputs[0])
        inner.links.new(entry.outputs[0], output.inputs[0])
    else:
        inner.links.new(image.outputs["Color"], output.inputs[0])
    if mode == "nested":
        outer, outer_entry, outer_output = group_tree(name + "_outer")
        child = outer.nodes.new("ShaderNodeGroup")
        child.node_tree = inner
        outer.links.new(outer_entry.outputs[0], child.inputs[0])
        outer.links.new(child.outputs[0], outer_output.inputs[0])
        group.node_tree = outer
    for port in ("Roughness", "Coat Roughness"):
        root.links.new(group.outputs[0], surface.inputs[port])
    return material, group, surface


def check_scalar_default(service, output):
    """核验组实例浮点默认值接反相颜色时成为灰度，未使用目标端口原来的默认值。"""
    material = bpy.data.materials.new("group_cast_default")
    material.use_nodes = True
    tree = bpy.data.node_groups.new("默认值转灰度", "ShaderNodeTree")
    tree.interface.new_socket(name="输入", in_out="INPUT", socket_type="NodeSocketFloat")
    tree.interface.new_socket(name="颜色", in_out="OUTPUT", socket_type="NodeSocketColor")
    entry, invert, end = [
        tree.nodes.new(kind) for kind in ("NodeGroupInput", "ShaderNodeInvert", "NodeGroupOutput")
    ]
    tree.links.new(entry.outputs[0], invert.inputs["Color"])
    tree.links.new(invert.outputs[0], end.inputs[0])
    group = material.node_tree.nodes.new("ShaderNodeGroup")
    group.node_tree = tree
    group.inputs[0].default_value = 0.23
    expected = [group.inputs[0].default_value] * 3
    surface = next(n for n in material.node_tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
    material.node_tree.links.new(group.outputs[0], surface.inputs["Base Color"])
    before = snapshot(material)
    adapter = BlenderAdapter(material, service)
    source = adapter.read_material()
    actual = next(n for n in source["nodes"] if n["type"] == "ShaderNodeInvert")["inputs"]["Color"]
    assert equal(actual, expected)
    exported = controller.export_material(
        adapter, service, output / "group_cast_default.mtlx", accept_warnings=True
    )
    assert exported.saved_path, exported
    xml = ET.fromstring(Path(exported.saved_path).read_text("utf-8"))
    value = xml.find("invert/input[@name='in']").attrib["value"]
    assert equal([float(v) for v in value.split(",")], expected)
    assert snapshot(material) == before
    return {"name": material.name, "source_unchanged": True, "scalar_default_verified": True}


def main():
    """从源端口核对颜色到标量连接及实际亮度系数，再检查往返类型转换阻断。"""
    if not bpy.app.background or "--factory-startup" not in sys.argv:
        raise RuntimeError("需要独立后台 Blender 和 --factory-startup")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    args.out.mkdir(parents=True, exist_ok=False)
    image_path = args.out / "group_cast.png"
    write_image(image_path, 3)
    digest = hashlib.sha256(image_path.read_bytes()).hexdigest()
    service = CachedService(controller.create_service())
    cases = []
    for mode in ("input", "output", "nested"):
        name = "group_cast_" + mode
        material, group, surface = make_case(name, mode, image_path)
        before = snapshot(material)
        adapter = BlenderAdapter(material, service)
        source = adapter.read_material()
        # 展开后仍是图片颜色到两个原生标量端口，亮度运算留给统一核心插入。
        image = next(n for n in source["nodes"] if n["type"] == "ShaderNodeTexImage")
        edges = [e for e in source["links"] if e["from"]["node"] == image["id"]]
        assert len(edges) == 2 and all(e["from"]["socket"] == "Color" for e in edges)
        assert {e["to"]["socket"] for e in edges} == {"Roughness", "Coat Roughness"}
        exported = controller.export_material(
            adapter, service, args.out / (name + ".mtlx"), accept_warnings=True
        )
        assert exported.saved_path, exported
        xml = ET.fromstring(Path(exported.saved_path).read_text("utf-8"))
        luminances = xml.findall("luminance")
        assert luminances
        coefficients = source["metadata"]["color_to_float"]["coefficients"]
        for luminance in luminances:
            actual = [
                float(v)
                for v in luminance.find("input[@name='lumacoeffs']").attrib["value"].split(",")
            ]
            assert equal(actual, coefficients)
        assert snapshot(material) == before
        # 同一标量组输出再接颜色端口会形成 RGBA→VALUE→RGBA，不能直接接回源颜色。
        connection = material.node_tree.links.new(
            group.outputs[0], surface.inputs["Emission Color"]
        )
        rejected = controller.check_material(adapter, service)
        assert rejected.status == "error" and any(
            i["code"] == "GROUP_SOCKET_TYPE_UNSUPPORTED" for i in rejected.issues
        )
        material.node_tree.links.remove(connection)
        assert snapshot(material) == before
        cases.append({"name": name, "source_unchanged": True, "multiple_casts_blocked": True})
    cases.append(check_scalar_default(service, args.out))
    assert hashlib.sha256(image_path.read_bytes()).hexdigest() == digest
    write_json(
        args.out / "summary.json", {"status": "ok", "cases": cases, "appearance_verified": False}
    )
    print(json.dumps({"status": "ok", "cases": len(cases)}), flush=True)


if __name__ == "__main__":
    main()
