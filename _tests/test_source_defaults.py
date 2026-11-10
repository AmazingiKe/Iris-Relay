"""未标记原生图的选择必须由字典明确声明并由调用者启用，不能按枚举顺序猜测。"""

from copy import deepcopy

from _tests.test_semantics import modes, request
from iris_relay import RelayAPI


def test_source_default_is_opt_in_and_cannot_bypass_current_properties(profiles):
    data = modes(profiles)
    data["nodes"]["Alias"] = deepcopy(data["nodes"]["Bitmap"])
    data["nodes"]["Alias"].pop("target_default", None)
    data["nodes"]["Bitmap"]["source_default"] = True
    api = RelayAPI([data])
    args = {
        "renderer": {"id": "reference.gloss", "version": "1.0"},
        "native_id": "MultiTexture",
        "properties": {"mode": "image"},
    }
    assert request(api, "identify", **args)["error"]["code"] == "AMBIGUOUS_NATIVE_NODE"
    response = request(api, "identify", prefer_source_default=True, **args)
    assert response["result"]["implementation"] == "Bitmap", response
    response = request(
        api, "identify", prefer_source_default=True, **dict(args, properties={"mode": "unknown"})
    )
    assert response["error"]["code"] == "UNSUPPORTED_NODE_CONFIGURATION"
    response = request(
        api, "identify", prefer_source_default=True, implementation_hint="Alias", **args
    )
    assert response["result"]["implementation"] == "Alias"
