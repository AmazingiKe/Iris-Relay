"""公开的宿主读写辅助函数；不依赖 DCC，也不执行材质语义转换。"""

from copy import deepcopy
from uuid import uuid4

from .model import ValidationError


def describe(service, renderer):
    response = service.handle(
        {
            "api_version": "1",
            "request_id": str(uuid4()),
            "operation": "describe",
            "payload": {"renderer": renderer},
        }
    )
    if response["status"] != "ok":
        raise ValidationError(response["error"]["message"])
    return response["result"]["dictionary"]


def identify(service, renderer, native_id, properties):
    response = service.handle(
        {
            "api_version": "1",
            "request_id": str(uuid4()),
            "operation": "identify",
            "payload": {"renderer": renderer, "native_id": native_id, "properties": properties},
        }
    )
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
        return None if value is None or list(value) == [0, 0, 0] else list(value)
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
