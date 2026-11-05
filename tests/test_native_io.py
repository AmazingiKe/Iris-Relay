import pytest

from iris_relay import RelayAPI
from iris_relay.native import decode_value, describe, encode_value, identify


def test_srgb_codec_preserves_linear_color_roundtrip():
    rule = {"native": {"codec": "srgb"}}
    assert decode_value([0.5, 0.5, 0.5], rule) == pytest.approx([0.21404114] * 3)
    linear = [0.05, 0.25, 0.8]
    assert decode_value(encode_value(linear, rule), rule) == pytest.approx(linear)


def test_rgba_codec_does_not_silently_drop_opacity():
    rule = {"native": {"codec": "rgb"}}
    assert decode_value([0.1, 0.2, 0.3, 1], rule) == [0.1, 0.2, 0.3]
    assert encode_value([0.1, 0.2, 0.3], rule) == [0.1, 0.2, 0.3, 1]
    with pytest.raises(ValueError):
        decode_value([0.1, 0.2, 0.3, 0.5], rule)


def test_implicit_normal_remains_implicit():
    rule = {"native": {"codec": "normal"}}
    assert decode_value([0, 0, 0], rule) is None
    assert decode_value([0, 0, 1], rule) == [0, 0, 1]
    assert encode_value(None, rule) is None


def test_describe_helper_uses_public_api_and_reports_missing_version(profiles):
    api = RelayAPI(profiles)
    assert describe(api, {"id": "reference.gloss", "version": "1.0"})["nodes"]
    with pytest.raises(ValueError, match="没有对应字典"):
        describe(api, {"id": "reference.gloss", "version": "missing"})


def test_native_identity_is_detached_and_bool_is_explicit():
    value = [1, 2, 3]
    assert decode_value(1, {"native": {"codec": "bool"}}) is True
    copied = decode_value(value, {})
    copied[0] = 9
    assert value == [1, 2, 3]


def test_gray_height_codec_does_not_guess_a_channel():
    rule = {"native": {"codec": "gray"}}
    assert encode_value(0.4, rule) == [0.4, 0.4, 0.4]
    assert decode_value([0.4, 0.4, 0.4], rule) == 0.4
    assert decode_value(0.4, rule) == 0.4
    with pytest.raises(ValueError):
        decode_value([0.2, 0.4, 0.6], rule)
    with pytest.raises(ValueError):
        encode_value([0.4, 0.4, 0.4], rule)


def test_identify_helper_uses_service_and_preserves_error_code(profiles):
    profiles[0]["nodes"]["Bitmap"]["native"] = {"id": 123, "properties": {"mode": 0}}
    api = RelayAPI(profiles)
    renderer = {"id": "reference.gloss", "version": "1.0"}
    assert identify(api, renderer, 123, {"mode": 0})["implementation"] == "Bitmap"
    with pytest.raises(ValueError) as caught:
        identify(api, renderer, 123, {"mode": 1})
    assert caught.value.code == "UNSUPPORTED_NODE_CONFIGURATION"
