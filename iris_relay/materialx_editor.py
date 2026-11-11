"""保留可编辑图的标签、功能来源和参数身份；显示元数据不参与着色语义。"""

import base64
import json
import math
from collections import defaultdict, deque
from copy import deepcopy

from .model import ValidationError


def computation_equivalence_options():
    """核验内联实现时仅排除编辑和溯源属性，计算值、端口及连接仍须完全一致。"""
    import MaterialX as mx

    options = mx.ElementEquivalenceOptions()
    options.attributeExclusionList = {
        "iris_source_name",
        "iris_source_name_encoding",
        "iris_editor",
        "uiname",
    }
    return options


def equivalent_computation(actual, expected):
    """在副本中统一内部节点名，再核验实现；兼容旧文档中的数字开头名称。"""
    import MaterialX as mx

    options = computation_equivalence_options()
    if actual.isEquivalent(expected, options)[0]:
        return True
    copies = []
    for element in (actual, expected):
        document = mx.createDocument()
        copied = document.addChildOfCategory(element.getCategory(), element.getName())
        copied.copyContentFrom(element)
        finalize_node_names(document)
        copies.append((document, copied))
    return copies[0][1].isEquivalent(copies[1][1], options)[0]


def set_text_attribute(element, key, value):
    """特殊字符用明确的 UTF-8 编码保留，避免 SDK 直接写出非法属性。"""
    if any(c in '<>&"\r\n\t' or ord(c) < 32 for c in value):
        element.setAttribute(key + "_encoding", "base64-utf8")
        value = base64.b64encode(value.encode("utf-8")).decode("ascii")
    element.setAttribute(key, value)


def get_text_attribute(element, key):
    """只解码本协议明确声明的文本编码，错误不能被当成可读名称。"""
    value = element.getAttribute(key)
    if element.getAttribute(key + "_encoding") == "base64-utf8":
        try:
            return base64.b64decode(value, validate=True).decode("utf-8")
        except (ValueError, UnicodeError) as exc:
            raise ValidationError("编辑文本编码无效", "MATERIALX_METADATA_INVALID") from exc
    return value


def checked_editor(value):
    """显示字段保持有界、有限且类型明确；不读取或执行任何命令。"""
    if not isinstance(value, dict) or set(value) - {
        "label",
        "source_label",
        "source_id",
        "source_position",
        "position",
        "function_path",
        "group_path",
        "function_label",
        "control",
        "generated",
    }:
        raise ValidationError("未知的编辑元数据字段", "MATERIALX_METADATA_INVALID")
    for key in ("label", "source_label", "source_id", "function_label", "control"):
        if key in value and not isinstance(value[key], str):
            raise ValidationError("编辑名称必须为字符串", "MATERIALX_METADATA_INVALID")
    for key in ("source_position", "position"):
        if key in value and (
            not isinstance(value[key], list)
            or len(value[key]) != 2
            or any(type(x) not in (int, float) or not math.isfinite(x) for x in value[key])
        ):
            raise ValidationError("编辑位置必须为有限二维数值", "MATERIALX_METADATA_INVALID")
    if "generated" in value and type(value["generated"]) is not bool:
        raise ValidationError("编辑生成标记必须为布尔值", "MATERIALX_METADATA_INVALID")
    for key in ("function_path", "group_path"):
        path = value.get(key, [])
        if (
            not isinstance(path, list)
            or len(path) > 64
            or any(not isinstance(x, str) or not x for x in path)
        ):
            raise ValidationError("功能来源路径无效", "MATERIALX_METADATA_INVALID")
    return deepcopy(value)


def set_editor(node, value):
    """将显示元数据完整编码；常规标签另提供标准 uiname 提示。"""
    value = checked_editor(value)
    node.setAttribute(
        "iris_editor",
        base64.b64encode(
            json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).decode("ascii"),
    )
    label = value.get("label", "")
    if label and not any(c in '<>&"\r\n\t' for c in label):
        node.setAttribute("uiname", label)


