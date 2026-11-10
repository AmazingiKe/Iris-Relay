"""按目标字典展开标准节点的组合实现；保留源文档，不执行宿主或外部代码。"""

import MaterialX as mx

from .conversion import issue
from .materialx_extensions import preserved_extensions
from .model import ValidationError


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
            ):
                raise ValidationError(
                    "组合规则的目标定义不存在或输出类型不同", "MATERIALX_BINDING_INVALID"
                )
            inputs = {p.getName(): p.getType() for p in target.getActiveInputs()}
            if any(source_inputs.get(k) != v for k, v in inputs.items()) or set(
                source_inputs
            ) - inputs.keys() != set(choice["when"]):
                raise ValidationError("组合规则接口或常量条件不完整", "MATERIALX_BINDING_INVALID")
    core.flatten(doc, preserve=preserve)
    imported = False
    changed = 0
    # 2. 只改当前工作图实例；定义本身不被覆写，动态选择不能当作常量。
    for _ in range(8):
        replacements = []
        for node in doc.getNodes():
            if node.hasAttribute("iris_image_source"):
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
            changed += 1
        core.flatten(doc, preserve=preserve)
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
