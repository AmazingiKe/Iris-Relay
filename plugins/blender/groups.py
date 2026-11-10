"""只读展开 Blender 节点组实例；不复制数据块，也不执行材质转换规则。

节点与端口视图沿组接口解析连接、实例默认值，供适配器按原有公开绑定读取。
每个组实例有独立路径；不能安全消除的接口类型转换明确失败。
"""

import hashlib
import json
from types import SimpleNamespace

from iris_relay.model import ValidationError


def _value(socket):
    """复制可序列化默认值，避免视图持有可写的宿主数组。"""
    value = getattr(socket, "default_value", None)
    return value if value is None or isinstance(value, (str, bool, int, float)) else list(value)


class _Ports(list):
    """保留原生顺序，并按稳定 identifier 或显示名称查询端口。"""

    def __getitem__(self, key):
        if not isinstance(key, str):
            return super().__getitem__(key)
        found = [p for p in self if p.identifier == key]
        if not found:
            found = [p for p in self if p.name == key]
        if len(found) != 1:
            raise ValidationError(f"节点组端口不存在或不唯一：{key}", "GROUP_INTERFACE_INVALID")
        return found[0]

    def by_identifier(self, identifier):
        """接口只按稳定标识匹配，不用重名显示标签猜测。"""
        found = [p for p in self if p.identifier == identifier]
        if len(found) != 1:
            raise ValidationError(
                f"节点组端口标识不存在或不唯一：{identifier}", "GROUP_INTERFACE_INVALID"
            )
        return found[0]


class _Socket:
    """输入按需解析组边界；输出保持所在实例身份。"""

    def __init__(self, node, native, is_input):
        self.node, self.native, self.is_input = node, native, is_input

    def __getattr__(self, name):
        return getattr(self.native, name)

    @property
    def links(self):
        source, _ = self.node.graph.resolve(self)
        return [SimpleNamespace(from_socket=source, is_valid=True)] if source else []

    @property
    def is_linked(self):
        return bool(self.links)

    @property
    def default_value(self):
        if not self.is_input:
            return _value(self.native)
        source, value = self.node.graph.resolve(self)
        return _value(self.native) if source else value


class _Node:
    """包装原生功能节点；名称按组实例隔离，属性仍从宿主只读获取。"""

    def __init__(self, graph, scope, native, name):
        self.graph, self.scope, self.native, self.name = graph, scope, native, name
        self.inputs = _Ports(_Socket(self, p, True) for p in native.inputs)
        self.outputs = _Ports(_Socket(self, p, False) for p in native.outputs)
        self.source_group_path = [*scope.path, native.name] if scope.path else []
        self.diagnostic_name = " / ".join(self.source_group_path) or native.name

    def __getattr__(self, name):
        return getattr(self.native, name)


