"""在 Cinema 4D 脚本管理器运行，打开 Iris Relay 原生材质交换窗口。"""

import importlib
import sys
from pathlib import Path

# 从仓库原文件运行自动定位；复制到脚本管理器时可填写本机路径，不提交个人配置。
REPO_ROOT = ""


def main():
    """定位仓库、释放旧窗口，再重新加载当前宿主界面。"""
    root = Path(REPO_ROOT).resolve() if REPO_ROOT else Path(__file__).resolve().parents[1]
    if not (root / "iris_relay").is_dir():
        raise RuntimeError("请将 REPO_ROOT 指向 Iris Relay 仓库")
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    previous = sys.modules.get("plugins.cinema4d.ui")
    dialog = getattr(previous, "_dialog", None)
    if dialog is not None:
        dialog.controller.reset_connection()
        dialog.Close()
    for name in (
        "plugins.presentation",
        "plugins.core",
        "plugins.cinema4d.controller",
        "plugins.cinema4d.adapter",
        "plugins.cinema4d.ui",
    ):
        if name in sys.modules:
            importlib.reload(sys.modules[name])
    from plugins.cinema4d.ui import show

    return show()


if __name__ == "__main__":
    main()
