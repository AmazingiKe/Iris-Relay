"""Maya 接入入口；按需加载宿主 API，不把 Maya 依赖引入材质核心。"""


def show():
    """从 Maya Python 脚本编辑器显示 MaterialX 导入面板。"""
    from .ui import show as show_window

    return show_window()
