from copy import deepcopy

import pytest


def make_profile(renderer, glossy=False):
    """仅用于验证协议，不冒充真实渲染器的参数定义。"""
    return {
        "schema_version": "1",
        "id": renderer + ".standard",
        "version": "0.1.0",
        "renderer": {"id": renderer, "versions": ["1.0"]},
        "nodes": {
            "Surface" if glossy else "Standard": {
                "semantic": "surface.standard",
                "inputs": {
                    "tint" if glossy else "base_color": {
                        "semantic": "base_color",
                        "type": "color",
                        "default": [0.5, 0.5, 0.5],
                        "range": [0, 1],
                        "adjustable": True,
                    },
                    "gloss" if glossy else "roughness": {
                        "semantic": "roughness",
                        "type": "float",
                        "default": 0.5,
                        "range": [0, 1],
                        "transform": "one_minus" if glossy else "identity",
                        "adjustable": True,
                    },
                    "metal" if glossy else "metalness": {
                        "semantic": "metalness",
                        "type": "float",
                        "default": 0.0,
                        "range": [0, 1],
                        "adjustable": True,
                    },
                },
                "outputs": {"out": {"semantic": "surface", "type": "shader"}},
            },
            "Bitmap" if glossy else "Texture": {
                "semantic": "texture.image",
                "inputs": {
                    "image": {"semantic": "image", "type": "texture", "default": None},
                },
                "outputs": {
                    "rgb": {"semantic": "color", "type": "color"},
                    "value": {"semantic": "value", "type": "float"},
                },
            },
        },
    }


@pytest.fixture
def profiles():
    return [make_profile("reference.gloss", True), make_profile("reference.rough")]


@pytest.fixture
def material():
    return {
        "schema_version": "1",
        "name": "磨损金属",
        "renderer": {"id": "reference.gloss", "version": "1.0"},
        "nodes": [
            {
                "id": "surface",
                "type": "Surface",
                "inputs": {"tint": [0.4, 0.3, 0.2], "gloss": 0.8, "metal": 0.9},
                "outputs": ["out"],
                "metadata": {"label": "原始材质"},
            },
            {
                "id": "texture",
                "type": "Bitmap",
                "inputs": {"image": {"texture": "albedo"}},
                "outputs": ["rgb", "value"],
                "metadata": {},
            },
        ],
        "links": [
            {
                "from": {"node": "texture", "socket": "rgb"},
                "to": {"node": "surface", "socket": "tint"},
            }
        ],
        "output": {"node": "surface", "socket": "out"},
        "textures": {
            "albedo": {
                "uri": "textures/磨损.png",
                "color_space": "sRGB",
                "coordinates": {"kind": "uv", "set": "UVMap"},
                "metadata": {"udim": False},
            }
        },
        "metadata": {"asset_family": "metal-a"},
    }


@pytest.fixture
def request_data(material):
    return {
        "api_version": "1",
        "request_id": "test-001",
        "operation": "convert",
        "payload": {
            "material": deepcopy(material),
            "target": {"id": "reference.rough", "version": "1.0"},
        },
    }