class ExpandedGraph:
    """为一个材质建立惰性实例视图；只展开读取路径上的组，限制深度和节点总量。"""

    def __init__(self, tree, *, max_depth=64, max_nodes=20000):
        self.root = SimpleNamespace(tree=tree, path=(), parent=None)
        self.max_depth, self.max_nodes = max_depth, max_nodes
        self.nodes, self.scopes = {}, {}
        self.names = {n.name for n in tree.nodes}
        self.root_groups = set()
        self.root_reroutes = set()

    def node(self, native, scope=None):
        """同一路径重复访问返回同一节点；不同实例不因共用组定义而合并。"""
        scope = scope or self.root
        key = (*scope.path, native.name)
        if key not in self.nodes:
            if len(self.nodes) >= self.max_nodes:
                raise ValidationError("节点组展开超过节点数量限制", "GROUP_LIMIT_EXCEEDED")
            name = native.name
            if scope.path:
                # Blender 名称存在字节长度限制；完整可读路径保存在来源 metadata。
                digest = hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()
                base = name = "group_" + digest[:24]
                suffix = 0
                while name in self.names:
                    suffix += 1
                    name = f"{base}_{suffix}"
                self.names.add(name)
            self.nodes[key] = _Node(self, scope, native, name)
        return self.nodes[key]

    def _scope(self, group):
        """进入具体组实例；拒绝缺失库占位、递归定义及过深的嵌套。"""
        path = (*group.scope.path, group.native.name)
        if path in self.scopes:
            return self.scopes[path]
        tree = group.node_tree
        if tree is None or not tree.nodes:
            raise ValidationError(
                f"节点组定义缺失或为空：{group.native.name}", "GROUP_DEFINITION_MISSING"
            )
        if len(path) > self.max_depth:
            raise ValidationError("节点组嵌套超过允许层数", "GROUP_LIMIT_EXCEEDED")
        ancestor = group.scope
        while ancestor:
            if ancestor.tree == tree:
                raise ValidationError("节点组定义递归引用", "GROUP_RECURSION")
            ancestor = ancestor.parent.scope if ancestor.parent else None
        scope = SimpleNamespace(tree=tree, path=path, parent=group)
        self.scopes[path] = scope
        self.root_groups.add(path[0])
        return scope

    @staticmethod
    def _same_type(left, right):
        """组边界上的隐式类型转换不能在展开时悄悄消失。"""
        if left.type != right.type:
            raise ValidationError(
                f"节点「{right.node.diagnostic_name}」的组接口含尚未显式表达的类型转换："
                f"{left.type} → {right.type}",
                "GROUP_SOCKET_TYPE_UNSUPPORTED",
            )

    def resolve(self, socket):
        """返回实际上游输出或实例默认值；迭代解析边界，检测接口旁路循环。"""
        current, seen, interfaces = socket, set(), []
        while True:
            key = (tuple(current.node.source_group_path), current.node.name, current.identifier)
            if key in seen:
                raise ValidationError("节点组接口连接包含循环", "INVALID_LINK")
            seen.add(key)
            links = list(current.native.links)
            if not links:
                if interfaces:
                    self._same_type(current, socket)
                    for interface in interfaces:
                        self._same_type(current, interface)
                    if socket.type not in {"VALUE", "RGBA"}:
                        raise ValidationError(
                            f"节点「{socket.node.diagnostic_name}」的组默认值需要显式常量节点，"
                            "不能替代目标端口的隐式几何输入",
                            "GROUP_DEFAULT_UNSUPPORTED",
                        )
                return None, _value(current.native)
            if len(links) != 1 or not links[0].is_valid:
                raise ValidationError("节点组连接无效或没有唯一来源", "INVALID_LINK")
            raw = links[0].from_socket
            upstream = self.node(raw.node, current.node.scope)
            source = upstream.outputs.by_identifier(raw.identifier)
            if upstream.mute:
                raise ValidationError(f"暂不转换静音节点：{upstream.name}", "MUTED_NODE")
            # 进入和离开接口时，其声明类型必须和实际连接一致。
            if current.node.bl_idname in {"NodeGroupOutput", "ShaderNodeGroup"}:
                self._same_type(source, current)
            if upstream.bl_idname == "ShaderNodeGroup":
                scope = self._scope(upstream)
                outputs = [
                    n
                    for n in scope.tree.nodes
                    if n.bl_idname == "NodeGroupOutput" and n.is_active_output
                ]
                if len(outputs) != 1:
                    raise ValidationError("节点组需要唯一活动输出", "GROUP_INTERFACE_INVALID")
                current = self.node(outputs[0], scope).inputs.by_identifier(source.identifier)
                self._same_type(source, current)
                interfaces.append(source)
            elif upstream.bl_idname == "NodeGroupInput":
                parent = upstream.scope.parent
                if parent is None:
                    raise ValidationError("组输入缺少所在实例", "GROUP_INTERFACE_INVALID")
                current = parent.inputs.by_identifier(source.identifier)
                self._same_type(source, current)
                interfaces.append(source)
            elif upstream.bl_idname == "NodeReroute":
                if not upstream.scope.path:
                    self.root_reroutes.add(upstream.name)
                current = upstream.inputs[0]
                if not current.native.links:
                    raise ValidationError("转接点缺少唯一输入", "INVALID_LINK")
            else:
                for interface in interfaces:
                    self._same_type(source, interface)
                return source, None
