"""Iris Relay：与 DCC 解耦的材质转换服务。"""

__all__ = ["RelayAPI", "RelayService"]
__version__ = "0.4.0"


def __getattr__(name):
    """仅在请求核心服务时加载新 Python 核心；旧宿主可单独导入传输和辅助接口。"""
    if name in __all__:
        from .api import RelayAPI, RelayService

        return {"RelayAPI": RelayAPI, "RelayService": RelayService}[name]
    raise AttributeError(name)
