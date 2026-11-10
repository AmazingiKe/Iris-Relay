"""在 Maya 2022 的 Python 脚本编辑器运行；只定位仓库并打开导入窗口。"""

import importlib
import sys
from pathlib import Path

# 从原文件执行时自动定位；复制到脚本编辑器时可在本机填写，不提交个人路径。
REPO_ROOT = ""


def main():
    """检查 Python 版本与仓库位置，然后加载宿主界面。"""
    if sys.version_info < (3, 7):  # noqa: UP036 — 检查 Maya 可选 Python 2 启动模式
        raise RuntimeError("请用 Maya 2022 的 Python 3 模式运行")
    root = Path(REPO_ROOT).resolve() if REPO_ROOT else Path(__file__).resolve().parents[1]
    if not (root / "iris_relay").is_dir():
        raise RuntimeError("请将 REPO_ROOT 指向 Iris Relay 仓库")
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    for name in ("plugins.maya.controller", "plugins.maya.adapter", "plugins.maya.ui"):
        if name in sys.modules:
            importlib.reload(sys.modules[name])
    from plugins.maya import show

    return show()


if __name__ == "__main__":
    main()
