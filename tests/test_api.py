from copy import deepcopy

import pytest

from iris_relay.api import RelayAPI


def test_conversion_preserves_graph_resources_and_source(profiles, request_data):
    original = deepcopy(request_data)
    api = RelayAPI(profiles)
    response = api.handle(request_data)
    assert response["status"] == "ok"
    result = response["result"]
    assert result["source"] == original["payload"]["material"]
    assert result["material"]["nodes"][0]["inputs"]["roughness"] == pytest.approx(0.2)
    assert result["intermediate"]["nodes"][0]["type"] == "surface.standard"
    assert result["material"]["links"][0]["to"]["socket"] == "base_color"
    assert result["material"]["textures"] == original["payload"]["material"]["textures"]
    assert result["material"]["metadata"] == original["payload"]["material"]["metadata"]
    assert result["report"]["calibration"]["status"] == "not_requested"
    assert len(result["report"]["dictionary_fingerprints"]["source"]) == 64
    assert request_data == original
    assert api.handle(request_data) == response


def test_roundtrip_preserves_material_meaning(profiles, request_data):
    api = RelayAPI(profiles)
    target = api.handle(request_data)["result"]["material"]
    request_data["payload"] = {
        "material": target,
        "target": {"id": "reference.gloss", "version": "1.0"},
    }
    back = api.handle(request_data)["result"]["material"]
    assert back["nodes"][0]["inputs"]["gloss"] == pytest.approx(0.8)
    assert back["links"][0]["to"]["socket"] == "tint"


@pytest.mark.parametrize("field,value", [("api_version", "2"), ("operation", "render_scene")])
def test_api_rejects_unknown_contract(profiles, request_data, field, value):
    request_data[field] = value
    response = RelayAPI(profiles).handle(request_data)
    assert response["status"] == "error"
    assert response["result"] is None


def test_capabilities_and_validation_share_envelope(profiles, material):
    api = RelayAPI(profiles)
    response = api.handle(
        {"api_version": "1", "request_id": "caps", "operation": "capabilities", "payload": {}}
    )
    assert len(response["result"]["renderers"]) == 2
    assert response["result"]["calibration_available"] is False
    response = api.handle(
        {
            "api_version": "1",
            "request_id": "valid",
            "operation": "validate",
            "payload": {"material": material},
        }
    )
    assert response["status"] == "ok"
    assert response["request_id"] == "valid"


@pytest.mark.parametrize(
    "change,code",
    [
        (lambda m: m["nodes"][0].update(type="UnknownShader"), "UNSUPPORTED_NODE"),
        (lambda m: m["nodes"][0]["inputs"].update(secret=1), "UNSUPPORTED_INPUT"),
        (lambda m: m["renderer"].update(version="9.0"), "UNSUPPORTED_RENDERER"),
        (lambda m: m["nodes"][0]["inputs"].update(gloss=2), "INVALID_VALUE"),
    ],
)
def test_unsupported_material_is_reported_without_loss(profiles, request_data, change, code):
    change(request_data["payload"]["material"])
    source = deepcopy(request_data["payload"]["material"])
    response = RelayAPI(profiles).handle(request_data)
    assert response["status"] == "error"
    if response["result"] is not None:
        assert response["result"]["source"] == source
        assert response["result"]["material"] is None
        assert code in {issue["code"] for issue in response["result"]["report"]["issues"]}
    else:
        assert response["error"]["code"] == code


def test_connected_nonidentity_transform_is_not_silently_copied(profiles, request_data):
    mat = request_data["payload"]["material"]
    mat["links"] = [
        {
            "from": {"node": "texture", "socket": "value"},
            "to": {"node": "surface", "socket": "gloss"},
        }
    ]
    response = RelayAPI(profiles).handle(request_data)
    assert response["status"] == "error"
    assert any(
        i["code"] == "LINK_TRANSFORM_UNSUPPORTED" for i in response["result"]["report"]["issues"]
    )


def test_missing_model_keeps_baseline_and_reports_unavailable(profiles, request_data):
    request_data["payload"]["calibrate"] = True
    response = RelayAPI(profiles).handle(request_data)
    assert response["status"] == "warning"
    assert response["result"]["material"] is not None
    assert response["result"]["report"]["calibration"]["status"] == "unavailable"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda m: m.update(exposure=2),
        lambda m: m["nodes"].append(deepcopy(m["nodes"][0])),
        lambda m: m["links"][0]["from"].update(node="missing"),
        lambda m: m["links"][0]["to"].update(socket="missing"),
        lambda m: m["links"].append(deepcopy(m["links"][0])),
        lambda m: m["nodes"][0]["inputs"].update(gloss=float("nan")),
        lambda m: m["nodes"][1]["inputs"].update(image={"texture": "missing"}),
        lambda m: m.update(output={"node": "surface", "socket": "missing"}),
    ],
)
def test_invalid_graph_is_rejected(profiles, request_data, mutation):
    mutation(request_data["payload"]["material"])
    assert RelayAPI(profiles).handle(request_data)["status"] == "error"


