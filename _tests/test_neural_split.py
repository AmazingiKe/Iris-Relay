"""实验数据隔离的负例；夹具身份不充当训练材质或渲染标签。"""

import pytest

from _scripts.neural_experiment import validate_split


def test_variants_in_same_family_remain_together():
    assets = [
        {"id": "a", "family": "wood", "split": "train", "resource_sha256": ["wood-image"]},
        {"id": "b", "family": "metal", "split": "test", "resource_sha256": ["metal-image"]},
    ]
    cases = [
        {"id": "a0", "asset": "a", "family": "wood", "split": "train"},
        {"id": "a1", "asset": "a", "family": "wood", "split": "train"},
        {"id": "b0", "asset": "b", "family": "metal", "split": "test"},
    ]
    validate_split(assets, cases)
    cases[1]["split"] = "test"
    with pytest.raises(ValueError):
        validate_split(assets, cases)


@pytest.mark.parametrize("mode", ["family", "resource", "duplicate", "unknown_asset"])
def test_family_resource_and_identity_leakage_is_rejected(mode):
    assets = [
        {"id": "a", "family": "wood", "split": "train", "resource_sha256": ["wood-image"]},
        {"id": "b", "family": "metal", "split": "test", "resource_sha256": ["metal-image"]},
    ]
    cases = [
        {"id": "a0", "asset": "a", "family": "wood", "split": "train"},
        {"id": "b0", "asset": "b", "family": "metal", "split": "test"},
    ]
    if mode == "family":
        assets[1]["family"] = "wood"
    elif mode == "resource":
        assets[1]["resource_sha256"] = ["wood-image"]
    elif mode == "duplicate":
        cases[1]["id"] = "a0"
    else:
        cases[1]["asset"] = "missing"
    with pytest.raises(ValueError):
        validate_split(assets, cases)
