"""Maya 脚本编辑器启动的 MaterialX 导入窗口；界面不解释转换规则。"""

import json
from pathlib import Path

from maya import cmds

from . import controller
from .adapter import MayaAdapter, ensure_environment

WINDOW = "IrisRelayMaterialXImport"
_panel = None


class ImportPanel:
    """选择文件、检查与批量新建材质；每个文件独立报告失败。"""

    def __init__(self):
        self.paths = []
        self.results = {}
        self.service = self.adapter = self.target = None
        if cmds.window(WINDOW, exists=True):
            cmds.deleteUI(WINDOW)
        self.window = cmds.window(
            WINDOW, title="Iris Relay · Maya 2022 MaterialX", widthHeight=(760, 620)
        )
        cmds.columnLayout(adjustableColumn=True, rowSpacing=7)
        cmds.text(label="导入为可编辑 Arnold 节点；创建新材质，不自动替换对象绑定。", align="left")
        cmds.rowLayout(numberOfColumns=2, adjustableColumn=1, columnWidth2=(570, 180))
        cmds.button(label="选择 .mtlx 文件…", height=32, command=self.choose_files)
        cmds.button(label="选择目录…", width=180, height=32, command=self.choose_directory)
        cmds.setParent("..")
        self.files = cmds.textScrollList(
            allowMultiSelection=True, height=210, selectCommand=self.show_report
        )
        self.warning = cmds.checkBox(label="允许带警告导入／导出（外观尚未验证）", value=False)
        cmds.rowLayout(numberOfColumns=3, adjustableColumn=1, columnWidth3=(300, 240, 200))
        cmds.button(label="检查选中", height=32, command=lambda *_: self.run(False))
        cmds.button(
            label="导入选中为新材质", width=240, height=32, command=lambda *_: self.run(True)
        )
        cmds.button(label="选中已导入材质", width=200, height=32, command=self.select_created)
        cmds.setParent("..")
        self.status = cmds.text(
            label="选择文件后检查；可多选，单个失败不影响其他文件。", align="left"
        )
        self.details = cmds.scrollField(editable=False, wordWrap=False, height=230, text="")
        cmds.button(label="复制当前报告", height=32, command=self.copy_report)
        cmds.button(label="查看节点与共享参数", height=32, command=self.show_created_graph)
        cmds.button(
            label="将场景选中的 Arnold 材质导出为 .mtlx…", height=32, command=self.export_selected
        )
        cmds.showWindow(self.window)
        # Maya 英文安装的默认字体可能缺少中文字形，只调整本面板的字体。
        from PySide2.QtWidgets import QWidget
        from shiboken2 import wrapInstance

        from maya.OpenMayaUI import MQtUtil

        widget = wrapInstance(int(MQtUtil.findWindow(self.window)), QWidget)
        widget.setStyleSheet('QWidget { font-family: "Microsoft YaHei UI"; font-size: 16px; }')
        cmds.window(self.window, edit=True, widthHeight=(760, 700))
        for event in ("Undo", "Redo", "SceneOpened", "NewSceneOpened"):
            cmds.scriptJob(event=[event, self.invalidate], parent=self.window)
        cmds.scriptJob(uiDeleted=[self.window, self.dispose], runOnce=True)

    def dispose(self, *_):
        """窗口关闭或脚本重载时回收本窗口启动的核心进程。"""
        if self.service is not None and hasattr(self.service, "close"):
            self.service.close()
        self.service = self.adapter = self.target = None

    def invalidate(self, *_):
        """撤销、重做或换场景后清除旧报告，避免把已不存在的材质显示为成功。"""
        self.results.clear()
        self.refresh()
        self.show_report()
        cmds.text(self.status, edit=True, label="场景已变化，请重新检查文件。")

    def set_paths(self, paths):
        """添加明确选择的文件，去重后全选；不递归扫描无关目录。"""
        self.paths = sorted({str(Path(p).resolve()) for p in paths})
        self.results.clear()
        self.refresh()
        if self.paths:
            cmds.textScrollList(
                self.files, edit=True, selectIndexedItem=list(range(1, len(self.paths) + 1))
            )

    def choose_files(self, *_):
        """由 Maya 文件选择器选择一份或多份 MaterialX。"""
        paths = cmds.fileDialog2(
            fileMode=4, fileFilter="MaterialX (*.mtlx)", caption="选择 MaterialX 文件"
        )
        if paths:
            self.set_paths(paths)

    def export_selected(self, *_):
        """从当前选择读取原生图并逐材质导出；不使用列表中的导入缓存。"""
        surfaces = [
            node
            for node in (cmds.ls(selection=True) or [])
            if cmds.nodeType(node) == "aiStandardSurface"
        ]
        if not surfaces:
            cmds.text(self.status, edit=True, label="请在场景中选择 aiStandardSurface 材质节点。")
            return
        paths = cmds.fileDialog2(fileMode=3, caption="选择新 MaterialX 文件的输出目录")
        if not paths:
            return
        try:
            if self.service is None:
                target = ensure_environment()
                service = controller.create_service()
                adapter = MayaAdapter(service, target)
                self.target, self.service, self.adapter = target, service, adapter
            results = []
            for index, surface in enumerate(surfaces):
                results.append(
                    controller.export_material(
                        self.adapter,
                        self.service,
                        surface,
                        Path(paths[0]) / f"maya_material_{index:04d}.mtlx",
                        accept_warnings=cmds.checkBox(self.warning, query=True, value=True),
                    )
                )
            cmds.scrollField(
                self.details, edit=True, text=json.dumps(results, ensure_ascii=False, indent=2)
            )
            cmds.text(
                self.status,
                edit=True,
                label=f"导出 {sum(bool(r['saved_path']) for r in results)} / {len(results)} 份；"
                "已有文件不会覆盖。",
            )
        except (ValueError, RuntimeError) as exc:
            cmds.text(self.status, edit=True, label="导出失败：" + str(exc))

    def choose_directory(self, *_):
        """读取选择目录的直属 .mtlx，不触碰其他文件。"""
        paths = cmds.fileDialog2(fileMode=3, caption="选择 MaterialX 目录")
        if paths:
            self.set_paths(Path(paths[0]).glob("*.mtlx"))

    def selected(self):
        """按列表索引返回路径，避免重名文件被误选。"""
        return [
            self.paths[i - 1]
            for i in (cmds.textScrollList(self.files, query=True, selectIndexedItem=True) or [])
        ]

    def refresh(self):
        """显示每份文档的当前状态，保持文件完整路径可在报告中查看。"""
        selected = cmds.textScrollList(self.files, query=True, selectIndexedItem=True) or []
        cmds.textScrollList(self.files, edit=True, removeAll=True)
        for path in self.paths:
            result = self.results.get(path, {})
            status = (
                "已导入"
                if result.get("created")
                else {"error": "阻断", "warning": "警告", "ok": "可导入"}.get(
                    result.get("status"), "未检查"
                )
            )
            cmds.textScrollList(self.files, edit=True, append="[" + status + "] " + Path(path).name)
        selected = [i for i in selected if i <= len(self.paths)]
        if selected:
            cmds.textScrollList(self.files, edit=True, selectIndexedItem=selected)

    def run(self, create):
        """逐文件调用统一控制层；支持取消后续文件，不中断正在创建的事务。"""
        paths = self.selected()
        if not paths:
            cmds.text(self.status, edit=True, label="请先选择至少一份文件。")
            return
        try:
            if self.service is None:
                target = ensure_environment()
                service = controller.create_service()
                adapter = MayaAdapter(service, target)
                self.target, self.service, self.adapter = target, service, adapter
        except Exception as exc:
            cmds.text(self.status, edit=True, label="环境检查失败：" + str(exc))
            return
        cmds.progressWindow(
            title="Iris Relay", progress=0, maxValue=len(paths), isInterruptable=True
        )
        count = 0
        try:
            for path in paths:
                if cmds.progressWindow(query=True, isCancelled=True):
                    break
                if create:
                    result = controller.import_material(
                        self.adapter,
                        self.service,
                        path,
                        self.target,
                        accept_warnings=cmds.checkBox(self.warning, query=True, value=True),
                    )
                else:
                    result = controller.check_import(self.adapter, self.service, path, self.target)
                self.results[path] = result
                count += 1
                cmds.progressWindow(edit=True, progress=count, status=Path(path).name)
        finally:
            cmds.progressWindow(endProgress=True)
            self.refresh()
            self.show_report()
        successful = sum(bool(self.results[p].get("created")) for p in paths if p in self.results)
        cmds.text(
            self.status,
            edit=True,
            label=f"处理 {count} / {len(paths)} 份；本次列表中已导入 {successful} 份。",
        )

    def show_report(self, *_):
        """显示完整问题和核心诊断，阻断不伪装成成功。"""
        paths = self.selected()
        result = (
            self.results.get(paths[0], {"source_path": paths[0], "message": "尚未检查"})
            if paths
            else {}
        )
        cmds.scrollField(
            self.details, edit=True, text=json.dumps(result, ensure_ascii=False, indent=2)
        )

    def select_created(self, *_):
        """只选择仍然存在的新材质，撤销后不存在的对象不会报成功。"""
        nodes = [
            self.results.get(p, {}).get("created", {}).get("material")
            for p in self.selected()
            if self.results.get(p, {}).get("created")
        ]
        nodes = [n for n in nodes if cmds.objExists(n)]
        if nodes:
            cmds.select(nodes, replace=True)

    def copy_report(self, *_):
        """将当前文本报告复制到 Maya 的 Qt 剪贴板。"""
        from PySide2.QtWidgets import QApplication

        QApplication.clipboard().setText(cmds.scrollField(self.details, query=True, text=True))

    def show_created_graph(self, *_):
        """打开独立节点编辑窗口，显示当前网络与仍然有效的原生发布参数。"""
        for path in self.selected():
            created = self.results.get(path, {}).get("created")
            if created and cmds.objExists(created["material"]):
                show_editable_graph(created)
                return
        cmds.text(self.status, edit=True, label="请先选择一份已导入且仍在场景中的材质。")