def get_editor(node):
    """读取本项目显示元数据，外部标准 uiname 仍可作为普通标签。"""
    encoded = node.getAttribute("iris_editor")
    if encoded:
        try:
            value = json.loads(base64.b64decode(encoded, validate=True).decode("utf-8"))
        except (ValueError, UnicodeError) as exc:
            raise ValidationError("编辑元数据编码无效", "MATERIALX_METADATA_INVALID") from exc
        return checked_editor(value)
    return {
        "label": node.getAttribute("uiname") or node.getName(),
    }


def export_editor(node, record):
    """从宿主已读标签创建非执行来源信息，稳定 ID 与显示名分开。"""
    editor = checked_editor(record.get("metadata", {}).get("editor", {}))
    editor.setdefault("label", record["id"])
    path = record.get("metadata", {}).get("source_group_path", [])
    if path and "function_path" not in editor:
        editor["function_path"] = path[:-1]
        editor["function_label"] = " / ".join(path[:-1])
    set_editor(node, editor)


def finalize_node_names(doc):
    """覆盖主图、内联子图和生成节点；每个节点仅一个原名字段，改名同步连接。"""
    import MaterialX as mx

    from .naming import normalize_names

    nodes = [n for n in doc.traverseTree() if isinstance(n, mx.Node)]
    connections = [
        (port, port.getConnectedNode(), port.getNodeName())
        for port in doc.traverseTree()
        if isinstance(port, (mx.Input, mx.Output)) and port.getNodeName()
    ]
    parents = {}
    for node in nodes:
        parents.setdefault(node.getParent().getNamePath(), []).append(node)
    for siblings in parents.values():
        records = []
        for node in siblings:
            records.append(
                {
                    "id": node.getName(),
                    "type": node.getCategory(),
                    "metadata": {
                        "source_name": get_text_attribute(node, "iris_source_name")
                        if node.hasAttribute("iris_source_name")
                        else node.getName(),
                        "editor": get_editor(node),
                    },
                }
            )
        normalized = normalize_names({"nodes": records})["nodes"]
        parent = siblings[0].getParent()
        for node, record in zip(siblings, normalized, strict=True):
            if node.getName() != record["id"]:
                node.setName(parent.createValidChildName("iris_rename"))
        for node, record in zip(siblings, normalized, strict=True):
            if node.getName() != record["id"]:
                node.setName(parent.createValidChildName(record["id"]))
            set_text_attribute(node, "iris_source_name", record["metadata"]["source_name"])
            export_editor(node, record)
    for port, upstream, reference in connections:
        if upstream is not None:
            prefix, separator, _ = reference.rpartition("/")
            port.setNodeName(prefix + separator + upstream.getName())


def flatten_editable(graph, core, preserve, *, controls=False):
    """逐实例使用 SDK 展开，保留功能来源；每个浮点／颜色接口由一份共享控制驱动。"""

    def expandable(node):
        definition = node.getNodeDef()
        return (
            definition is not None
            and definition.getName() not in core.standard_names
            and definition.getName() not in preserve
        )

    count = 0
    while True:
        selected = next((n for n in graph.getNodes() if expandable(n)), None)
        if selected is None:
            return
        count += 1
        if count > 20000:
            raise ValidationError("可编辑子图展开超限", "MATERIALX_LIMIT")
        editor = get_editor(selected)
        path = [*editor.get("function_path", []), selected.getName()]
        label = editor.get("function_label") or editor.get("label") or selected.getName()
        if controls and selected.getAttribute("iris_internal_lowering") != "true":
            for declared in selected.getNodeDef().getActiveInputs():
                port = selected.getInput(declared.getName())
                if declared.getType() not in controls or (
                    port is not None
                    and any(
                        port.getAttribute(a) for a in ("nodename", "nodegraph", "interfacename")
                    )
                ):
                    continue
                source = port if port is not None and port.hasValueString() else declared
                if not source.hasValueString():
                    continue
                definition = core.library.getNodeDef("ND_constant_" + declared.getType())
                control = graph.addNodeInstance(
                    definition,
                    graph.createValidChildName(selected.getName() + "__" + declared.getName()),
                )
                control.addInput("value", declared.getType()).setValueString(
                    source.getValueString()
                )
                set_editor(
                    control,
                    {
                        "label": label + " / " + declared.getName(),
                        "function_path": path,
                        "function_label": label,
                        "control": declared.getName(),
                        "generated": True,
                        "source_id": editor.get("source_id", selected.getName()),
                    },
                )
                port = port or selected.addInput(declared.getName(), declared.getType())
                port.removeAttribute("value")
                port.setConnectedNode(control)
        before = {n.getName() for n in graph.getNodes()}
        selected_path = selected.getNamePath()
        graph.flattenSubgraphs(
            "",
            lambda n, selected_path=selected_path: (
                n.getNamePath() == selected_path and expandable(n)
            ),
        )
        if graph.getNode(selected.getName()) is not None:
            raise ValidationError("SDK 未完成子图实例展开", "MATERIALX_IMPLEMENTATION_MISSING")
        for node in graph.getNodes():
            if node.getName() not in before:
                child = get_editor(node)
                child.update(function_path=path, function_label=label, generated=True)
                child["label"] = label + " / " + node.getCategory()
                child.setdefault("source_id", editor.get("source_id", selected.getName()))
                set_editor(node, child)


