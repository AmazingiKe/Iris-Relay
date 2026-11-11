"""在独立 Cinema 4D 进程验证窗口控制链路；不操作用户已有场景。"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def verify(source, out):
    """使用自建文档检查当前控制层、原生节点、保存重开、撤销及报告失效。"""
    import c4d

    from _scripts.verify_cinema4d_import import verify as verify_graph
    from plugins.cinema4d.adapter import Cinema4DAdapter
    from plugins.cinema4d.controller import PanelController

    before = hashlib.sha256(source.read_bytes()).hexdigest()
    out.mkdir(parents=True, exist_ok=False)
    document = c4d.documents.BaseDocument()
    panel = PanelController()
    reopened = None
    try:
        panel.connect("LOCAL", "")
        checked = panel.perform("check_import", document, str(source))
        assert checked["status"] != "error", checked
        assert len(document.GetMaterials()) == 0
        imported = panel.perform("import", document, str(source), accept_warnings=True)
        assert imported.get("created"), imported
        first = document.GetMaterials()[0]
        document.SetActiveMaterial(first)
        adapter = Cinema4DAdapter(document, panel.service)
        context = adapter.context()
        original_graph = verify_graph(adapter, imported["material"], first.GetName())

        standard = c4d.BaseMaterial(c4d.Mmaterial)
        standard.SetName("Existing_Standard_Material")
        document.InsertMaterial(standard)

        sentinel = c4d.BaseObject(c4d.Ocube)
        document.InsertObject(sentinel)
        tag = c4d.TextureTag()
        sentinel.InsertTag(tag)
        tag.SetMaterial(first)
        baseline_names = [material.GetName() for material in document.GetMaterials()]
        second = panel.perform("import", document, str(source), accept_warnings=True)
        assert second.get("created"), second
        assert tag.GetMaterial() == first and document.GetActiveMaterial() == first
        assert adapter.context() == context
        assert document.DoUndo()
        assert [material.GetName() for material in document.GetMaterials()] == baseline_names
        assert panel.invalidate_if_changed(document)
        assert panel.result is None
        assert document.DoRedo()
        assert len(document.GetMaterials()) == len(baseline_names) + 1
        assert document.DoUndo()

        exported = panel.perform("check_export", document)
        assert exported["status"] != "error", exported
        assert not exported.get("saved_path")
        path = out / "exported.mtlx"
        exported = panel.perform("export", document, str(path), accept_warnings=True)
        assert exported.get("saved_path") == str(path), exported
        protected = path.read_bytes()
        failed = panel.perform("export", document, str(path), accept_warnings=True)
        assert failed["status"] == "error"
        assert path.read_bytes() == protected

        scene = out / "restored.c4d"
        assert c4d.documents.SaveDocument(
            document, str(scene), c4d.SAVEDOCUMENTFLAGS_DONTADDTORECENTLIST, c4d.FORMAT_C4DEXPORT
        )
        reopened = c4d.documents.LoadDocument(
            str(scene), c4d.SCENEFILTER_MATERIALS | c4d.SCENEFILTER_OBJECTS
        )
        assert reopened is not None
        reopened_graph = verify_graph(
            Cinema4DAdapter(reopened, panel.service), imported["material"], first.GetName()
        )
        reopened.SetActiveMaterial(
            next(
                material
                for material in reopened.GetMaterials()
                if material.GetName() == first.GetName()
            )
        )
        roundtrip = panel.perform(
            "export", reopened, str(out / "reopened.mtlx"), accept_warnings=True
        )
        assert roundtrip.get("saved_path"), roundtrip
        assert hashlib.sha256(source.read_bytes()).hexdigest() == before
        summary = {
            "status": "ok",
            "host": adapter.target,
            "connection_verified": True,
            "import_check_no_mutation": True,
            "imported_graph": original_graph,
            "reopened_graph": reopened_graph,
            "undo": True,
            "redo": True,
            "binding_preserved": True,
            "selection_preserved": True,
            "context_preserved": True,
            "stale_report_cleared_after_undo": True,
            "existing_output_protected": True,
            "current_native_export_verified": True,
            "mixed_standard_material_scene": True,
            "source_sha256_unchanged": before,
            "gui_layout_verified": False,
            "appearance_verified": False,
        }
        (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), "utf8")
        return summary
    finally:
        panel.reset_connection()
        if reopened is not None:
            c4d.documents.KillDocument(reopened)
        c4d.documents.KillDocument(document)


def main():
    """仅用于独立 c4dpy；真实窗口的打开与截图须另行记录，不能由本检查冒充。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--document", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.document.resolve(), args.out.resolve())
    print(json.dumps(result, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    main()
