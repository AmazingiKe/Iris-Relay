"""用宿主图替身核对组实例寻址、默认值、边界连接及失败诊断。"""

from types import SimpleNamespace as NS

import pytest

from iris_relay.model import ValidationError
from plugins.blender.groups import ExpandedGraph


class Ports(list):
    """模拟 Blender 的索引与名称端口查询。"""

    def __getitem__(self, key):
        return (
            next(p for p in self if p.identifier == key)
            if isinstance(key, str)
            else super().__getitem__(key)
        )


def node(tree, name, kind="ShaderNodeMath", inputs=(), outputs=("Value",)):
    """建立最小宿主节点，保留原始对象供修改保护检查。"""
    item = NS(name=name, bl_idname=kind, mute=False, inputs=Ports(), outputs=Ports())
    for direction, names in (("inputs", inputs), ("outputs", outputs)):
        for identifier in names:
            getattr(item, direction).append(
                NS(
                    node=item,
                    identifier=identifier,
                    name=identifier,
                    type="VALUE",
                    default_value=0.5,
                    links=[],
                )
            )
    tree.nodes.append(item)
    return item


def link(source, target):
    """建立唯一有效输入连接；输出端不用存图外信息。"""
    target.links.append(NS(from_socket=source, is_valid=True))


def fixture():
    """同一组定义实例化两次，实例输入与内部默认值刻意不同。"""
    root, definition = NS(nodes=[], name="root"), NS(nodes=[], name="shared")
    entry = node(definition, "Group Input", "NodeGroupInput", outputs=("factor",))
    math = node(definition, "Multiply", inputs=("Value", "Value_001"))
    exit_node = node(definition, "Group Output", "NodeGroupOutput", inputs=("result",), outputs=())
    exit_node.is_active_output = True
    link(entry.outputs[0], math.inputs[0])
    link(math.outputs[0], exit_node.inputs[0])
    groups, targets = [], []
    for index in range(2):
        group = node(
            root, f"Group {index}", "ShaderNodeGroup", inputs=("factor",), outputs=("result",)
        )
        group.node_tree = definition
        group.inputs[0].default_value = index + 2.0
        target = node(root, f"Target {index}", inputs=("Value",))
        link(group.outputs[0], target.inputs[0])
        groups.append(group)
        targets.append(target)
    return root, definition, groups, targets, math


def test_instances_keep_distinct_values_and_shared_outputs():
    root, definition, groups, targets, original = fixture()
    graph = ExpandedGraph(root)
    views = [graph.node(t).inputs[0].links[0].from_socket.node for t in targets]
    assert views[0].name != views[1].name
    assert [v.inputs[0].default_value for v in views] == [2.0, 3.0]
    assert all(not v.inputs[0].is_linked for v in views)
    assert views[0].inputs[1].default_value == 0.5
    assert graph.node(targets[0]).inputs[0].links[0].from_socket.node is views[0]
    assert graph.root_groups == {g.name for g in groups}
    assert views[0].source_group_path == ["Group 0", "Multiply"]
    assert original.inputs[0].default_value == 0.5
    assert len(definition.nodes) == 3 and len(root.nodes) == 4


def test_linked_instance_input_resolves_to_root_node():
    root, _, groups, targets, _ = fixture()
    source = node(root, "Source")
    link(source.outputs[0], groups[0].inputs[0])
    graph = ExpandedGraph(root)
    internal = graph.node(targets[0]).inputs[0].links[0].from_socket.node
    assert internal.inputs[0].links[0].from_socket.node is graph.node(source)


def test_passthrough_and_unlinked_group_output_preserve_defaults():
    root, definition, groups, targets, _ = fixture()
    exit_port = definition.nodes[-1].inputs[0]
    exit_port.links.clear()
    link(definition.nodes[0].outputs[0], exit_port)
    graph = ExpandedGraph(root)
    assert not graph.node(targets[0]).inputs[0].is_linked
    assert graph.node(targets[0]).inputs[0].default_value == 2.0
    exit_port.links.clear()
    exit_port.default_value = 0.73
    graph = ExpandedGraph(root)
    assert graph.node(targets[0]).inputs[0].default_value == 0.73


