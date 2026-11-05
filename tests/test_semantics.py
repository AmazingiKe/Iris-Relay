"""通用功能定义、原生模式识别与多实现选择的公开 API 回归。"""

import json
import subprocess
import sys
from copy import deepcopy

import pytest

from iris_relay import RelayAPI


def request(api, operation, **payload):
    return api.handle(
        {"api_version": "1", "request_id": "语义测试", "operation": operation, "payload": payload}
    )


def modes(profiles):
    profile = profiles[0]
    image = profile["nodes"]["Bitmap"]
    image["native"] = {"id": "MultiTexture", "properties": {"mode": "image"}}
    other = deepcopy(image)
    other["native"]["properties"]["mode"] = "alternate"
    profile["nodes"]["AlternateBitmap"] = other
    image["target_default"] = True
    return profile


def test_semantics_are_queryable_without_dcc_or_profiles():
    result = request(RelayAPI(), "semantics")
    assert result["status"] == "ok", result
    definitions = result["result"]["nodes"]
    assert definitions["bump.height"]["inputs"]["height"]["type"] == "float"
    assert definitions["normal.tangent"]["inputs"]["normal.color"]["type"] == "color"
    assert definitions["texture.image"]["inputs"]["image"]["type"] == "texture"
    definitions.clear()
    assert request(RelayAPI(), "semantics")["result"]["nodes"]


def test_identify_uses_native_id_and_mode_not_display_name(profiles):
    profile = modes(profiles)
    api = RelayAPI([profile])
    result = request(
        api,
        "identify",
        renderer={"id": "reference.gloss", "version": "1.0"},
        native_id="MultiTexture",
        properties={"mode": "alternate"},
    )
    assert result["status"] == "ok", result
    assert result["result"]["implementation"] == "AlternateBitmap"
    assert result["result"]["semantic"] == "texture.image"


@pytest.mark.parametrize(
    "native_id,properties,code",
    [
        ("unknown", {}, "UNSUPPORTED_NODE"),
        ("MultiTexture", {}, "UNSUPPORTED_NODE_CONFIGURATION"),
        ("MultiTexture", {"mode": "noise"}, "UNSUPPORTED_NODE_CONFIGURATION"),
        (True, {}, "INVALID_REQUEST"),
        ("MultiTexture", [], "INVALID_REQUEST"),
    ],
)
def test_identify_reports_unknown_or_missing_mode(profiles, native_id, properties, code):
    api = RelayAPI([modes(profiles)])
    result = request(
        api,
        "identify",
        renderer={"id": "reference.gloss", "version": "1.0"},
        native_id=native_id,
        properties=properties,
    )
    assert result["status"] == "error"
    assert result["error"]["code"] == code


def test_ambiguous_native_match_is_never_chosen_by_order(profiles):
    profile = modes(profiles)
    profile["nodes"]["AlternateBitmap"]["native"]["properties"] = {"mode": "image"}
    result = request(
        RelayAPI([profile]),
        "identify",
        renderer={"id": "reference.gloss", "version": "1.0"},
        native_id="MultiTexture",
        properties={"mode": "image"},
    )
    assert result["error"]["code"] == "AMBIGUOUS_NATIVE_NODE"


def test_multiple_source_implementations_normalize_to_same_semantic(profiles, request_data):
    modes(profiles)
    request_data["payload"]["material"]["nodes"][1]["type"] = "AlternateBitmap"
    result = RelayAPI(profiles).handle(request_data)
    assert result["status"] == "ok", result
    assert result["result"]["intermediate"]["nodes"][1]["type"] == "texture.image"
    assert result["result"]["material"]["nodes"][1]["type"] == "Texture"


