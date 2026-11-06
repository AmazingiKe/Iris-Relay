"""Blender 接入入口；按需加载材质适配器，GUI 可独立于转换核心运行。"""

__all__ = ["BlenderAdapter", "renderer_ref", "find_socket", "socket_value"]


def __getattr__(name):
    if name in __all__:
        from . import adapter

        return getattr(adapter, name)
    raise AttributeError(f"{__name__} 没有属性 {name}")
