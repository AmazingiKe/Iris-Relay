"""在 Blender 文本编辑器中运行，侧栏的 Iris Relay 标签显示 GUI demo。"""

import importlib
import sys
from pathlib import Path

# 填写本机仓库根目录（包含 iris_relay 和 plugins），不要填写 plugins 子目录。
# 留空时，从当前脚本文件的位置自动定位；复制到未保存的文本块时必须填写。
# 本机路径只在 Blender 文本编辑器中修改，不提交到仓库。
REPO_ROOT = ""


def main(repo_root=REPO_ROOT):
    if repo_root:
        root = Path(repo_root).expanduser()
        if not root.is_absolute():
            raise ValueError("REPO_ROOT 需要填写仓库的绝对路径")
        root = root.resolve()
    else:
        script = Path(globals().get("__file__", ""))
        if not script.is_absolute() or not script.is_file():
            import bpy

            space = bpy.context.space_data
            text = getattr(space, "text", None)
            script = Path(text.filepath) if text and text.filepath else Path()
        if not script.is_absolute() or not script.is_file():
            raise ValueError("请在脚本顶部 REPO_ROOT 中填写 Iris Relay 仓库的绝对路径")
        root = script.resolve().parents[1]
    expected = root / "plugins/blender/ui.py"
    if not expected.is_file() or not (root / "iris_relay").is_dir():
        raise ValueError("仓库路径无效：需要同时包含 plugins/blender/ui.py 和 iris_relay/")
    if not repo_root and script.resolve() != root / "_examples/blender_demo_gui.py":
        raise ValueError("入口脚本已移出仓库，请明确填写 REPO_ROOT")

    module_name = "plugins.blender.ui"
    old_ui = sys.modules.get(module_name)
    if old_ui and Path(old_ui.__file__).resolve() != expected:
        raise ValueError("已加载另一份仓库的 GUI，请重新启动 Blender 后再指定新路径")
    root_string = str(root)
    if root_string not in sys.path:
        sys.path.insert(0, root_string)
    importlib.invalidate_caches()
    ui = importlib.import_module(module_name)
    if Path(ui.__file__).resolve() != expected:
        raise ValueError("导入路径与指定仓库不一致，请检查 Blender 中已有的 plugins 模块")
    ui.unregister()
    for name in ("plugins.blender.controller", "plugins.blender.adapter"):
        if name in sys.modules:
            importlib.reload(sys.modules[name])
    ui = importlib.reload(ui)
    ui.register()
    ui.show()
    print("Iris Relay GUI demo 已加载：文本编辑器或 3D 视图 → N 侧栏 → Iris Relay")


if __name__ == "__main__":
    main()
