"""色彩语义在资源表中保留，原生 ID 只在公开绑定辅助接口中编码。"""

import pytest

from iris_relay.native import resource_color_space


def test_native_resource_color_space_roundtrip():
    rule = {
        "native": {
            "properties": {
                "color_spaces": {
                    "Raw": "RS_INPUT_COLORSPACE_RAW",
                    "sRGB": "RS_INPUT_COLORSPACE_SRGB",
                }
            }
        }
    }
    for name in ("Raw", "sRGB"):
        value = resource_color_space(name, rule)
        assert value.startswith("RS_INPUT_COLORSPACE_")
        assert resource_color_space(value, rule, encode=False) == name
    with pytest.raises(ValueError, match="尚未核验"):
        resource_color_space("unknown", rule)


def test_ambiguous_reverse_color_binding_is_rejected():
    rule = {"native": {"properties": {"color_spaces": {"a": "same", "b": "same"}}}}
    with pytest.raises(ValueError, match="不唯一"):
        resource_color_space("same", rule, encode=False)