def test_target_default_is_explicit_and_ambiguity_preserves_source(profiles, request_data):
    target = profiles[1]["nodes"]
    target["OtherTexture"] = deepcopy(target["Texture"])
    before = deepcopy(request_data["payload"]["material"])
    failed = RelayAPI(profiles).handle(request_data)
    assert failed["status"] == "error"
    assert failed["result"]["source"] == before
    assert failed["result"]["material"] is None
    assert any(i["code"] == "AMBIGUOUS_TARGET_NODE" for i in failed["result"]["report"]["issues"])
    target["OtherTexture"]["target_default"] = True
    result = RelayAPI(profiles).handle(request_data)
    assert result["result"]["material"]["nodes"][1]["type"] == "OtherTexture"
    assert request_data["payload"]["material"] == before


def test_duplicate_default_is_rejected(profiles):
    profile = modes(profiles)
    profile["nodes"]["AlternateBitmap"]["target_default"] = True
    with pytest.raises(ValueError, match="默认"):
        RelayAPI(profiles)


def test_opted_in_dictionary_checks_function_and_port_contract(profiles):
    profile = profiles[0]
    profile["semantic_schema_version"] = "1"
    profile["nodes"] = {"Bitmap": profile["nodes"]["Bitmap"]}
    del profile["nodes"]["Bitmap"]["outputs"]["value"]
    RelayAPI([profile])
    for field, value in [("semantic", "texture.typo"), ("semantic", "normal.tangent")]:
        invalid = deepcopy(profile)
        invalid["nodes"]["Bitmap"][field] = value
        with pytest.raises(ValueError):
            RelayAPI([invalid])
    invalid = deepcopy(profile)
    invalid["nodes"]["Bitmap"]["inputs"]["image"]["type"] = "string"
    invalid["nodes"]["Bitmap"]["inputs"]["image"]["default"] = ""
    with pytest.raises(ValueError, match="类型"):
        RelayAPI([invalid])


def test_semantics_available_over_json_process(tmp_path, profiles):
    for index, profile in enumerate(profiles):
        (tmp_path / f"{index}.json").write_text(json.dumps(profile), encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "-m", "iris_relay", "--dictionaries", str(tmp_path)],
        input=json.dumps(
            {"api_version": "1", "request_id": "process", "operation": "semantics", "payload": {}}
        ),
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["result"]["version"] == "1"


def test_invalid_semantic_version_and_target_flag_are_rejected(profiles):
    profile = deepcopy(profiles[0])
    profile["semantic_schema_version"] = "999"
    with pytest.raises(ValueError, match="版本"):
        RelayAPI([profile])
    profile = deepcopy(profiles[0])
    profile["nodes"]["Bitmap"]["target_default"] = 1
    with pytest.raises(ValueError, match="布尔"):
        RelayAPI([profile])


def test_native_mode_cannot_confuse_boolean_and_integer(profiles):
    profile = modes(profiles)
    profile["nodes"]["Bitmap"]["native"]["properties"] = {"mode": 1}
    api = RelayAPI([profile])
    result = request(
        api,
        "identify",
        renderer={"id": "reference.gloss", "version": "1.0"},
        native_id="MultiTexture",
        properties={"mode": True},
    )
    assert result["error"]["code"] == "UNSUPPORTED_NODE_CONFIGURATION"


@pytest.mark.parametrize("mutation", ["unknown_port", "nullable", "native_not_default", "required"])
def test_semantic_port_boundaries_are_validated(profiles, mutation):
    profile = profiles[0]
    profile["semantic_schema_version"] = "1"
    image = profile["nodes"]["Bitmap"]
    profile["nodes"] = {"Bitmap": image}
    del image["outputs"]["value"]
    if mutation == "unknown_port":
        image["outputs"]["rgb"]["semantic"] = "misspelled"
    elif mutation == "nullable":
        image["inputs"]["image"]["nullable"] = True
    elif mutation == "native_not_default":
        image["inputs"]["extra"] = {
            "semantic": "native.example.extra",
            "type": "float",
            "default": 0,
        }
    else:
        image["inputs"].clear()
    with pytest.raises(ValueError):
        RelayAPI([profile])
