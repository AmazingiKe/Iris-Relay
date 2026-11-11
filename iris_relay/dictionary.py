"""渲染器语义字典；只接受白名单变换，不执行字典中的代码。"""

import json
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from .model import ValidationError, fields, json_value, text
from .semantics import validate_contract

VALUE_TYPES = {"float", "color", "color4", "vector", "string", "bool", "texture"}


def version_limits(binding: dict) -> frozenset[str] | None:
    """读取可选的精确版本限制；省略表示共享规则，空表及通配符不冒充支持声明。"""
    if "versions" not in binding:
        return None
    versions = binding["versions"]
    if not isinstance(versions, list) or not versions:
        raise ValidationError("版本限制必须为非空列表；跨版本共享请省略 versions")
    for version in versions:
        text(version, "版本限制")
        if "*" in version:
            raise ValidationError("版本限制不接受通配符；跨版本共享请省略 versions")
    if len(set(versions)) != len(versions):
        raise ValidationError("版本限制重复")
    return frozenset(versions)


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
    projection = value.get("properties", {}).get("output_projection")
    if projection is not None:
        fields(projection, {"asset", "input", "source"})
        for key, item in projection.items():
            text(item, "输出投影的 " + key)
    if value.get("kind", "socket") not in {"socket", "property", "resource"}:
        raise ValidationError("不支持的原生绑定类型")
    if value.get("codec", "identity") not in {
        "identity",
        "rgb",
        "bool",
        "normal",
        "texture",
        "srgb",
        "gray",
        "color_ramp",
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
            {
                "host",
                "description",
                "evidence",
                "semantic_schema_version",
                "materialx_export",
                "materialx_extensions",
                "materialx_lowerings",
                "materialx_recipes",
                "texture_coordinates",
                "texture_alpha_modes",
                "texture_image_channels",
                "texture_alpha_behavior",
            },
        )
        if value["schema_version"] != "1":
            raise ValidationError("不支持的字典协议版本")
        text(value["id"], "字典 ID")
        text(value["version"], "字典版本")
        if "description" in value:
            text(value["description"], "字典说明")
        if "evidence" in value and not isinstance(value["evidence"], dict):
            raise ValidationError("字典依据必须为对象")
        if "texture_image_channels" in value:
            channels = value["texture_image_channels"]
            if (
                not isinstance(channels, list)
                or not channels
                or any(type(c) is not int or c not in {1, 3, 4} for c in channels)
                or len(set(channels)) != len(channels)
            ):
                raise ValidationError("图片通道能力必须为不重复的 1/3/4 列表")
        behavior = value.get("texture_alpha_behavior", "independent")
        choices = list(behavior.values()) if isinstance(behavior, dict) else [behavior]
        if not choices or any(
            type(v) is not str or v not in {"premultiplied", "independent"} for v in choices
        ):
            raise ValidationError("未知的原生图片颜色关联行为")
        if "materialx_lowerings" in value:
            rules = value["materialx_lowerings"]
            if not isinstance(rules, dict):
                raise ValidationError("组合规则必须为对象")
            for name, choices in rules.items():
                text(name, "组合规则的官方定义")
                if not isinstance(choices, list) or not choices or len(choices) > 32:
                    raise ValidationError("每个组合规则需要 1 至 32 个候选")
                for choice in choices:
                    fields(choice, {"when", "replacement"})
                    text(choice["replacement"], "组合规则的替换定义")
                    if not isinstance(choice["when"], dict):
                        raise ValidationError("组合规则的常量条件必须为对象")
                    for key, expected in choice["when"].items():
                        text(key, "组合规则的输入")
                        if type(expected) not in (float, int, str, bool):
                            raise ValidationError("组合规则只接受标量常量条件")
        if "materialx_recipes" in value:
            recipes = value["materialx_recipes"]
            if not isinstance(recipes, dict) or any(
                k not in value["nodes"] or v not in {"blender.color_ramp", "blender.normal_map"}
                for k, v in recipes.items()
            ):
                raise ValidationError("未知或未绑定的原生计算配方")
        if "materialx_export" in value:
            rules = fields(value["materialx_export"], {"color_to_float"})
            if rules["color_to_float"] != "scene_linear_luminance":
                raise ValidationError("未知的 MaterialX 隐式颜色连接规则")
        if "materialx_extensions" in value:
            text(value["materialx_extensions"], "内联 MaterialX 扩展库")
        if "texture_coordinates" in value:
            coordinates = fields(
                value["texture_coordinates"], {"named_uv", "transforms"}, {"sampler"}
            )
            if type(coordinates["named_uv"]) is not bool or coordinates["transforms"] not in (
                "none",
                "trs",
                "trs2d",
            ):
                raise ValidationError("未知的贴图坐标目标能力")
            if "sampler" in coordinates:
                sampler = fields(
                    coordinates["sampler"], {"rotation_unit", "pivot", "order", "verified"}
                )
                if (
                    coordinates["transforms"] != "trs2d"
                    or sampler["rotation_unit"] not in {"degrees", "radians"}
                    or sampler["order"] != "scale_rotate_translate"
                    or type(sampler["verified"]) is not bool
                    or not isinstance(sampler["pivot"], list)
                    or len(sampler["pivot"]) != 2
                    or any(type(v) not in (float, int) for v in sampler["pivot"])
                ):
                    raise ValidationError("未知的二维采样器坐标约定")
            elif coordinates["transforms"] == "trs2d":
                raise ValidationError("二维采样器须声明坐标约定")
        if "texture_alpha_modes" in value:
            modes = value["texture_alpha_modes"]
            if (
                not isinstance(modes, list)
                or any(
                    mode not in ("STRAIGHT", "PREMUL", "CHANNEL_PACKED", "NONE") for mode in modes
                )
                or len(set(modes)) != len(modes)
            ):
                raise ValidationError("未知或重复的图片 Alpha 目标模式")
        if "host" in value:
            host = value["host"]
            fields(host, {"id", "node_system"}, {"versions"})
            text(host["id"], "宿主 ID")
            text(host["node_system"], "节点系统")
            version_limits(host)
        fields(value["renderer"], {"id"}, {"versions"})
        text(value["renderer"]["id"], "渲染器 ID")
        version_limits(value["renderer"])
        if not isinstance(value["nodes"], dict) or not value["nodes"]:
            raise ValidationError("字典 nodes 必须为非空对象")
        if "semantic_schema_version" in value and value["semantic_schema_version"] != "1":
            raise ValidationError("不支持的通用语义版本")
        defaults = set()
        for native, node in value["nodes"].items():
            text(native, "原生节点类型")
            fields(
                node,
                {"semantic", "inputs", "outputs"},
                {"native", "approximation", "target_default", "source_default"},
            )
            if "native" in node:
                native_binding(node["native"])
            if "approximation" in node:
                text(node["approximation"], "近似说明")
            semantic = text(node["semantic"], "节点语义")
            if type(node.get("target_default", False)) is not bool:
                raise ValidationError("target_default 必须为布尔值")
            if type(node.get("source_default", False)) is not bool:
                raise ValidationError("source_default 必须为布尔值")
            if node.get("target_default"):
                if semantic in defaults:
                    raise ValidationError(f"通用功能存在多个默认目标实现：{semantic}")
                defaults.add(semantic)
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
                                "adjustment_range",
                                "native",
                                "omit_if_default",
                                "nullable",
                                "inactive_when",
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
                    for range_name in ("range", "adjustment_range"):
                        if range_name not in rule:
                            continue
                        bounds = rule[range_name]
                        if (
                            rule["type"] not in {"float", "color", "vector"}
                            or not isinstance(bounds, list)
                            or len(bounds) != 2
                            or any(type(v) not in (int, float) for v in bounds)
                            or bounds[0] > bounds[1]
                        ):
                            raise ValidationError("无效的输入范围")
                        if range_name == "adjustment_range" and not rule.get("adjustable"):
                            raise ValidationError("校正范围只允许用于已开放的数值端口")
                    validate_value(rule["default"], rule)
            for port_name, rule in node["inputs"].items():
                if "inactive_when" not in rule:
                    continue
                condition = fields(rule["inactive_when"], {"input", "equals"})
                text(condition["input"], "条件端口")
                controlling = node["inputs"].get(condition["input"])
                if (
                    not rule.get("omit_if_default")
                    or controlling is None
                    or condition["input"] == port_name
                    or controlling["type"] != "float"
                ):
                    raise ValidationError("无效的未启用原生参数条件")
                validate_value(condition["equals"], controlling)
            if value.get("semantic_schema_version"):
                validate_contract(node)
        encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode(
            "utf-8"
        )
        return cls(deepcopy(value), sha256(encoded).hexdigest())

    @property
    def nodes(self) -> dict:
        return self.data["nodes"]

    def target_node(self, semantic):
        candidates = [
            (key, rule) for key, rule in self.nodes.items() if rule["semantic"] == semantic
        ]
        defaults = [entry for entry in candidates if entry[1].get("target_default")]
        if defaults:
            return defaults[0]
        if len(candidates) > 1:
            raise ValidationError(
                f"通用功能缺少明确的默认目标实现：{semantic}", "AMBIGUOUS_TARGET_NODE"
            )
        return candidates[0] if candidates else None

    def identify(
        self, native_id, properties, implementation_hint=None, prefer_source_default=False
    ):
        """依据当前原生证据识别；可选提示只在匹配实现内消解宿主投影歧义。"""
        if type(native_id) not in (str, int) or native_id == "":
            raise ValidationError("原生节点 ID 必须为非空字符串或整数")
        if not isinstance(properties, dict):
            raise ValidationError("原生配置必须为对象")
        if type(prefer_source_default) is not bool:
            raise ValidationError("prefer_source_default 必须为布尔值")
        candidates = [
            (key, rule)
            for key, rule in self.nodes.items()
            if rule.get("native", {}).get("id") == native_id
        ]
        if not candidates:
            raise ValidationError(f"未支持的原生节点：{native_id}", "UNSUPPORTED_NODE")
        matches = [
            (key, rule)
            for key, rule in candidates
            if all(
                k in properties and type(properties[k]) is type(v) and properties[k] == v
                for k, v in rule["native"].get("properties", {}).items()
            )
        ]
        if implementation_hint is not None:
            if not isinstance(implementation_hint, str) or implementation_hint not in {
                key for key, _ in matches
            }:
                raise ValidationError("实现提示与当前原生类型或配置不符", "NATIVE_HINT_MISMATCH")
            matches = [entry for entry in matches if entry[0] == implementation_hint]
        elif prefer_source_default and len(matches) > 1:
            defaults = [entry for entry in matches if entry[1].get("source_default")]
            if len(defaults) == 1:
                matches = defaults
        if not matches:
            raise ValidationError(
                f"原生节点配置未支持或缺失：{native_id}", "UNSUPPORTED_NODE_CONFIGURATION"
            )
        if len(matches) > 1:
            raise ValidationError(f"原生配置匹配多个实现：{native_id}", "AMBIGUOUS_NATIVE_NODE")
        key, rule = matches[0]
        return {
            "implementation": key,
            "semantic": rule["semantic"],
            "native": deepcopy(rule["native"]),
            "dictionary_fingerprint": self.fingerprint,
        }

    def summary(self) -> dict:
        return {
            "dictionary_id": self.data["id"],
            "dictionary_version": self.data["version"],
            "semantic_schema_version": self.data.get("semantic_schema_version"),
            "renderer": deepcopy(self.data["renderer"]),
            "host": deepcopy(self.data.get("host")),
            "evidence": deepcopy(self.data.get("evidence", {})),
            "fingerprint": self.fingerprint,
            "conversion_scope": {
                "evidence": "dictionary_declaration",
                "native_node_bindings": len(self.nodes),
                "semantics": sorted({rule["semantic"] for rule in self.nodes.values()}),
                "texture_image_channels": deepcopy(self.data.get("texture_image_channels", [])),
                "texture_coordinates": deepcopy(self.data.get("texture_coordinates", {})),
                "texture_alpha_behavior": deepcopy(self.data.get("texture_alpha_behavior", {})),
                "runtime_preflight_required": True,
                "appearance_verified": False,
            },
        }


