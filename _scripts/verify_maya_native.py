"""直接创建无 Iris 标记的 Maya 原生图，验证参数和拓扑编辑后仍按当前图导出。"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    """所有场景和节点独立创建，逐项记录源码无关的原生证据和负例。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    import maya.standalone

    maya.standalone.initialize(name="python")
    try:
        from maya import cmds

        from plugins.core import create_service, export_material
        from plugins.maya.adapter import MayaAdapter, ensure_environment, set_value

        target = ensure_environment()
        cmds.file(new=True, force=True)
        surface = cmds.shadingNode("aiStandardSurface", asShader=True, name="独立原生材质")
        image = cmds.shadingNode("aiImage", asTexture=True, name="原始贴图")
        set_value(image + ".filename", args.image.resolve().as_posix())
        set_value(image + ".colorSpace", "Raw")
        set_value(image + ".autoTx", False)
        set_value(image + ".ignoreColorSpaceFileRules", True)
        multiply = cmds.shadingNode("aiMultiply", asUtility=True, name="颜色乘积")
        set_value(multiply + ".input2", [0.7, 0.8, 0.9])
        cmds.connectAttr(image + ".outColor", multiply + ".input1")
        cmds.connectAttr(multiply + ".outColor", surface + ".baseColor")
        set_value(surface + ".specularRoughness", 0.24)
        group = cmds.sets(renderable=True, noSurfaceShader=True, empty=True)
        cmds.connectAttr(surface + ".outColor", group + ".surfaceShader")
        for node in (surface, image, multiply):
            assert not cmds.attributeQuery("irisImplementation", node=node, exists=True)
        cases = []
        with create_service(ROOT) as service:
            adapter = MayaAdapter(service, target)
            first = export_material(
                adapter, service, surface, args.out / "before.mtlx", accept_warnings=True
            )
            assert first["saved_path"], first
            assert len(first["material"]["nodes"]) == 3 and len(first["material"]["links"]) == 2
            cases.append({"id": "before", "nodes": 3, "links": 2, "exported": True})
            # 参数和拓扑均在当前宿主中修改；导出入口不能重放最初的三节点图。
            set_value(surface + ".specularRoughness", 0.43)
            set_value(multiply + ".input2", [0.5, 0.8, 1.1])
            add = cmds.shadingNode("aiAdd", asUtility=True, name="编辑后新增颜色加法")
            set_value(add + ".input2", [0.05, 0.03, 0.01])
            cmds.connectAttr(multiply + ".outColor", add + ".input1")
            cmds.connectAttr(add + ".outColor", surface + ".baseColor", force=True)
            cmds.file(rename=str(args.out / "edited.ma"))
            cmds.file(save=True, type="mayaAscii")
            cmds.file(new=True, force=True)
            cmds.file(str(args.out / "edited.ma"), open=True, force=True, prompt=False)
            second = export_material(
                adapter, service, surface, args.out / "after.mtlx", accept_warnings=True
            )
            assert second["saved_path"], second
            assert len(second["material"]["nodes"]) == 4 and len(second["material"]["links"]) == 3
            actual = next(n for n in second["material"]["nodes"] if n["id"] == surface)
            assert abs(actual["inputs"]["specularRoughness"] - 0.43) < 1e-6
            cases.append(
                {
                    "id": "after",
                    "nodes": 4,
                    "links": 3,
                    "exported": True,
                    "reopened": True,
                    "current_values_verified": True,
                }
            )
            displacement = cmds.shadingNode("displacementShader", asShader=True)
            cmds.connectAttr(displacement + ".displacement", group + ".displacementShader")
            try:
                adapter.read_material(surface)
            except Exception as exc:
                assert getattr(exc, "code", None) == "MAYA_OUTPUT_UNSUPPORTED", repr(exc)
            else:
                raise AssertionError("位移出口被静默丢弃")
            cmds.disconnectAttr(displacement + ".displacement", group + ".displacementShader")
            unknown = cmds.shadingNode("file", asTexture=True)
            cmds.connectAttr(unknown + ".outColor", surface + ".baseColor", force=True)
            try:
                adapter.read_material(surface)
            except Exception as exc:
                assert getattr(exc, "code", None) == "UNSUPPORTED_NODE", repr(exc)
            else:
                raise AssertionError("未知原生节点被静默丢弃")
            (args.out / "details.json").write_text(
                json.dumps({"before": first, "after": second}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        (args.out / "summary.json").write_text(
            json.dumps(
                {
                    "cases": cases,
                    "unmarked_native_graph": True,
                    "negative_cases": {"displacement": True, "unknown_node": True},
                    "appearance_verified": False,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    finally:
        maya.standalone.uninitialize()


if __name__ == "__main__":
    main()
