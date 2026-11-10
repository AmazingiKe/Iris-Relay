"""建立真实嵌套组和共享组实例，独立核对展开值、连接、源保护及失败边界。"""

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from _scripts.verify_blender_alpha import write_image  # noqa: E402
from _scripts.verify_blender_batch import CachedService, write_json  # noqa: E402
from _scripts.verify_native_host import equal  # noqa: E402
from plugins.blender import controller  # noqa: E402
from plugins.blender.adapter import BlenderAdapter, source_snapshot  # noqa: E402


def group_tree(name, kind="NodeSocketFloat"):
    """建立输入、输出及旁路接口；名称相同也按 identifier 对应。"""
    tree = bpy.data.node_groups.new(name, "ShaderNodeTree")
    tree.interface.new_socket(name="输入", in_out="INPUT", socket_type=kind)
    tree.interface.new_socket(name="结果", in_out="OUTPUT", socket_type=kind)
    tree.interface.new_socket(name="旁路", in_out="OUTPUT", socket_type=kind)
    entry = tree.nodes.new("NodeGroupInput")
    output = tree.nodes.new("NodeGroupOutput")
    output.is_active_output = True
    tree.links.new(entry.outputs[0], output.inputs[1])
    return tree, entry, output


def instance(tree, definition, name, value):
    """共用组定义，输入值属于各自实例。"""
    group = tree.nodes.new("ShaderNodeGroup")
    group.name, group.node_tree = name, definition
    group.inputs[0].default_value = value
    return group


def snapshot(material):
    """同时保存材质和所有组定义，避免只核对顶层而漏掉共享库修改。"""
    return {
        "material": source_snapshot(material),
        "groups": {
            group.name: source_snapshot(SimpleNamespace(name=group.name, node_tree=group))
            for group in bpy.data.node_groups
        },
    }


