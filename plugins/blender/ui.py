"""Blender GUI demo：验证交互，不调用转换核心，不生成文件或修改材质。"""

from pathlib import Path

import bpy
from bpy.props import PointerProperty, StringProperty

_registered_classes = []


def reset_result(self, context):
    self.status = "idle"
    self.message = "请选择材质和输出路径"


class IRISRELAY_PG_demo(bpy.types.PropertyGroup):
    material: PointerProperty(
        name="材质", type=bpy.types.Material, update=reset_result, options={"SKIP_SAVE"}
    )
    filepath: StringProperty(name="输出文件", update=reset_result, options={"SKIP_SAVE"})
    status: StringProperty(default="idle", options={"SKIP_SAVE"})
    message: StringProperty(default="请选择材质和输出路径", options={"SKIP_SAVE"})


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
    bl_label = "选择输出路径（演示）"
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


class IRISRELAY_OT_demo_export(bpy.types.Operator):
    bl_idname = "iris_relay.demo_export"
    bl_label = "演示导出"
    bl_description = "检查选择和路径，展示演示结果；不转换材质、不生成文件"

    def execute(self, context):
        settings = context.window_manager.iris_relay_demo
        material = settings.material
        if material is None:
            return feedback(self, settings, "error", "请先选择一个材质")
        raw_path = settings.filepath.strip()
        if not raw_path:
            return feedback(self, settings, "error", "请选择输出文件路径")
        if raw_path.startswith("//"):
            return feedback(self, settings, "error", "演示入口请使用绝对路径")
        try:
            path = Path(raw_path)
            if not path.is_absolute():
                return feedback(self, settings, "error", "请选择输出文件的绝对路径")
            if path.suffix.lower() != ".mtlx":
                return feedback(self, settings, "error", "输出文件扩展名需要为 .mtlx")
            if not path.parent.is_dir():
                return feedback(self, settings, "error", "输出目录不存在，请重新选择")
            if path.exists():
                return feedback(self, settings, "error", "目标路径已存在，请选择新的文件名")
        except (OSError, ValueError):
            return feedback(self, settings, "error", "输出路径无效，请重新选择")
        return feedback(self, settings, "ready", "演示检查通过，未生成文件")


def draw_demo(layout, context):
    settings = context.window_manager.iris_relay_demo
    layout.use_property_split = False
    layout.use_property_decorate = False
    intro = layout.box()
    intro.label(text="MaterialX 导出 · GUI Demo", icon="MATERIAL")
    intro.label(text="仅测试交互，不生成文件", icon="INFO")

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
    action = layout.row()
    action.scale_y = 1.5
    action.operator("iris_relay.demo_export", icon="EXPORT")
    result = layout.box()
    result.alert = settings.status == "error"
    icon = {"idle": "INFO", "ready": "CHECKMARK", "error": "ERROR"}[settings.status]
    result.label(text=settings.message, icon=icon)


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
    IRISRELAY_PG_demo,
    IRISRELAY_OT_demo_use_active,
    IRISRELAY_OT_demo_choose_path,
    IRISRELAY_OT_demo_export,
    IRISRELAY_PT_demo_text,
    IRISRELAY_PT_demo_view,
)


def unregister():
    if hasattr(bpy.types.WindowManager, "iris_relay_demo"):
        for manager in bpy.data.window_managers:
            settings = manager.iris_relay_demo
            for name in ("material", "filepath", "status", "message"):
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
