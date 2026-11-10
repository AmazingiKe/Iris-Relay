"""公开的宿主读写辅助函数；不依赖 DCC，也不执行材质语义转换。"""

from copy import deepcopy
from uuid import uuid4

from .protocol import ValidationError


def request(service, operation, **payload):
    """核对统一响应版本与请求 ID，宿主不能应用跨请求或未知状态的数据。"""
    identifier = str(uuid4())
    response = service.handle(
        {
            "api_version": "1",
            "request_id": identifier,
            "operation": operation,
            "payload": payload,
        }
    )
    if (
        not isinstance(response, dict)
        or response.get("api_version") != "1"
        or response.get("request_id") != identifier
        or response.get("status") not in {"ok", "warning", "error"}
    ):
        raise ValidationError("核心响应协议或请求 ID 不匹配", "INVALID_RESPONSE")
    return response


def describe(service, renderer):
    """取得公开字典；协议身份校验后才返回可用于原生读写的绑定。"""
    response = request(service, "describe", renderer=renderer)
    if response["status"] != "ok":
        raise ValidationError(response["error"]["message"])
    return response["result"]["dictionary"]


def identify(
    service,
    renderer,
    native_id,
    properties,
    implementation_hint=None,
    *,
    prefer_source_default=False,
):
    """请求核心识别当前宿主证据；提示不能替代原生类型与配置。"""
    payload = {"renderer": renderer, "native_id": native_id, "properties": properties}
    if implementation_hint is not None:
        payload["implementation_hint"] = implementation_hint
    if prefer_source_default:
        payload["prefer_source_default"] = True
    response = request(service, "identify", **payload)
    if response["status"] != "ok":
        raise ValidationError(response["error"]["message"], response["error"]["code"])
    return response["result"]


def decode_value(value, rule):
    codec = rule.get("native", {}).get("codec", "identity")
    if codec == "gray":
        if type(value) in (float, int):
            return value
        if (
            not isinstance(value, list)
            or len(value) != 3
            or any(type(v) not in (float, int) for v in value)
            or max(value) - min(value) > 1e-6
        ):
            raise ValidationError("高度输入必须为标量或灰度三通道，不能猜测颜色通道")
        return value[0]
    if codec == "rgb":
        if len(value) == 4 and abs(value[3] - 1) > 1e-6:
            raise ValidationError("该颜色端口含非默认 Alpha，不能静默丢弃")
        return list(value[:3])
    if codec == "srgb":
        return [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in value]
    if codec == "normal":
        implicit = rule.get("native", {}).get("properties", {}).get("implicit_value", [0, 0, 0])
        return None if value is None or list(value) == implicit else list(value)
    if codec == "bool":
        return bool(value)
    return deepcopy(value)


def encode_value(value, rule):
    codec = rule.get("native", {}).get("codec", "identity")
    if codec == "gray":
        if type(value) not in (float, int):
            raise ValidationError("高度输入必须为标量")
        return [value, value, value]
    if codec == "rgb":
        return list(value) + [1.0]
    if codec == "srgb":
        return [12.92 * v if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055 for v in value]
    return deepcopy(value)


def resource_color_space(value, rule, *, encode=True):
    """按资源端口的声明映射逻辑色彩名称与原生 ID；未知名称不回退到 Auto。"""
    mapping = rule.get("native", {}).get("properties", {}).get("color_spaces")
    if mapping is None:
        return value
    if not isinstance(mapping, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) for k, v in mapping.items()
    ):
        raise ValidationError("资源色彩绑定必须为字符串映射")
    if not encode:
        if len(set(mapping.values())) != len(mapping):
            raise ValidationError("资源色彩绑定反向映射不唯一")
        mapping = {v: k for k, v in mapping.items()}
    if value not in mapping:
        raise ValidationError("字典尚未核验图片色彩名称：" + str(value), "COLORSPACE_UNSUPPORTED")
    return mapping[value]
