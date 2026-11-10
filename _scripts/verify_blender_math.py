"""在独立 Blender 创建加减乘夹具，经公开 API 导出标准节点，并检查未支持模式阻断。"""

import argparse
import json
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from _scripts.verify_blender_batch import CachedService  # noqa: E402
from plugins.blender import controller  # noqa: E402
from plugins.blender.adapter import BlenderAdapter, source_snapshot  # noqa: E402


def main():
    """每次输出到新目录；后续用批量回读工具在另一空场景验证文件可独立还原。"""
    if not bpy.app.background or "--factory-startup" not in sys.argv:
        raise RuntimeError("需要独立后台 Blender 和 --factory-startup")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    args.out.mkdir(parents=True, exist_ok=False)
    service = CachedService(controller.create_service())
    cases = []
    for operation in ("ADD", "SUBTRACT", "MULTIPLY"):
        # 1. 手工创建原生链路，含负数、非对称输入及共享输出，避免只测生成器自己的图。
        material = bpy.data.materials.new("数学_" + operation)
        material.use_nodes = True
        tree = material.node_tree
        surface = next(n for n in tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
        first = tree.nodes.new("ShaderNodeMath")
        first.operation = operation
        first.inputs[0].default_value = -2.5
        first.inputs[1].default_value = 4.0
        second = tree.nodes.new("ShaderNodeMath")
        second.operation = operation
        second.inputs[1].default_value = 0.2
        tree.links.new(first.outputs[0], second.inputs[0])
        tree.links.new(first.outputs[0], surface.inputs["Metallic"])
        tree.links.new(second.outputs[0], surface.inputs["Roughness"])
        before = source_snapshot(material)
        adapter = BlenderAdapter(material, service)
        result = controller.export_material(
            adapter, service, args.out / (operation.lower() + ".mtlx"), accept_warnings=True
        )
        assert result.saved_path, result
        assert source_snapshot(material) == before
        # 2. 钳制和其他数学语义不等同于这三个标准节点，必须阻断并保留源快照。
        first.use_clamp = True
        blocked = controller.check_material(adapter, service)
        assert blocked.status == "error" and blocked.source_snapshot
        first.use_clamp = False
        first.operation = "DIVIDE"
        assert controller.check_material(adapter, service).status == "error"
        first.operation = operation
        assert source_snapshot(material) == before
        cases.append(
            {
                "operation": operation,
                "status": "ok",
                "source_unchanged": True,
                "clamp_and_unhandled_mode_blocked": True,
            }
        )
    report = {
        "status": "ok",
        "version": bpy.app.version_string,
        "cases": cases,
        "appearance_verified": False,
    }
    (args.out / "summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), "utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