def decorate_material(material, dictionary):
    """按当前 DAG 排列并分组，保持共享节点单一身份；参数发布只引用实际常量端口。"""
    root = material["output"]["node"]
    records = {n["id"]: n for n in material["nodes"]}
    root_rule = dictionary.nodes[records[root]["type"]]
    outgoing = defaultdict(list)
    indegree = dict.fromkeys(records, 0)
    for edge in material["links"]:
        outgoing[edge["from"]["node"]].append(edge["to"])
        indegree[edge["to"]["node"]] += 1
    queue = deque(sorted(name for name, count in indegree.items() if count == 0))
    depth = dict.fromkeys(records, 0)
    order = []
    while queue:
        name = queue.popleft()
        order.append(name)
        for endpoint in outgoing[name]:
            target = endpoint["node"]
            depth[target] = max(depth[target], depth[name] + 1)
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    destinations = defaultdict(set)
    for name in reversed(order):
        for endpoint in outgoing[name]:
            if endpoint["node"] == root:
                destinations[name].add(root_rule["inputs"][endpoint["socket"]]["semantic"])
            else:
                destinations[name].update(destinations[endpoint["node"]])
    labels = {
        "base.color": "底色",
        "reflection.roughness": "粗糙度",
        "normal": "法线",
        "metalness": "金属度",
        "opacity": "透明度",
    }
    groups = {}
    rows = defaultdict(int)
    for name in order:
        record = records[name]
        editor = record.setdefault("metadata", {}).setdefault("editor", {})
        editor.setdefault("label", name)
        editor["position"] = (
            editor.get("source_position")
            if not editor.get("generated") and "source_position" in editor
            else [depth[name] * 300, -rows[depth[name]] * 170]
        )
        rows[depth[name]] += 1
        if name == root:
            continue
        path = editor.get("group_path", editor.get("function_path", []))
        if path:
            key = "function:" + path[0]
            label = editor.get("function_label") or path[0]
        else:
            ends = destinations[name]
            semantic = next(iter(ends)) if len(ends) == 1 else "shared"
            key = "branch:" + semantic
            label = labels.get(semantic, "共享上游" if semantic == "shared" else semantic)
        group = groups.setdefault(key, {"id": key, "label": label, "nodes": [], "controls": []})
        group["nodes"].append(name)
        if editor.get("control"):
            rule = dictionary.nodes[record["type"]]
            inputs = [(key, p) for key, p in rule["inputs"].items() if p["semantic"] == "value"]
            if len(inputs) == 1:
                key, port = inputs[0]
                group["controls"].append(
                    {"node": name, "port": key, "label": editor["control"], "type": port["type"]}
                )
    material.setdefault("metadata", {})["editor"] = {
        "version": "1",
        "groups": list(groups.values()),
        "layout": "source_or_topological",
    }
    return material
