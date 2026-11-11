"""Blender MaterialX 导入导出界面；流程由控制层协调，转换规则由核心提供。"""

import json
import traceback
import unicodedata
from dataclasses import asdict

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)

from plugins import presentation

_registered_classes = []
_service = None


def reset_result(self, context):
    """输入选择改变时清除旧报告，让界面不会显示过期的成功状态。"""
    self.status = "idle"
    self.message = "请选择输入 .mtlx 文件" if self.mode == "IMPORT" else "请选择材质和输出路径"
    self.issues.clear()
    self.issue_index = 0
    self.report_json = ""
    self.created = False
    self.saved = False


def reset_connection(self, context):
    """连接配置变化时回收本界面持有的服务，清除连接状态及旧操作结果。"""
    global _service
    presentation.close_service(_service)
    _service = None
    self.connection_status = "idle"
    self.connection_message = "尚未检测连接"
    reset_result(self, context)


@bpy.app.handlers.persistent
def reset_after_history(_context):
    """撤销、重做或打开文件后清除报告，避免把历史创建结果显示为当前状态。"""
    if hasattr(bpy.types.WindowManager, "iris_relay_demo"):
        for manager in bpy.data.window_managers:
            reset_result(manager.iris_relay_demo, None)


class IRISRELAY_PG_issue(bpy.types.PropertyGroup):
    severity: StringProperty()
    code: StringProperty()
    message: StringProperty()
    node: StringProperty()
    port: StringProperty()


class IRISRELAY_PG_demo(bpy.types.PropertyGroup):
    transport: EnumProperty(
        name="调用方式",
        items=tuple((key, label, label) for key, label in presentation.TRANSPORT_ITEMS),
        default="AUTO",
        update=reset_connection,
        options={"SKIP_SAVE"},
    )
    http_url: StringProperty(
        name="服务地址",
        default=presentation.DEFAULT_URL,
        update=reset_connection,
        options={"SKIP_SAVE"},
    )
    show_connection: BoolProperty(name="连接设置", default=True, options={"SKIP_SAVE"})
    connection_status: StringProperty(default="idle", options={"SKIP_SAVE"})
    connection_message: StringProperty(default="尚未检测连接", options={"SKIP_SAVE"})
    show_details: BoolProperty(name="详细报告", default=False, options={"SKIP_SAVE"})
    created: BoolProperty(default=False, options={"SKIP_SAVE"})
    saved: BoolProperty(default=False, options={"SKIP_SAVE"})
    mode: EnumProperty(
        name="操作",
        items=(("IMPORT", "导入", "从文档创建新材质"), ("EXPORT", "导出", "导出所选材质")),
        default="IMPORT",
        update=reset_result,
        options={"SKIP_SAVE"},
    )
    material: PointerProperty(
        name="材质", type=bpy.types.Material, update=reset_result, options={"SKIP_SAVE"}
    )
    filepath: StringProperty(name="输出文件", update=reset_result, options={"SKIP_SAVE"})
    import_filepath: StringProperty(name="输入文件", update=reset_result, options={"SKIP_SAVE"})
    accept_import_warnings: BoolProperty(
        name="允许带警告导入",
        default=False,
        options={"SKIP_SAVE"},
        description="接受报告中的近似转换；阻断错误仍然不能导入",
    )
    surface_only: BoolProperty(
        name="仅导出表面（排除位移）",
        default=False,
        update=reset_result,
        options={"SKIP_SAVE"},
        description="明确排除位移输出并保留警告和源快照；不会修改源材质",
    )
    status: StringProperty(default="idle", options={"SKIP_SAVE"})
    message: StringProperty(default="请选择输入 .mtlx 文件", options={"SKIP_SAVE"})
    accept_warnings: BoolProperty(
        name="允许带警告导出",
        default=False,
        options={"SKIP_SAVE"},
        description="接受报告中的近似转换；阻断错误仍然不能导出",
    )
    issues: CollectionProperty(type=IRISRELAY_PG_issue, options={"SKIP_SAVE"})
    issue_index: IntProperty(default=0, options={"SKIP_SAVE"})
    report_json: StringProperty(options={"SKIP_SAVE"})


def feedback(operator, settings, status, message):
    """在尚未进入控制层时显示输入错误，并结束当前按钮操作。"""
    settings.status = status
    settings.message = message
    operator.report({"WARNING" if status == "error" else "INFO"}, message)
    return {"CANCELLED" if status == "error" else "FINISHED"}


