"""Maya 材质交换面板；连接、来源选择和报告与其他宿主遵循相同工作流。"""

import json
import re
from pathlib import Path

from maya import cmds
from plugins.presentation import (
    DEFAULT_URL,
    TITLE,
    TRANSPORT_ITEMS,
    close_service,
    connect_service,
    format_report,
    status_label,
)

from . import controller
from .adapter import MayaAdapter, ensure_environment

WINDOW = "IrisRelayMaterialXImport"
_panel = None


class ImportPanel:
    """同一连接下分别处理导入与导出；宿主读写仍交给控制层与适配器。"""

    def __init__(self):
        self.paths, self.results = [], {}
        self.export_surfaces, self.export_results = [], []
        self.service = self.adapter = self.target = None
        if cmds.window(WINDOW, exists=True):
            cmds.deleteUI(WINDOW)
        self.window = cmds.window(WINDOW, title=TITLE + " · Maya", widthHeight=(760, 700))
        form = cmds.formLayout()
        header = cmds.columnLayout(adjustableColumn=True, rowSpacing=3)
        cmds.text(label=TITLE, align="left", font="boldLabelFont", height=20)
        cmds.text(label="Maya · Arnold  /  可编辑材质交换", align="left", height=18)
        cmds.frameLayout(label="服务连接", marginWidth=10, marginHeight=5)
        cmds.columnLayout(adjustableColumn=True, rowSpacing=6)
        cmds.rowLayout(
            numberOfColumns=3,
            adjustableColumn=2,
            columnWidth3=(65, 200, 100),
            columnAttach=[(1, "both", 0), (2, "both", 8)],
        )
        cmds.text(label="调用方式", align="left", width=65)
        self.mode = cmds.optionMenu(changeCommand=self.change_connection)
        for _, label in TRANSPORT_ITEMS:
            cmds.menuItem(label=label)
        cmds.button(label="检测连接", width=100, height=29, command=self.detect_connection)
        cmds.setParent("..")
        self.url_row = cmds.rowLayout(
            numberOfColumns=2, adjustableColumn=2, columnWidth2=(65, 200), manage=False
        )
        cmds.text(label="服务地址", align="left", width=65)
        self.url = cmds.textField(
            text=DEFAULT_URL,
            changeCommand=self.change_connection,
            annotation="HTTP 服务根地址；切换连接将清除旧报告",
            enable=False,
        )
        cmds.setParent("..")
        self.connection_status = cmds.text(
            label="未连接 · 请选择调用方式并检测连接", align="left", height=23
        )
        cmds.setParent(form)
        self.tabs = cmds.tabLayout(innerMarginWidth=10, innerMarginHeight=10)
        self.import_tab = self._build_import()
        self.export_tab = self._build_export()
        cmds.tabLayout(
            self.tabs,
            edit=True,
            tabLabel=[(self.import_tab, "导入"), (self.export_tab, "导出")],
        )
        cmds.formLayout(
            form,
            edit=True,
            attachForm=[
                (header, "top", 10),
                (header, "left", 14),
                (header, "right", 14),
                (self.tabs, "left", 4),
                (self.tabs, "right", 4),
                (self.tabs, "bottom", 8),
            ],
            attachControl=[(self.tabs, "top", 10, header)],
        )
        cmds.showWindow(self.window)
        self._style()
        cmds.window(self.window, edit=True, widthHeight=(760, 700))
        for event in ("Undo", "Redo", "SceneOpened", "NewSceneOpened"):
            cmds.scriptJob(event=[event, self.invalidate], parent=self.window)
        cmds.scriptJob(uiDeleted=[self.window, self.dispose], runOnce=True)

    def _section(self, title):
        """创建统一编号区块；原生布局允许窄窗滚动而不裁切内容。"""
        frame = cmds.frameLayout(label=title, marginWidth=10, marginHeight=5)
        column = cmds.columnLayout(adjustableColumn=True, rowSpacing=5)
        return frame, column

    def _build_import(self):
        """导入页保留多文件检查、新建材质、原生节点查看和完整报告。"""
        page = cmds.scrollLayout(parent=self.tabs, childResizable=True)
        column = cmds.columnLayout(adjustableColumn=True, rowSpacing=6)
        self._section("01 选择来源")
        cmds.text(label="选择 MaterialX 文件，可添加多个文件或目录。", align="left", height=20)
        cmds.rowLayout(numberOfColumns=3, adjustableColumn=1, columnWidth3=(300, 130, 105))
        cmds.button(label="添加文件…", height=29, command=self.choose_files)
        cmds.button(label="添加目录…", width=130, height=29, command=self.choose_directory)
        cmds.button(label="清空列表", width=105, height=29, command=self.clear_paths)
        cmds.setParent("..")
        self.files = cmds.textScrollList(
            allowMultiSelection=True, height=70, selectCommand=self.show_report
        )
        cmds.setParent(column)
        self._section("02 检查与执行")
        self.warning = cmds.checkBox(label="允许带警告导入", value=False)
        cmds.rowLayout(numberOfColumns=2, adjustableColumn=2, columnWidth2=(150, 350))
        cmds.button(label="检查材质", width=150, height=34, command=lambda *_: self.run(False))
        cmds.button(
            label="导入为新材质",
            height=34,
            backgroundColor=(0.18, 0.39, 0.43),
            command=lambda *_: self.run(True),
        )
        cmds.setParent("..")
        cmds.text(
            label="新建可撤销的材质，不自动替换对象绑定；外观一致性需另行验证。",
            align="left",
            wordWrap=True,
            height=24,
        )
        cmds.setParent(column)
        self._section("03 结果报告")
        self.status = cmds.text(
            label="选择文件后点击“检查材质”。", align="left", wordWrap=True, height=20
        )
        self.details = cmds.scrollField(
            editable=False, wordWrap=True, height=95, text="尚无检查结果。"
        )
        cmds.rowLayout(numberOfColumns=2, adjustableColumn=1, columnWidth2=(330, 150))
        self.details_json = cmds.checkBox(
            label="显示详细 JSON", value=False, changeCommand=self.show_report
        )
        cmds.button(label="复制完整报告", width=150, height=27, command=self.copy_report)
        cmds.setParent("..")
        cmds.rowLayout(numberOfColumns=2, adjustableColumn=2, columnWidth2=(170, 330))
        cmds.button(label="选中已导入材质", width=170, height=29, command=self.select_created)
        cmds.button(label="查看节点与共享参数", height=29, command=self.show_created_graph)
        cmds.setParent(self.tabs)
        return page

    def _build_export(self):
        """导出页使用当前选择与明确输出目录，检查不会写入文件。"""
        page = cmds.scrollLayout(parent=self.tabs, childResizable=True)
        column = cmds.columnLayout(adjustableColumn=True, rowSpacing=6)
        self._section("01 选择来源")
        cmds.text(
            label="在场景或 Hypershade 中选中 Arnold 标准表面材质。",
            align="left",
            wordWrap=True,
            height=24,
        )
        cmds.button(label="读取当前选中材质", height=29, command=self.select_export_materials)
        self.export_files = cmds.textScrollList(height=90, allowMultiSelection=False)
        self.export_directory = cmds.textFieldButtonGrp(
            label="输出目录",
            buttonLabel="浏览…",
            columnWidth3=(65, 400, 75),
            adjustableColumn=2,
            buttonCommand=self.choose_export_directory,
            changeCommand=self.change_export_directory,
        )
        cmds.text(label="每个材质保存为单独的 .mtlx；已有文件不会覆盖。", align="left", height=20)
        cmds.setParent(column)
        self._section("02 检查与执行")
        self.export_warning = cmds.checkBox(label="允许带警告导出", value=False)
        cmds.rowLayout(numberOfColumns=2, adjustableColumn=2, columnWidth2=(150, 350))
        cmds.button(label="检查材质", width=150, height=34, command=self.check_export)
        cmds.button(
            label="导出 .mtlx",
            height=34,
            backgroundColor=(0.18, 0.39, 0.43),
            command=self.export_selected,
        )
        cmds.setParent(column)
        self._section("03 结果报告")
        self.export_status = cmds.text(
            label="选中材质后点击“读取当前选中材质”。", align="left", wordWrap=True, height=20
        )
        self.export_details = cmds.scrollField(
            editable=False, wordWrap=True, height=115, text="尚无检查结果。"
        )
        cmds.rowLayout(numberOfColumns=2, adjustableColumn=1, columnWidth2=(330, 150))
        self.export_json = cmds.checkBox(
            label="显示详细 JSON", value=False, changeCommand=self.show_export_report
        )
        cmds.button(label="复制完整报告", width=150, height=27, command=self.copy_export_report)
        cmds.setParent(self.tabs)
        return page

    def _style(self):
        """仅为本窗口设置中文字体与字号，保留 Maya 原生深色主题。"""
        from PySide2.QtWidgets import QWidget
        from shiboken2 import wrapInstance

        from maya.OpenMayaUI import MQtUtil

        widget = wrapInstance(int(MQtUtil.findWindow(self.window)), QWidget)
        widget.setStyleSheet('QWidget { font-family: "Microsoft YaHei UI"; font-size: 13px; }')

    def dispose(self, *_):
        """回收本窗口拥有的客户端；不会关闭外部共享 HTTP 服务。"""
        close_service(self.service)
        self.service = self.adapter = self.target = None

    def _reset_import_results(self, message):
        """同时清理输入报告、列表状态与统计，避免来源变化后残留旧成功。"""
        self.results.clear()
        self.refresh()
        self.show_report()
        cmds.text(self.status, edit=True, label=message)

    def _reset_export_results(self, message):
        """同时清理导出报告与统计，保留用户选择的材质和目标目录。"""
        self.export_results.clear()
        self.show_export_report()
        cmds.text(self.export_status, edit=True, label=message)

    def change_connection(self, *_):
        """连接选项变化立即丢弃旧服务及报告，不修改进程环境变量。"""
        self.dispose()
        mode = TRANSPORT_ITEMS[cmds.optionMenu(self.mode, query=True, select=True) - 1][0]
        cmds.textField(self.url, edit=True, enable=mode == "HTTP")
        cmds.rowLayout(self.url_row, edit=True, manage=mode == "HTTP")
        self._reset_import_results("连接已变化，请重新检查材质。")
        self._reset_export_results("连接已变化，请重新检查材质。")
        cmds.text(self.connection_status, edit=True, label="未连接 · 配置已变化，请检测连接")

    def detect_connection(self, *_):
        """真实能力查询与宿主版本预检均成功后才显示已连接。"""
        self.dispose()
        self._reset_import_results("连接已重置，请重新检查材质。")
        self._reset_export_results("连接已重置，请重新检查材质。")
        cmds.text(self.connection_status, edit=True, label="正在检测连接…")
        service = None
        try:
            mode = TRANSPORT_ITEMS[cmds.optionMenu(self.mode, query=True, select=True) - 1][0]
            url = cmds.textField(self.url, query=True, text=True)
            service, capabilities = connect_service(controller.create_service, mode, url)
            target = ensure_environment()
            adapter = MayaAdapter(service, target)
            self.service, self.target, self.adapter = service, target, adapter
            version = capabilities.get("materialx", {}).get("version", "未知")
            label = "已连接 · MaterialX " + str(version) + " · Maya / Arnold 就绪"
            cmds.text(self.connection_status, edit=True, label=label)
            return True
        except Exception as exc:
            close_service(service)
            cmds.text(self.connection_status, edit=True, label="连接失败 · " + str(exc))
            return False

    def _ensure_service(self):
        """仅在尚未连接时检测，错误不切换到其他调用方式。"""
        return self.service is not None or self.detect_connection()

    def invalidate(self, *_):
        """撤销、重做或换场景后清除旧报告，不把已删除材质显示为成功。"""
        self._reset_import_results("场景已变化，请重新检查材质。")
        self._reset_export_results("场景已变化，请重新检查材质。")

    def set_paths(self, paths):
        """设置文件列表并全选，供入口及独立验证复用。"""
        self.paths = sorted({str(Path(p).resolve()) for p in paths})
        self._reset_import_results(f"已选择 {len(self.paths)} 份文件，请检查材质。")
        if self.paths:
            cmds.textScrollList(
                self.files, edit=True, selectIndexedItem=list(range(1, len(self.paths) + 1))
            )
        self.show_report()

    def clear_paths(self, *_):
        """清空面板列表，不删除文件或已创建材质。"""
        self.set_paths([])

    def choose_files(self, *_):
        """将明确选择的文件追加到列表。"""
        paths = cmds.fileDialog2(
            fileMode=4, fileFilter="MaterialX (*.mtlx)", caption="添加 MaterialX 文件"
        )
        if paths:
            self.set_paths(self.paths + paths)

    def choose_directory(self, *_):
        """仅添加所选目录直属文件，不递归扫描。"""
        paths = cmds.fileDialog2(fileMode=3, caption="添加 MaterialX 目录")
        if paths:
            self.set_paths(self.paths + list(Path(paths[0]).glob("*.mtlx")))

    def selected(self):
        """按列表索引返回文件路径，重名文件仍可分别处理。"""
        return [
            self.paths[i - 1]
            for i in (cmds.textScrollList(self.files, query=True, selectIndexedItem=True) or [])
            if 0 < i <= len(self.paths)
        ]

    def refresh(self):
        """列表只显示简明状态，完整路径保存在结果报告。"""
        selected = cmds.textScrollList(self.files, query=True, selectIndexedItem=True) or []
        cmds.textScrollList(self.files, edit=True, removeAll=True)
        for path in self.paths:
            result = self.results.get(path, {})
            label = status_label(result.get("status"), created=bool(result.get("created")))
            cmds.textScrollList(self.files, edit=True, append="[" + label + "] " + Path(path).name)
        selected = [i for i in selected if i <= len(self.paths)]
        if selected:
            cmds.textScrollList(self.files, edit=True, selectIndexedItem=selected)

    def run(self, create):
        """逐文件重新检查并可选创建；取消只停止后续文件，不打断宿主事务。"""
        paths = self.selected()
        if not paths:
            cmds.text(self.status, edit=True, label="请先选择至少一份文件。")
            return
        if not self._ensure_service():
            cmds.text(self.status, edit=True, label="服务未连接，请检查顶部连接状态。")
            return
        cmds.progressWindow(title=TITLE, progress=0, maxValue=len(paths), isInterruptable=True)
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
            label=f"已处理 {count} / {len(paths)} 份 · 已导入 {successful} 份",
        )

    def _import_report(self):
        """返回当前选中报告；没有选择时给出可读的空状态。"""
        paths = self.selected()
        return (
            self.results.get(paths[0], {"source_path": paths[0], "message": "尚未检查"})
            if paths
            else {"message": "选择文件后检查材质。"}
        )

    def show_report(self, *_):
        """默认显示摘要；用户明确展开时才显示完整 JSON。"""
        result = self._import_report()
        detailed = cmds.checkBox(self.details_json, query=True, value=True)
        text = (
            json.dumps(result, ensure_ascii=False, indent=2) if detailed else format_report(result)
        )
        cmds.scrollField(self.details, edit=True, text=text, wordWrap=not detailed)

    def copy_report(self, *_):
        """复制完整结构化报告，不受摘要显示选项影响。"""
        from PySide2.QtWidgets import QApplication

        QApplication.clipboard().setText(
            json.dumps(self._import_report(), ensure_ascii=False, indent=2)
        )

    def select_created(self, *_):
        """只选中仍然存在的本次目标材质。"""
        nodes = [
            self.results.get(p, {}).get("created", {}).get("material")
            for p in self.selected()
            if self.results.get(p, {}).get("created")
        ]
        nodes = [n for n in nodes if cmds.objExists(n)]
        if nodes:
            cmds.select(nodes, replace=True)

    def show_created_graph(self, *_):
        """使用实际原生节点和已发布属性打开可编辑窗口。"""
        for path in self.selected():
            created = self.results.get(path, {}).get("created")
            if created and cmds.objExists(created["material"]):
                show_editable_graph(created)
                return
        cmds.text(self.status, edit=True, label="请先选择一份已导入且仍在场景中的材质。")

    def select_export_materials(self, *_):
        """只读取当前选中的 aiStandardSurface，避免隐式导出整个场景。"""
        self._reset_export_results("来源已更新，请检查当前选中的材质。")
        self.export_surfaces = [
            node
            for node in (cmds.ls(selection=True) or [])
            if cmds.nodeType(node) == "aiStandardSurface"
        ]
        cmds.textScrollList(self.export_files, edit=True, removeAll=True)
        for index, node in enumerate(self.export_surfaces):
            cmds.textScrollList(
                self.export_files, edit=True, append=node + "  →  " + self._export_name(index, node)
            )
        if not self.export_surfaces:
            cmds.text(
                self.export_status, edit=True, label="请在场景中选中 aiStandardSurface 材质。"
            )

    def _export_name(self, index, surface):
        """生成不含路径分隔符的稳定文件名，序号区分规范化后的重名。"""
        stem = re.sub(r"[^\w.-]+", "_", surface).strip(".") or "material"
        return f"{index + 1:03d}_{stem}.mtlx"

    def choose_export_directory(self, *_):
        """明确选择输出目录，只更改面板值。"""
        paths = cmds.fileDialog2(fileMode=3, caption="选择 MaterialX 输出目录")
        if paths:
            cmds.textFieldButtonGrp(self.export_directory, edit=True, text=paths[0])
            self.change_export_directory()

    def change_export_directory(self, *_):
        """手输和浏览目录共用失效处理，不让旧文件的成功状态沿用到新目录。"""
        self._reset_export_results("输出目录已变化，请重新检查材质。")

    def check_export(self, *_):
        """检查当前选择的材质图，不创建文件。"""
        self._run_export(False)

    def export_selected(self, *_):
        """将当前原生图导出到明确目录，已有文件由控制层阻断。"""
        self._run_export(True)

    def _run_export(self, save):
        """共用检查和保存入口，每次重新读取当前选择与材质参数。"""
        self.select_export_materials()
        self.export_results = []
        if not self.export_surfaces:
            self.show_export_report()
            return
        if not self._ensure_service():
            self.show_export_report()
            cmds.text(self.export_status, edit=True, label="服务未连接，请检查顶部连接状态。")
            return
        directory = Path(
            cmds.textFieldButtonGrp(self.export_directory, query=True, text=True)
        ).expanduser()
        if save and (not directory.is_absolute() or not directory.is_dir()):
            self.show_export_report()
            cmds.text(self.export_status, edit=True, label="请选择存在的绝对输出目录。")
            return
        for index, surface in enumerate(self.export_surfaces):
            if save:
                result = controller.export_material(
                    self.adapter,
                    self.service,
                    surface,
                    directory / self._export_name(index, surface),
                    accept_warnings=cmds.checkBox(self.export_warning, query=True, value=True),
                )
            else:
                result = controller.check_export(self.adapter, self.service, surface)
            result["source_material"] = surface
            self.export_results.append(result)
        self.show_export_report()
        count = sum(bool(item.get("saved_path")) for item in self.export_results)
        failed = sum(item.get("status") == "error" for item in self.export_results)
        cmds.text(
            self.export_status,
            edit=True,
            label=f"已检查 {len(self.export_results)} 份 · 已导出 {count} 份 · 阻断 {failed} 份",
        )

    def show_export_report(self, *_):
        """汇总各材质检查结果，详细数据通过显式选项展示。"""
        detailed = cmds.checkBox(self.export_json, query=True, value=True)
        if detailed:
            text = json.dumps(self.export_results, ensure_ascii=False, indent=2)
        else:
            text = (
                "\n\n".join(
                    item.get("source_material", "材质") + "\n" + format_report(item)
                    for item in self.export_results
                )
                or "尚无检查结果。"
            )
        cmds.scrollField(self.export_details, edit=True, text=text, wordWrap=not detailed)

    def copy_export_report(self, *_):
        """复制所有当前导出结果的完整 JSON。"""
        from PySide2.QtWidgets import QApplication

        QApplication.clipboard().setText(
            json.dumps(self.export_results, ensure_ascii=False, indent=2)
        )


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
