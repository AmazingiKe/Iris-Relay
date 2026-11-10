"""验证混合与渐变的原生导出、原生回导、保存重开及修改后再导出。"""

import argparse
import json
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from plugins.blender.adapter import BlenderAdapter  # noqa: E402
from plugins.core import create_service, request  # noqa: E402


def main():
    """只操作独立夹具场景；核对原生类型、控制点和颜色／Alpha 路由。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    args.out.mkdir(parents=True, exist_ok=False)
    cases = []
    with create_service(ROOT) as service:
        for mode in ("LINEAR", "EASE", "CONSTANT", "B_SPLINE"):
            material = bpy.data.materials.new("渐变与混合_" + mode)
            material.use_nodes = True
            tree = material.node_tree
            surface = next(n for n in tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
            ramp = tree.nodes.new("ShaderNodeValToRGB")
            ramp.name = "渐变"
            ramp.color_ramp.interpolation = mode
            ramp.color_ramp.elements[0].position = 0.2
            ramp.color_ramp.elements[0].color = [0.1, 0.2, 0.3, 0.4]
            ramp.color_ramp.elements[1].position = 0.8
            ramp.color_ramp.elements[1].color = [0.9, 0.8, 0.7, 0.6]
            mix = tree.nodes.new("ShaderNodeMixRGB")
            mix.name = "混合"
            mix.inputs["Fac"].default_value = 0.3
            tree.links.new(ramp.outputs["Color"], mix.inputs["Color1"])
            tree.links.new(mix.outputs["Color"], surface.inputs["Base Color"])
            tree.links.new(ramp.outputs["Alpha"], surface.inputs["Roughness"])
            source = BlenderAdapter(material, service).read_material()
            response = request(service, "materialx.export", material=source)
            document = response["result"]["document"]
            assert document, response
            (args.out / (mode + ".mtlx")).write_text(document, encoding="utf-8")
            target = request(
                service, "materialx.convert", document=document, target=source["renderer"]
            )
            assert target["result"]["material"], target
            created = BlenderAdapter(None, service).create_material(target["result"]["material"])
            restored_ramp = next(
                n for n in created.node_tree.nodes if n.bl_idname == "ShaderNodeValToRGB"
            )
            assert restored_ramp.color_ramp.interpolation == mode
            assert any(
                e.from_node == restored_ramp
                and e.from_socket.name == "Alpha"
                and e.to_socket.name == "Roughness"
                for e in created.node_tree.links
            )
            cases.append({"mode": mode, "material": created.name})
        path = args.out / "restored.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(path))
        bpy.ops.wm.open_mainfile(filepath=str(path))
        for case in cases:
            material = bpy.data.materials[case["material"]]
            ramp = next(n for n in material.node_tree.nodes if n.bl_idname == "ShaderNodeValToRGB")
            ramp.inputs["Fac"].default_value = 0.61
            source = BlenderAdapter(material, service).read_material()
            response = request(service, "materialx.export", material=source)
            assert response["result"]["document"], response
            case.update(reopened=True, edited_and_reexported=True)
    (args.out / "summary.json").write_text(
        json.dumps({"status": "ok", "cases": cases}, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
