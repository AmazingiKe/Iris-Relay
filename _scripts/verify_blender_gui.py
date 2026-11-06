"""在独立 Blender 中验证 GUI demo；使用 --background --factory-startup --python 运行。"""

import importlib
import json
import runpy
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import bpy

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "_examples/blender_demo_gui.py"


def main():
    if not bpy.app.background or "--factory-startup" not in sys.argv:
        raise RuntimeError("请使用 --background --factory-startup 在独立 Blender 进程运行验证")
    checks = []
    # 直接运行入口两次，模拟在文本编辑器反复点击“运行脚本”。
    entry = runpy.run_path(str(ENTRY), run_name="__main__")
    bpy.context.window_manager.iris_relay_demo.filepath = "上次输入.mtlx"
    runpy.run_path(str(ENTRY), run_name="__main__")
    from plugins.blender import ui

    assert "iris_relay" not in sys.modules, "GUI demo 不应提前加载转换核心"
    assert hasattr(bpy.types, "IRISRELAY_PT_demo_text")
    assert hasattr(bpy.types, "IRISRELAY_PT_demo_view")
    assert bpy.context.window_manager.iris_relay_demo.filepath == ""
    checks.append("入口重复运行及 GUI 独立加载")

    area = next(area for area in bpy.context.window.screen.areas if area.type == "VIEW_3D")
    area.type = "TEXT_EDITOR"
    area.spaces.active.text = bpy.data.texts.load(str(ENTRY))
    namespace = {"__name__": "demo_entry", "__file__": ENTRY.name}
    exec(compile(ENTRY.read_text(encoding="utf-8"), ENTRY.name, "exec"), namespace)
    with bpy.context.temp_override(area=area):
        namespace["main"]()
    area.type = "VIEW_3D"
    checks.append("文本编辑器仅提供文件名时从文本路径定位仓库")

    try:
        entry["main"](str(ROOT / "不存在的仓库"))
    except ValueError:
        pass
    else:
        raise AssertionError("错误仓库路径必须被拒绝")
    assert hasattr(bpy.types, "IRISRELAY_PT_demo_text")
    checks.append("错误入口路径不卸载现有界面")

    settings = bpy.context.window_manager.iris_relay_demo
    material = bpy.data.materials.new("GUI验证材质")
    cube = bpy.context.active_object
    cube.data.materials.clear()
    cube.data.materials.append(material)
    original_color = tuple(
        material.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value
    )
    original_data = (len(bpy.data.materials), len(bpy.data.images), len(material.node_tree.nodes))
    original_engine = bpy.context.scene.render.engine

    def demo(expected):
        result = bpy.ops.iris_relay.demo_export()
        assert result == {"FINISHED" if expected == "ready" else "CANCELLED"}, result
        assert settings.status == expected, settings.message

    with TemporaryDirectory(prefix="iris-gui-") as temporary:
        output = Path(temporary) / "中文材质.mtlx"
        settings.material = None
        settings.filepath = str(output)
        demo("error")
        checks.append("未选材质的错误反馈")

        assert bpy.ops.iris_relay.demo_use_active() == {"FINISHED"}
        assert settings.material == material
        demo("ready")
        assert "未生成" in settings.message
        assert not output.exists()
        checks.append("活动材质选择与演示成功，未生成文件")

        settings.filepath = ""
        assert settings.status == "idle", "修改输入应清除旧结果"
        demo("error")
        for path in (
            "relative.mtlx",
            "//relative.mtlx",
            str(output.with_suffix(".txt")),
            str(output.parent / "不存在" / "demo.mtlx"),
        ):
            settings.filepath = path
            demo("error")
        checks.append("空路径、歧义相对路径、扩展名和目录检查")

        output.write_text("保留已有内容", encoding="utf-8")
        settings.filepath = str(output)
        demo("error")
        assert output.read_text(encoding="utf-8") == "保留已有内容"
        checks.append("已有文件不被覆盖")

        selected = output.parent / "选择路径.mtlx"
        assert bpy.ops.iris_relay.demo_choose_path(filepath=str(selected)) == {"FINISHED"}
        assert settings.filepath == str(selected)
        assert settings.status == "idle"
        demo("ready")
        assert not selected.exists()
        checks.append("文件选择确认只更新路径")

        bpy.context.view_layer.objects.active = None
        assert bpy.ops.iris_relay.demo_use_active() == {"CANCELLED"}
        assert settings.material is None
        bpy.context.view_layer.objects.active = cube
        checks.append("无活动对象的错误反馈")

        assert (
            tuple(material.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value)
            == original_color
        )
        assert len(material.node_tree.nodes) == original_data[2]
        bpy.ops.iris_relay.demo_use_active()
        bpy.data.materials.remove(material)
        demo("error")
        checks.append("材质删除后不使用失效选择")

        # 验证其余数据和渲染条件；材质数量扣除测试主动删除的夹具。
        assert (len(bpy.data.materials) + 1, len(bpy.data.images)) == original_data[:2]
        assert bpy.context.scene.render.engine == original_engine
        checks.append("演示未新增材质资源或更改渲染器")

    ui.unregister()
    ui.unregister()
    assert not hasattr(bpy.types.WindowManager, "iris_relay_demo")
    assert "iris_relay_demo" not in bpy.context.window_manager
    assert not hasattr(bpy.types, "IRISRELAY_PT_demo_text")
    ui = importlib.reload(ui)
    ui.register()
    ui.unregister()
    checks.append("重复卸载与重新注册")

    from plugins.blender import BlenderAdapter
    from plugins.blender.adapter import BlenderAdapter as Adapter

    assert BlenderAdapter is Adapter
    sys.path.insert(0, str(ROOT / "plugins"))
    from blender import BlenderAdapter as LegacyAdapter

    assert LegacyAdapter.__name__ == "BlenderAdapter"
    checks.append("包入口和原验证脚本的导入兼容")
    print(
        json.dumps(
            {"status": "ok", "version": bpy.app.version_string, "checks": checks},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
