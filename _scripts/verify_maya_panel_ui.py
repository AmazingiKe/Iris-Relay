"""在独立 Maya 2022 GUI 中验证连接、导入导出面板并保存真实截图。"""

import hashlib
import json
import os
import sys
import traceback
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    """仅执行显式 job 的测试场景；所有新资源写入独占输出目录。"""
    from maya import cmds
    from maya.OpenMayaUI import MQtUtil
    from PySide2.QtCore import QTimer
    from PySide2.QtWidgets import QApplication, QLineEdit, QWidget
    from shiboken2 import wrapInstance

    job_path = os.environ.get("IRIS_PANEL_UI_JOB")
    if not job_path:
        raise RuntimeError("只能在工具专门启动的 GUI 进程中运行")
    job = json.loads(Path(job_path).read_text("utf8"))
    output = Path(job["out"])
    output.mkdir(parents=True, exist_ok=False)
    checks = {"pid": os.getpid(), "status": "running", "checks": []}
    panel = None

    def save():
        (output / "summary.json").write_text(
            json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf8"
        )

    def capture(name):
        QApplication.processEvents()
        widget = wrapInstance(int(MQtUtil.findWindow(panel.window)), QWidget)
        assert widget.grab().save(str(output / (name + ".png")))

    def verify():
        """按用户操作顺序检查原生场景副作用，截图反映实际控件与报告。"""
        nonlocal panel
        try:
            cmds.file(new=True, force=True)
            cmds.loadPlugin("mtoa", quiet=True)
            from plugins.maya.ui import show

            panel = show()
            cmds.optionMenu(panel.mode, edit=True, select=2)
            panel.change_connection()
            assert panel.detect_connection()
            checks["checks"].append("LOCAL 连接与能力查询")

            source = cmds.shadingNode("aiStandardSurface", asShader=True, name="IrisPanelSource")
            cmds.setAttr(source + ".baseColor", 0.32, 0.18, 0.08, type="double3")
            cmds.setAttr(source + ".specularRoughness", 0.38)
            other_source = cmds.shadingNode(
                "aiStandardSurface", asShader=True, name="IrisPanelOther"
            )
            before = set(cmds.ls(materials=True) or [])
            source_value = cmds.getAttr(source + ".baseColor")
            cmds.select(source, replace=True)
            panel.select_export_materials()
            export_dir = output / "export"
            export_dir.mkdir()
            cmds.textFieldButtonGrp(panel.export_directory, edit=True, text=str(export_dir))
            cmds.checkBox(panel.export_warning, edit=True, value=True)
            cmds.tabLayout(panel.tabs, edit=True, selectTab=panel.export_tab)
            panel.check_export()
            assert not list(export_dir.glob("*.mtlx"))
            assert len(panel.export_results) == 1
            assert panel.export_results[0]["status"] != "error", panel.export_results
            checks["checks"].append("导出检查不写文件")
            cmds.checkBox(panel.export_warning, edit=True, value=False)
            panel.export_selected()
            assert not list(export_dir.glob("*.mtlx"))
            cmds.checkBox(panel.export_warning, edit=True, value=True)
            panel.export_selected()
            assert len(list(export_dir.glob("*.mtlx"))) == 1
            document = list(export_dir.glob("*.mtlx"))[0]
            assert panel.export_results[0]["saved_path"] == str(document)
            assert cmds.getAttr(source + ".baseColor") == source_value
            capture("export")
            checks["checks"].append("当前选中 Arnold 材质真实导出")
            saved_export = deepcopy(panel.export_results)
            saved_export_status = cmds.text(panel.export_status, query=True, label=True)
            cmds.select(other_source, replace=True)
            panel.select_export_materials()
            assert not panel.export_results
            assert "已导出" not in cmds.text(panel.export_status, query=True, label=True)
            cmds.select(source, replace=True)
            panel.export_results = deepcopy(saved_export)
            panel.show_export_report()
            cmds.text(panel.export_status, edit=True, label=saved_export_status)
            field = wrapInstance(int(MQtUtil.findControl(panel.export_directory)), QWidget)
            edit = field.findChildren(QLineEdit)[0]
            edit.setText(str(output))
            edit.editingFinished.emit()
            QApplication.processEvents()
            assert not panel.export_results
            assert "已导出" not in cmds.text(panel.export_status, query=True, label=True)
            panel.export_results = deepcopy(saved_export)
            panel.show_export_report()
            cmds.text(panel.export_status, edit=True, label=saved_export_status)
            dialog = cmds.fileDialog2
            try:
                cmds.fileDialog2 = lambda **kwargs: [str(export_dir)]
                panel.choose_export_directory()
            finally:
                cmds.fileDialog2 = dialog
            assert not panel.export_results
            assert "已导出" not in cmds.text(panel.export_status, query=True, label=True)
            checks["checks"].append("新选择、手输输出目录及浏览目录清旧报告和统计")
            digest = hashlib.sha256(document.read_bytes()).hexdigest()
            panel.export_selected()
            assert panel.export_results[0]["status"] == "error"
            assert hashlib.sha256(document.read_bytes()).hexdigest() == digest
            checks["checks"].append("已有输出拒绝覆盖；警告需显式允许")

            panel.set_paths([document])
            cmds.tabLayout(panel.tabs, edit=True, selectTab=panel.import_tab)
            cmds.checkBox(panel.warning, edit=True, value=True)
            panel.run(False)
            assert set(cmds.ls(materials=True) or []) == before
            assert panel.results[str(document)]["status"] != "error", panel.results
            checks["checks"].append("导入检查不创建节点")
            cmds.checkBox(panel.warning, edit=True, value=False)
            panel.run(True)
            assert set(cmds.ls(materials=True) or []) == before
            cmds.checkBox(panel.warning, edit=True, value=True)
            panel.run(True)
            created = panel.results[str(document)]["created"]
            assert created and cmds.objExists(created["material"])
            assert cmds.getAttr(source + ".baseColor") == source_value
            panel.select_created()
            assert created["material"] in cmds.ls(selection=True)
            report = cmds.scrollField(panel.details, query=True, text=True)
            assert report and not report.lstrip().startswith("{")
            panel.copy_report()
            copied = json.loads(QApplication.clipboard().text())
            assert copied["created"]["material"] == created["material"]
            capture("import")
            checks["checks"].append("导入新材质、摘要与复制完整报告")
            saved_import = deepcopy(panel.results)
            saved_import_status = cmds.text(panel.status, query=True, label=True)
            panel.set_paths([document])
            assert not panel.results
            assert "已导入" not in cmds.text(panel.status, query=True, label=True)
            checks["checks"].append("设置输入文件清旧导入统计")
            panel.results = saved_import
            panel.export_results = deepcopy(saved_export)
            panel.refresh()
            panel.show_report()
            panel.show_export_report()
            cmds.text(panel.status, edit=True, label=saved_import_status)
            cmds.text(panel.export_status, edit=True, label=saved_export_status)
            # 不调用 change_connection，直接检测以覆盖同一配置失联时的旧成功状态。
            cmds.optionMenu(panel.mode, edit=True, select=3)
            cmds.textField(panel.url, edit=True, text="http://127.0.0.1:1")
            assert not panel.detect_connection()
            assert not panel.results and not panel.export_results
            assert "已导入" not in cmds.text(panel.status, query=True, label=True)
            assert "已导出" not in cmds.text(panel.export_status, query=True, label=True)
            checks["checks"].append("手动重新检测失败清两页旧成功状态")

            cmds.window(panel.window, edit=True, widthHeight=(620, 620))
            capture("compact")
            cmds.window(panel.window, edit=True, widthHeight=(760, 700))
            if job.get("http_url"):
                cmds.optionMenu(panel.mode, edit=True, select=3)
                cmds.textField(panel.url, edit=True, text=job["http_url"])
                panel.change_connection()
                assert not panel.results and not panel.export_results
                assert panel.detect_connection()
                panel.set_paths([document])
                panel.run(False)
                assert panel.results[str(document)]["status"] != "error"
                capture("http")
                checks["checks"].append("HTTP 真实连接与导入检查、切换清旧报告")
                cmds.textField(panel.url, edit=True, text="http://127.0.0.1:1")
                panel.change_connection()
                assert not panel.detect_connection()
                assert panel.service is None
                capture("connection-error")
                checks["checks"].append("连接失败显示错误，不回退")

            checks["status"] = "passed"
            checks["source_preserved"] = cmds.getAttr(source + ".baseColor") == source_value
            save()
        except Exception:
            checks["status"] = "failed"
            checks["error"] = traceback.format_exc()
            save()
        finally:
            if panel is not None:
                panel.dispose()
            QTimer.singleShot(500, lambda: cmds.quit(force=True))

    save()
    QTimer.singleShot(2000, verify)


if __name__ == "__main__":
    main()
