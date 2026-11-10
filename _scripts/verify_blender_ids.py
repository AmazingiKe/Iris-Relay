"""验证超过 Blender 原生名称长度的协议 ID、保存重开及原生参数编辑。"""

import argparse
import json
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from _scripts.verify_blender_batch import compare_target_graph  # noqa: E402
from plugins.blender.adapter import BlenderAdapter, renderer_ref  # noqa: E402
from plugins.core import create_service, request  # noqa: E402


def main():
    """逻辑 ID 只用于追踪身份，数值和连线始终读取当前宿主。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    args.out.mkdir(parents=True, exist_ok=False)
    identifier = "long_identity_" + "material_node_" * 8
    document = f'''<materialx version="1.39">
<standard_surface name="{identifier}" type="surfaceshader"
 nodedef="ND_standard_surface_surfaceshader"/>
<surfacematerial name="material" type="material" nodedef="ND_surfacematerial">
<input name="surfaceshader" type="surfaceshader" nodename="{identifier}"/>
</surfacematerial></materialx>'''
    with create_service(ROOT) as service:
        response = request(service, "materialx.convert", document=document, target=renderer_ref())
        expected = response["result"]["material"]
        assert expected, response
        material = BlenderAdapter(None, service).create_material(expected)
        name = material.name
        bpy.ops.wm.save_as_mainfile(filepath=str(args.out / "restored.blend"))
        bpy.ops.wm.open_mainfile(filepath=str(args.out / "restored.blend"))
        material = bpy.data.materials[name]
        actual = BlenderAdapter(material, service).read_material()
        compare_target_graph(expected, actual)
        surface = next(
            n for n in material.node_tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled"
        )
        surface.inputs["IOR"].default_value = 1.57
        current = BlenderAdapter(material, service).read_material()
        assert current["output"]["node"] == identifier
        assert (
            abs(next(n for n in current["nodes"] if n["id"] == identifier)["inputs"]["IOR"] - 1.57)
            < 1e-6
        )
    (args.out / "summary.json").write_text(
        json.dumps(
            {"identity_preserved": True, "current_edit_read": True, "reopened": True}, indent=2
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