def test_nested_instance_resolves_outer_override():
    root, definition, _, targets, math = fixture()
    inner = NS(nodes=[], name="inner")
    entry = node(inner, "In", "NodeGroupInput", outputs=("factor",))
    output = node(inner, "Out", "NodeGroupOutput", inputs=("result",), outputs=())
    output.is_active_output = True
    link(entry.outputs[0], output.inputs[0])
    nested = node(definition, "Nested", "ShaderNodeGroup", inputs=("factor",), outputs=("result",))
    nested.node_tree = inner
    link(definition.nodes[0].outputs[0], nested.inputs[0])
    math.inputs[0].links.clear()
    link(nested.outputs[0], math.inputs[0])
    view = ExpandedGraph(root).node(targets[1]).inputs[0].links[0].from_socket.node
    assert view.inputs[0].default_value == 3.0


@pytest.mark.parametrize(
    "problem",
    ["missing", "no_output", "multiple_outputs", "mute", "invalid_link", "type_cast", "recursive"],
)
def test_incomplete_or_ambiguous_groups_fail_without_guessing(problem):
    root, definition, groups, targets, _ = fixture()
    if problem == "missing":
        groups[0].node_tree = None
    elif problem == "no_output":
        definition.nodes[-1].is_active_output = False
    elif problem == "multiple_outputs":
        extra = node(definition, "Extra", "NodeGroupOutput", inputs=("result",), outputs=())
        extra.is_active_output = True
    elif problem == "mute":
        groups[0].mute = True
    elif problem == "invalid_link":
        definition.nodes[-1].inputs[0].links[0].is_valid = False
    elif problem == "type_cast":
        definition.nodes[-1].inputs[0].type = "RGBA"
    else:
        groups[0].node_tree = root
    with pytest.raises(ValidationError):
        _ = ExpandedGraph(root).node(targets[0]).inputs[0].is_linked


def test_group_input_conversion_does_not_disappear():
    root, _, groups, targets, _ = fixture()
    source = node(root, "Color")
    source.outputs[0].type = "RGBA"
    link(source.outputs[0], groups[0].inputs[0])
    graph = ExpandedGraph(root)
    internal = graph.node(targets[0]).inputs[0].links[0].from_socket.node
    with pytest.raises(ValidationError, match="类型"):
        _ = internal.inputs[0].is_linked


def test_limits_stop_expansion_before_unbounded_instances():
    root, _, _, targets, _ = fixture()
    graph = ExpandedGraph(root, max_depth=0)
    with pytest.raises(ValidationError, match="层"):
        _ = graph.node(targets[0]).inputs[0].is_linked


def test_interface_identifier_must_not_fall_back_to_display_name():
    root, definition, _, targets, _ = fixture()
    definition.nodes[-1].inputs[0].identifier = "changed"
    with pytest.raises(ValidationError, match="端口"):
        _ = ExpandedGraph(root).node(targets[0]).inputs[0].is_linked


def test_vector_default_keeps_implicit_geometry_input_distinct():
    root, definition, _, targets, _ = fixture()
    exit_port = definition.nodes[-1].inputs[0]
    exit_port.links.clear()
    exit_port.type = targets[0].inputs[0].type = "VECTOR"
    root.nodes[0].outputs[0].type = "VECTOR"
    exit_port.default_value = [0, 0, 0]
    with pytest.raises(ValidationError, match="隐式几何"):
        _ = ExpandedGraph(root).node(targets[0]).inputs[0].default_value


def test_passthrough_cycle_and_node_limit_are_reported():
    root, definition, groups, targets, _ = fixture()
    exit_port = definition.nodes[-1].inputs[0]
    exit_port.links.clear()
    link(definition.nodes[0].outputs[0], exit_port)
    link(groups[0].outputs[0], groups[0].inputs[0])
    with pytest.raises(ValidationError, match="循环"):
        _ = ExpandedGraph(root).node(targets[0]).inputs[0].is_linked
    with pytest.raises(ValidationError, match="数量"):
        ExpandedGraph(root, max_nodes=0).node(targets[0])


def test_reroute_does_not_hide_group_boundary_cast():
    root, definition, _, targets, _ = fixture()
    exit_port = definition.nodes[-1].inputs[0]
    reroute = node(definition, "Reroute", "NodeReroute", inputs=("Input",))
    source = node(definition, "RGBA")
    source.outputs[0].type = "RGBA"
    link(source.outputs[0], reroute.inputs[0])
    exit_port.links.clear()
    link(reroute.outputs[0], exit_port)
    with pytest.raises(ValidationError, match="类型"):
        _ = ExpandedGraph(root).node(targets[0]).inputs[0].is_linked