def show_editable_graph(created):
    """原生节点自动布局；参数控件直接绑定当前属性，不用缓存值重放计算图。"""
    window = cmds.window(title="Iris Relay · 节点与共享参数", widthHeight=(1400, 850))
    pane = cmds.paneLayout(parent=window, configuration="vertical2", paneSize=(1, 22, 100))
    scroll = cmds.scrollLayout(parent=pane, childResizable=True)
    column = cmds.columnLayout(parent=scroll, adjustableColumn=True, rowSpacing=6)
    for group in created.get("containers", []):
        if not cmds.objExists(group["node"]):
            continue
        label = cmds.getAttr(group["node"] + ".irisLabel")
        frame = cmds.frameLayout(
            parent=column, label=label, collapsable=True, marginWidth=8, marginHeight=8
        )
        contents = cmds.columnLayout(parent=frame, adjustableColumn=True)
        for control in group["published"]:
            if not cmds.objExists(control["plug"]):
                continue
            bound = cmds.container(
                group["node"], query=True, publishName=True, publishAttr=control["plug"]
            )
            if bound == [control["alias"]]:
                node = control["plug"].split(".")[0]
                notes = (
                    cmds.getAttr(node + ".notes")
                    if cmds.attributeQuery("notes", node=node, exists=True)
                    else control["alias"]
                )
                cmds.setParent(contents)
                cmds.attrControlGrp(attribute=control["plug"], label=notes.split(" / ")[-1])
    form = cmds.formLayout(parent=pane)
    editor = cmds.nodeEditor(parent=form, addNewNodes=False, useAssets=False, showNamespace=False)
    control = cmds.nodeEditor(editor, query=True, control=True)
    cmds.formLayout(
        form,
        edit=True,
        attachForm=[(control, side, 0) for side in ("top", "bottom", "left", "right")],
    )
    cmds.showWindow(window)
    nodes = [created["material"]] + (
        cmds.listHistory(created["material"], pruneDagObjects=True) or []
    )
    for group in created.get("containers", []):
        if cmds.objExists(group["node"]):
            nodes.extend(cmds.container(group["node"], query=True, nodeList=True) or [])
    for node in sorted(set(nodes)):
        cmds.nodeEditor(editor, edit=True, addNode=node)
    cmds.nodeEditor(editor, edit=True, layout=True, frameAll=True)
    return {"window": window, "editor": editor}


def show():
    """重复运行只替换面板，保留用户已经导入的材质。"""
    global _panel
    _panel = ImportPanel()
    return _panel
