"""真实绑定的离线回归；宿主创建与重开验证由 _scripts 单独执行。"""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from _scripts.native_fixture import fixture, renderer
from iris_relay import RelayAPI

ROOT = Path(__file__).resolve().parents[1]
PROFILES = [
    json.loads(p.read_text(encoding="utf-8"))
    for p in sorted((ROOT / "dictionaries").glob("*.json"))
]


@pytest.mark.parametrize(
    ("distribution", "subsurface_method"),
    [
        ("MULTI_GGX", "RANDOM_WALK_LEGACY"),
        ("GGX", "RANDOM_WALK_SKIN"),
        ("GGX", "BURLEY"),
        ("MULTI_GGX", "BURLEY"),
        ("MULTI_GGX", "RANDOM_WALK_SKIN"),
        ("GGX", "RANDOM_WALK_LEGACY"),
    ],
)
@pytest.mark.parametrize("sss_weight", [0.0, 0.2])
def test_observed_principled_variants_export_with_explicit_sss_boundary(
    distribution, subsurface_method, sss_weight
):
    """A1 实文件中的历史模式可识别；模式识别不得绕过活动 SSS 的支持边界。"""
    profile = next(p for p in PROFILES if p["renderer"]["id"] == "cycles")
    api = RelayAPI(PROFILES)
    identified = api.handle(
        {
            "api_version": "1",
            "request_id": "历史模式",
            "operation": "identify",
            "payload": {
                "renderer": renderer(profile),
                "native_id": "ShaderNodeBsdfPrincipled",
                "properties": {
                    "distribution": distribution,
                    "subsurface_method": subsurface_method,
                },
            },
        }
    )
    assert identified["status"] == "ok", identified
    material = fixture(profile)
    surface = next(n for n in material["nodes"] if n["type"] == "ShaderNodeBsdfPrincipled")
    surface["type"] = identified["result"]["implementation"]
    surface["inputs"]["Subsurface Weight"] = sss_weight
    original = deepcopy(material)
    exported = api.handle(
        {
            "api_version": "1",
            "request_id": "历史模式导出",
            "operation": "materialx.export",
            "payload": {"material": material},
        }
    )
    assert exported["result"]["source"] == original
    assert material == original
    assert bool(exported["result"]["document"]) == (sss_weight == 0)
    assert exported["status"] == ("warning" if sss_weight == 0 else "error")


@pytest.mark.parametrize("source", PROFILES, ids=lambda p: p["renderer"]["id"])
@pytest.mark.parametrize("target", PROFILES, ids=lambda p: p["renderer"]["id"])
@pytest.mark.parametrize("normal_semantic", ["normal.tangent", "bump.height"])
def test_real_profiles_preserve_nodes_links_resources(source, target, normal_semantic):
    data = fixture(source, normal_semantic=normal_semantic)
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
    assert len(output["nodes"]) == (4 if normal_semantic == "bump.height" else 3)
    assert len(output["links"]) == (3 if normal_semantic == "bump.height" else 2)
    intermediates = result["result"]["intermediate"]
    target_input = "height" if normal_semantic == "bump.height" else "normal.color"
    assert any(
        link["to"] == {"node": normal_semantic.replace(".", "_"), "socket": target_input}
        for link in intermediates["links"]
    )
    assert all(resource["color_space"] == "Raw" for resource in output["textures"].values())
    assert output["textures"] == data["textures"]
    assert data == before


def test_generated_profiles_are_reproducible_and_declare_semantic_contract():
    from _scripts.build_native_dictionaries import build

    for path in sorted((ROOT / "dictionaries/catalogs").glob("*.json")):
        catalog = json.loads(path.read_text(encoding="utf-8"))
        built = build(catalog)
        filename = f"{catalog['host']['id']}-{catalog['renderer']['id']}.json"
        stored = json.loads((ROOT / "dictionaries" / filename).read_text(encoding="utf-8"))
        assert built == stored
        assert built["semantic_schema_version"] == "1"
        assert {n["semantic"] for n in built["nodes"].values()} >= {
            "texture.image",
            "normal.tangent",
            "bump.height",
        }


def test_real_redshift_modes_identified_by_public_api():
    api = RelayAPI(PROFILES)
    profile = next(p for p in PROFILES if p["renderer"]["id"] == "redshift")
    for semantic in ("normal.tangent", "bump.height"):
        implementation, rule = next(
            (k, n) for k, n in profile["nodes"].items() if n["semantic"] == semantic
        )
        response = api.handle(
            {
                "api_version": "1",
                "request_id": "mode",
                "operation": "identify",
                "payload": {
                    "renderer": renderer(profile),
                    "native_id": rule["native"]["id"],
                    "properties": rule["native"]["properties"],
                },
            }
        )
        assert response["status"] == "ok", response
        assert response["result"]["implementation"] == implementation
        assert response["result"]["semantic"] == semantic


def test_rgb_is_not_implicitly_interpreted_as_height():
    source = PROFILES[0]
    data = fixture(source, normal_semantic="bump.height")
    # 用户跳过通道提取时必须拒绝，不能利用宿主的隐式转换猜测含义。
    image_link = next(link for link in data["links"] if link["from"]["node"] == "texture_image")
    height_link = next(link for link in data["links"] if link["to"]["node"] == "bump_height")
    height_link["from"] = deepcopy(image_link["from"])
    result = RelayAPI(PROFILES).handle(
        {
            "api_version": "1",
            "request_id": "no-guess",
            "operation": "convert",
            "payload": {"material": data, "target": renderer(PROFILES[1])},
        }
    )
    assert result["status"] == "error"
    assert any(i["code"] == "LINK_TYPE_MISMATCH" for i in result["result"]["report"]["issues"])


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
