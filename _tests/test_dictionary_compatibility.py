"""软件升级不应阻断共享字典；真实接口错误及显式限制仍须拒绝。"""

import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from iris_relay import RelayAPI
from iris_relay.dictionary import Dictionary, Registry
from iris_relay.native import request
from iris_relay.protocol import ValidationError
from iris_relay.transport import JsonCommandService

ROOT = Path(__file__).resolve().parents[1]


def shared(profile):
    """同一组测试规则跨软件版本使用，保留字典内容版本。"""
    profile = deepcopy(profile)
    profile["renderer"].pop("versions", None)
    profile["host"] = {"id": "test_host", "node_system": "test.nodes"}
    profile["evidence"] = {"verified_environments": [reference(profile, "1.0", "2022")]}
    return profile


def reference(profile, version="99.1", host_version="2030"):
    """构造不同于验证环境的实际运行引用，不伪造实机验证。"""
    return {
        "id": profile["renderer"]["id"],
        "version": version,
        "host": profile["host"]["id"],
        "host_version": host_version,
        "node_system": profile["host"]["node_system"],
    }


def test_shared_dictionary_preserves_graph_across_versions(profiles, request_data):
    profiles = [shared(p) for p in profiles]
    source = request_data["payload"]["material"]
    source["renderer"] = reference(profiles[0])
    request_data["payload"]["target"] = reference(profiles[1], "88.2", "2028")
    before = deepcopy(request_data)
    result = RelayAPI(profiles).handle(request_data)
    assert result["status"] == "ok", result
    converted = result["result"]["material"]
    assert converted["renderer"] == before["payload"]["target"]
    assert converted["textures"] == source["textures"]
    assert converted["links"][0]["to"]["socket"] == "base_color"
    assert converted["nodes"][0]["inputs"]["roughness"] == pytest.approx(0.2)
    assert result["result"]["source"] == source
    assert request_data == before


def test_host_independent_dictionary_can_omit_version_limits(profiles, request_data):
    for profile in profiles:
        profile["renderer"].pop("versions")
    request_data["payload"]["material"]["renderer"]["version"] = "new"
    request_data["payload"]["target"]["version"] = "different"
    assert RelayAPI(profiles).handle(request_data)["status"] == "ok"


@pytest.mark.parametrize("key", ["id", "host", "node_system"])
def test_shared_dictionary_never_crosses_identity(profiles, key):
    profile = shared(profiles[0])
    target = reference(profile)
    target[key] = "different"
    with pytest.raises(ValidationError, match="没有对应字典"):
        Registry([profile]).get(target)


@pytest.mark.parametrize("dimension,field", [("renderer", "version"), ("host", "host_version")])
def test_optional_version_restrictions_remain_explicit(profiles, dimension, field):
    profile = shared(profiles[0])
    profile[dimension]["versions"] = ["allowed"]
    registry = Registry([profile])
    target = reference(profile)
    with pytest.raises(ValidationError):
        registry.get(target)
    target[field] = "allowed"
    assert registry.get(target).data == profile


@pytest.mark.parametrize("reverse", [False, True])
def test_shared_and_restricted_overlap_is_rejected_in_any_order(profiles, reverse):
    common = shared(profiles[0])
    restricted = deepcopy(common)
    restricted["id"] += ".restricted"
    restricted["renderer"]["versions"] = ["1"]
    items = [common, restricted]
    with pytest.raises(ValidationError, match="重叠"):
        Registry(list(reversed(items)) if reverse else items)


def test_disjoint_explicit_restrictions_resolve_deterministically(profiles):
    first = shared(profiles[0])
    second = deepcopy(first)
    first["host"]["versions"] = ["2022"]
    second["host"]["versions"] = ["2030"]
    second["id"] += ".other"
    registry = Registry([first, second])
    assert registry.get(reference(first, host_version="2022")).data == first
    assert registry.get(reference(second)).data == second


@pytest.mark.parametrize("dimension", ["renderer", "host"])
@pytest.mark.parametrize("versions", [[], "1", [""], [1], ["1", "1"], ["*"]])
def test_ambiguous_version_lists_are_not_wildcards(profiles, dimension, versions):
    profile = shared(profiles[0])
    profile[dimension]["versions"] = versions
    with pytest.raises(ValidationError):
        Dictionary.parse(profile)


def test_capabilities_separate_evidence_from_matching(profiles):
    profile = shared(profiles[0])
    api = RelayAPI([profile])
    summary = request(api, "capabilities")["result"]["renderers"][0]
    assert summary["renderer"] == profile["renderer"]
    assert summary["evidence"] == profile["evidence"]
    assert summary["conversion_scope"]["appearance_verified"] is False
    summary["evidence"]["verified_environments"].clear()
    assert request(api, "capabilities")["result"]["renderers"][0]["evidence"] == profile["evidence"]


def test_new_version_does_not_bypass_unsupported_nodes(profiles, request_data):
    profiles = [shared(p) for p in profiles]
    source = request_data["payload"]["material"]
    source["renderer"] = reference(profiles[0])
    source["nodes"][0]["type"] = "NewUnknownShader"
    request_data["payload"]["target"] = reference(profiles[1])
    result = RelayAPI(profiles).handle(request_data)
    assert result["status"] == "error"
    assert result["result"]["source"] == source
    assert result["result"]["material"] is None
    assert any(i["code"] == "UNSUPPORTED_NODE" for i in result["result"]["report"]["issues"])


def test_version_independent_dictionary_through_real_subprocess(profiles, request_data, tmp_path):
    profiles = [shared(p) for p in profiles]
    for index, profile in enumerate(profiles):
        (tmp_path / (str(index) + ".json")).write_text(json.dumps(profile), encoding="utf-8")
    request_data["payload"]["material"]["renderer"] = reference(profiles[0])
    request_data["payload"]["target"] = reference(profiles[1])
    service = JsonCommandService(
        [sys.executable, "-m", "iris_relay", "--dictionaries", str(tmp_path)], cwd=str(ROOT)
    )
    result = service.handle(request_data)
    assert result["status"] == "ok", result
    assert result == RelayAPI(profiles).handle(request_data)