def ensure_service(settings):
    """按当前界面配置建立服务，通过能力检查后才显示已连接。"""
    global _service
    from . import controller

    if _service is None:
        try:
            _service, capabilities = presentation.connect_service(
                controller.create_service, settings.transport, settings.http_url
            )
        except Exception as exc:
            settings.connection_status = "error"
            settings.connection_message = str(exc)
            raise
        settings.connection_status = "ok"
        name = "HTTP 服务" if type(_service).__name__ == "HttpRelayService" else "本地核心"
        settings.connection_message = "已连接 · " + name
    return _service


def connection_failed(settings, exc):
    """传输失败时撤销旧连接状态；业务阻断仍保留可用服务。"""
    if getattr(exc, "code", "") in {"TRANSPORT_ERROR", "INVALID_RESPONSE"}:
        reset_connection(settings, None)
        settings.connection_status = "error"
        settings.connection_message = str(exc)


class IRISRELAY_OT_check_connection(bpy.types.Operator):
    bl_idname = "iris_relay.check_connection"
    bl_label = "检测连接"
    bl_description = "按所选调用方式检查核心服务，不更改材质"

    def execute(self, context):
        settings = context.window_manager.iris_relay_demo
        reset_connection(settings, context)
        try:
            ensure_service(settings)
        except Exception as exc:
            return feedback(self, settings, "error", "连接失败：" + str(exc))
        self.report({"INFO"}, settings.connection_message)
        return {"FINISHED"}


class IRISRELAY_OT_demo_use_active(bpy.types.Operator):
    bl_idname = "iris_relay.demo_use_active"
    bl_label = "使用活动材质"
    bl_description = "选择活动对象当前材质槽中的材质，不更改场景绑定"

    def execute(self, context):
        settings = context.window_manager.iris_relay_demo
        obj = context.active_object
        settings.material = getattr(obj, "active_material", None)
        if settings.material is None:
            return feedback(self, settings, "error", "活动对象没有可用材质")
        return {"FINISHED"}


class IRISRELAY_OT_demo_choose_path(bpy.types.Operator):
    bl_idname = "iris_relay.demo_choose_path"
    bl_label = "选择输出路径"
    bl_description = "只选择 .mtlx 路径，不创建文件"

    filepath: StringProperty(subtype="FILE_PATH")
    filter_glob: StringProperty(default="*.mtlx", options={"HIDDEN"})

    def invoke(self, context, event):
        self.filepath = context.window_manager.iris_relay_demo.filepath or "material.mtlx"
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        context.window_manager.iris_relay_demo.filepath = self.filepath
        return {"FINISHED"}


class IRISRELAY_OT_demo_choose_import(bpy.types.Operator):
    bl_idname = "iris_relay.demo_choose_import"
    bl_label = "选择输入文件"
    bl_description = "选择已有 .mtlx 文件，尚不创建材质"

    filepath: StringProperty(subtype="FILE_PATH")
    filter_glob: StringProperty(default="*.mtlx", options={"HIDDEN"})

    def invoke(self, context, event):
        self.filepath = context.window_manager.iris_relay_demo.import_filepath
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        context.window_manager.iris_relay_demo.import_filepath = self.filepath
        return {"FINISHED"}


def run_export(operator, context, *, check_only=False):
    """检查/导出按钮的共用入口：读取选择 → 准备服务 → 调用控制层 → 显示结果。"""
    global _service
    # 1. 读取 GUI 选择并清除上次结果，先处理未选择材质的情况。
    settings = context.window_manager.iris_relay_demo
    reset_result(settings, context)
    if settings.material is None:
        return feedback(operator, settings, "error", "请先选择一个材质")
    from . import controller

    try:
        # 2. 保存时先检查路径；首次操作才创建核心服务和宿主适配器。
        if not check_only:
            controller.output_path(settings.filepath)
        ensure_service(settings)
        from .adapter import BlenderAdapter

        adapter = BlenderAdapter(settings.material, _service, surface_only=settings.surface_only)
        # 3. 将检查或保存交给 controller；UI 不解释节点或执行转换规则。
        if check_only:
            result = controller.check_material(adapter, _service)
        else:
            result = controller.export_material(
                adapter, _service, settings.filepath, accept_warnings=settings.accept_warnings
            )
    except Exception as exc:
        connection_failed(settings, exc)
        # 意外宿主异常也必须显示失败；详细堆栈仅输出到本地控制台。
        if not isinstance(exc, (ValueError, OSError)):
            traceback.print_exc()
        result = controller.failure(
            controller.ExportResult(), getattr(exc, "code", "EXPORT_FAILED"), str(exc)
        )

    # 4. 展示统一结果，并返回 Blender 操作状态。
    if result.saved_path:
        settings.import_filepath = result.saved_path
    _show_result(operator, settings, result)
    return {
        "FINISHED"
        if result.saved_path or (check_only and result.status != "error")
        else "CANCELLED"
    }


