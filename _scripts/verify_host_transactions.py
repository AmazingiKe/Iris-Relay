"""在独立宿主场景验证事务、失败回滚、原生编辑后的导出和未知节点拒绝。"""

import argparse
import json
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def maya_checks(service, args):
    """重开已验证材质库，在内存内编辑与注入失败，不保存覆盖原场景。"""
    from maya import cmds

    from _scripts.verify_maya_import import verify_transactions
    from plugins.core import export_material, import_material
    from plugins.maya.adapter import MayaAdapter, ensure_environment

    cmds.file(str(args.scene), open=True, force=True, prompt=False)
    adapter = MayaAdapter(service, ensure_environment())
    path = sorted(args.documents.glob("*.mtlx"))[0]
    imported = import_material(adapter, service, path, adapter.target, accept_warnings=True)
    assert imported["created"], imported
    result = verify_transactions(adapter, imported["material"], imported["created"])
    surface = imported["created"]["material"]
    cmds.setAttr(surface + ".coat", 0.223)
    live = export_material(
        adapter, service, surface, args.out / "edited.mtlx", accept_warnings=True
    )
    assert live["saved_path"], live
    coat = ET.fromstring(Path(live["saved_path"]).read_text("utf-8")).find(
        "standard_surface/input[@name='coat']"
    )
    assert abs(float(coat.get("value")) - 0.223) < 2e-6
    result["live_edit_exported"] = True
    before = set(cmds.ls())
    # 1. 未知计算节点不能被旧导入提示或原文缓存掩盖。
    unknown = cmds.createNode("multiplyDivide")
    cmds.connectAttr(unknown + ".outputX", surface + ".coat")
    failed = export_material(
        adapter, service, surface, args.out / "unknown.mtlx", accept_warnings=True
    )
    assert failed["status"] == "error" and not failed["saved_path"]
    assert failed["issues"][-1]["code"] == "UNSUPPORTED_NODE", failed
    cmds.delete(unknown)
    assert set(cmds.ls()) == before
    result["unknown_node_blocked"] = True
    # 2. 变更未映射的着色属性后，旧实现提示也必须失败。
    cmds.setAttr(surface + ".subsurface", 0.31)
    failed = export_material(
        adapter, service, surface, args.out / "stale.mtlx", accept_warnings=True
    )
    assert failed["status"] == "error" and not failed["saved_path"]
    assert failed["issues"][-1]["code"] == "NATIVE_HINT_MISMATCH", failed
    cmds.setAttr(surface + ".subsurface", 0)
    result["stale_hint_blocked"] = True
    return result


def cinema_checks(service, args):
    """在独立 C4D 文档检查新增材质撤销和重做，并注入部分创建失败。"""
    import c4d

    from _scripts.verify_cinema4d_import import verify
    from plugins.cinema4d import adapter as host
    from plugins.core import import_material

    document = c4d.documents.BaseDocument()
    try:
        adapter = host.Cinema4DAdapter(document, service)
        path = sorted(args.documents.glob("*.mtlx"))[0]
        first = import_material(adapter, service, path, adapter.target, accept_warnings=True)
        assert first["created"], first
        existing = document.GetMaterials()[0]
        sentinel = c4d.BaseObject(c4d.Ocube)
        document.InsertObject(sentinel)
        tag = c4d.TextureTag()
        sentinel.InsertTag(tag)
        tag.SetMaterial(existing)
        document.SetActiveMaterial(existing)
        before = [m.GetName() for m in document.GetMaterials()]
        context = adapter.context()
        second = import_material(adapter, service, path, adapter.target, accept_warnings=True)
        assert second["created"], second
        verify(adapter, second["material"], second["created"]["material"])
        assert document.DoUndo()
        assert [m.GetName() for m in document.GetMaterials()] == before, (
            [m.GetName() for m in document.GetMaterials()],
            before,
        )
        assert document.DoRedo()
        verify(adapter, second["material"], second["created"]["material"])
        assert document.DoUndo()
        assert [m.GetName() for m in document.GetMaterials()] == before
        original, calls = host.set_value, []

        def fail_after_partial_creation(*values):
            calls.append(values[1])
            if len(calls) == 2:
                raise RuntimeError("注入原生写入失败")
            return original(*values)

        host.set_value = fail_after_partial_creation
        try:
            failed = import_material(adapter, service, path, adapter.target, accept_warnings=True)
        finally:
            host.set_value = original
        assert len(calls) == 2 and failed["status"] == "error" and failed["created"] is None
        assert [m.GetName() for m in document.GetMaterials()] == before
        original_groups = adapter.create_editing_groups

        def fail_after_groups(*values):
            """验证分组完成后的失败也只影响私有准备材质。"""
            original_groups(*values)
            raise RuntimeError("注入分组后的原生失败")

        adapter.create_editing_groups = fail_after_groups
        try:
            failed = import_material(adapter, service, path, adapter.target, accept_warnings=True)
        finally:
            adapter.create_editing_groups = original_groups
        assert failed["status"] == "error" and failed["created"] is None
        assert [m.GetName() for m in document.GetMaterials()] == before
        assert tag.GetMaterial() == existing
        assert document.GetActiveMaterial() == existing
        assert adapter.context() == context
        return {
            "undo": True,
            "redo": True,
            "partial_failure_cleanup": True,
            "group_failure_cleanup": True,
            "binding_preserved": True,
            "selection_preserved": True,
            "context_preserved": True,
        }
    finally:
        c4d.documents.KillDocument(document)


def main():
    """只在测试进程运行；输出目录须新建，失败不能生成成功报告。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=["maya", "cinema4d"], required=True)
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--scene", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    if args.host == "maya":
        import maya.standalone

        maya.standalone.initialize(name="python")
    from plugins.core import create_service

    with create_service() as service:
        result = maya_checks(service, args) if args.host == "maya" else cinema_checks(service, args)
    (args.out / "summary.json").write_text(
        json.dumps(
            {"status": "ok", "host": args.host, "checks": result}, ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )
    if args.host == "maya":
        maya.standalone.uninitialize()


if __name__ == "__main__":
    main()
