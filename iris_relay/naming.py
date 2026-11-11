"""统一节点名称处理；溯源备份只传递，不用于生成当前名称。"""

import hashlib
import re
import unicodedata
from copy import deepcopy

from pypinyin import lazy_pinyin

from .model import ValidationError

MAX_NAME_LENGTH = 63
_VALID = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def _readable(value):
    """删除标点、符号和空白；仅转写汉字，其他语言的字母保留供拒绝判断。"""
    cleaned = "".join(c for c in value if c == "_" or unicodedata.category(c)[0] in {"L", "N"})
    start = 0
    while start < len(cleaned) and cleaned[start].isnumeric():
        start += 1
    cleaned = cleaned[start:]
    converted = "_".join(lazy_pinyin(cleaned))
    return re.sub(r"^[0-9]+", "", converted)


def _digest(identity):
    """使用跨进程稳定摘要，避免 Python 随机哈希使重复导出不断改名。"""
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]


def node_name(name, identity, node_type):
    """生成 ASCII 候选名；无法转写用身份，清理为空用类型，最终不会数字开头。"""
    candidate = _readable(name)
    if not candidate.strip("_"):
        candidate = _readable(node_type)
    elif not _VALID.fullmatch(candidate):
        candidate = re.sub(r"^[0-9]+", "", identity)
    if not _VALID.fullmatch(candidate) or not candidate.strip("_"):
        candidate = "node_" + _digest(identity)
    if len(candidate) > MAX_NAME_LENGTH:
        candidate = candidate[: MAX_NAME_LENGTH - 13] + "_" + _digest(identity)
    return candidate


def normalize_names(material):
    """复制并统一图中的节点及端点名称；同名按稳定身份分配，源图和备份不改变。"""
    result = deepcopy(material)
    proposals, identities = {}, {}
    # 1. 当前名称与不可变的原名分离；宿主可提供与协议 ID 不同的当前实例名。
    for record in result["nodes"]:
        identity = record["id"]
        metadata = record.setdefault("metadata", {})
        current = metadata.pop("native_name", identity)
        if not isinstance(current, str):
            raise ValidationError("当前节点名称必须为字符串", "INVALID_NODE_NAME")
        original = metadata.setdefault("source_name", current)
        if not isinstance(original, str):
            raise ValidationError("原始节点名称必须为字符串", "INVALID_NODE_NAME")
        proposals[identity] = node_name(current, identity, record["type"])
    # 2. 合法现有名称优先保留；其他冲突用摘要并做实际去重，不假设摘要绝不碰撞。
    occupied = {key for key, value in proposals.items() if key == value}
    for identity in sorted(proposals):
        candidate = proposals[identity]
        if candidate != identity and candidate in occupied:
            counter = 0
            while True:
                suffix = "_" + _digest(identity + (":" + str(counter) if counter else ""))
                unique = candidate[: MAX_NAME_LENGTH - len(suffix)] + suffix
                if unique not in occupied:
                    candidate = unique
                    break
                counter += 1
        occupied.add(candidate)
        identities[identity] = candidate
    # 3. 编辑标签不再自动取备份名；清除旧文件自动复制的同值备份字段。
    for record in result["nodes"]:
        old = record["id"]
        record["id"] = identities[old]
        metadata = record["metadata"]
        editor = metadata.get("editor", {})
        if not isinstance(editor, dict) or any(
            key in editor and not isinstance(editor[key], str)
            for key in ("source_id", "source_label", "label")
        ):
            raise ValidationError("编辑名称必须为字符串字段", "MATERIALX_METADATA_INVALID")
        for key in ("source_id", "source_label"):
            if editor.get(key) in {old, metadata["source_name"]}:
                editor.pop(key, None)
        if editor.get("label") in {old, metadata["source_name"]}:
            editor["label"] = record["id"]
    for edge in result.get("links", []):
        for endpoint in edge.values():
            endpoint["node"] = identities[endpoint["node"]]
    if result.get("output"):
        result["output"]["node"] = identities[result["output"]["node"]]
    return result
