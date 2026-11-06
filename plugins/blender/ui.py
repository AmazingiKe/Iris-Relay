"""Blender MaterialX 导出界面；流程由控制层协调，转换规则由核心提供。"""

import json
import textwrap
import traceback
from dataclasses import asdict

import bpy
from bpy.props import BoolProperty, CollectionProperty, IntProperty, PointerProperty, StringProperty

_registered_classes = []
_service = None


def reset_result(self, context):
    self.status = "idle"
    self.message = "请选择材质和输出路径"
    self.issues.clear()
    self.issue_index = 0
    self.report_json = ""


class IRISRELAY_PG_issue(bpy.types.PropertyGroup):
    severity: StringProperty()
    code: StringProperty()
    message: StringProperty()
    node: StringProperty()
    port: StringProperty()


class IRISRELAY_PG_demo(bpy.types.PropertyGroup):
    material: PointerProperty(
        name="材质", type=bpy.types.Material, update=reset_result, options={"SKIP_SAVE"}
    )
    filepath: StringProperty(name="输出文件", update=reset_result, options={"SKIP_SAVE"})
    surface_only: BoolProperty(
        name="仅导出表面（排除位移）",
        default=False,
        update=reset_result,
        options={"SKIP_SAVE"},
        description="明确排除位移输出并保留警告和源快照；不会修改源材质",
    )
    status: StringProperty(default="idle", options={"SKIP_SAVE"})
    message: StringProperty(default="请选择材质和输出路径", options={"SKIP_SAVE"})
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
    settings.status = status
    settings.message = message
    operator.report({"WARNING" if status == "error" else "INFO"}, message)
    return {"CANCELLED" if status == "error" else "FINISHED"}


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


def run_export(operator, context, *, check_only=False):
    global _service
    settings = context.window_manager.iris_relay_demo
    reset_result(settings, context)
    if settings.material is None:
        return feedback(operator, settings, "error", "请先选择一个材质")
    from . import controller

    try:
        if not check_only:
            controller.output_path(settings.filepath)
        if _service is None:
            _service = controller.create_service()
        from .adapter import BlenderAdapter

        adapter = BlenderAdapter(settings.material, _service, surface_only=settings.surface_only)
        if check_only:
            result = controller.check_material(adapter, _service)
        else:
            result = controller.export_material(
                adapter, _service, settings.filepath, accept_warnings=settings.accept_warnings
            )
    except Exception as exc:
        # 意外宿主异常也必须显示失败；详细堆栈仅输出到本地控制台。
        if not isinstance(exc, (ValueError, OSError)):
            traceback.print_exc()
        result = controller.failure(
            controller.ExportResult(), getattr(exc, "code", "EXPORT_FAILED"), str(exc)
        )
    settings.status = result.status
    settings.message = result.message
    settings.report_json = json.dumps(asdict(result), ensure_ascii=False, indent=2)
    priorities = {"error": 0, "warning": 1, "info": 2}
    for issue in sorted(result.issues, key=lambda item: priorities.get(item["severity"], 3)):
        item = settings.issues.add()
        for name in ("severity", "code", "message", "node", "port"):
            setattr(item, name, issue.get(name) or "")
    operator.report({"INFO" if result.status == "ok" else "WARNING"}, result.message)
    return {
        "FINISHED"
        if result.saved_path or (check_only and result.status != "error")
        else "CANCELLED"
    }


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
    width = max(12, int((context.region.width - 48) / (13 * context.preferences.system.ui_scale)))
    for line in textwrap.wrap(text, width=width):
        layout.label(text=line)


def draw_demo(layout, context):
    settings = context.window_manager.iris_relay_demo
    layout.use_property_split = False
    layout.use_property_decorate = False
    intro = layout.box()
    intro.label(text="MaterialX 导出 · GUI Demo", icon="MATERIAL")
    intro.label(text="导出文档 · 保留源材质", icon="INFO")

    source = layout.column(align=True)
    source.label(text="1. 选择源材质")
    source.prop_search(settings, "material", bpy.data, "materials", text="")
    source.operator("iris_relay.demo_use_active", icon="EYEDROPPER")

    layout.separator()
    destination = layout.column(align=True)
    destination.label(text="2. 选择输出文件 (.mtlx)")
    destination.prop(settings, "filepath", text="")
    destination.operator("iris_relay.demo_choose_path", icon="FILE_FOLDER")

    layout.separator()
    layout.prop(settings, "surface_only")
    layout.operator("iris_relay.check_material", icon="VIEWZOOM")
    layout.prop(settings, "accept_warnings")
    action = layout.row()
    action.scale_y = 1.5
    action.operator("iris_relay.export_materialx", icon="EXPORT")
    result = layout.box()
    result.alert = settings.status == "error"
    wrapped_label(result, context, settings.message)
    if settings.report_json:
        result.label(text="外观一致性尚未验证", icon="INFO")
        result.operator("iris_relay.copy_report", icon="COPYDOWN")
    if settings.issues:
        layout.template_list(
            "IRISRELAY_UL_issues", "", settings, "issues", settings, "issue_index", rows=4
        )
        if 0 <= settings.issue_index < len(settings.issues):
            issue = settings.issues[settings.issue_index]
            detail = layout.box()
            wrapped_label(detail, context, issue.code)
            if issue.node:
                wrapped_label(detail, context, f"节点：{issue.node}  端口：{issue.port}")
            wrapped_label(detail, context, issue.message)


class IRISRELAY_PT_demo_text(bpy.types.Panel):
    bl_label = "Iris Relay · GUI Demo"
    bl_idname = "IRISRELAY_PT_demo_text"
    bl_space_type = "TEXT_EDITOR"
    bl_region_type = "UI"
    bl_category = "Iris Relay"

    def draw(self, context):
        draw_demo(self.layout, context)


class IRISRELAY_PT_demo_view(bpy.types.Panel):
    bl_label = "Iris Relay · GUI Demo"
    bl_idname = "IRISRELAY_PT_demo_view"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Iris Relay"

    def draw(self, context):
        draw_demo(self.layout, context)


CLASSES = (
    IRISRELAY_PG_issue,
    IRISRELAY_PG_demo,
    IRISRELAY_OT_demo_use_active,
    IRISRELAY_OT_demo_choose_path,
    IRISRELAY_OT_check_material,
    IRISRELAY_OT_export_materialx,
    IRISRELAY_OT_copy_report,
    IRISRELAY_UL_issues,
    IRISRELAY_PT_demo_text,
    IRISRELAY_PT_demo_view,
)


def unregister():
    global _service
    _service = None
    if hasattr(bpy.types.WindowManager, "iris_relay_demo"):
        for manager in bpy.data.window_managers:
            settings = manager.iris_relay_demo
            settings.issues.clear()
            for name in (
                "material",
                "filepath",
                "surface_only",
                "status",
                "message",
                "accept_warnings",
                "issue_index",
                "report_json",
            ):
                settings.property_unset(name)
            if "iris_relay_demo" in manager:
                del manager["iris_relay_demo"]
        del bpy.types.WindowManager.iris_relay_demo
    for cls in reversed(_registered_classes):
        bpy.utils.unregister_class(cls)
    _registered_classes.clear()


def register():
    unregister()
    try:
        for cls in CLASSES:
            bpy.utils.register_class(cls)
            _registered_classes.append(cls)
        bpy.types.WindowManager.iris_relay_demo = PointerProperty(
            type=IRISRELAY_PG_demo, options={"SKIP_SAVE"}
        )
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
