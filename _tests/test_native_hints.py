"""宿主保存的实现提示只消解歧义，不能绕过当前原生类型与配置校验。"""

from copy import deepcopy

from _scripts.native_fixture import renderer
from _tests.test_maya_materialx import maya_profile
from iris_relay import RelayAPI


def identify(native_id, properties, hint=None):
    profile = maya_profile()
    current = deepcopy(profile["nodes"]["constant_color3"]["native"]["properties"])
    current.update(properties)
    payload = {"renderer": renderer(profile), "native_id": native_id, "properties": current}
    if hint is not None:
        payload["implementation_hint"] = hint
    return RelayAPI([deepcopy(profile)]).handle(
        {"api_version": "1", "request_id": "hint", "operation": "identify", "payload": payload}
    )


def test_hint_resolves_only_a_current_matching_implementation():
    result = identify("aiAdd", {"input2": [0, 0, 0]}, "constant_color3")
    assert result["status"] == "ok", result
    assert result["result"]["implementation"] == "constant_color3"


def test_without_hint_ambiguous_native_type_stays_blocked():
    result = identify("aiAdd", {"input2": [0, 0, 0]})
    assert result["error"]["code"] == "AMBIGUOUS_NATIVE_NODE"


def test_stale_hint_cannot_override_changed_native_properties():
    result = identify("aiAdd", {"input2": [0, 1, 0]}, "constant_color3")
    assert result["status"] == "error"
    assert result["error"]["code"] == "NATIVE_HINT_MISMATCH"


def test_forged_hint_cannot_override_native_type():
    result = identify("aiMultiply", {}, "constant_color3")
    assert result["error"]["code"] == "NATIVE_HINT_MISMATCH"


def test_native_read_helper_rejects_crossed_response():
    import pytest

    from iris_relay.native import describe

    class Broken:
        def handle(self, request):
            return {
                "api_version": "1",
                "request_id": "another request",
                "status": "ok",
                "result": {"dictionary": {"nodes": {}}},
            }

    with pytest.raises(ValueError, match="请求 ID"):
        describe(Broken(), {})