def _show_result(operator, settings, result):
    """将结果写入状态区、完整 JSON 报告和按严重程度排序的诊断列表。"""
    for issue in result.issues:
        if issue.get("code") in {"TRANSPORT_ERROR", "INVALID_RESPONSE"}:
            reset_connection(settings, None)
            settings.connection_status = "error"
            settings.connection_message = issue["message"]
            break
    settings.status = result.status
    settings.message = result.message
    settings.report_json = json.dumps(asdict(result), ensure_ascii=False, indent=2)
    settings.created = bool(getattr(result, "created_material", None))
    settings.saved = bool(getattr(result, "saved_path", None))
    priorities = {"error": 0, "warning": 1, "info": 2}
    for issue in sorted(result.issues, key=lambda item: priorities.get(item["severity"], 3)):
        item = settings.issues.add()
        for name in ("severity", "code", "message", "node", "port"):
            setattr(item, name, issue.get(name) or "")
    operator.report({"INFO" if result.status == "ok" else "WARNING"}, result.message)


class IRISRELAY_OT_check_material(bpy.types.Operator):
    bl_idname = "iris_relay.check_material"
    bl_label = "检查材质"
    bl_description = "读取当前材质，检查 MaterialX 导出结果和诊断，不保存文件"

    def execute(self, context):
        return run_export(self, context, check_only=True)


class IRISRELAY_OT_export_materialx(bpy.types.Operator):
    bl_idname = "iris_relay.export_materialx"
    bl_label = "导出 .mtlx"
    bl_description = "重新读取并检查材质，保存为新 MaterialX 文件，不覆盖现有文件"

    def execute(self, context):
        return run_export(self, context)


def run_import(operator, context, *, check_only=False):
    """导入入口：准备路径与服务 → 核心检查 → 创建新材质 → 显示报告。"""
    global _service
    # 1. 导入无需源材质或活动对象；检查路径后才启动核心服务。
    settings = context.window_manager.iris_relay_demo
    reset_result(settings, context)
    from . import controller

    try:
        controller.input_path(settings.import_filepath)
        ensure_service(settings)
        from .adapter import BlenderAdapter, renderer_ref

        # 2. 使用当前 Blender 版本作为目标，核心处理转换规则与支持边界。
        if check_only:
            result = controller.check_import(_service, settings.import_filepath, renderer_ref())
        else:
            result = controller.import_material(
                BlenderAdapter(None, _service),
                _service,
                settings.import_filepath,
                renderer_ref(),
                accept_warnings=settings.accept_import_warnings,
            )
    except Exception as exc:
        connection_failed(settings, exc)
        if not isinstance(exc, (ValueError, OSError)):
            traceback.print_exc()
        result = controller.failure(
            controller.ImportResult(), getattr(exc, "code", "IMPORT_FAILED"), str(exc)
        )
    # 3. 完整报告含输入路径和新材质名；只有真正创建完成才显示导入成功。
    _show_result(operator, settings, result)
    return {
        "FINISHED"
        if result.created_material or (check_only and result.status != "error")
        else "CANCELLED"
    }


class IRISRELAY_OT_check_import(bpy.types.Operator):
    bl_idname = "iris_relay.check_import"
    bl_label = "检查材质"
    bl_description = "检查文档、转换支持和贴图文件，不创建材质"

    def execute(self, context):
        return run_import(self, context, check_only=True)


class IRISRELAY_OT_import_materialx(bpy.types.Operator):
    bl_idname = "iris_relay.import_materialx"
    bl_label = "导入为新材质"
    bl_description = "重新检查并创建新材质，保留以供保存，不替换对象绑定；可通过 Blender 撤销"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        return run_import(self, context)