def test_dictionary_rejects_ambiguous_or_executable_rules(profiles):
    profiles[0]["nodes"]["Surface"]["inputs"]["gloss"]["transform"] = "eval(value)"
    with pytest.raises(ValueError):
        RelayAPI(profiles)


def test_registry_rejects_overlapping_versions(profiles):
    with pytest.raises(ValueError):
        RelayAPI([profiles[0], profiles[0]])


def test_request_and_dictionary_mutation_cannot_affect_core(profiles, request_data):
    api = RelayAPI(profiles)
    profiles[0]["nodes"].clear()
    result = api.handle(request_data)
    result["result"]["material"]["nodes"].clear()
    assert len(api.handle(request_data)["result"]["material"]["nodes"]) == 2


def test_layered_material_preserves_shader_connections(profiles, request_data):
    for profile in profiles:
        profile["nodes"]["Mix"] = {
            "semantic": "surface.mix",
            "inputs": {
                "a": {"semantic": "a", "type": "shader", "default": None},
                "b": {"semantic": "b", "type": "shader", "default": None},
                "weight": {
                    "semantic": "weight",
                    "type": "float",
                    "default": 0.5,
                    "range": [0, 1],
                    "adjustable": True,
                },
            },
            "outputs": {"out": {"semantic": "surface", "type": "shader"}},
        }
    mat = request_data["payload"]["material"]
    second = deepcopy(mat["nodes"][0])
    second["id"] = "second"
    second["inputs"]["metal"] = 0
    mat["nodes"].extend(
        [
            second,
            {
                "id": "mix",
                "type": "Mix",
                "inputs": {"a": None, "b": None, "weight": 0.7},
                "outputs": ["out"],
            },
        ]
    )
    mat["links"].extend(
        [
            {"from": {"node": "surface", "socket": "out"}, "to": {"node": "mix", "socket": "a"}},
            {"from": {"node": "second", "socket": "out"}, "to": {"node": "mix", "socket": "b"}},
        ]
    )
    mat["output"] = {"node": "mix", "socket": "out"}
    response = RelayAPI(profiles).handle(request_data)
    assert response["status"] == "ok"
    assert response["result"]["material"]["output"] == mat["output"]
    assert len(response["result"]["material"]["links"]) == 3


def test_cycle_and_connection_type_mismatch_are_rejected(profiles, request_data):
    mat = request_data["payload"]["material"]
    mat["links"][0]["to"]["socket"] = "metal"
    response = RelayAPI(profiles).handle(request_data)
    assert any(i["code"] == "LINK_TYPE_MISMATCH" for i in response["result"]["report"]["issues"])
    mat["links"][0]["from"] = {"node": "surface", "socket": "out"}
    assert "循环" in RelayAPI(profiles).handle(request_data)["error"]["message"]


def test_target_default_is_visible_and_target_unsupported_input_blocks(profiles, request_data):
    target = profiles[1]["nodes"]["Standard"]["inputs"]
    target["coat"] = {"semantic": "coat", "type": "float", "default": 0.0, "range": [0, 1]}
    response = RelayAPI(profiles).handle(request_data)
    assert response["status"] == "warning"
    assert any(i["code"] == "TARGET_DEFAULT" for i in response["result"]["report"]["issues"])
    del target["metalness"]
    response = RelayAPI(profiles).handle(request_data)
    assert response["status"] == "error"
    assert response["result"]["material"] is None


def test_defaults_are_decoded_and_target_range_is_validated(profiles, request_data):
    del request_data["payload"]["material"]["nodes"][0]["inputs"]["gloss"]
    target = profiles[1]["nodes"]["Standard"]["inputs"]["roughness"]
    target.update(range=[0, 0.3], default=0.1)
    response = RelayAPI(profiles).handle(request_data)
    assert response["result"]["intermediate"]["nodes"][0]["inputs"]["roughness"] == 0.5
    assert response["result"]["material"] is None


@pytest.mark.parametrize(
    "envelope",
    [
        None,
        [],
        {},
        {"api_version": "1"},
        {"api_version": "1", "request_id": "x", "operation": [], "payload": {}},
        {"request_id": 1},
    ],
)
def test_invalid_envelopes_return_errors_instead_of_exceptions(profiles, envelope):
    assert RelayAPI(profiles).handle(envelope)["status"] == "error"
