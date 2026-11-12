"""功能接口交换与目标实现选择；配方只进入工作图，不写回交换文件。"""

from ..conversion import issue
from ..model import ValidationError, fields, text
from .editor import equivalent_computation


def validate_function_rules(value):
    """校验声明式功能规则的结构；具体接口类型在加载官方库后检查。"""
    for field in ("materialx_function_exports", "materialx_function_implementations"):
        rules = value.get(field, {})
        if not isinstance(rules, dict):
            raise ValidationError("功能规则必须为对象")
        for name, entries in rules.items():
            text(name, "功能定义")
            exporting = field.endswith("exports")
            choices = [entries] if exporting else entries
            if not isinstance(choices, list) or not 1 <= len(choices) <= 256:
                raise ValidationError("功能实现需要 1 至 256 个候选")
            for entry in choices:
                fields(
                    entry,
                    {"definition", "inputs", "constants"}
                    if exporting
                    else {"definition", "inputs", "when", "kind", "evidence"},
                )
                text(entry["definition"], "功能实现定义")
                if not isinstance(entry["inputs"], dict):
                    raise ValidationError("功能端口对应必须为对象")
                for source, target in entry["inputs"].items():
                    text(source, "源功能端口")
                    text(target, "目标功能端口")
                if len(set(entry["inputs"].values())) != len(entry["inputs"]):
                    raise ValidationError("功能端口不能重复映射到同一输入")
                conditions = entry["constants" if exporting else "when"]
                if not isinstance(conditions, dict):
                    raise ValidationError("功能常量或条件必须为对象")
                if exporting and set(conditions) & set(entry["inputs"].values()):
                    raise ValidationError("功能常量不能覆盖已映射的输入及连接")
                for key, expected in conditions.items():
                    text(key, "功能条件端口")
                    if type(expected) not in {float, int, str, bool} and not (
                        isinstance(expected, list)
                        and len(expected) in {3, 4}
                        and all(type(v) in {float, int} for v in expected)
                    ):
                        raise ValidationError("功能条件只接受有类型的常量，不接受表达式或空值")
                if not exporting:
                    if entry["kind"] not in {"native", "hybrid", "recipe"}:
                        raise ValidationError("未知的功能实现种类")
                    text(entry["evidence"], "功能实现依据")


def _value(port):
    """返回可比较的标量或向量，不把连接误当作默认常量。"""
    if port is None or any(
        port.getAttribute(k) for k in ("nodename", "nodegraph", "interfacename")
    ):
        return None
    result = port.getValue()
    return list(result) if port.getType() in {"color3", "color4", "vector3"} else result


def _port(node, name):
    """读取当前值或定义默认值；当前连接优先于定义。"""
    return node.getInput(name) or node.getNodeDef().getActiveInput(name)


def _copy_definition(doc, definition):
    """接口发生重名冲突时拒绝替换，防止来源伪装。"""
    if definition.getDocument() == doc.getDataLibrary():
        return
    current = doc.getNodeDef(definition.getName())
    if current is not None:
        if not equivalent_computation(current, definition):
            raise ValidationError("功能接口与目标字典不同", "MATERIALX_BINDING_INVALID")
        return
    # addNodeDef 的默认 color3 出口不会被 copyContentFrom 覆盖；从空元素复制全部接口。
    doc.addChildOfCategory("nodedef", definition.getName()).copyContentFrom(definition)


def _check_interface(source, expected):
    """核对已知接口，允许新增有类型的细节；既有类型、默认值和输出不可更换。"""
    doc = source.getDocument()
    if any(
        g.getDocument() == doc and g.getNodeDefString() == source.getName()
        for g in [*doc.getNodeGraphs(), *doc.getImplementations()]
    ):
        raise ValidationError("功能声明不能隐藏另一份计算实现", "MATERIALX_BINDING_INVALID")
    if expected is None:
        raise ValidationError("目标缺少功能接口定义", "MATERIALX_BINDING_INVALID")
    unknown = {p.getName() for p in source.getInputs()} - {
        p.getName() for p in expected.getActiveInputs()
    }
    if not unknown and equivalent_computation(source, expected):
        return unknown
    if unknown:
        import MaterialX as mx

        document = mx.createDocument()
        document.copyContentFrom(source.getDocument())
        document.setDataLibrary(source.getDocument().getDataLibrary())
        candidate = document.getNodeDef(source.getName())
        for name in unknown:
            if candidate.getInput(name).getType() not in {
                "float",
                "integer",
                "color3",
                "vector3",
                "boolean",
                "string",
            }:
                raise ValidationError("未知细节输入类型不受支持", "MATERIALX_BINDING_INVALID")
            candidate.removeInput(name)
        if equivalent_computation(candidate, expected):
            return unknown
    raise ValidationError("功能接口与目标已知契约不同", "MATERIALX_BINDING_INVALID")


