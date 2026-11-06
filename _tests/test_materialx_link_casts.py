"""原生隐式连接必须在 MaterialX 中显式表达，并保留色彩上下文。"""

import json
from copy import deepcopy
from pathlib import Path

import MaterialX as mx
import pytest

from _scripts.native_fixture import fixture
from iris_relay import RelayAPI
from iris_relay.dictionary import Dictionary
from iris_relay.model import ValidationError

ROOT = Path(__file__).resolve().parents[1]


def setup():
    profile = json.loads((ROOT / "dictionaries/blender-cycles-5.2.3.json").read_text("utf-8"))
    source = fixture(profile, "roughness.exr")
    image = next(n for n in source["nodes"] if n["type"] == "ShaderNodeTexImage")
    surface = next(n for n in source["nodes"] if n["type"] == "ShaderNodeBsdfPrincipled")
    source["links"].append(
        {
            "from": {"node": image["id"], "socket": "Color"},
            "to": {"node": surface["id"], "socket": "Roughness"},
        }
    )
    source["metadata"]["color_to_float"] = {
        "working_space": "lin_rec709_scene",
        "coefficients": [0.2126729, 0.7151522, 0.0721750],
    }
    return profile, source


def export(profile, source):
    return RelayAPI([profile]).handle(
        {
            "api_version": "1",
            "request_id": "cast",
            "operation": "materialx.export",
            "payload": {"material": source},
        }
    )


def test_color_to_float_creates_explicit_luminance_with_source_coefficients():
    profile, source = setup()
    before = deepcopy(source)
    response = export(profile, source)
    assert response["status"] == "warning", response
    doc = mx.createDocument()
    mx.readFromXmlString(doc, response["result"]["document"])
    luma = next(n for n in doc.getNodes() if n.getCategory() == "luminance")
    assert list(luma.getInput("lumacoeffs").getValue()) == pytest.approx(
        source["metadata"]["color_to_float"]["coefficients"]
    )
    assert luma.getInput("in").getNodeName()
    surface = next(n for n in doc.getNodes() if n.getCategory() == "standard_surface")
    extract = surface.getInput("specular_roughness").getConnectedNode()
    assert extract.getCategory() == "extract"
    assert extract.getInput("index").getValue() == 0
    assert extract.getInput("in").getConnectedNode() == luma
    assert source == before == response["result"]["source"]
    assert any(
        i["code"] == "IMPLICIT_CAST_EXPLICIT" for i in response["result"]["report"]["issues"]
    )


@pytest.mark.parametrize(
    "context",
    [
        None,
        {},
        {"working_space": "lin_ap1_scene", "coefficients": [0.2, 0.7, 0.1]},
        {"working_space": "lin_rec709_scene", "coefficients": [1, 0]},
        {"working_space": "lin_rec709_scene", "coefficients": [1, 0, -1]},
    ],
)
def test_missing_or_unsupported_context_blocks_without_losing_source(context):
    profile, source = setup()
    source["metadata"]["color_to_float"] = context
    response = export(profile, source)
    assert response["status"] == "error"
    assert response["result"]["document"] is None
    assert response["result"]["source"] == source


def test_no_dictionary_rule_still_rejects_implicit_cast():
    profile, source = setup()
    profile.pop("materialx_export", None)
    response = export(profile, source)
    assert response["status"] == "error"
    assert any(i["code"] == "LINK_TYPE_MISMATCH" for i in response["result"]["report"]["issues"])


def test_exported_luminance_does_not_claim_target_conversion_support():
    profile, source = setup()
    api = RelayAPI([profile])
    document = export(profile, source)["result"]["document"]
    response = api.handle(
        {
            "api_version": "1",
            "request_id": "target",
            "operation": "materialx.convert",
            "payload": {"document": document, "target": source["renderer"]},
        }
    )
    assert response["status"] == "error"
    assert response["result"]["material"] is None
    assert response["result"]["source"] == document
    old = api.handle(
        {
            "api_version": "1",
            "request_id": "old",
            "operation": "convert",
            "payload": {"material": source, "target": source["renderer"]},
        }
    )
    assert old["status"] == "error"
    assert any(i["code"] == "LINK_TYPE_MISMATCH" for i in old["result"]["report"]["issues"])


def test_invalid_link_conversion_rule_rejected():
    profile, _ = setup()
    profile["materialx_export"] = {"color_to_float": "take_red"}
    with pytest.raises(ValidationError):
        Dictionary.parse(profile)


def test_legacy_principled_configuration_is_explicit_and_nondefault_sss_stays_blocked():
    profile, source = setup()
    dictionary = Dictionary.parse(profile)
    match = dictionary.identify(
        "ShaderNodeBsdfPrincipled",
        {
            "distribution": "GGX",
            "subsurface_method": "BURLEY",
        },
    )
    shader = next(n for n in source["nodes"] if n["type"] == "ShaderNodeBsdfPrincipled")
    shader["type"] = match["implementation"]
    assert export(profile, source)["status"] == "warning"
    shader["inputs"]["Subsurface Weight"] = 0.1
    assert export(profile, source)["status"] == "error"
    assert dictionary.target_node("surface.standard")[0] == "ShaderNodeBsdfPrincipled"


def test_inactive_sss_scale_is_reported_but_active_or_connected_scale_is_blocked():
    profile, source = setup()
    shader = next(n for n in source["nodes"] if n["type"] == "ShaderNodeBsdfPrincipled")
    shader["inputs"]["Subsurface Weight"] = 0.0
    shader["inputs"]["Subsurface Scale"] = 0.05
    response = export(profile, source)
    assert response["status"] == "warning"
    assert any(
        i["code"] == "INACTIVE_INPUT_OMITTED" for i in response["result"]["report"]["issues"]
    )
    shader["inputs"]["Subsurface Weight"] = 0.2
    assert export(profile, source)["status"] == "error"
    shader["inputs"]["Subsurface Weight"] = 0.0
    source["links"][0]["to"] = {"node": shader["id"], "socket": "Subsurface Scale"}
    assert export(profile, source)["status"] == "error"


@pytest.mark.parametrize(
    "condition",
    [
        {"input": [], "equals": 0},
        {"input": "unknown", "equals": 0},
        {"input": "Subsurface Scale", "equals": 0},
        {"input": "Subsurface Weight", "equals": "0"},
    ],
)
def test_invalid_inactive_conditions_rejected(condition):
    profile, _ = setup()
    profile["nodes"]["ShaderNodeBsdfPrincipled"]["inputs"]["Subsurface Scale"]["inactive_when"] = (
        condition
    )
    with pytest.raises(ValidationError):
        Dictionary.parse(profile)
