"""Maxon 包装对象的真值表示对象有效性，不能作为布尔端口的数据值。"""

import pytest

from plugins.cinema4d.adapter import json_value


def test_reflected_native_vector2_keeps_both_components():
    class UnknownDataType:
        x = 0.25
        y = -0.5

    assert json_value(UnknownDataType()) == [0.25, -0.5]


@pytest.mark.parametrize("literal, expected", [("false", False), ("true", True)])
def test_maxon_bool_uses_value_representation(literal, expected):
    class Bool:
        def __str__(self):
            return literal

        def __bool__(self):
            return True

    assert bool(Bool()) is True
    assert json_value(Bool()) is expected


def test_unknown_maxon_bool_representation_blocks():
    class Bool:
        def __str__(self):
            return "unreadable"

    with pytest.raises(ValueError):
        json_value(Bool())