def _remap(node, definition, mapping, constants=None):
    """先核对端口类型，再整体更换接口；保留节点身份、上游连接与编辑元数据。"""
    ports = []
    for source, target in mapping.items():
        port = _port(node, source)
        expected = definition.getActiveInput(target)
        if port is None or expected is None or port.getType() != expected.getType():
            raise ValidationError("功能映射的端口类型不匹配", "MATERIALX_BINDING_INVALID")
        ports.append(
            (target, port.getType(), {a: port.getAttribute(a) for a in port.getAttributeNames()})
        )
    for port in list(node.getInputs()):
        node.removeInput(port.getName())
    node.setCategory(definition.getNodeString())
    node.setNodeDefString(definition.getName())
    for name, datatype, attributes in ports:
        port = node.addInput(name, datatype)
        for key, value in attributes.items():
            port.setAttribute(key, value)
    for name, value in (constants or {}).items():
        declared = definition.getActiveInput(name)
        if declared is None:
            raise ValidationError("功能常量端口未声明", "MATERIALX_BINDING_INVALID")
        port = node.addInput(name, declared.getType())
        literal = ", ".join(map(str, value)) if isinstance(value, list) else str(value)
        port.setValueString(literal.lower() if isinstance(value, bool) else literal)


def export_functions(core, doc, dictionary, extensions):
    """将已登记的实现节点投影到通用功能接口，并移除不再需要的实现定义。"""
    rules = dictionary.data.get("materialx_function_exports", {})
    if rules and extensions is None:
        raise ValidationError("功能导出规则缺少接口库", "MATERIALX_BINDING_INVALID")
    replaced = set()
    for node in list(doc.getNodes()):
        name = node.getNodeDefString()
        if name not in rules:
            continue
        rule = rules[name]
        definition = extensions.getNodeDef(rule["definition"])
        if definition is None or (
            definition.getName() not in core.standard_names
            and definition.getAttribute("iris_function_version") != "1"
        ):
            raise ValidationError("功能导出缺少版本化接口", "MATERIALX_BINDING_INVALID")
        _copy_definition(doc, definition)
        _remap(node, definition, rule["inputs"], rule["constants"])
        replaced.add(name)
    for name in replaced:
        for graph in list(doc.getNodeGraphs()):
            if graph.getNodeDefString() == name:
                doc.removeNodeGraph(graph.getName())
        doc.removeNodeDef(name)


def _active_nodes(doc, material_name):
    """从材质出口向上游访问；在节点处理后读连接，跳过已归档细节的独占支路。"""
    import MaterialX as mx

    pending = [
        n
        for n in doc.getNodes()
        if n.getCategory() == "surfacematerial"
        and (material_name is None or n.getName() == material_name)
    ]
    if len(pending) != 1:
        raise ValidationError("需要选择唯一的 surfacematerial", "MATERIALX_MATERIAL_SELECTION")
    visited = set()
    while pending:
        element = pending.pop()
        if element.getNamePath() in visited:
            continue
        visited.add(element.getNamePath())
        if isinstance(element, mx.Node):
            yield element
            ports = element.getInputs()
        else:
            ports = [element]
        for port in ports:
            upstream = port.getConnectedOutput() or port.getConnectedNode()
            if upstream is not None:
                pending.append(upstream)