class IRISRELAY_OT_copy_report(bpy.types.Operator):
    bl_idname = "iris_relay.copy_report"
    bl_label = "复制完整报告"
    bl_description = "复制本次诊断、核心响应和源材质快照，便于检查具体原因"

    def execute(self, context):
        context.window_manager.clipboard = context.window_manager.iris_relay_demo.report_json
        self.report({"INFO"}, "报告已复制")
        return {"FINISHED"}


class IRISRELAY_UL_issues(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.label(text=item.message, icon="ERROR" if item.severity == "error" else "INFO")


def wrapped_label(layout, context, text):
    """按中英文实际占位估算折行，避免窄侧栏截断材质名或错误说明。"""
    width = max(12, int((context.region.width - 80) / (8 * context.preferences.system.ui_scale)))
    line, used = "", 0
    for char in text:
        size = 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
        if char == "\n" or used + size > width:
            layout.label(text=line)
            line, used = "", 0
            if char == "\n":
                continue
        line += char
        used += size
    if line:
        layout.label(text=line)


def draw_demo(layout, context):
    """按导出或导入模式绘制操作区，两种操作共享完整诊断展示。"""
    settings = context.window_manager.iris_relay_demo
    layout.use_property_split = False
    layout.use_property_decorate = False
    layout.label(text="Blender · Cycles", icon="MATERIAL")
    connection = layout.box()
    row = connection.row()
    row.prop(
        settings,
        "show_connection",
        text="服务连接",
        emboss=False,
        icon="TRIA_DOWN" if settings.show_connection else "TRIA_RIGHT",
    )
    if not settings.show_connection:
        row.label(
            text={"ok": "已连接", "error": "连接失败"}.get(settings.connection_status, "待检测")
        )
    else:
        connection.prop(settings, "transport", text="")
        if settings.transport == "HTTP":
            connection.prop(settings, "http_url", text="")
        connection.operator("iris_relay.check_connection", icon="FILE_REFRESH")
        state = connection.column()
        state.alert = settings.connection_status == "error"
        wrapped_label(state, context, settings.connection_message)
    layout.separator(factor=0.6)
    layout.prop(settings, "mode", expand=True)
    if settings.mode == "IMPORT":
        _draw_import(layout, context, settings)
    else:
        _draw_export(layout, settings)
    _draw_result(layout, context, settings)


def _draw_export(layout, settings):
    """绘制导出选择和保存动作。"""

    source = layout.box()
    source.label(text="01  选择来源")
    source.prop_search(settings, "material", bpy.data, "materials", text="")
    source.operator("iris_relay.demo_use_active", icon="EYEDROPPER")

    source.separator(factor=0.5)
    source.label(text="输出文件")
    row = source.row(align=True)
    row.prop(settings, "filepath", text="")
    row.operator("iris_relay.demo_choose_path", text="", icon="FILE_FOLDER")

    execute = layout.box()
    execute.label(text="02  检查与执行")
    execute.prop(settings, "surface_only")
    execute.operator("iris_relay.check_material", icon="VIEWZOOM")
    execute.prop(settings, "accept_warnings")
    action = execute.row()
    action.scale_y = 1.3
    action.operator("iris_relay.export_materialx", icon="EXPORT")


def _draw_import(layout, context, settings):
    """绘制输入路径与新建动作，明确导入后材质的使用方式。"""
    destination = layout.box()
    destination.label(text="01  选择来源")
    destination.label(text="输入文件 · .mtlx")
    row = destination.row(align=True)
    row.prop(settings, "import_filepath", text="")
    row.operator("iris_relay.demo_choose_import", text="", icon="FILE_FOLDER")
    execute = layout.box()
    execute.label(text="02  检查与执行")
    execute.operator("iris_relay.check_import", icon="VIEWZOOM")
    execute.prop(settings, "accept_import_warnings")
    action = execute.row()
    action.scale_y = 1.3
    action.operator("iris_relay.import_materialx", icon="IMPORT")


def _draw_result(layout, context, settings):
    """展示最近一次操作的状态、完整报告及具体问题。"""
    result = layout.box()
    result.label(text="03  结果报告")
    result.alert = settings.status == "error"
    result.label(
        text=presentation.status_label(
            settings.status, created=settings.created, saved=settings.saved
        ),
        icon="CHECKMARK" if settings.created or settings.saved else "INFO",
    )
    wrapped_label(result, context, settings.message)
    if settings.issues:
        counts = presentation.issue_counts(
            [{"severity": issue.severity} for issue in settings.issues]
        )
        result.label(text="错误 {error}  ·  警告 {warning}  ·  提示 {info}".format(**counts))
    if settings.report_json:
        result.operator("iris_relay.copy_report", icon="COPYDOWN")
    if settings.report_json or settings.issues:
        result.prop(
            settings,
            "show_details",
            emboss=False,
            icon="TRIA_DOWN" if settings.show_details else "TRIA_RIGHT",
        )
    if settings.show_details and settings.report_json:
        result.label(text="外观一致性尚未验证", icon="INFO")
    if settings.show_details and settings.issues:
        result.template_list(
            "IRISRELAY_UL_issues", "", settings, "issues", settings, "issue_index", rows=4
        )
        if 0 <= settings.issue_index < len(settings.issues):
            issue = settings.issues[settings.issue_index]
            detail = result.box()
            wrapped_label(detail, context, issue.code)
            if issue.node:
                wrapped_label(detail, context, f"节点：{issue.node}  端口：{issue.port}")
            wrapped_label(detail, context, issue.message)


class IRISRELAY_PT_demo_text(bpy.types.Panel):
    bl_label = presentation.TITLE
    bl_idname = "IRISRELAY_PT_demo_text"
    bl_space_type = "TEXT_EDITOR"
    bl_region_type = "UI"
    bl_category = "Iris Relay"

    def draw(self, context):
        draw_demo(self.layout, context)


class IRISRELAY_PT_demo_view(bpy.types.Panel):
    bl_label = presentation.TITLE
    bl_idname = "IRISRELAY_PT_demo_view"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Iris Relay"

    def draw(self, context):
        draw_demo(self.layout, context)


CLASSES = (
    IRISRELAY_PG_issue,
    IRISRELAY_PG_demo,
    IRISRELAY_OT_check_connection,
    IRISRELAY_OT_demo_use_active,
    IRISRELAY_OT_demo_choose_path,
    IRISRELAY_OT_demo_choose_import,
    IRISRELAY_OT_check_material,
    IRISRELAY_OT_export_materialx,
    IRISRELAY_OT_check_import,
    IRISRELAY_OT_import_materialx,
    IRISRELAY_OT_copy_report,
    IRISRELAY_UL_issues,
    IRISRELAY_PT_demo_text,
    IRISRELAY_PT_demo_view,
)


def unregister():
    """清理会话属性、服务引用和已注册类，支持脚本重复运行。"""
    global _service
    presentation.close_service(_service)
    _service = None
    for handlers in (
        bpy.app.handlers.undo_post,
        bpy.app.handlers.redo_post,
        bpy.app.handlers.load_post,
    ):
        if reset_after_history in handlers:
            handlers.remove(reset_after_history)
    if hasattr(bpy.types.WindowManager, "iris_relay_demo"):
        for manager in bpy.data.window_managers:
            settings = manager.iris_relay_demo
            settings.issues.clear()
            for name in (
                "mode",
                "material",
                "filepath",
                "import_filepath",
                "accept_import_warnings",
                "surface_only",
                "status",
                "message",
                "accept_warnings",
                "issue_index",
                "report_json",
                "transport",
                "http_url",
                "show_connection",
                "connection_status",
                "connection_message",
                "show_details",
                "created",
                "saved",
            ):
                settings.property_unset(name)
            if "iris_relay_demo" in manager:
                del manager["iris_relay_demo"]
        del bpy.types.WindowManager.iris_relay_demo
    for cls in reversed(_registered_classes):
        bpy.utils.unregister_class(cls)
    _registered_classes.clear()


def register():
    """先清理旧注册，再注册界面类和会话属性；中途失败时撤销本次注册。"""
    unregister()
    try:
        for cls in CLASSES:
            bpy.utils.register_class(cls)
            _registered_classes.append(cls)
        bpy.types.WindowManager.iris_relay_demo = PointerProperty(
            type=IRISRELAY_PG_demo, options={"SKIP_SAVE"}
        )
        for handlers in (
            bpy.app.handlers.undo_post,
            bpy.app.handlers.redo_post,
            bpy.app.handlers.load_post,
        ):
            handlers.append(reset_after_history)
    except Exception:
        unregister()
        raise


def show():
    """展开当前编辑器侧栏；不切换工作区或改动场景。"""
    area = bpy.context.area
    if area and area.type in {"TEXT_EDITOR", "VIEW_3D"}:
        area.spaces.active.show_region_ui = True
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            area.tag_redraw()
