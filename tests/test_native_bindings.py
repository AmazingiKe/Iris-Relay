from copy import deepcopy

import pytest

from iris_relay import RelayAPI


def bind(profile, host, system):
    profile["host"] = {"id": host, "versions": ["2025.3.2"], "node_system": system}
    profile["description"] = "实际宿主绑定协议测试"
    for key, node in profile["nodes"].items():
        node["native"] = {"id": key, "label": key, "properties": {}}
        for port, rule in node["inputs"].items():
            rule["native"] = {"id": port, "kind": "socket", "codec": "identity"}
        for port, rule in node["outputs"].items():
            rule["native"] = {"id": port, "kind": "socket", "codec": "identity"}
    return profile


def ref(renderer, host="cinema4d", system="redshift.maxon"):
    return {
        "id": renderer,
        "version": "1.0",
        "host": host,
        "host_version": "2025.3.2",
        "node_system": system,
    }


def test_same_renderer_is_resolved_by_host_and_node_system(profiles, request_data):
    first = bind(profiles[0], "cinema4d", "redshift.maxon")
    second = bind(deepcopy(profiles[0]), "maya", "dependency_graph")
    api = RelayAPI([first, second, profiles[1]])
    request_data["payload"]["material"]["renderer"] = ref("reference.gloss")
    assert api.handle(request_data)["status"] == "ok"
    request_data["payload"]["material"]["renderer"]["host_version"] = "2024"
    assert api.handle(request_data)["status"] == "error"
    request_data["payload"]["material"]["renderer"] = {"id": "reference.gloss", "version": "1.0"}
    assert api.handle(request_data)["status"] == "error"


def test_describe_returns_numeric_native_codes_without_exposing_mutable_registry(profiles):
    profile = bind(profiles[0], "cinema4d", "arnold.gvnodes")
    profile["nodes"]["Surface"]["native"]["id"] = 314733630
    profile["nodes"]["Surface"]["inputs"]["tint"]["native"]["id"] = 1044225467
    api = RelayAPI([profile])
    request = {
        "api_version": "1",
        "request_id": "schema",
        "operation": "describe",
        "payload": {"renderer": ref("reference.gloss", system="arnold.gvnodes")},
    }
    response = api.handle(request)
    assert response["status"] == "ok"
    schema = response["result"]["dictionary"]
    assert schema["nodes"]["Surface"]["native"]["id"] == 314733630
    schema["nodes"].clear()
    assert api.handle(request)["result"]["dictionary"]["nodes"]


def test_optional_native_default_is_retained_same_host_but_can_be_omitted_cross_host(
    profiles, request_data
):
    source = profiles[0]["nodes"]["Surface"]["inputs"]
    source["samples"] = {
        "semantic": "reference.samples",
        "type": "float",
        "default": 16,
        "omit_if_default": True,
    }
    request_data["payload"]["material"]["nodes"][0]["inputs"]["samples"] = 16
    result = RelayAPI(profiles).handle(request_data)
    assert result["status"] != "error"
    assert result["result"]["source"]["nodes"][0]["inputs"]["samples"] == 16
    request_data["payload"]["material"]["nodes"][0]["inputs"]["samples"] = 32
    assert RelayAPI(profiles).handle(request_data)["status"] == "error"


def test_reversible_scale_and_scalar_color_transforms(profiles, request_data):
    source = profiles[0]["nodes"]["Surface"]["inputs"]["metal"]
    source.update(transform={"op": "scale", "factor": 2}, range=[0, 0.5], default=0)
    request_data["payload"]["material"]["nodes"][0]["inputs"]["metal"] = 0.4
    api = RelayAPI(profiles)
    response = api.handle(request_data)
    assert response["result"]["material"]["nodes"][0]["inputs"]["metalness"] == pytest.approx(0.8)
    request_data["payload"] = {
        "material": response["result"]["material"],
        "target": {"id": "reference.gloss", "version": "1.0"},
    }
    assert api.handle(request_data)["result"]["material"]["nodes"][0]["inputs"][
        "metal"
    ] == pytest.approx(0.4)


def test_cross_renderer_fidelity_warning_is_explicit(profiles, request_data):
    profiles[0]["nodes"]["Surface"]["approximation"] = "不同 BSDF，需渲染对照"
    result = RelayAPI(profiles).handle(request_data)
    assert result["status"] == "warning"
    assert any(
        i["code"] == "SHADING_MODEL_APPROXIMATION" for i in result["result"]["report"]["issues"]
    )


@pytest.mark.parametrize(
    "native",
    [
        {"id": True, "label": "bad", "properties": {}},
        {"id": "node", "label": "node", "properties": {}, "execute": "code"},
    ],
)
def test_native_binding_is_data_not_executable_code(profiles, native):
    profiles[0]["nodes"]["Surface"]["native"] = native
    with pytest.raises(ValueError):
        RelayAPI(profiles)


def test_unused_known_output_can_be_omitted_but_connected_output_cannot(profiles, request_data):
    del profiles[1]["nodes"]["Texture"]["outputs"]["value"]
    response = RelayAPI(profiles).handle(request_data)
    assert response["status"] != "error"
    assert response["result"]["material"]["nodes"][1]["outputs"] == ["rgb"]
    request_data["payload"]["material"]["links"][0]["from"]["socket"] = "value"
    request_data["payload"]["material"]["links"][0]["to"]["socket"] = "metal"
    assert RelayAPI(profiles).handle(request_data)["status"] == "error"


def test_nullable_normal_input_is_preserved(profiles, request_data):
    for profile in profiles:
        surface = next(n for n in profile["nodes"].values() if n["semantic"] == "surface.standard")
        surface["inputs"]["normal"] = {
            "semantic": "normal",
            "type": "vector",
            "default": None,
            "nullable": True,
        }
    assert (
        RelayAPI(profiles).handle(request_data)["result"]["material"]["nodes"][0]["inputs"][
            "normal"
        ]
        is None
    )


def test_scalar_opacity_to_color_and_non_gray_rejection(profiles, request_data):
    profiles[0]["nodes"]["Surface"]["inputs"]["opacity"] = {
        "semantic": "opacity",
        "type": "float",
        "default": 1,
        "transform": "scalar_to_color",
    }
    profiles[1]["nodes"]["Standard"]["inputs"]["opacity"] = {
        "semantic": "opacity",
        "type": "color",
        "default": [1, 1, 1],
    }
    api = RelayAPI(profiles)
    response = api.handle(request_data)
    target = response["result"]["material"]
    assert target["nodes"][0]["inputs"]["opacity"] == [1, 1, 1]
    target["nodes"][0]["inputs"]["opacity"] = [1, 0.5, 0]
    request_data["payload"] = {
        "material": target,
        "target": {"id": "reference.gloss", "version": "1.0"},
    }
    assert api.handle(request_data)["status"] == "error"