def import_functions(
    core, doc, dictionary, extensions, bindings, issues, material_name=None, deferred_errors=None
):
    """按原生、混合、配方优先级选择目标实现；不从源文档提取目标配方。"""
    import MaterialX as mx

    rules = dictionary.data.get("materialx_function_implementations", {})
    consumed = set()
    bound = {b[0] for b in bindings.values()}
    originally_used = {n.getNamePath() for n in _active_nodes(doc, material_name)}
    for node in _active_nodes(doc, material_name):
        if deferred_errors and node.getNamePath() in deferred_errors:
            raise deferred_errors[node.getNamePath()]
        source = node.getNodeDef()
        if source is None:
            continue
        name = source.getName()
        functional = source.getAttribute("iris_function_version") == "1"
        if not functional and name not in rules:
            continue
        choices = rules.get(name, [])

        def available(entry):
            """原生及混合候选必须有目标绑定；缺失时继续尝试字典的数学后备。"""
            if entry["kind"] == "native":
                return entry["definition"] in bound
            if entry["kind"] == "hybrid":
                definition = extensions.getNodeDef(entry["definition"]) if extensions else None
                graph = definition.getImplementation() if definition is not None else None
                if not isinstance(graph, mx.NodeGraph):
                    raise ValidationError("混合配方缺少目标实现图", "MATERIALX_BINDING_INVALID")
                lowered = set(dictionary.data.get("materialx_lowerings", {}))
                return all(n.getNodeDefString() in bound | lowered for n in graph.getNodes())
            return True

        matches = [
            entry
            for entry in choices
            if available(entry)
            if all(_value(_port(node, port)) == value for port, value in entry["when"].items())
        ]
        if not matches:
            if not functional:
                # 官方功能仍可走原有直接绑定或基础运算组合；特定模式映射并非唯一实现。
                continue
            raise ValidationError(
                "目标字典没有适用的功能实现：" + name, "MATERIALX_FUNCTION_UNSUPPORTED"
            )
        priority = {"native": 0, "hybrid": 1, "recipe": 2}
        rank = min(priority[e["kind"]] for e in matches)
        matches = [e for e in matches if priority[e["kind"]] == rank]
        if len(matches) != 1:
            raise ValidationError("功能实现存在同优先级歧义", "MATERIALX_BINDING_INVALID")
        entry = matches[0]
        expected = extensions.getNodeDef(name) if extensions is not None else None
        unknown = _check_interface(source, expected) if functional else set()
        # 未映射的非默认细节先报告；来源文档保留完整值与连接。
        supported = set(entry["inputs"]) | set(entry["when"])
        detail_names = {p.getName() for p in source.getInputs()}
        for port in source.getActiveInputs():
            actual = node.getInput(port.getName())
            if port.getName() not in supported and (
                port.getName() in unknown | detail_names
                or (actual is not None and _value(actual) != _value(port))
            ):
                issues.append(
                    issue(
                        "MATERIALX_DETAIL_UNAPPLIED",
                        "目标实现未应用此细节，完整信息保留在源文档",
                        node.getName(),
                        port.getName(),
                        "warning",
                    )
                )
        replacement = extensions.getNodeDef(entry["definition"])
        if replacement is None:
            raise ValidationError("目标功能实现未定义", "MATERIALX_BINDING_INVALID")
        _copy_definition(doc, replacement)
        # 只从目标字典复制选中的实现及依赖；原生绑定稍后决定是否需要展开。
        pending, copied = [replacement.getName()], set()
        while pending:
            current = pending.pop()
            if current in copied:
                continue
            copied.add(current)
            if current in core.standard_names:
                continue
            definition = extensions.getNodeDef(current)
            _copy_definition(doc, definition)
            graph = definition.getImplementation()
            if isinstance(graph, mx.NodeGraph):
                existing = doc.getChild(graph.getName())
                if existing is not None and not equivalent_computation(existing, graph):
                    raise ValidationError("目标配方与已有图冲突", "MATERIALX_BINDING_INVALID")
                if existing is None:
                    doc.addNodeGraph(graph.getName()).copyContentFrom(graph)
                pending.extend(
                    n.getNodeDefString()
                    for n in graph.getNodes()
                    if n.getNodeDefString() not in core.standard_names
                )
        _remap(node, replacement, entry["inputs"])
        if entry["kind"] == "hybrid":
            node.setAttribute("iris_native_function", "true")
        consumed.add(name)
        issues.append(
            issue(
                "MATERIALX_FUNCTION_" + entry["kind"].upper(),
                entry["evidence"],
                node.getName(),
                severity="info",
            )
        )
    # 只移除本次忽略细节后失去全部使用者的节点；共享上游仍须参与正常转换。
    currently_used = {n.getNamePath() for n in _active_nodes(doc, material_name)}
    detached = originally_used - currently_used
    for node in list(doc.getNodes()):
        if node.getNamePath() in detached:
            doc.removeNode(node.getName())
    # 来源已完整保留在响应中，工作图不再携带已处理接口，避免未知细节污染目标库。
    for name in consumed:
        if not any(n.getNodeDefString() == name for n in doc.getNodes()):
            doc.removeNodeDef(name)
