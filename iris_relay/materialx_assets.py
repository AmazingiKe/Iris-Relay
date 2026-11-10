"""通过官方 SDK 枚举和改写文件资源；不访问文件系统或自动下载。"""

from hashlib import sha256

import MaterialX as mx

from .conversion import status_for
from .model import ValidationError, fields, text


def checked_document(core, document):
    """要求完整文档可校验，外部声明的警告保留给调用者。"""
    doc = core.read(document)
    issues = core.inspect(doc)
    if status_for(issues) == "error":
        raise ValidationError("资源操作要求有效 MaterialX 文档", "MATERIALX_INVALID")
    return doc, issues


def filename_ports(doc):
    """枚举文档自身的非空 filename 值，包含内联定义中的默认文件引用。"""
    return {
        item.getNamePath(): item
        for item in doc.traverseTree()
        if isinstance(item, mx.ValueElement)
        and item.getType() == "filename"
        and item.hasValueString()
        and item.getValueString()
    }


def resources(core, payload):
    """返回原始 URI 与按 MaterialX 前缀解析的 URI；不证明资源实际存在。"""
    fields(payload, {"document"})
    doc, issues = checked_document(core, payload["document"])
    return {
        "document_sha256": sha256(payload["document"].encode("utf-8")).hexdigest(),
        "resources": [
            {
                "path": name,
                "uri": port.getValueString(),
                "resolved_uri": port.getResolvedValueString(),
                "color_space": port.getActiveColorSpace(),
            }
            for name, port in sorted(filename_ports(doc).items())
        ],
        "issues": issues,
        "files_verified": False,
    }


def rewrite_resources(core, payload):
    """核对完整映射和原值，再生成独立文档；旧 URI 和前缀不能与新路径混用。"""
    fields(payload, {"document", "resources"})
    doc, issues = checked_document(core, payload["document"])
    mapping = payload["resources"]
    ports = filename_ports(doc)
    if not isinstance(mapping, dict) or set(mapping) != set(ports):
        raise ValidationError(
            "资源改写须覆盖所有文件引用且不能含额外项", "MATERIALX_RESOURCE_MAPPING"
        )
    for name, item in mapping.items():
        fields(item, {"expected_uri", "uri"})
        text(item["uri"], "新资源 URI")
        if item["expected_uri"] != ports[name].getValueString():
            raise ValidationError("资源原值已改变：" + name, "MATERIALX_RESOURCE_MAPPING")
    for name, item in mapping.items():
        ports[name].setValueString(item["uri"])
    for item in doc.traverseTree():
        item.removeAttribute("fileprefix")
    issues.extend(core.inspect(doc))
    if status_for(issues) == "error":
        raise ValidationError("改写后的资源文档无效", "MATERIALX_INVALID")
    document = core.serialize(doc)
    return {
        "document": document,
        "document_sha256": sha256(document.encode("utf-8")).hexdigest(),
        "rewritten": len(ports),
        "issues": issues,
        "files_verified": False,
    }
