"""独立 Blender 进程验证表面读取、坐标链和明确排除的位移。"""

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "_scripts")]


def main():
    if not bpy.app.background or "--factory-startup" not in sys.argv:
        raise RuntimeError("请使用独立的 --background --factory-startup 进程")
    from native_fixture import fixture
    from verify_native import write_png

    from plugins.blender import BlenderAdapter
    from plugins.blender.controller import check_material, create_service, export_material

    service = create_service()
    profile = json.loads((ROOT / "dictionaries/blender-cycles-5.2.3.json").read_text("utf-8"))
    with TemporaryDirectory(prefix="iris-read-") as directory:
        image_path = Path(directory) / "fixture.png"
        write_png(image_path)
        original_texture = image_path.read_bytes()
        material = BlenderAdapter(None, service).create_material(fixture(profile, str(image_path)))
        tree = material.node_tree
        image = next(n for n in tree.nodes if n.bl_idname == "ShaderNodeTexImage")
        shader = next(n for n in tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
        output = next(n for n in tree.nodes if n.bl_idname == "ShaderNodeOutputMaterial")
        uv = tree.nodes.new("ShaderNodeTexCoord")
        mapping = tree.nodes.new("ShaderNodeMapping")
        reroute = tree.nodes.new("NodeReroute")
        frame = tree.nodes.new("NodeFrame")
        image.parent = frame
        tree.links.new(uv.outputs["UV"], mapping.inputs["Vector"])
        tree.links.new(mapping.outputs["Vector"], reroute.inputs[0])
        tree.links.new(reroute.outputs[0], image.inputs["Vector"])
        tree.links.new(image.outputs["Color"], shader.inputs["Roughness"])
        displacement = tree.nodes.new("ShaderNodeDisplacement")
        displacement.inputs["Scale"].default_value = 0.014
        tree.links.new(image.outputs["Color"], displacement.inputs["Height"])
        tree.links.new(displacement.outputs[0], output.inputs["Displacement"])
        nodes_before, links_before = len(tree.nodes), len(tree.links)
        strict = check_material(BlenderAdapter(material, service), service)
        assert strict.status == "error"
        assert strict.source_snapshot
        adapter = BlenderAdapter(material, service, surface_only=True)
        path = Path(directory) / "surface.mtlx"
        result = export_material(adapter, service, str(path))
        assert result.status == "warning" and not path.exists(), result
        codes = {i["code"] for i in result.issues}
        assert {"DISPLACEMENT_EXCLUDED", "DEFAULT_UV_RESOLVED", "IMPLICIT_CAST_EXPLICIT"} <= codes
        result = export_material(adapter, service, str(path), accept_warnings=True)
        assert result.saved_path == str(path), result
        assert result.validation_response["result"]["valid"] is True
        assert "luminance" in path.read_text("utf-8")
        assert any(n["type"] == "ShaderNodeDisplacement" for n in result.source_snapshot["nodes"])
        assert len(tree.nodes) == nodes_before and len(tree.links) == links_before
        assert image_path.read_bytes() == original_texture

        # 未接入输出的节点保留快照并报告，不误判为表面支持。
        noise = tree.nodes.new("ShaderNodeTexNoise")
        result = check_material(adapter, service)
        assert result.status == "warning"
        assert any(i["code"] == "UNUSED_NODES" for i in result.issues)
        tree.links.new(noise.outputs["Color"], shader.inputs["Base Color"])
        assert check_material(adapter, service).status == "error"
        tree.nodes.remove(noise)

        # 恒等映射以外、错误坐标来源与静音节点均不能退化成默认 UV。
        mapping.inputs["Scale"].default_value = (2, 1, 1)
        assert check_material(adapter, service).status == "error"
        mapping.inputs["Scale"].default_value = (1, 1, 1)
        tree.links.new(uv.outputs["Generated"], mapping.inputs["Vector"])
        assert check_material(adapter, service).status == "error"
        tree.links.new(uv.outputs["UV"], mapping.inputs["Vector"])
        mapping.mute = True
        assert check_material(adapter, service).status == "error"
        mapping.mute = False
        assert check_material(adapter, service).status == "warning"

    print(
        json.dumps(
            {
                "status": "ok",
                "version": bpy.app.version_string,
                "checks": "位移策略、源快照、恒等 UV、转接点、颜色转标量、未知节点和源保护",
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
