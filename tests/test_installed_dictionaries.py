"""真实绑定的离线回归；宿主创建与重开验证由 scripts 单独执行。"""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from iris_relay import RelayAPI
from scripts.native_fixture import fixture, renderer

ROOT = Path(__file__).resolve().parents[1]
PROFILES = [
    json.loads(p.read_text(encoding="utf-8"))
    for p in sorted((ROOT / "dictionaries").glob("*.json"))
]


@pytest.mark.parametrize("source", PROFILES, ids=lambda p: p["renderer"]["id"])
@pytest.mark.parametrize("target", PROFILES, ids=lambda p: p["renderer"]["id"])
def test_real_profiles_preserve_nodes_links_resources(source, target):
    data = fixture(source)
    before = deepcopy(data)
    result = RelayAPI(PROFILES).handle(
        {
            "api_version": "1",
            "request_id": "matrix",
            "operation": "convert",
            "payload": {"material": data, "target": renderer(target)},
        }
    )
    assert result["status"] != "error", result
    output = result["result"]["material"]
    assert len(output["nodes"]) == 3
    assert len(output["links"]) == 2
    assert output["textures"] == data["textures"]
    assert data == before


def test_catalogs_have_actual_instantiation_evidence_and_codes():
    for path in (ROOT / "dictionaries/catalogs").glob("*.json"):
        catalog = json.loads(path.read_text(encoding="utf-8"))
        assert catalog["nodes"]
        assert all(
            record["status"] in {"instantiated", "unavailable"}
            for record in catalog["nodes"].values()
        )
    arnold = next(p for p in PROFILES if p["renderer"]["id"] == "arnold")
    assert arnold["nodes"]["standard_surface"]["native"]["id"] == 314733630
    assert arnold["nodes"]["standard_surface"]["inputs"]["base_color"]["native"]["id"] == 1044225467
