"""Cinema 4D 原生材质交换窗口；从脚本管理器打开，不安装永久插件。"""

import json

import c4d

from plugins.presentation import DEFAULT_URL, TITLE, TRANSPORT_ITEMS, format_report, status_label

from .controller import PanelController, failure

MODE, URL, CONNECT, CONNECTION = 100, 101, 102, 103
TABS, IMPORT_TAB, EXPORT_TAB = 200, 201, 202
IMPORT_PATH, IMPORT_BROWSE, IMPORT_CHECK, IMPORT_RUN, IMPORT_WARN = 210, 211, 212, 213, 214
EXPORT_PATH, EXPORT_BROWSE, EXPORT_CHECK, EXPORT_RUN, EXPORT_WARN = 220, 221, 222, 223, 224
SELECTED, REFRESH = 225, 226
RESULT_STATE, SUMMARY, DETAILS_TOGGLE, DETAILS_GROUP, DETAILS, COPY_REPORT = (
    300,
    301,
    302,
    303,
    304,
    305,
)
_dialog = None


class RelayDialog(c4d.gui.GeDialog):
    """用统一的连接、来源、执行和报告顺序组织宿主原生控件。"""

    def __init__(self):
        super().__init__()
        self.controller = PanelController()
        self._running = False
        self._document = None

    def _heading(self, identifier, text):
        """添加统一步骤标题，沿用宿主字体和缩放设置。"""
        self.AddStaticText(identifier, c4d.BFH_LEFT, name=text)

    def _path_row(self, group, field, button):
        """把文件路径与浏览按钮放在同一可伸缩行。"""
        self.GroupBegin(group, c4d.BFH_SCALEFIT, 2, 1)
        self.AddEditText(field, c4d.BFH_SCALEFIT, initw=420)
        self.AddButton(button, c4d.BFH_RIGHT, name="浏览…")
        self.GroupEnd()

    def CreateLayout(self):
        """布局仅使用官方 GeDialog 控件，窗口可以缩放并保留完整报告。"""
        self.SetTitle(TITLE)
        self.GroupBegin(10, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 1, 0)
        self.GroupBorderSpace(16, 14, 16, 14)
        self._heading(11, TITLE)
        self._heading(12, "Cinema 4D · Redshift")
        self.AddSeparatorH(0, c4d.BFH_SCALEFIT)

        self.GroupBegin(20, c4d.BFH_SCALEFIT, 3, 1)
        self.AddComboBox(MODE, c4d.BFH_LEFT, initw=125)
        for index, (_, label) in enumerate(TRANSPORT_ITEMS):
            self.AddChild(MODE, index, label)
        self.AddEditText(URL, c4d.BFH_SCALEFIT, initw=280)
        self.AddButton(CONNECT, c4d.BFH_RIGHT, name="检测连接")
        self.GroupEnd()
        self.AddStaticText(CONNECTION, c4d.BFH_SCALEFIT, name="尚未检测连接")

        self.TabGroupBegin(TABS, c4d.BFH_SCALEFIT, c4d.TAB_TABS)
        self.GroupBegin(IMPORT_TAB, c4d.BFH_SCALEFIT, 1, 0, "导入")
        self.GroupBorderSpace(10, 12, 10, 12)
        self._heading(31, "01 选择来源")
        self._path_row(32, IMPORT_PATH, IMPORT_BROWSE)
        self._heading(33, "02 检查与执行")
        self.AddCheckbox(IMPORT_WARN, c4d.BFH_LEFT, 0, 0, "允许带警告导入")
        self.GroupBegin(34, c4d.BFH_SCALEFIT, 2, 1)
        self.AddButton(IMPORT_CHECK, c4d.BFH_SCALEFIT, name="检查材质")
        self.AddButton(IMPORT_RUN, c4d.BFH_SCALEFIT, name="导入为新材质")
        self.GroupEnd()
        self.GroupEnd()

        self.GroupBegin(EXPORT_TAB, c4d.BFH_SCALEFIT, 1, 0, "导出")
        self.GroupBorderSpace(10, 12, 10, 12)
        self._heading(41, "01 选择来源")
        self.GroupBegin(42, c4d.BFH_SCALEFIT, 2, 1)
        self.AddStaticText(SELECTED, c4d.BFH_SCALEFIT, name="未选中材质")
        self.AddButton(REFRESH, c4d.BFH_RIGHT, initw=90, name="刷新选择")
        self.GroupEnd()
        self._path_row(43, EXPORT_PATH, EXPORT_BROWSE)
        self._heading(44, "02 检查与执行")
        self.AddCheckbox(EXPORT_WARN, c4d.BFH_LEFT, 0, 0, "允许带警告导出")
        self.GroupBegin(45, c4d.BFH_SCALEFIT, 2, 1)
        self.AddButton(EXPORT_CHECK, c4d.BFH_SCALEFIT, name="检查材质")
        self.AddButton(EXPORT_RUN, c4d.BFH_SCALEFIT, name="导出 .mtlx")
        self.GroupEnd()
        self.GroupEnd()
        self.GroupEnd()

        self._heading(50, "03 结果报告")
        self.AddStaticText(RESULT_STATE, c4d.BFH_SCALEFIT, name="待检查")
        style = c4d.DR_MULTILINE_READONLY | c4d.DR_MULTILINE_WORDWRAP
        self.AddMultiLineEditText(
            SUMMARY, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, inith=130, style=style
        )
        self.GroupBegin(51, c4d.BFH_SCALEFIT, 2, 1)
        self.AddCheckbox(DETAILS_TOGGLE, c4d.BFH_LEFT, 0, 0, "显示详细 JSON")
        self.AddButton(COPY_REPORT, c4d.BFH_RIGHT, initw=110, name="复制完整报告")
        self.GroupEnd()
        self.GroupBegin(DETAILS_GROUP, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 1, 1)
        self.AddMultiLineEditText(
            DETAILS, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, inith=180, style=style
        )
        self.GroupEnd()
        self.GroupEnd()
        return True

    def InitValues(self):
        """默认不连接、不接受警告；详细报告按需展开。"""
        self.SetInt32(MODE, 0)
        self.SetString(URL, DEFAULT_URL)
        self.Enable(URL, False)
        self.SetBool(IMPORT_WARN, False)
        self.SetBool(EXPORT_WARN, False)
        self.SetBool(DETAILS_TOGGLE, False)
        self.HideElement(DETAILS_GROUP, True)
        self.SetTimer(300)
        self._refresh_selection()
        self._show_report()
        return True

    def _refresh_selection(self):
        """只读获取当前文档和材质名称，保持用户选择不变。"""
        self._document = c4d.documents.GetActiveDocument()
        material = self._document.GetActiveMaterial() if self._document else None
        self.SetString(SELECTED, material.GetName() if material else "未选中材质")

    def _show_report(self):
        """默认展示共用摘要；完整原始响应保留在详细字段和复制操作中。"""
        result = self.controller.result
        self.SetString(
            RESULT_STATE,
            status_label(
                result["status"],
                created=bool(result.get("created")),
                saved=bool(result.get("saved_path")),
            )
            if result
            else "待检查",
        )
        self.SetString(
            SUMMARY, format_report(result) if result else "选择来源后检查材质，结果将在这里显示。"
        )
        self.SetString(DETAILS, json.dumps(result, ensure_ascii=False, indent=2) if result else "")
        self.Enable(COPY_REPORT, result is not None)

    def Timer(self, msg):
        """轮询文档修改戳；用户撤销、编辑或切换场景后移除过期成功。"""
        if self._running:
            return
        try:
            self._refresh_selection()
            if self.controller.invalidate_if_changed(self._document):
                self._show_report()
        except Exception:
            self.controller.clear_report()
            self._show_report()

    def _choose_path(self, importing):
        """用原生文件对话框选择输入或新输出路径，选择变化使旧检查失效。"""
        path = c4d.storage.LoadDialog(
            title="选择 MaterialX 文件" if importing else "保存 MaterialX 文件",
            flags=c4d.FILESELECT_LOAD if importing else c4d.FILESELECT_SAVE,
            force_suffix="mtlx" if not importing else "",
        )
        if path:
            self.SetString(IMPORT_PATH if importing else EXPORT_PATH, path)
            self.controller.clear_report()
            self._show_report()

    def Command(self, identifier, msg):
        """按钮只组织当前操作；材质读写在宿主主线程执行。"""
        try:
            if identifier in {MODE, URL}:
                self.controller.reset_connection()
                self.Enable(URL, TRANSPORT_ITEMS[self.GetInt32(MODE)][0] == "HTTP")
                self.SetString(CONNECTION, "连接设置已改变，请重新检测")
                self._show_report()
            elif identifier == CONNECT:
                self.SetString(CONNECTION, "正在检测连接…")
                self.controller.connect(
                    TRANSPORT_ITEMS[self.GetInt32(MODE)][0], self.GetString(URL)
                )
                self.SetString(CONNECTION, "已连接 · 服务可用")
                self._show_report()
            elif identifier in {IMPORT_PATH, EXPORT_PATH, IMPORT_WARN, EXPORT_WARN, REFRESH, TABS}:
                self._refresh_selection()
                self.controller.clear_report()
                self._show_report()
            elif identifier in {IMPORT_BROWSE, EXPORT_BROWSE}:
                self._choose_path(identifier == IMPORT_BROWSE)
            elif identifier == DETAILS_TOGGLE:
                self.HideElement(DETAILS_GROUP, not self.GetBool(DETAILS_TOGGLE))
                self.LayoutChanged(10)
            elif identifier == COPY_REPORT:
                if self.controller.result is not None:
                    c4d.CopyStringToClipboard(
                        json.dumps(self.controller.result, ensure_ascii=False, indent=2)
                    )
            elif identifier in {IMPORT_CHECK, IMPORT_RUN, EXPORT_CHECK, EXPORT_RUN}:
                self._running = True
                self._refresh_selection()
                actions = {
                    IMPORT_CHECK: "check_import",
                    IMPORT_RUN: "import",
                    EXPORT_CHECK: "check_export",
                    EXPORT_RUN: "export",
                }
                importing = identifier in {IMPORT_CHECK, IMPORT_RUN}
                self.controller.perform(
                    actions[identifier],
                    self._document,
                    self.GetString(IMPORT_PATH if importing else EXPORT_PATH),
                    accept_warnings=self.GetBool(IMPORT_WARN if importing else EXPORT_WARN),
                )
                self._show_report()
                if identifier == IMPORT_RUN and self.controller.result.get("created"):
                    c4d.EventAdd()
        except Exception as exc:
            if identifier == CONNECT:
                self.SetString(CONNECTION, "连接失败 · 请检查服务地址与服务状态")
            self.controller.record_result(
                failure(exc), c4d.documents.GetActiveDocument(), track_selection=False
            )
            self._show_report()
        finally:
            self._running = False
        return True

    def DestroyWindow(self):
        """窗口关闭或布局切换时释放本窗口自己的核心连接。"""
        self.SetTimer(0)
        self.controller.reset_connection()


def show():
    """保留异步对话框引用；重复运行入口时关闭旧窗口和旧服务。"""
    global _dialog
    if _dialog is not None:
        _dialog.controller.reset_connection()
        _dialog.Close()
    _dialog = RelayDialog()
    _dialog.Open(c4d.DLG_TYPE_ASYNC, defaultw=620, defaulth=640)
    return _dialog
