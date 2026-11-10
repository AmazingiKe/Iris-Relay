"""薄插件接入约定；只使用公开 API，不直接访问字典或转换实现。"""

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import uuid4

from .api import RelayService
from .model import Material, ValidationError
from .transport import JsonCommandService as JsonCommandService


class MaterialAdapter(Protocol):
    def read_material(self) -> dict:
        """读取选中材质，按统一协议导出完整图信息。"""
        ...

    def create_material(self, material: dict) -> Any:
        """新建材质并返回宿主句柄；由适配器保证失败回滚与源材质保留。"""
        ...


@dataclass(frozen=True)
class PluginResult:
    response: dict
    created: Any = None


def convert_material(
    adapter: MaterialAdapter,
    service: RelayService,
    target: dict,
    *,
    calibrate: bool = False,
    accept_warnings: bool = False,
) -> PluginResult:
    """默认只应用无警告方案；不会自动替换场景中的材质绑定。"""
    request = {
        "api_version": "1",
        "request_id": str(uuid4()),
        "operation": "convert",
        "payload": {
            "material": deepcopy(adapter.read_material()),
            "target": deepcopy(target),
            "calibrate": calibrate,
        },
    }
    response = service.handle(request)
    if response.get("api_version") != "1" or response.get("request_id") != request["request_id"]:
        raise ValidationError("服务返回的 API 版本或请求 ID 不匹配")
    allowed = {"ok", "warning"} if accept_warnings else {"ok"}
    if response.get("status") not in allowed:
        return PluginResult(response)
    result = response.get("result")
    if not isinstance(result, dict) or result.get("material") is None:
        raise ValidationError("转换响应缺少目标材质")
    material = Material.parse(result["material"]).to_dict()
    if material["renderer"] != target:
        raise ValidationError("服务返回的目标渲染器与请求不一致")
    return PluginResult(response, adapter.create_material(material))
