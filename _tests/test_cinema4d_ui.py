"""Cinema 4D 控制层复用公开流程，失败与场景变更不留下过期成功。"""

import sys
from types import SimpleNamespace

from plugins.cinema4d import controller


class Document:
    def __init__(self):
        self.material = object()
        self.revision = 0

    def GetActiveMaterial(self):
        return self.material


def setup_controller(monkeypatch):
    document = Document()
    service = object()
    adapter = SimpleNamespace(target={"id": "redshift"})
    monkeypatch.setattr(controller, "make_adapter", lambda doc, srv: adapter)
    monkeypatch.setattr(controller, "document_signature", lambda doc: (doc, doc.revision))
    panel = controller.PanelController()
    panel.service = service
    return document, service, adapter, panel


def test_import_uses_common_warning_policy(monkeypatch):
    document, service, adapter, panel = setup_controller(monkeypatch)
    calls = []

    def import_material(*args, **kwargs):
        calls.append((args, kwargs))
        return {"status": "warning", "created": None, "issues": []}

    monkeypatch.setattr(controller.core, "import_material", import_material)
    monkeypatch.setattr(controller, "stop_threads", lambda: None)
    result = panel.perform("import", document, "input.mtlx", accept_warnings=False)
    assert result["created"] is None
    assert calls == [((adapter, service, "input.mtlx", adapter.target), {"accept_warnings": False})]
    assert panel.result is result


def test_export_and_check_use_current_selection(monkeypatch):
    document, service, adapter, panel = setup_controller(monkeypatch)
    calls = []
    monkeypatch.setattr(
        controller.core,
        "check_export",
        lambda *args: calls.append(args) or {"status": "ok", "issues": []},
    )
    panel.perform("check_export", document)
    assert calls == [(adapter, service, document.material)]
    document.material = None
    result = panel.perform("check_export", document)
    assert result["status"] == "error"
    assert result["issues"][0]["code"] == "MATERIAL_NOT_SELECTED"


def test_scene_changes_clear_report(monkeypatch):
    document, _, _, panel = setup_controller(monkeypatch)
    panel.result = {"status": "ok", "created": {"material": "old"}}
    panel.signature = controller.document_signature(document)
    assert panel.invalidate_if_changed(document) is False
    document.revision += 1
    assert panel.invalidate_if_changed(document) is True
    assert panel.result is None


def test_connection_switch_closes_owned_service(monkeypatch):
    _, _, _, panel = setup_controller(monkeypatch)
    closed = []
    monkeypatch.setattr(controller, "close_service", closed.append)
    old = panel.service
    panel.result = {"status": "ok"}
    panel.reset_connection()
    assert closed == [old]
    assert panel.service is None and panel.result is None


def test_operation_without_connection_is_explicit_error(monkeypatch):
    monkeypatch.setattr(controller, "document_signature", lambda doc: None)
    panel = controller.PanelController()
    result = panel.perform("check_import", Document(), "input.mtlx")
    assert result["status"] == "error"
    assert result["issues"][0]["code"] == "SERVICE_NOT_CONNECTED"


def test_standard_material_without_node_graph_is_valid_snapshot(monkeypatch):
    monkeypatch.setitem(sys.modules, "c4d", SimpleNamespace(DIRTYFLAGS_DATA=0))

    class StandardMaterial:
        def GetNodeMaterialReference(self):
            raise RuntimeError("此材质没有节点系统")

        def GetDirty(self, flags):
            return 7

        def GetName(self):
            return "Existing Standard"

    material = StandardMaterial()
    document = SimpleNamespace(
        GetMaterials=lambda: [material],
        GetDirty=lambda flags: 11,
        GetActiveMaterial=lambda: material,
    )
    assert controller.document_signature(document)[-1] == (
        (material, "Existing Standard", 7, None),
    )


def test_snapshot_failure_does_not_erase_completed_action(monkeypatch):
    document, _, _, panel = setup_controller(monkeypatch)
    expected = {"status": "ok", "created": {"material": "created"}, "issues": []}
    monkeypatch.setattr(controller.core, "import_material", lambda *args, **kwargs: expected)
    monkeypatch.setattr(controller, "stop_threads", lambda: None)

    def fail_snapshot(doc):
        raise RuntimeError("document lifetime changed")

    monkeypatch.setattr(controller, "document_signature", fail_snapshot)
    assert panel.perform("import", document, "material.mtlx") is expected
    assert panel.signature is None


def test_connection_error_report_is_retained_until_scene_changes(monkeypatch):
    document, _, _, panel = setup_controller(monkeypatch)
    report = controller.failure(RuntimeError("连接失败"))
    panel.record_result(report, document)
    assert panel.invalidate_if_changed(document) is False
    assert panel.result is report
    document.revision += 1
    assert panel.invalidate_if_changed(document) is True


def test_import_report_ignores_selection_but_detects_graph_edits(monkeypatch):
    document, _, _, panel = setup_controller(monkeypatch)
    monkeypatch.setattr(
        controller, "document_signature", lambda doc: (doc, 0, doc.material, doc.revision)
    )
    panel.record_result({"status": "ok"}, document, track_selection=False)
    document.material = object()
    assert panel.invalidate_if_changed(document) is False
    document.revision += 1
    assert panel.invalidate_if_changed(document) is True


def test_export_report_tracks_selected_source(monkeypatch):
    document, _, _, panel = setup_controller(monkeypatch)
    monkeypatch.setattr(
        controller, "document_signature", lambda doc: (doc, 0, doc.material, doc.revision)
    )
    panel.record_result({"status": "ok"}, document, track_selection=True)
    document.material = object()
    assert panel.invalidate_if_changed(document) is True
