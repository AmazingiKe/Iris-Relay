"""可由 Python 3.7 宿主使用的公开 JSON 校验与错误类型，不加载材质核心。"""

import json
import math


class ValidationError(ValueError):
    """包含可用于 API 返回的稳定错误码。"""

    def __init__(self, message: str, code: str = "INVALID_REQUEST"):
        super().__init__(message)
        self.code = code


def json_value(value, depth: int = 0) -> None:
    """递归检查 JSON 值与深度，拒绝非有限数值及不可传输对象。"""
    if depth > 64:
        raise ValidationError("JSON 嵌套超过 64 层")
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, (int, float)):
        try:
            valid = math.isfinite(value)
        except OverflowError:
            valid = False
        if not valid:
            raise ValidationError("数值必须有限，不能使用 NaN 或 Infinity")
        return
    if isinstance(value, list):
        for item in value:
            json_value(item, depth + 1)
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for item in value.values():
            json_value(item, depth + 1)
        return
    raise ValidationError("值不属于支持的 JSON 类型")


MAX_JSON_BYTES = 16 * 1024 * 1024


def parse_json(content: bytes):
    """拒绝重复键与非标准数值，限制输入体积。"""

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValidationError(f"JSON 字段重复：{key}")
            result[key] = value
        return result

    if len(content) > MAX_JSON_BYTES:
        raise ValidationError("JSON 文件超过 16 MiB")
    try:
        result = json.loads(content.decode("utf-8-sig"), object_pairs_hook=pairs)
        json_value(result)
        return result
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValidationError(f"无法解析 JSON：{exc}") from exc