def make_math_case(name, linked, nested, passthrough):
    """从已知公式 x*0.25 建立两个实例，并让其中一个输出共享到两个输入。"""
    definition, entry, output = group_tree(name + "_inner")
    math = definition.nodes.new("ShaderNodeMath")
    math.name, math.operation = "共享乘法", "MULTIPLY"
    math.inputs[1].default_value = 0.25
    definition.links.new(entry.outputs[0], math.inputs[0])
    definition.links.new(math.outputs[0], output.inputs[0])
    if nested:
        outer, entry, output = group_tree(name + "_outer")
        group = instance(outer, definition, "嵌套", 0.99)
        outer.links.new(entry.outputs[0], group.inputs[0])
        outer.links.new(group.outputs[0], output.inputs[0])
        definition = outer
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    tree = material.node_tree
    surface = next(n for n in tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
    groups = [instance(tree, definition, f"实例{i}", v) for i, v in enumerate((0.2, 0.6))]
    tree.links.new(groups[0].outputs[0], surface.inputs["Roughness"])
    tree.links.new(groups[0].outputs[0], surface.inputs["Coat Roughness"])
    tree.links.new(groups[1].outputs[0], surface.inputs["Metallic"])
    if passthrough:
        tree.links.new(groups[1].outputs[1], surface.inputs["Alpha"])
    if linked:
        source = tree.nodes.new("ShaderNodeMath")
        source.name, source.operation = "外部加法", "ADD"
        source.inputs[0].default_value, source.inputs[1].default_value = 0.17, 0.31
        tree.links.new(source.outputs[0], groups[0].inputs[0])
    return material, groups


def audit_math(data, linked, passthrough):
    """按手工建立的连线和数值核对原生展开图，不使用展开器生成期望值。"""
    nodes = {n["id"]: n for n in data["nodes"]}
    surface = next(n for n in nodes.values() if n["type"] == "ShaderNodeBsdfPrincipled")
    incoming = {
        e["to"]["socket"]: e["from"]["node"]
        for e in data["links"]
        if e["to"]["node"] == surface["id"]
    }
    first, second = nodes[incoming["Roughness"]], nodes[incoming["Metallic"]]
    assert first["id"] != second["id"] and incoming["Coat Roughness"] == first["id"]
    assert equal(second["inputs"]["Value"], 0.6)
    assert equal(first["inputs"]["Value_001"], 0.25)
    assert equal(second["inputs"]["Value_001"], 0.25)
    if linked:
        edge = next(e for e in data["links"] if e["to"] == {"node": first["id"], "socket": "Value"})
        assert edge["from"] == {"node": "外部加法", "socket": "Value"}
        assert equal(nodes["外部加法"]["inputs"]["Value"], 0.17)
    else:
        assert equal(first["inputs"]["Value"], 0.2)
    if passthrough:
        assert equal(surface["inputs"]["Alpha"], 0.6)
        assert "Alpha" not in incoming
    assert first["metadata"]["source_group_path"][0] == "实例0"
    assert second["metadata"]["source_group_path"][0] == "实例1"


def make_image_case(path):
    """组外命名 UV 驱动组内图片，图片双输出和共享颜色输出都穿过接口。"""
    definition = bpy.data.node_groups.new("图片组", "ShaderNodeTree")
    for name, direction, kind in (
        ("坐标", "INPUT", "NodeSocketVector"),
        ("颜色", "OUTPUT", "NodeSocketColor"),
        ("透明度", "OUTPUT", "NodeSocketFloat"),
    ):
        definition.interface.new_socket(name=name, in_out=direction, socket_type=kind)
    entry = definition.nodes.new("NodeGroupInput")
    output = definition.nodes.new("NodeGroupOutput")
    texture = definition.nodes.new("ShaderNodeTexImage")
    texture.image = bpy.data.images.load(str(path), check_existing=False)
    texture.image.colorspace_settings.name = "Non-Color"
    mapping = definition.nodes.new("ShaderNodeMapping")
    mapping.inputs["Scale"].default_value = [2, 3, 1]
    definition.links.new(entry.outputs[0], mapping.inputs["Vector"])
    definition.links.new(mapping.outputs[0], texture.inputs["Vector"])
    definition.links.new(texture.outputs["Color"], output.inputs[0])
    definition.links.new(texture.outputs["Alpha"], output.inputs[1])
    material = bpy.data.materials.new("group_image_uv")
    material.use_nodes = True
    tree = material.node_tree
    surface = next(n for n in tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
    group = instance(tree, definition, "图片实例", [0, 0, 0])
    uv = tree.nodes.new("ShaderNodeUVMap")
    uv.uv_map = "组用UV.001"
    tree.links.new(uv.outputs[0], group.inputs[0])
    tree.links.new(group.outputs[0], surface.inputs["Base Color"])
    tree.links.new(group.outputs[0], surface.inputs["Emission Color"])
    tree.links.new(group.outputs[1], surface.inputs["Alpha"])
    return material, group


def make_shader_case():
    """完整表面位于组内，颜色输入使用实例覆盖值。"""
    tree = bpy.data.node_groups.new("表面组", "ShaderNodeTree")
    tree.interface.new_socket(name="底色", in_out="INPUT", socket_type="NodeSocketColor")
    tree.interface.new_socket(name="表面", in_out="OUTPUT", socket_type="NodeSocketShader")
    entry, output = tree.nodes.new("NodeGroupInput"), tree.nodes.new("NodeGroupOutput")
    surface = tree.nodes.new("ShaderNodeBsdfPrincipled")
    tree.links.new(entry.outputs[0], surface.inputs["Base Color"])
    tree.links.new(surface.outputs[0], output.inputs[0])
    material = bpy.data.materials.new("group_surface")
    material.use_nodes = True
    material.node_tree.nodes.clear()
    group = instance(material.node_tree, tree, "完整表面", [0.13, 0.37, 0.81, 1])
    output = material.node_tree.nodes.new("ShaderNodeOutputMaterial")
    material.node_tree.links.new(group.outputs[0], output.inputs["Surface"])
    return material


def main():
    """创建数学、图片和表面夹具；第二进程从独立文档完成保存重开验证。"""
    if not bpy.app.background or "--factory-startup" not in sys.argv:
        raise RuntimeError("需要独立后台 Blender 和 --factory-startup")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    args.out.mkdir(parents=True, exist_ok=False)
    service = CachedService(controller.create_service())
    cases = []
    for linked, nested, passthrough in (
        (False, False, False),
        (True, False, False),
        (False, True, False),
        (True, True, False),
        (False, True, True),
        (True, True, True),
    ):
        name = f"groups_{int(linked)}{int(nested)}{int(passthrough)}"
        material, groups = make_math_case(name, linked, nested, passthrough)
        before = snapshot(material)
        counts = (len(bpy.data.materials), len(bpy.data.node_groups), len(bpy.data.images))
        native = BlenderAdapter(material, service).read_material()
        audit_math(native, linked, passthrough)
        result = controller.export_material(
            BlenderAdapter(material, service),
            service,
            args.out / (name + ".mtlx"),
            accept_warnings=True,
        )
        assert result.saved_path, result
        assert snapshot(material) == before
        assert counts == (len(bpy.data.materials), len(bpy.data.node_groups), len(bpy.data.images))
        groups[0].mute = True
        blocked = controller.check_material(BlenderAdapter(material, service), service)
        assert blocked.status == "error" and any(i["code"] == "MUTED_NODE" for i in blocked.issues)
        groups[0].mute = False
        assert snapshot(material) == before
        cases.append({"name": name, "source_unchanged": True, "instances_and_links_verified": True})
        write_json(args.out / (name + "-source.json"), native)
    image_path = args.out / "group_texture.png"
    write_image(image_path, 4)
    image_material, group = make_image_case(image_path)
    for material in (image_material, make_shader_case()):
        before = snapshot(material)
        native = BlenderAdapter(material, service).read_material()
        if material == image_material:
            texture = next(iter(native["textures"].values()))
            assert texture["uri"] == str(image_path)
            assert texture["coordinates"]["set"] == "组用UV.001"
            assert texture["coordinates"]["transforms"] == [
                [2, 0, 0, 0, 0, 3, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
            ]
            assert sum(e["from"]["socket"] == "Color" for e in native["links"]) == 2
            assert sum(e["from"]["socket"] == "Alpha" for e in native["links"]) == 1
        else:
            assert len(native["nodes"]) == 1
            assert equal(native["nodes"][0]["inputs"]["Base Color"], [0.13, 0.37, 0.81])
        result = controller.export_material(
            BlenderAdapter(material, service),
            service,
            args.out / (material.name + ".mtlx"),
            accept_warnings=True,
        )
        assert result.saved_path, result
        assert snapshot(material) == before
        cases.append(
            {"name": material.name, "source_unchanged": True, "instances_and_links_verified": True}
        )
        write_json(args.out / (material.name + "-source.json"), native)
    # 常量向量连接与未连接图片的默认 UV 含义不同，不能在展开时合并。
    tree = image_material.node_tree
    tree.links.remove(group.inputs[0].links[0])
    rejected = controller.check_material(BlenderAdapter(image_material, service), service)
    assert rejected.status == "error" and any(
        i["code"] == "GROUP_DEFAULT_UNSUPPORTED" for i in rejected.issues
    )
    write_json(
        args.out / "summary.json",
        {"version": bpy.app.version_string, "cases": cases, "appearance_verified": False},
    )
    print(json.dumps({"cases": len(cases), "status": "ok"}), flush=True)


if __name__ == "__main__":
    main()
