"""渲染器语义字典；只接受白名单变换，不执行字典中的代码。"""

import json
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from .model import ValidationError, fields, json_value, text

VALUE_TYPES = {"float", "color", "vector", "string", "bool", "texture"}


def validate_value(value: Any, rule: dict) -> None:
    kind = rule["type"]
    scalar = isinstance(value, (int, float)) and not isinstance(value, bool)
    valid = False
    if kind == "float":
        valid = scalar
    elif kind in {"color", "vector"}:
        valid = (
            isinstance(value, list)
            and len(value) == 3
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)
        )
    elif kind == "string":
        valid = isinstance(value, str)
    elif kind == "bool":
        valid = isinstance(value, bool)
    elif kind == "texture":
        valid = value is None or (
            isinstance(value, dict)
            and set(value) == {"texture"}
            and isinstance(value["texture"], str)
            and bool(value["texture"])
        )
    elif kind == "shader":
        valid = value is None
    if not valid:
        raise ValidationError(f"值与类型 {kind} 不匹配", "INVALID_VALUE")
    json_value(value)
    if "range" in rule:
        low, high = rule["range"]
        values = value if isinstance(value, list) else [value]
        if any(v < low or v > high for v in values):
            raise ValidationError(f"值超出允许范围 [{low}, {high}]", "INVALID_VALUE")


def transform(value: Any, rule: dict) -> Any:
    # 首版仅提供自身可逆的数值操作；贴图连接不能直接套用此变换。
    return 1.0 - value if rule.get("transform", "identity") == "one_minus" else deepcopy(value)


@dataclass(frozen=True)
class Dictionary:
    data: dict
    fingerprint: str

    @classmethod
    def parse(cls, value: Any) -> "Dictionary":
        json_value(value)
        fields(value, {"schema_version", "id", "version", "renderer", "nodes"})
        if value["schema_version"] != "1":
            raise ValidationError("不支持的字典协议版本")
        text(value["id"], "字典 ID")
        text(value["version"], "字典版本")
        fields(value["renderer"], {"id", "versions"})
        text(value["renderer"]["id"], "渲染器 ID")
        versions = value["renderer"]["versions"]
        if not isinstance(versions, list) or not versions:
            raise ValidationError("字典必须明确列出支持的渲染器版本")
        for version in versions:
            text(version, "渲染器版本")
        if len(versions) != len(set(versions)):
            raise ValidationError("字典版本列表重复")
        if not isinstance(value["nodes"], dict) or not value["nodes"]:
            raise ValidationError("字典 nodes 必须为非空对象")
        semantics = set()
        for native, node in value["nodes"].items():
            text(native, "原生节点类型")
            fields(node, {"semantic", "inputs", "outputs"})
            semantic = text(node["semantic"], "节点语义")
            if semantic in semantics:
                raise ValidationError(f"节点语义有多个目标实现，无法确定选择：{semantic}")
            semantics.add(semantic)
            for direction in ("inputs", "outputs"):
                if not isinstance(node[direction], dict):
                    raise ValidationError("字典端口映射必须为对象")
                port_semantics = set()
                for port, rule in node[direction].items():
                    text(port, "原生端口")
                    if direction == "inputs":
                        fields(
                            rule,
                            {"semantic", "type", "default"},
                            {"range", "transform", "adjustable"},
                        )
                    else:
                        fields(rule, {"semantic", "type"})
                    semantic_port = text(rule["semantic"], "端口语义")
                    if semantic_port in port_semantics:
                        raise ValidationError(f"端口语义重复：{semantic_port}")
                    port_semantics.add(semantic_port)
                    if not isinstance(rule["type"], str) or rule["type"] not in VALUE_TYPES | {
                        "shader"
                    }:
                        raise ValidationError("不支持的字典数据类型")
                    if direction == "outputs":
                        continue
                    if rule.get("transform", "identity") not in ("identity", "one_minus"):
                        raise ValidationError("不支持的字典变换")
                    if rule.get("transform") == "one_minus" and rule["type"] != "float":
                        raise ValidationError("one_minus 只支持浮点输入")
                    if type(rule.get("adjustable", False)) is not bool:
                        raise ValidationError("adjustable 必须为布尔值")
                    if rule.get("adjustable") and rule["type"] not in {"float", "color", "vector"}:
                        raise ValidationError("首版校正只开放数值和向量输入")
                    if "range" in rule:
                        bounds = rule["range"]
                        if (
                            rule["type"] not in {"float", "color", "vector"}
                            or not isinstance(bounds, list)
                            or len(bounds) != 2
                            or any(type(v) not in (int, float) for v in bounds)
                            or bounds[0] > bounds[1]
                        ):
                            raise ValidationError("无效的输入范围")
                    validate_value(rule["default"], rule)
        encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode(
            "utf-8"
        )
        return cls(deepcopy(value), sha256(encoded).hexdigest())

    @property
    def nodes(self) -> dict:
        return self.data["nodes"]

    def summary(self) -> dict:
        return {
            "dictionary_id": self.data["id"],
            "dictionary_version": self.data["version"],
            "renderer": deepcopy(self.data["renderer"]),
            "fingerprint": self.fingerprint,
        }


class Registry:
    def __init__(self, profiles):
        self._profiles = {}
        self._dictionaries = []
        for profile in profiles:
            dictionary = Dictionary.parse(profile)
            renderer = dictionary.data["renderer"]
            for version in renderer["versions"]:
                key = (renderer["id"], version)
                if key in self._profiles:
                    raise ValidationError(f"渲染器版本存在重复字典：{key}")
                self._profiles[key] = dictionary
            self._dictionaries.append(dictionary)

    def get(self, renderer: dict) -> Dictionary:
        try:
            return self._profiles[(renderer["id"], renderer["version"])]
        except KeyError as exc:
            raise ValidationError(
                f"没有对应字典：{renderer['id']} / {renderer['version']}", "UNSUPPORTED_RENDERER"
            ) from exc

    def capabilities(self) -> list[dict]:
        return [dictionary.summary() for dictionary in self._dictionaries]
