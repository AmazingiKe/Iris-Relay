"""批量请求复用已核验字典扩展，返回副本防止任一请求污染后续材质。"""

from _tests.test_materialx_hsv import profile
from iris_relay.dictionary import Dictionary
from iris_relay.materialx.core import MaterialXCore
from iris_relay.materialx.extensions import load_extensions


def test_repeated_extensions_are_validated_once_and_remain_request_local(monkeypatch):
    core = MaterialXCore()
    data = Dictionary.parse(profile())
    original = core.flatten
    calls = []

    def checked(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(core, "flatten", checked)
    first = load_extensions(core, data)
    first.removeNodeDef("ND_iris_blender_hsv_color3")
    second = load_extensions(core, data)
    assert second.getNodeDef("ND_iris_blender_hsv_color3") is not None
    assert len(calls) == 1
