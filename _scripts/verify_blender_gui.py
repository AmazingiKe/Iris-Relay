"""在独立 Blender 中验证统一界面；使用 --background --factory-startup --python 运行。"""

import importlib
import json
import os
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
    bpy.context.window_manager.iris_relay_demo.surface_only = True
    bpy.context.window_manager.iris_relay_demo.import_filepath = "上次导入.mtlx"
    runpy.run_path(str(ENTRY), run_name="__main__")
    from plugins.blender import ui

    assert "iris_relay" not in sys.modules, "GUI demo 不应提前加载转换核心"
    assert hasattr(bpy.types, "IRISRELAY_PT_demo_text")
    assert hasattr(bpy.types, "IRISRELAY_PT_demo_view")
    assert bpy.context.window_manager.iris_relay_demo.filepath == ""
    assert bpy.context.window_manager.iris_relay_demo.surface_only is False
    assert bpy.context.window_manager.iris_relay_demo.import_filepath == ""
    for handlers in (
        bpy.app.handlers.undo_post,
        bpy.app.handlers.redo_post,
        bpy.app.handlers.load_post,
    ):
        assert handlers.count(ui.reset_after_history) == 1
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
    assert settings.mode == "IMPORT"
    settings.mode = "EXPORT"
    assert settings.transport == "AUTO"
    assert settings.http_url == "http://127.0.0.1:8765"
    assert settings.connection_status == "idle"
    assert bpy.types.IRISRELAY_PT_demo_view.bl_label == "Iris Relay"
    previous_url = os.environ.get("IRIS_RELAY_HTTP_URL")
    try:
        os.environ["IRIS_RELAY_HTTP_URL"] = "http://127.0.0.1:1"
        settings.transport = "LOCAL"
        assert bpy.ops.iris_relay.check_connection() == {"FINISHED"}
        assert settings.connection_status == "ok"
        local_service = ui._service
        settings.report_json = '{"status":"ok"}'
        settings.status = "ok"
        settings.transport = "HTTP"
        assert ui._service is None
        assert local_service.pid is None
        assert settings.status == "idle" and not settings.report_json
        settings.http_url = "http://127.0.0.1:1"
        assert bpy.ops.iris_relay.check_connection() == {"CANCELLED"}
        assert settings.connection_status == "error"
        assert ui._service is None
        assert os.environ["IRIS_RELAY_HTTP_URL"] == "http://127.0.0.1:1"
        settings.transport = "LOCAL"
        assert bpy.ops.iris_relay.check_connection() == {"FINISHED"}
    finally:
        if previous_url is None:
            os.environ.pop("IRIS_RELAY_HTTP_URL", None)
        else:
            os.environ["IRIS_RELAY_HTTP_URL"] = previous_url
    checks.append("统一连接界面、本地绕过环境、HTTP失败不回退及切换回收服务")
    http_url = os.environ.get("IRIS_RELAY_UI_VERIFY_HTTP_URL")
    if http_url:
        settings.transport = "HTTP"
        settings.http_url = http_url
        assert bpy.ops.iris_relay.check_connection() == {"FINISHED"}
        assert type(ui._service).__name__ == "HttpRelayService"
        checks.append("真实 HTTP 检测通过，后续导入导出按钮使用 HTTP")
    material = bpy.data.materials.new("GUI验证材质")
    cube = bpy.context.active_object
    cube.data.materials.clear()
    cube.data.materials.append(material)
    original_color = tuple(
        material.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value
    )
    original_data = (len(bpy.data.materials), len(bpy.data.images), len(material.node_tree.nodes))
    original_engine = bpy.context.scene.render.engine

    def export(expected, *, saved=False):
        result = bpy.ops.iris_relay.export_materialx()
        assert result == {"FINISHED" if saved else "CANCELLED"}, result
        assert settings.status == expected, settings.message

    with TemporaryDirectory(prefix="iris-gui-") as temporary:
        output = Path(temporary) / "中文材质.mtlx"
        settings.material = None
        settings.filepath = str(output)
        export("error")
        checks.append("未选材质的错误反馈")

        assert bpy.ops.iris_relay.demo_use_active() == {"FINISHED"}
        assert settings.material == material
        assert bpy.ops.iris_relay.check_material() == {"FINISHED"}
        assert settings.status == "warning"
        assert any(i.code == "SHADING_MODEL_APPROXIMATION" for i in settings.issues)
        export("warning")
        assert not output.exists()
        settings.accept_warnings = True
        export("warning", saved=True)
        assert output.is_file()
        report = json.loads(settings.report_json)
        assert report["validation_response"]["result"]["valid"] is True
        assert report["saved_path"] == str(output)
        original_document = output.read_text("utf-8")
        checks.append("真实导出通过 MaterialX 校验，警告默认阻止保存")

        settings.filepath = ""
        assert settings.status == "idle", "修改输入应清除旧结果"
        export("error")
        for path in (
            "relative.mtlx",
            "//relative.mtlx",
            str(output.with_suffix(".txt")),
            str(output.parent / "不存在" / "demo.mtlx"),
        ):
            settings.filepath = path
            export("error")
        checks.append("空路径、歧义相对路径、扩展名和目录检查")

        settings.filepath = str(output)
        export("error")
        assert output.read_text(encoding="utf-8") == original_document
        checks.append("已有文件不被覆盖")

        selected = output.parent / "选择路径.mtlx"
        assert bpy.ops.iris_relay.demo_choose_path(filepath=str(selected)) == {"FINISHED"}
        assert settings.filepath == str(selected)
        assert settings.status == "idle"
        assert not selected.exists()
        checks.append("文件选择确认只更新路径")

        unsupported = material.node_tree.nodes.new("ShaderNodeTexNoise")
        material.node_tree.links.new(
            unsupported.outputs["Color"],
            material.node_tree.nodes["Principled BSDF"].inputs["Base Color"],
        )
        export("error")
        assert not selected.exists()
        assert settings.issues
        material.node_tree.nodes.remove(unsupported)
        checks.append("未知节点明确报告并阻断保存")

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
        export("error")
        checks.append("材质删除后不使用失效选择")

        # 验证其余数据和渲染条件；材质数量扣除测试主动删除的夹具。
        assert (len(bpy.data.materials) + 1, len(bpy.data.images)) == original_data[:2]
        assert bpy.context.scene.render.engine == original_engine
        checks.append("导出未修改源材质、资源或渲染器")

        sys.path.insert(0, str(ROOT / "_scripts"))
        from native_fixture import fixture
        from verify_native import write_png

        from plugins.blender import BlenderAdapter

        texture = output.parent / "法线高度.png"
        write_png(texture)
        original_texture = texture.read_bytes()
        profile = json.loads((ROOT / "dictionaries/blender-cycles-5.2.3.json").read_text("utf-8"))
        for semantic in ("normal.tangent", "bump.height"):
            source = BlenderAdapter(None, ui._service).create_material(
                fixture(profile, str(texture), normal_semantic=semantic)
            )
            before = BlenderAdapter(source, ui._service).read_material()
            settings.material = source
            settings.filepath = str(output.parent / (semantic + ".mtlx"))
            export("warning", saved=True)
            report = json.loads(settings.report_json)
            assert report["validation_response"]["result"]["valid"] is True
            document = Path(settings.filepath).read_text("utf-8")
            assert "raw" in document and str(texture) in document
            assert BlenderAdapter(source, ui._service).read_material() == before
            assert texture.read_bytes() == original_texture
        checks.append("图片驱动法线和高度凹凸的真实导出、连接与 Raw 语义")

        # 使用刚导出的文件走真实 UI 导入；不依赖活动对象或所选源材质。
        assert settings.import_filepath == settings.filepath
        settings.mode = "IMPORT"
        settings.material = None
        original_materials = set(bpy.data.materials.keys())
        original_images = set(bpy.data.images.keys())
        original_binding = list(cube.data.materials)
        assert bpy.ops.iris_relay.check_import() == {"FINISHED"}
        assert settings.status == "warning"
        assert bpy.ops.iris_relay.import_materialx() == {"CANCELLED"}
        assert set(bpy.data.materials.keys()) == original_materials
        settings.accept_import_warnings = True
        assert bpy.ops.iris_relay.import_materialx() == {"FINISHED"}
        imported_report = json.loads(settings.report_json)
        imported_name = imported_report["created_material"]
        imported = bpy.data.materials[imported_name]
        assert imported.use_fake_user and imported not in list(cube.data.materials)
        assert set(bpy.data.materials.keys()) - original_materials == {imported_name}
        assert list(cube.data.materials) == original_binding
        assert BlenderAdapter(source, ui._service).read_material() == before
        assert texture.read_bytes() == original_texture
        assert bpy.context.scene.render.engine == original_engine
        assert BlenderAdapter(imported, ui._service).read_material()["links"]
        checks.append("导出后自动填入导入路径、检查警告、确认后创建独立材质")

        # 输入损坏或资源缺失时保留已有导入结果，不遗留额外数据。
        after_import = (set(bpy.data.materials.keys()), set(bpy.data.images.keys()))
        broken = output.parent / "损坏.mtlx"
        broken.write_text("<broken", encoding="utf-8")
        assert bpy.ops.iris_relay.demo_choose_import(filepath=str(broken)) == {"FINISHED"}
        assert settings.status == "idle"
        assert bpy.ops.iris_relay.import_materialx() == {"CANCELLED"}
        assert settings.status == "error"
        settings.import_filepath = settings.filepath
        texture.rename(texture.with_suffix(".hidden"))
        assert bpy.ops.iris_relay.import_materialx() == {"CANCELLED"}
        texture.with_suffix(".hidden").rename(texture)
        assert (set(bpy.data.materials.keys()), set(bpy.data.images.keys())) == after_import
        assert original_images < set(bpy.data.images.keys())
        checks.append("损坏文档与缺失贴图阻断，既有材质和图片不受影响")

        # 未绑定材质必须在保存重开后仍然存在。
        blend = output.parent / "导入保存验证.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(blend))
        bpy.ops.wm.open_mainfile(filepath=str(blend))
        assert imported_name in bpy.data.materials
        assert bpy.data.materials[imported_name].use_fake_user
        assert bpy.context.window_manager.iris_relay_demo.status == "idle"
        assert bpy.context.window_manager.iris_relay_demo.report_json == ""
        checks.append("导入的新材质未绑定对象时仍能保存重开")

    ui.unregister()
    ui.unregister()
    assert ui.reset_after_history not in bpy.app.handlers.undo_post
    assert ui.reset_after_history not in bpy.app.handlers.redo_post
    assert ui.reset_after_history not in bpy.app.handlers.load_post
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
