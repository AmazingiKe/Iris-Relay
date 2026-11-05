"""渲染器语义字典；只接受白名单变换，不执行字典中的代码。"""

import json
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from .model import ValidationError, fields, json_value, text

VALUE_TYPES = {"float", "color", "color4", "vector", "string", "bool", "texture"}


def validate_value(value: Any, rule: dict) -> None:
    if value is None and rule.get("nullable", False):
        return
    kind = rule["type"]
    scalar = isinstance(value, (int, float)) and not isinstance(value, bool)
    valid = False
    if kind == "float":
        valid = scalar
    elif kind in {"color", "color4", "vector"}:
        valid = (
            isinstance(value, list)
            and len(value) == (4 if kind == "color4" else 3)
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


def transform(value: Any, rule: dict, *, encode: bool = False) -> Any:
    operation = rule.get("transform", "identity")
    if isinstance(operation, dict):
        factor = operation["factor"]
        return value / factor if encode else value * factor
    if operation == "one_minus":
        return 1.0 - value
    if operation == "scalar_to_color":
        if encode:
            if not isinstance(value, list) or len(value) != 3 or max(value) - min(value) > 1e-6:
                raise ValidationError("非灰度颜色无法无损转换成标量", "INVALID_VALUE")
            return value[0]
        return [value, value, value]
    return deepcopy(value)


def native_binding(value: Any) -> None:
    fields(value, {"id"}, {"label", "properties", "kind", "codec", "colorspace_id", "uv_id"})
    identifier = value["id"]
    if type(identifier) not in (str, int) or (isinstance(identifier, str) and not identifier):
        raise ValidationError("原生 ID 必须为非空字符串或整数 code")
    if "label" in value:
        text(value["label"], "原生名称")
    if not isinstance(value.get("properties", {}), dict):
        raise ValidationError("原生属性必须为声明式对象")
    if value.get("kind", "socket") not in {"socket", "property", "resource"}:
        raise ValidationError("不支持的原生绑定类型")
    if value.get("codec", "identity") not in {
        "identity",
        "rgb",
        "bool",
        "normal",
        "texture",
        "srgb",
    }:
        raise ValidationError("不支持的原生值编码")
    for key in ("colorspace_id", "uv_id"):
        if key in value and type(value[key]) not in (str, int):
            raise ValidationError("资源端口 ID 必须为字符串或整数")


@dataclass(frozen=True)
class Dictionary:
    data: dict
    fingerprint: str

    @classmethod
    def parse(cls, value: Any) -> "Dictionary":
        json_value(value)
        fields(
            value,
            {"schema_version", "id", "version", "renderer", "nodes"},
            {"host", "description", "evidence"},
        )
        if value["schema_version"] != "1":
            raise ValidationError("不支持的字典协议版本")
        text(value["id"], "字典 ID")
        text(value["version"], "字典版本")
        if "description" in value:
            text(value["description"], "字典说明")
        if "evidence" in value and not isinstance(value["evidence"], dict):
            raise ValidationError("字典依据必须为对象")
        if "host" in value:
            host = value["host"]
            fields(host, {"id", "versions", "node_system"})
            text(host["id"], "宿主 ID")
            text(host["node_system"], "节点系统")
            if not isinstance(host["versions"], list) or not host["versions"]:
                raise ValidationError("必须明确支持的宿主版本")
            for version in host["versions"]:
                text(version, "宿主版本")
            if len(set(host["versions"])) != len(host["versions"]):
                raise ValidationError("宿主版本重复")
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
            fields(node, {"semantic", "inputs", "outputs"}, {"native", "approximation"})
            if "native" in node:
                native_binding(node["native"])
            if "approximation" in node:
                text(node["approximation"], "近似说明")
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
                            {
                                "range",
                                "transform",
                                "adjustable",
                                "native",
                                "omit_if_default",
                                "nullable",
                            },
                        )
                    else:
                        fields(rule, {"semantic", "type"}, {"native"})
                    if "native" in rule:
                        native_binding(rule["native"])
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
                    operation = rule.get("transform", "identity")
                    if isinstance(operation, dict):
                        fields(operation, {"op", "factor"})
                        if (
                            operation["op"] != "scale"
                            or type(operation["factor"]) not in (int, float)
                            or operation["factor"] == 0
                        ):
                            raise ValidationError("scale 必须使用非零有限数值系数")
                    elif operation not in ("identity", "one_minus", "scalar_to_color"):
                        raise ValidationError("不支持的字典变换")
                    if operation != "identity" and rule["type"] != "float":
                        raise ValidationError("数值变换只支持浮点输入")
                    for option in ("omit_if_default", "nullable"):
                        if type(rule.get(option, False)) is not bool:
                            raise ValidationError(f"{option} 必须为布尔值")
                    if rule.get("nullable") and operation != "identity":
                        raise ValidationError("可空输入不能执行数值变换")
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
            "host": deepcopy(self.data.get("host")),
            "fingerprint": self.fingerprint,
        }


class Registry:
    def __init__(self, profiles):
        self._profiles = {}
        self._dictionaries = []
        for profile in profiles:
            dictionary = Dictionary.parse(profile)
            renderer = dictionary.data["renderer"]
            host = dictionary.data.get("host", {})
            for version in renderer["versions"]:
                for host_version in host.get("versions", [None]):
                    key = (
                        renderer["id"],
                        version,
                        host.get("id"),
                        host_version,
                        host.get("node_system"),
                    )
                    if key in self._profiles:
                        raise ValidationError(f"渲染器和宿主版本存在重复字典：{key}")
                    self._profiles[key] = dictionary
            self._dictionaries.append(dictionary)

    def get(self, renderer: dict) -> Dictionary:
        try:
            return self._profiles[
                (
                    renderer["id"],
                    renderer["version"],
                    renderer.get("host"),
                    renderer.get("host_version"),
                    renderer.get("node_system"),
                )
            ]
        except KeyError as exc:
            raise ValidationError(
                f"没有对应字典：{renderer['id']} / {renderer['version']}", "UNSUPPORTED_RENDERER"
            ) from exc

    def capabilities(self) -> list[dict]:
        return [dictionary.summary() for dictionary in self._dictionaries]
