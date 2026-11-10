"""字典声明的内联 NodeDef 与标准子图；原生回导前核验实际实现，避免仅凭名字匹配。"""

import MaterialX as mx

from .conversion import status_for
from .model import ValidationError


def load_extensions(core, dictionary):
    """读取可选扩展库，要求每个自定义定义有唯一内联子图，不加载外部实现。"""
    source = dictionary.data.get("materialx_extensions")
    if source is None:
        return None
    document = core.read(source)
    if any(not isinstance(c, (mx.NodeDef, mx.NodeGraph)) for c in document.getChildren()):
        raise ValidationError(
            "字典扩展库只接受内联 NodeDef 和 NodeGraph", "MATERIALX_BINDING_INVALID"
        )
    definitions = {d.getName() for d in document.getNodeDefs() if d.getDocument() == document}
    graphs = [g for g in document.getNodeGraphs() if g.getDocument() == document]
    if not definitions or any(g.getNodeDefString() not in definitions for g in graphs):
        raise ValidationError("扩展子图需要对应的本地 NodeDef", "MATERIALX_BINDING_INVALID")
    for name in definitions:
        if sum(g.getNodeDefString() == name for g in graphs) != 1:
            raise ValidationError("每个字典扩展定义需要唯一内联子图", "MATERIALX_BINDING_INVALID")
    if status_for(core.inspect(document)) == "error":
        raise ValidationError("字典扩展未通过 MaterialX 校验", "MATERIALX_BINDING_INVALID")
    # 在副本检查递归与展开规模，保留原始声明用于导出及回导实现比对。
    probe = mx.createDocument()
    probe.copyContentFrom(document)
    probe.setDataLibrary(core.library)
    core.flatten(probe)
    return document


def install_extensions(document, extensions, bindings, semantic):
    """仅在材质实际使用扩展时内联定义；普通材质不因新增字典规则改变 XML。"""
    definitions = (
        {d.getName() for d in extensions.getNodeDefs() if d.getDocument() == extensions}
        if extensions is not None
        else set()
    )
    if any(
        bindings[n["type"]][0] in definitions for n in semantic["nodes"] if n["type"] in bindings
    ):
        document.importLibrary(extensions)


def preserved_extensions(document, extensions, bindings):
    """逐项核对目标声明和文档实现，只有相同的完整子图才能还原为原生单节点。"""
    if extensions is None:
        return set()
    definitions = {d.getName() for d in extensions.getNodeDefs() if d.getDocument() == extensions}
    names = {
        b[0]
        for b in bindings.values()
        if b[0] in definitions and document.getNodeDef(b[0]) is not None
    }
    graphs = {
        g.getNodeDefString(): g for g in extensions.getNodeGraphs() if g.getDocument() == extensions
    }
    pending, checked = list(names), set()
    options = mx.ElementEquivalenceOptions()
    while pending:
        name = pending.pop()
        if name in checked:
            continue
        checked.add(name)
        expected_graph = graphs[name]
        expected_definition = extensions.getNodeDef(name)
        for expected in (expected_definition, expected_graph):
            actual = document.getChild(expected.getName())
            if actual is None or not actual.isEquivalent(expected, options)[0]:
                raise ValidationError(
                    f"文档中的扩展实现与目标原生绑定不同：{expected.getName()}",
                    "MATERIALX_BINDING_INVALID",
                )
        implementations = [
            g
            for g in [*document.getNodeGraphs(), *document.getImplementations()]
            if g.getNodeDefString() == name
        ]
        if len(implementations) != 1 or implementations[0].getName() != expected_graph.getName():
            raise ValidationError("扩展包含未核验的替代实现", "MATERIALX_BINDING_INVALID")
        for node in expected_graph.getNodes():
            definition = node.getNodeDef()
            if definition is not None and definition.getName() in graphs:
                pending.append(definition.getName())
    return checked
