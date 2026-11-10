"""比较文档的实际计算字段；显式节点对齐不能变更参数或忽略连接。"""

import math

import MaterialX as mx

from .materialx_assets import checked_document
from .model import ValidationError, fields


def value_data(port):
    """通过 SDK 解析数值，区分格式变化与参数变化。"""
    value = port.getValue()
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    try:
        return [float(x) for x in value]
    except (TypeError, ValueError):
        return port.getValueString()


def snapshot(doc, rename):
    """保留类型、有效默认输入、端口来源、单位、贴图及 Alpha；不读取像素。"""
    result = {}
    for node in doc.traverseTree():
        if isinstance(node, mx.Implementation):
            result["@implementation/" + node.getNamePath()] = {
                key: node.getAttribute(key) for key in node.getAttributeNames() if key != "name"
            }
            continue
        if isinstance(node, mx.NodeDef):
            result["@definition/" + node.getNamePath()] = {
                "category": node.getNodeString(),
                "version": node.getVersionString(),
                "ports": {
                    port.getName(): {
                        key: port.getAttribute(key)
                        for key in port.getAttributeNames()
                        if key != "name"
                    }
                    for port in [*node.getActiveInputs(), *node.getActiveOutputs()]
                },
            }
            continue
        if not isinstance(node, (mx.Node, mx.Output)):
            continue
        path = rename.get(node.getNamePath(), node.getNamePath())
        record = {"category": node.getCategory(), "type": node.getType()}
        if isinstance(node, mx.Node):
            definition = node.getNodeDef()
            record["definition"] = definition.getName() if definition else node.getNodeDefString()
            defaults = {p.getName(): p for p in definition.getActiveInputs()} if definition else {}
            ports = {**defaults, **{p.getName(): p for p in node.getInputs()}}
        else:
            ports = {"out": node}
        record["inputs"] = {}
        for name, port in ports.items():
            source = port.getConnectedNode()
            record["inputs"][name] = {
                "type": port.getType(),
                "value": value_data(port) if port.hasValueString() else None,
                "source": rename.get(source.getNamePath(), source.getNamePath())
                if source
                else None,
                "nodegraph": port.getNodeGraphString(),
                "output": port.getOutputString(),
                "interface": port.getInterfaceName(),
                "color_space": port.getActiveColorSpace(),
                "unit": port.getAttribute("unit"),
                "unit_type": port.getAttribute("unittype"),
            }
            if port.getType() == "filename":
                record["inputs"][name]["value"] = port.getResolvedValueString()
        record["alpha"] = {
            a: node.getAttribute(a)
            for a in node.getAttributeNames()
            if a.startswith(("iris_alpha", "iris_native_association", "iris_image_channels"))
        }
        record["color_space"] = node.getActiveColorSpace()
        result[path] = record
    return result


def compare(core, payload):
    """返回逐字段差异；不推断任意图同构或着色外观等价。"""
    fields(payload, {"before", "after"}, {"node_mapping", "tolerance"})
    before, issues = checked_document(core, payload["before"])
    after, more = checked_document(core, payload["after"])
    mapping = payload.get("node_mapping", {})
    tolerance = payload.get("tolerance", 2e-6)
    if (
        type(tolerance) not in (int, float)
        or not math.isfinite(tolerance)
        or not 0 <= tolerance <= 0.01
    ):
        raise ValidationError("差异容差必须为 0 到 0.01 的有限数值")
    if not isinstance(mapping, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) for k, v in mapping.items()
    ):
        raise ValidationError("节点对齐必须为字符串映射")
    left, right = snapshot(before, {}), snapshot(after, {})
    if set(mapping) - set(left) or set(mapping.values()) - set(right):
        raise ValidationError("对齐引用了不存在的计算节点", "MATERIALX_DIFF_MAPPING")
    aligned = [mapping.get(k, k) for k in left]
    if len(set(aligned)) != len(aligned):
        raise ValidationError("节点对齐身份冲突", "MATERIALX_DIFF_MAPPING")
    left = snapshot(before, mapping)
    changes = []

    def visit(a, b, path):
        """递归报告差异，浮点容差不应用到通道选择、文本或布尔。"""
        if isinstance(a, dict) and isinstance(b, dict):
            for key in sorted(a.keys() | b.keys()):
                if key not in a or key not in b:
                    changes.append(
                        {"path": path + "/" + key, "before": a.get(key), "after": b.get(key)}
                    )
                else:
                    visit(a[key], b[key], path + "/" + key)
        elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
            for index, (x, y) in enumerate(zip(a, b, strict=True)):
                visit(x, y, path + "/" + str(index))
        elif type(a) in (int, float) and type(b) in (int, float):
            if not math.isclose(a, b, abs_tol=tolerance, rel_tol=tolerance):
                changes.append({"path": path, "before": a, "after": b})
        elif a != b:
            changes.append({"path": path, "before": a, "after": b})

    visit(left, right, "graph")
    return {
        "identical": not changes,
        "changes": changes,
        "node_mapping": mapping,
        "comparison": "document_semantic_fields",
        "appearance_verified": False,
        "files_compared": False,
        "issues": issues + more,
    }
