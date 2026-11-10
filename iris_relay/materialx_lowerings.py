"""按目标字典展开标准节点的组合实现；保留源文档，不执行宿主或外部代码。"""

import MaterialX as mx

from .conversion import issue
from .materialx_extensions import preserved_extensions
from .model import ValidationError


def scalar_bridges(doc, dictionary, bindings):
    """保留目标标量端口所需的显式灰度广播；共享颜色使用者另保留可展开副本。"""
    ports = set()
    for rule in dictionary.nodes.values():
        binding = bindings.get(rule["semantic"])
        if binding is None:
            continue
        ports.update(
            (binding[0], binding[1][p["semantic"]])
            for p in rule["inputs"].values()
            if p.get("transform") == "scalar_to_color" and p["semantic"] in binding[1]
        )
    consumers = {}
    for node in doc.getNodes():
        definition = node.getNodeDef()
        for port in node.getInputs():
            upstream = port.getConnectedNode()
            source_definition = upstream.getNodeDef() if upstream is not None else None
            if source_definition is not None and source_definition.getName() in {
                "ND_convert_float_color3",
                "ND_combine3_color3",
            }:
                consumers.setdefault(upstream.getNamePath(), (upstream, []))[1].append(
                    (port, (definition.getName(), port.getName()) in ports)
                )
    protected = set()
    for original, uses in consumers.values():
        scalar = [port for port, selected in uses if selected]
        if not scalar:
            continue
        bridge = original
        if len(scalar) != len(uses):
            bridge = doc.addNodeInstance(
                original.getNodeDef(),
                doc.createValidChildName(original.getName() + "_scalar_bridge"),
            )
            bridge.copyContentFrom(original)
            for port in scalar:
                port.setConnectedNode(bridge)
        protected.add(bridge.getNamePath())
    return protected


def apply_lowerings(core, doc, dictionary, extensions, bindings, preserve, issues):
    """核验接口与常量条件，再在工作文档内替换并展开，循环和规模继续由核心限制。"""
    rules = dictionary.data.get("materialx_lowerings", {})
    if not rules:
        return preserve
    if extensions is None:
        raise ValidationError("组合规则需要完整内联扩展库", "MATERIALX_BINDING_INVALID")
    # 1. 原始标准定义保持不变；替换定义只能来自已校验的目标内联库。
    for name, choices in rules.items():
        source = core.library.getNodeDef(name)
        if source is None:
            raise ValidationError("组合规则引用未知官方定义：" + name, "MATERIALX_BINDING_INVALID")
        source_inputs = {p.getName(): p.getType() for p in source.getActiveInputs()}
        for choice in choices:
            target = extensions.getNodeDef(choice["replacement"])
            if (
                target is None
                or target.getDocument() != extensions
                or target.getType() != source.getType()
                or {p.getName(): p.getType() for p in target.getActiveOutputs()}
                != {p.getName(): p.getType() for p in source.getActiveOutputs()}
            ):
                raise ValidationError(
                    "组合规则的目标定义不存在或输出类型不同", "MATERIALX_BINDING_INVALID"
                )
            inputs = {p.getName(): p.getType() for p in target.getActiveInputs()}
            if any(source_inputs.get(k) != v for k, v in inputs.items()) or set(
                source_inputs
            ) - inputs.keys() != set(choice["when"]):
                raise ValidationError("组合规则接口或常量条件不完整", "MATERIALX_BINDING_INVALID")
    control_types = {
        datatype
        for datatype in ("float", "color3")
        if any(binding[0] == "ND_constant_" + datatype for binding in bindings.values())
    }
    if len(control_types) < 2 and any(
        node.getNodeDef() is not None
        and node.getNodeDef().getName() not in core.standard_names | set(preserve)
        for node in doc.getNodes()
    ):
        issues.append(
            issue(
                "EDITOR_CONTROL_LIMITED",
                "目标缺少部分原生常量控制，相关接口保留为有效内联值",
                severity="info",
            )
        )
    core.flatten(doc, preserve=preserve, editable=True, editable_types=control_types)
    protected_scalar_bridges = scalar_bridges(doc, dictionary, bindings)
    imported = False
    changed = 0
    # 2. 只改当前工作图实例；定义本身不被覆写，动态选择不能当作常量。
    for _ in range(8):
        replacements = []
        for node in doc.getNodes():
            if (
                node.hasAttribute("iris_image_source")
                or node.getNamePath() in protected_scalar_bridges
            ):
                # 图片辅助结构稍后由 ImageGraphReader 整组核验、合并，不参与通用运算替换。
                continue
            nd = node.getNodeDef()
            if nd is None or nd.getName() not in rules:
                continue
            matches = []
            for choice in rules[nd.getName()]:
                valid = True
                for name, value in choice["when"].items():
                    actual = node.getInput(name)
                    declared = nd.getActiveInput(name)
                    if actual is not None and any(
                        actual.getAttribute(a) for a in ("nodename", "nodegraph", "interfacename")
                    ):
                        valid = False
                        break
                    candidate = (
                        actual if actual is not None and actual.hasValueString() else declared
                    )
                    if candidate.getValue() != value:
                        valid = False
                        break
                if valid:
                    matches.append(choice)
            if len(matches) > 1:
                raise ValidationError("同一节点匹配多个组合规则", "MATERIALX_BINDING_INVALID")
            if matches:
                replacements.append((node, matches[0]))
        if not replacements:
            break
        if not imported:
            for expected in extensions.getChildren():
                actual = doc.getChild(expected.getName())
                if (
                    actual is not None
                    and not actual.isEquivalent(expected, mx.ElementEquivalenceOptions())[0]
                ):
                    raise ValidationError(
                        "源文档与目标组合定义重名但实现不同：" + expected.getName(),
                        "MATERIALX_BINDING_INVALID",
                    )
            doc.importLibrary(extensions)
            preserve = preserved_extensions(doc, extensions, bindings) | set(preserve)
            imported = True
        for node, choice in replacements:
            replacement = extensions.getNodeDef(choice["replacement"])
            for name in choice["when"]:
                node.removeInput(name)
            node.setCategory(replacement.getNodeString())
            node.setNodeDefString(replacement.getName())
            node.setAttribute("iris_internal_lowering", "true")
            changed += 1
        core.flatten(doc, preserve=preserve, editable=True, editable_types=control_types)
    else:
        raise ValidationError("目标组合规则展开未收敛", "MATERIALX_LIMIT")
    if changed:
        issues.append(
            issue(
                "MATERIALX_LOWERED",
                f"按目标字典展开 {changed} 个标准节点为可编辑组合",
                severity="info",
            )
        )
    return preserve