class Registry:
    def __init__(self, profiles):
        """按接入身份注册共享规则；显式版本限制仅作附加过滤，重叠规则启动时拒绝。"""
        self._profiles = {}
        self._dictionaries = []
        for profile in profiles:
            dictionary = Dictionary.parse(profile)
            renderer = dictionary.data["renderer"]
            host = dictionary.data.get("host", {})
            key = (renderer["id"], host.get("id"), host.get("node_system"))
            limits = (version_limits(renderer), version_limits(host))
            entries = self._profiles.setdefault(key, [])
            for prior, _ in entries:
                if all(a is None or b is None or a & b for a, b in zip(prior, limits, strict=True)):
                    raise ValidationError(f"接入身份存在重叠字典：{key}")
            entries.append((limits, dictionary))
            self._dictionaries.append(dictionary)

    def get(self, renderer: dict) -> Dictionary:
        """选择唯一的身份匹配字典，保留请求中的实际版本供报告追踪。"""
        key = (renderer["id"], renderer.get("host"), renderer.get("node_system"))
        versions = (renderer["version"], renderer.get("host_version"))
        for limits, dictionary in self._profiles.get(key, []):
            if all(
                limit is None or version in limit
                for limit, version in zip(limits, versions, strict=True)
            ):
                return dictionary
        raise ValidationError(
            f"没有对应字典：{key}；实际版本 {versions}（检查接入身份及显式版本限制）",
            "UNSUPPORTED_RENDERER",
        )

    def capabilities(self) -> list[dict]:
        return [dictionary.summary() for dictionary in self._dictionaries]
