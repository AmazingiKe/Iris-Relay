"""保存无法转换的原生图结构；自定义声明没有可执行含义，禁止冒充标准着色节点。"""

import base64
import json
from copy import deepcopy

import MaterialX as mx

from ..conversion import issue, status_for
from ..model import ValidationError, fields, json_value, text
from ..tools.naming import normalize_names
from .editor import finalize_node_names, set_text_attribute

TYPES = {
    "float",
    "integer",
    "boolean",
    "color3",
    "color4",
    "vector2",
    "vector3",
    "vector4",
    "matrix33",
    "matrix44",
    "string",
    "filename",
    "surfaceshader",
    "volumeshader",
    "displacementshader",
    "material",
}


def _native_attribute(element, key, value):
    """XML 元数据中的特殊字符按显式 Base64 保留，避免 SDK 原样输出非法属性文本。"""
    if any(c in '<>&"\r\n\t' or ord(c) < 32 for c in value):
        value = base64.b64encode(value.encode("utf-8")).decode("ascii")
        element.setAttribute(key + "_encoding", "base64-utf8")
    element.setAttribute(key, value)


def preserve_snapshot(core, snapshot):
    """验证原生端点，保存所有节点、属性和连接；失败也返回完整快照及诊断。"""
    issues = []
    result = {
        "snapshot": deepcopy(snapshot),
        "document": None,
        "conversion_supported": False,
        "appearance_verified": False,
        "report": {"status": "blocked", "issues": issues},
    }
    try:
        json_value(snapshot)
        fields(snapshot, {"name", "host", "nodes", "links"}, {"metadata"})
        text(snapshot["name"], "原生材质名称")
        if not isinstance(snapshot["nodes"], list) or not isinstance(snapshot["links"], list):
            raise ValidationError("原生节点与连接须为列表")
        if not snapshot["nodes"] or len(snapshot["nodes"]) > 5000:
            raise ValidationError("原生节点数量须为 1 至 5000", "MATERIALX_LIMIT")
        doc = mx.createDocument()
        doc.setDataLibrary(core.library)
        doc.setAttribute("iris_preservation_only", "true")
        _native_attribute(doc, "iris_material_name", snapshot["name"])
        doc.setAttribute(
            "iris_native_snapshot",
            base64.b64encode(
                json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            ).decode("ascii"),
        )
        doc.setAttribute("iris_native_snapshot_encoding", "base64-utf8-json")
        nodes, inputs, outputs = {}, {}, {}
        linked = {(e["to"]["node"], e["to"]["socket"]) for e in snapshot["links"]}
        normalized = normalize_names({"nodes": snapshot["nodes"]})["nodes"]
        # 1. 每个原生实例有显式接口与原生标识；未知实现不会被按名称套到标准功能。
        for index, record in enumerate(snapshot["nodes"]):
            fields(record, {"id", "type", "inputs", "outputs"}, {"properties", "metadata"})
            name = text(record["id"], "原生节点 ID")
            text(record["type"], "原生节点类型")
            if name in nodes:
                raise ValidationError("原生节点 ID 重复：" + name)
            declared_outputs = record["outputs"] or [{"id": "__native_state", "type": "string"}]
            dtype = "multioutput"
            nd = doc.addNodeDef("ND_iris_native_" + str(index), dtype, "iris_native_" + str(index))
            # addNodeDef 会创建默认 out；移除后按原生完整端口重建。
            for port in list(nd.getOutputs()):
                nd.removeOutput(port.getName())
            port_names = {}
            for direction, records in (("inputs", record["inputs"]), ("outputs", declared_outputs)):
                if not isinstance(records, list):
                    raise ValidationError("原生接口必须为列表")
                seen = set()
                for specification in records:
                    fields(specification, {"id", "type"}, {"value", "metadata"})
                    key = text(specification["id"], "原生端口")
                    if key in seen or specification["type"] not in TYPES:
                        raise ValidationError("原生接口重复或类型未知：" + name + "." + key)
                    seen.add(key)
                    safe = nd.createValidChildName(mx.createValidName(direction + "_" + key))
                    declared = (nd.addInput if direction == "inputs" else nd.addOutput)(
                        safe, specification["type"]
                    )
                    _native_attribute(declared, "iris_native_socket", key)
                    port_names[(direction, key)] = safe
            node = doc.addNodeInstance(nd, doc.createValidChildName(normalized[index]["id"]))
            node.setAttribute("iris_snapshot_index", str(index))
            set_text_attribute(
                node, "iris_source_name", normalized[index]["metadata"]["source_name"]
            )
            _native_attribute(node, "iris_native_type", record["type"])
            nodes[name] = node
            for specification in record["inputs"]:
                key = specification["id"]
                value = specification.get("value")
                if value is None and (name, key) not in linked:
                    # 接口仍在 NodeDef 和原生快照中，不生成无值、无连接的非法绑定。
                    continue
                port = node.addInput(port_names[("inputs", key)], specification["type"])
                if value is not None:
                    if isinstance(value, list):
                        encoded = ", ".join(str(v) for v in value)
                    elif isinstance(value, bool):
                        encoded = "true" if value else "false"
                    else:
                        encoded = str(value)
                    if specification["type"] in {"string", "filename"} and any(
                        c in '<>&"\r\n\t' or ord(c) < 32 for c in encoded
                    ):
                        encoded = base64.b64encode(encoded.encode("utf-8")).decode("ascii")
                        port.setAttribute("iris_native_value_encoding", "base64-utf8")
                    port.setValueString(encoded)
                inputs[(name, key)] = port
            for specification in declared_outputs:
                outputs[(name, specification["id"])] = (
                    port_names[("outputs", specification["id"])],
                    specification["type"],
                )
        # 2. 宿主隐式转换保存为无实现的显式节点，不猜测其数学含义。
        for edge in snapshot["links"]:
            fields(edge, {"from", "to"})
            start, end = edge["from"], edge["to"]
            fields(start, {"node", "socket"})
            fields(end, {"node", "socket"})
            output_key, input_key = (start["node"], start["socket"]), (end["node"], end["socket"])
            if output_key not in outputs or input_key not in inputs:
                raise ValidationError("原生连接端点不存在", "INVALID_LINK")
            target = inputs[input_key]
            if target.getNodeName():
                raise ValidationError("原生输入含多个来源，尚无已定义顺序", "INVALID_LINK")
            output, source_type = outputs[output_key]
            source = nodes[start["node"]]
            if source_type != target.getType():
                nd = doc.addNodeDef(
                    doc.createValidChildName("ND_iris_native_cast"),
                    "multioutput",
                    "iris_native_cast_" + str(len(doc.getNodes())),
                )
                nd.addInput("in", source_type)
                nd.addOutput("out", target.getType())
                cast = doc.addNodeInstance(nd, doc.createValidChildName("iris_native_cast"))
                incoming = cast.addInput("in", source_type)
                incoming.setNodeName(source.getName())
                if source.getType() == "multioutput":
                    incoming.setOutputString(output)
                source, output = cast, "out"
            target.removeAttribute("value")
            target.setNodeName(source.getName())
            if source.getType() == "multioutput":
                target.setOutputString(output)
        # 3. 使用与正常文档相同的 SDK、类型、循环和规模检查，只有通过才返回文本。
        finalize_node_names(doc)
        xml = core.serialize(doc)
        checked = core.read(xml)
        issues.extend(core.inspect(checked))
        if status_for(issues) != "error":
            result["document"] = xml
            result["report"]["status"] = "preserved"
            issues.append(
                issue(
                    "NATIVE_GRAPH_PRESERVED",
                    "仅保存原生结构与参数；自定义声明没有可转换实现",
                    severity="warning",
                )
            )
    except (ValidationError, mx.Exception, KeyError, TypeError) as exc:
        issues.append(issue(getattr(exc, "code", "INVALID_SNAPSHOT"), str(exc)))
    return result
