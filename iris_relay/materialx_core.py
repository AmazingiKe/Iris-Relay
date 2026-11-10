"""MaterialX 文档核心：使用官方节点库，保存扩展定义，不执行着色代码。"""

import math
import re
from collections import deque
from xml.etree import ElementTree as ET

import MaterialX as mx

from .conversion import issue, status_for
from .model import ValidationError, fields, text

MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
MAX_ELEMENTS = 20000


class MaterialXCore:
    """每个服务持有只读标准库；每次请求创建独立文档。"""

    def __init__(self):
        self.library = mx.createDocument()
        mx.loadLibraries(
            mx.getDefaultDataLibraryFolders(), mx.getDefaultDataSearchPath(), self.library
        )
        self.standard_names = {n.getName() for n in self.library.getNodeDefs()}
        self.standard_categories = {n.getNodeString() for n in self.library.getNodeDefs()}

    def capabilities(self):
        return {
            "available": True,
            "version": mx.getVersionString(),
            "document_version": "1.39",
            "standard_nodedef_count": len(self.standard_names),
            "custom_nodedefs": True,
            "custom_nodegraphs": True,
            "executes_shader_code": False,
            "external_includes": False,
        }

    def definitions(self, payload):
        fields(payload, set(), {"category", "document"})
        category = text(payload["category"], "节点类别") if "category" in payload else None
        definitions = list(self.library.getNodeDefs())
        if "document" in payload:
            document = self.read(payload["document"])
            issues = self.inspect(document)
            if status_for(issues) == "error":
                raise ValidationError("自定义库未通过校验", "MATERIALX_INVALID")
            definitions.extend(n for n in document.getNodeDefs() if n.getDocument() == document)

        def ports(items):
            return {
                p.getName(): {
                    "type": p.getType(),
                    "default": p.getValueString() if p.hasValueString() else None,
                    "attributes": {a: p.getAttribute(a) for a in p.getAttributeNames()},
                }
                for p in items
            }

        return {
            **self.capabilities(),
            "definitions": [
                {
                    "name": n.getName(),
                    "category": n.getNodeString(),
                    "type": n.getType(),
                    "version": n.getVersionString(),
                    "inputs": ports(n.getActiveInputs()),
                    "outputs": ports(n.getActiveOutputs()),
                }
                for n in sorted(definitions, key=lambda n: n.getName())
                if category is None or n.getNodeString() == category
            ],
        }

    def read(self, document):
        text(document, "MaterialX 文档")
        if len(document.encode("utf-8")) > MAX_DOCUMENT_BYTES:
            raise ValidationError("MaterialX 文档超过 8 MiB", "MATERIALX_LIMIT")
        if re.search(r"<!\s*(DOCTYPE|ENTITY)\b", document, re.I):
            raise ValidationError("MaterialX 不接受 DTD 或实体声明", "MATERIALX_EXTERNAL_REFERENCE")
        try:
            root = ET.fromstring(document)
        except ET.ParseError as exc:
            raise ValidationError(str(exc), "MATERIALX_XML_INVALID") from exc
        if root.tag != "materialx" or root.get("version") != "1.39":
            raise ValidationError("需要 MaterialX 1.39 文档，不自动升级", "MATERIALX_VERSION")
        pending, count = [(root, 0)], 0
        while pending:
            element, depth = pending.pop()
            count += 1
            if depth > 64 or count > MAX_ELEMENTS:
                raise ValidationError("MaterialX 文档层数或元素数超限", "MATERIALX_LIMIT")
            if element.tag.split("}")[-1].split(":")[-1] == "include":
                raise ValidationError(
                    "请内联自定义库；不自动读取 XInclude", "MATERIALX_EXTERNAL_REFERENCE"
                )
            names = [child.get("name") for child in element if child.get("name") is not None]
            if len(names) != len(set(names)):
                raise ValidationError("同一作用域存在重复名称", "MATERIALX_DUPLICATE_NAME")
            pending.extend((child, depth + 1) for child in element)
        doc = mx.createDocument()
        options = mx.XmlReadOptions()
        options.upgradeVersion = False
        options.readComments = True

        def reject_include(*args):
            raise ValidationError("禁止外部文档读取", "MATERIALX_EXTERNAL_REFERENCE")

        options.readXIncludeFunction = reject_include
        try:
            mx.readFromXmlString(doc, document, mx.FileSearchPath(), options)
        except mx.Exception as exc:
            raise ValidationError(str(exc), "MATERIALX_XML_INVALID") from exc
        for child in doc.getChildren():
            if (
                isinstance(child, (mx.NodeGraph, mx.Implementation))
                and child.getNodeDefString() in self.standard_names
            ):
                raise ValidationError("文档不能重定义标准节点实现", "MATERIALX_LIBRARY_CONFLICT")
            if (
                not isinstance(child, mx.Node)
                and self.library.getChild(child.getName()) is not None
            ):
                raise ValidationError("文档不能覆盖标准库元素", "MATERIALX_LIBRARY_CONFLICT")
        for definition in doc.getNodeDefs():
            if definition.getQualifiedName(definition.getNodeString()) in self.standard_categories:
                raise ValidationError(
                    "扩展节点须使用独立类别或命名空间", "MATERIALX_LIBRARY_CONFLICT"
                )
        doc.setDataLibrary(self.library)
        return doc

    def inspect(self, doc):
        issues = []
        valid, message = doc.validate()
        if not valid:
            issues.append(issue("MATERIALX_INVALID", message))
        elements = list(doc.traverseTree())
        vertices = {e.getNamePath(): e for e in elements if isinstance(e, (mx.Node, mx.Output))}
        edges = {name: [] for name in vertices}
        degrees = dict.fromkeys(vertices, 0)
        for name, element in vertices.items():
            ports = element.getInputs() if isinstance(element, mx.Node) else [element]
            for port in ports:
                upstream = port.getConnectedOutput()
                if upstream is None:
                    upstream = port.getConnectedNode()
                if upstream is not None and upstream.getNamePath() in vertices:
                    edges[upstream.getNamePath()].append(name)
                    degrees[name] += 1
        queue = deque(name for name, degree in degrees.items() if degree == 0)
        count = 0
        while queue:
            name = queue.popleft()
            count += 1
            for downstream in edges[name]:
                degrees[downstream] -= 1
                if degrees[downstream] == 0:
                    queue.append(downstream)
        if count != len(vertices):
            issues.append(issue("MATERIALX_CYCLE", "节点或子图输出包含循环连接"))
        for element in elements:
            if isinstance(element, mx.Node):
                definition = element.getNodeDef()
                if definition is None:
                    issues.append(
                        issue(
                            "MATERIALX_NODEDEF_MISSING",
                            "找不到匹配的节点定义",
                            element.getNamePath(),
                        )
                    )
                elif (
                    definition.getName() not in self.standard_names
                    and definition.getImplementation() is None
                ):
                    issues.append(
                        issue(
                            "MATERIALX_IMPLEMENTATION_MISSING",
                            "自定义节点已保存，但未提供可展开的子图或代码实现",
                            element.getNamePath(),
                            severity="warning",
                        )
                    )
            if isinstance(element, mx.ValueElement) and element.hasValueString():
                kind = element.getType()
                if kind in {
                    "float",
                    "color3",
                    "color4",
                    "vector2",
                    "vector3",
                    "vector4",
                    "matrix33",
                    "matrix44",
                    "floatarray",
                }:
                    try:
                        finite = all(
                            math.isfinite(float(v)) for v in element.getValueString().split(",")
                        )
                    except ValueError:
                        finite = False
                    if not finite:
                        issues.append(
                            issue(
                                "MATERIALX_NONFINITE", "数值必须有效且有限", element.getNamePath()
                            )
                        )
        return issues

    def serialize(self, doc):
        """写出后复核 XML；SDK 对特殊字符串未转义时用等价的标准 XML 写出补足。"""
        unsafe = any(
            any(c in '<>&"\r\n\t' for c in element.getAttribute(name))
            for element in doc.traverseTree()
            for name in element.getAttributeNames()
        )
        if not unsafe:
            content = mx.writeToXmlString(doc)
            try:
                ET.fromstring(content)
                return content
            except ET.ParseError:
                pass

        def element_xml(element):
            """只序列化 SDK 已有元素和属性，转义不改变原始字符串值。"""
            attributes = {name: element.getAttribute(name) for name in element.getAttributeNames()}
            if element.getName():
                attributes["name"] = element.getName()
            result = ET.Element(element.getCategory(), attributes)
            for child in element.getChildren():
                result.append(element_xml(child))
            return result

        content = ET.tostring(element_xml(doc), encoding="unicode", xml_declaration=True)
        restored = self.read(content)
        if not doc.isEquivalent(restored, mx.ElementEquivalenceOptions())[0]:
            raise ValidationError("XML 字符串转义后与原图不等价", "MATERIALX_SERIALIZATION_FAILED")
        return content

    def flatten(
        self,
        doc,
        *,
        preserve=frozenset(),
        editable=False,
        editable_types=frozenset({"float", "color3"}),
    ):
        """展开自定义实现；标准节点及调用方已核验的原生绑定子图保持完整。"""
        own_graphs = [g for g in doc.getNodeGraphs() if g.getDocument() == doc]
        graphs = {g.getNodeDefString(): g for g in own_graphs if g.getNodeDefString()}
        visiting, sizes = set(), {}

        def visit(name):
            if name in visiting:
                raise ValidationError("自定义子图递归引用", "MATERIALX_RECURSIVE_GRAPH")
            if name in sizes:
                return sizes[name]
            if name not in graphs:
                return 1
            if len(visiting) >= 64:
                raise ValidationError("自定义子图嵌套超过 64 层", "MATERIALX_LIMIT")
            visiting.add(name)
            size = 1
            for node in graphs[name].getNodes():
                definition = node.getNodeDef()
                if definition is not None:
                    size += visit(definition.getName()) * (1 + len(node.getInputs()))
                if size > MAX_ELEMENTS:
                    raise ValidationError("自定义子图展开规模超限", "MATERIALX_LIMIT")
            visiting.remove(name)
            sizes[name] = size
            return size

        for name in graphs:
            visit(name)
        estimate = sum(
            sizes.get(n.getNodeDef().getName(), 1)
            for n in doc.traverseTree()
            if isinstance(n, mx.Node) and n.getNodeDef() is not None
        )
        if estimate > MAX_ELEMENTS:
            raise ValidationError("自定义子图总展开规模超限", "MATERIALX_LIMIT")

        def custom(node):
            definition = node.getNodeDef()
            return (
                definition is not None
                and definition.getName() not in self.standard_names
                and definition.getName() not in preserve
            )

        for graph in [doc, *own_graphs]:
            if isinstance(graph, mx.NodeGraph) and graph.getNodeDefString() in preserve:
                continue
            if editable and graph == doc:
                from .materialx_editor import flatten_editable

                flatten_editable(graph, self, preserve, controls=editable_types)
            else:
                graph.flattenSubgraphs("", custom)
            if sum(1 for _ in doc.traverseTree()) > MAX_ELEMENTS:
                raise ValidationError("展开后的 MaterialX 图超限", "MATERIALX_LIMIT")

    def process(self, payload, *, normalize=False):
        fields(payload, {"document"}, {"flatten"} if normalize else set())
        if type(payload.get("flatten", False)) is not bool:
            raise ValidationError("flatten 必须为布尔值")
        source = payload["document"]
        result = {
            "source": source,
            "document": None,
            "valid": False,
            "issues": [],
            "appearance_verified": False,
            "materialx_version": mx.getVersionString(),
        }
        try:
            doc = self.read(source)
            result["issues"] = self.inspect(doc)
            if status_for(result["issues"]) != "error" and payload.get("flatten", False):
                self.flatten(doc)
                result["issues"] = self.inspect(doc)
            result["valid"] = status_for(result["issues"]) != "error"
            if result["valid"] and normalize:
                result["document"] = self.serialize(doc)
        except (ValidationError, mx.Exception) as exc:
            result["valid"] = False
            result["issues"].append(issue(getattr(exc, "code", "MATERIALX_INVALID"), str(exc)))
        return result
