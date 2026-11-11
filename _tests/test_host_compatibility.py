"""版本变化可继续接入；实际端口缺失、类型变化及插件加载失败仍须阻断。"""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from iris_relay.protocol import ValidationError
from plugins.maya import adapter as maya_adapter


def maya_commands(monkeypatch, **commands):
    """替换本测试的宿主命令，不引入或启动真实 DCC。"""
    module = NS(**commands)
    monkeypatch.setitem(sys.modules, "maya", NS(cmds=module))
    return module


def test_maya_environment_accepts_changed_versions(monkeypatch):
    loaded = []
    maya_commands(
        monkeypatch,
        about=lambda **kw: "2030",
        pluginInfo=lambda *a, **kw: bool(loaded) if kw.get("loaded") else "99.1",
        loadPlugin=lambda *a, **kw: loaded.append(a[0]),
    )
    monkeypatch.setitem(sys.modules, "arnold", NS(AiGetVersion=lambda: (b"99", b"1", b"0", b"0")))
    target = maya_adapter.ensure_environment()
    assert target["host_version"] == "2030"
    assert target["version"] == "99.1.0.0"
    assert loaded == ["mtoa"]


def test_maya_plugin_load_failure_is_not_hidden(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("MtoA 无法加载")

    maya_commands(
        monkeypatch, about=lambda **kw: "2030", pluginInfo=lambda *a, **kw: False, loadPlugin=fail
    )
    with pytest.raises(RuntimeError, match="MtoA 无法加载"):
        maya_adapter.ensure_environment()


def rule(kind="float", codec="identity"):
    """最小原生绑定，输出也必须被核验而不能仅检查写入端口。"""
    return {
        "native": {"id": "Shader", "properties": {}},
        "inputs": {"value": {"type": kind, "native": {"id": "value", "codec": codec}}},
        "outputs": {"out": {"type": "float", "native": {"id": "out"}}},
    }


@pytest.mark.parametrize(
    "kind,codec,actual",
    [("float", "identity", "float"), ("float", "gray", "float3"), ("color", "identity", "double3")],
)
def test_maya_checks_current_attribute_types(monkeypatch, kind, codec, actual):
    maya_commands(
        monkeypatch,
        attributeQuery=lambda *a, **kw: True,
        getAttr=lambda plug, **kw: actual if plug.endswith(".value") else "float",
    )
    maya_adapter.validate_node_interface("node", rule(kind, codec))


@pytest.mark.parametrize("missing", [True, False])
def test_maya_rejects_missing_output_or_changed_input_type(monkeypatch, missing):
    maya_commands(
        monkeypatch,
        attributeQuery=lambda attr, **kw: not (missing and attr == "out"),
        getAttr=lambda plug, **kw: "float" if missing else "string",
    )
    with pytest.raises(ValidationError) as exc:
        maya_adapter.validate_node_interface("node", rule())
    assert exc.value.code == "NATIVE_INTERFACE_MISMATCH"


@pytest.fixture
def blender_adapter(monkeypatch):
    """用独立模块名加载 Blender 适配器，测试结束不污染其他宿主替身。"""
    monkeypatch.setitem(sys.modules, "bpy", NS(types=NS(NodeSocket=object)))
    path = Path(__file__).resolve().parents[1] / "plugins/blender/adapter.py"
    name = "plugins.blender._compatibility_test"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


def test_blender_checks_socket_types_and_missing_outputs(blender_adapter):
    node = NS(
        inputs=[NS(identifier="value", type="VALUE")], outputs=[NS(identifier="out", type="VALUE")]
    )
    blender_adapter.validate_node_interface(node, rule())
    node.inputs[0].type = "VECTOR"
    with pytest.raises(ValidationError, match="类型"):
        blender_adapter.validate_node_interface(node, rule())
    node.inputs[0].type = "VALUE"
    node.outputs.clear()
    with pytest.raises(ValidationError, match="不存在"):
        blender_adapter.validate_node_interface(node, rule())


def test_cinema4d_checks_all_declared_ports(monkeypatch):
    from plugins.cinema4d import adapter

    calls = []

    def port(node, identifier, output=False):
        calls.append((identifier, output))
        if identifier == "out":
            raise ValidationError("原生端口不存在", "C4D_PORT_MISSING")

    monkeypatch.setattr(adapter, "port", port)
    with pytest.raises(ValidationError, match="不存在"):
        adapter.validate_node_interface(NS(), rule())
    assert calls == [("value", False), ("out", True)]


def test_cinema4d_projection_checks_owner_output_before_helper_is_needed(monkeypatch):
    from plugins.cinema4d import adapter

    calls = []
    monkeypatch.setattr(
        adapter, "port", lambda node, key, output=False: calls.append((key, output))
    )
    binding = rule()
    binding["outputs"]["out"]["native"] = {
        "id": "helper.alpha",
        "properties": {
            "output_projection": {
                "source": "image.rgba",
                "asset": "splitter",
                "input": "helper.input",
            }
        },
    }
    adapter.validate_node_interface(NS(), binding)
    assert calls == [("value", False), ("image.rgba", True)]
