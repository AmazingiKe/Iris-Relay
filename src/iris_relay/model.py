"""统一材质图协议及校验，不读取或修改 DCC 场景。"""

import json
import math
from collections import deque
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ValidationError(ValueError):
    """包含可用于 API 返回的稳定错误码。"""

    def __init__(self, message: str, code: str = "INVALID_REQUEST"):
        super().__init__(message)
        self.code = code


def fields(value: Any, required: set[str], optional: set[str] | None = None) -> dict:
    if not isinstance(value, dict):
        raise ValidationError("应为 JSON 对象")
    missing = required - value.keys()
    unknown = value.keys() - required - (optional or set())
    if missing or unknown:
        raise ValidationError(f"缺少字段：{sorted(missing)}；未知字段：{sorted(unknown)}")
    return value


def text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{label} 必须为非空字符串")
    return value


def json_value(value: Any, depth: int = 0) -> None:
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


def renderer_ref(value: Any) -> dict:
    fields(value, {"id", "version"})
    text(value["id"], "渲染器 ID")
    text(value["version"], "渲染器版本")
    return value


def endpoint(value: Any) -> tuple[str, str]:
    fields(value, {"node", "socket"})
    return text(value["node"], "节点 ID"), text(value["socket"], "端口")


@dataclass(frozen=True)
class Material:
    data: dict

    @classmethod
    def parse(cls, value: Any) -> "Material":
        json_value(value)
        fields(
            value,
            {"schema_version", "name", "renderer", "nodes", "links", "output"},
            {"textures", "metadata"},
        )
        if value["schema_version"] != "1":
            raise ValidationError("不支持的材质协议版本", "UNSUPPORTED_SCHEMA")
        text(value["name"], "材质名称")
        renderer_ref(value["renderer"])
        data = deepcopy(value)
        data.setdefault("textures", {})
        data.setdefault("metadata", {})
        if not isinstance(data["metadata"], dict) or not isinstance(data["textures"], dict):
            raise ValidationError("metadata 和 textures 必须为对象")
        for texture_id, resource in data["textures"].items():
            text(texture_id, "贴图 ID")
            fields(resource, {"uri", "color_space", "coordinates"}, {"metadata"})
            text(resource["uri"], "贴图 URI")
            text(resource["color_space"], "贴图色彩空间")
            if not isinstance(resource["coordinates"], dict):
                raise ValidationError("贴图坐标描述必须为对象")
            if not isinstance(resource.get("metadata", {}), dict):
                raise ValidationError("贴图 metadata 必须为对象")
        if not isinstance(data["nodes"], list) or not 0 < len(data["nodes"]) <= 10000:
            raise ValidationError("材质必须包含 1 至 10000 个节点")
        nodes = {}
        for node in data["nodes"]:
            fields(node, {"id", "type", "inputs", "outputs"}, {"metadata"})
            node_id = text(node["id"], "节点 ID")
            text(node["type"], "节点类型")
            if node_id in nodes:
                raise ValidationError(f"节点 ID 重复：{node_id}")
            if not isinstance(node["inputs"], dict) or not isinstance(node["outputs"], list):
                raise ValidationError("节点 inputs 必须为对象，outputs 必须为列表")
            for name, item in node["inputs"].items():
                text(name, "输入端口")
                if isinstance(item, dict) and "texture" in item:
                    fields(item, {"texture"})
                    if text(item["texture"], "贴图引用") not in data["textures"]:
                        raise ValidationError(f"贴图资源不存在：{item['texture']}")
            for name in node["outputs"]:
                text(name, "输出端口")
            if len(set(node["outputs"])) != len(node["outputs"]):
                raise ValidationError("输出端口重复")
            node.setdefault("metadata", {})
            if not isinstance(node["metadata"], dict):
                raise ValidationError("节点 metadata 必须为对象")
            nodes[node_id] = node
        if not isinstance(data["links"], list) or len(data["links"]) > 100000:
            raise ValidationError("links 必须为至多 100000 项的列表")
        incoming = set()
        edges = {node_id: [] for node_id in nodes}
        degrees = {node_id: 0 for node_id in nodes}
        for link in data["links"]:
            fields(link, {"from", "to"})
            source, source_socket = endpoint(link["from"])
            target, target_socket = endpoint(link["to"])
            if source not in nodes or target not in nodes:
                raise ValidationError("连接引用了不存在的节点")
            if source_socket not in nodes[source]["outputs"]:
                raise ValidationError(f"连接源端口不存在：{source}.{source_socket}")
            if target_socket not in nodes[target]["inputs"]:
                raise ValidationError(f"连接目标端口不存在：{target}.{target_socket}")
            if (target, target_socket) in incoming:
                raise ValidationError("同一输入端口不能有多条连接")
            incoming.add((target, target_socket))
            edges[source].append(target)
            degrees[target] += 1
        queue = deque(node_id for node_id, degree in degrees.items() if degree == 0)
        visited = 0
        while queue:
            source = queue.popleft()
            visited += 1
            for target in edges[source]:
                degrees[target] -= 1
                if degrees[target] == 0:
                    queue.append(target)
        if visited != len(nodes):
            raise ValidationError("材质图包含循环连接")
        output_node, output_socket = endpoint(data["output"])
        if output_node not in nodes or output_socket not in nodes[output_node]["outputs"]:
            raise ValidationError("材质输出引用了不存在的节点或端口")
        return cls(data)

    def to_dict(self) -> dict:
        return deepcopy(self.data)


MAX_JSON_BYTES = 16 * 1024 * 1024


def parse_json(content: bytes) -> Any:
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


def load_json(path: str | Path) -> Any:
    with Path(path).open("rb") as stream:
        return parse_json(stream.read(MAX_JSON_BYTES + 1))
