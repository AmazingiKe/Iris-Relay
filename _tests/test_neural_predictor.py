"""推理协议的人工小矩阵夹具；不能作为训练、渲染或模型有效性的证明。"""

from copy import deepcopy

import pytest

from iris_relay.model import ValidationError


def model_data():
    return {
        "format_version": "1",
        "model_id": "protocol-matrix-only",
        "feature_schema": "albedo4x4-roughness4x4-params4/v1",
        "source_shading_model": "cycles.principled.MULTI_GGX",
        "target": {
            "id": "arnold",
            "version": "7.2.5.3",
            "host": "maya",
            "host_version": "2022",
            "node_system": "mtoa",
        },
        "dictionary_version": "0.4.0",
        "conditions_sha256": "a" * 64,
        "input_mean": [0.0] * 68,
        "input_std": [1.0] * 68,
        "layers": [
            {
                "weights": [[0.3] + [0.0] * 67, [0.6] + [0.0] * 67],
                "bias": [0.0, 0.0],
                "activation": "tanh",
            }
        ],
        "delta_scale": [0.12, 0.25],
    }


def context():
    data = model_data()
    return {
        "target": {
            "renderer": data["target"],
            "output": {"node": "root", "socket": "surface"},
            "nodes": [
                {
                    "id": "root",
                    "type": "standard_surface",
                    "inputs": {
                        "specularRoughness": 0.4,
                        "specular": 0.6,
                        "specularIOR": 1.5,
                        "metalness": 0.0,
                        "coat": 0.0,
                        "emission": 0.0,
                        "sheen": 0.0,
                        "opacity": [1.0, 1.0, 1.0],
                    },
                }
            ],
            "links": [],
        },
        "dictionary_versions": {"target": "0.4.0"},
        "material_context": {
            "source_shading_model": "cycles.principled.MULTI_GGX",
            "feature_schema": data["feature_schema"],
            "conditions_sha256": data["conditions_sha256"],
            "spatial": [0.2] * 64,
        },
    }


def test_shared_spatial_context_jointly_changes_both_ports_without_mutating_input():
    from iris_relay.neural import NeuralJointPredictor

    predictor = NeuralJointPredictor(model_data())
    first = context()
    original = deepcopy(first)
    a = predictor.predict(first)
    first["material_context"]["spatial"][0] = 0.8
    b = predictor.predict(first)
    assert all(x.value != y.value for x, y in zip(a.changes, b.changes, strict=True))
    assert 0.05 <= b.changes[0].value <= 0.95
    assert 0 <= b.changes[1].value <= 1
    assert original["target"] == first["target"]


@pytest.mark.parametrize(
    "mode", ["conditions", "connected", "unknown_renderer", "nonfinite", "source_model"]
)
def test_unproven_context_and_connected_controls_are_rejected(mode):
    from iris_relay.neural import NeuralJointPredictor

    data = context()
    if mode == "conditions":
        data["material_context"]["conditions_sha256"] = "b" * 64
    elif mode == "connected":
        data["target"]["links"] = [{"to": {"node": "root", "socket": "specular"}}]
    elif mode == "unknown_renderer":
        data["target"]["renderer"]["version"] = "unknown"
    elif mode == "nonfinite":
        data["material_context"]["spatial"][0] = float("nan")
    else:
        data["material_context"]["source_shading_model"] = "cycles.principled.GGX"
    with pytest.raises(ValidationError):
        NeuralJointPredictor(model_data()).predict(data)


def test_invalid_weight_shapes_and_nonfinite_values_cannot_load():
    from iris_relay.neural import NeuralJointPredictor

    data = model_data()
    data["layers"][0]["weights"][0][0] = float("inf")
    with pytest.raises(ValidationError):
        NeuralJointPredictor(data)
    data = model_data()
    data["layers"][0]["bias"] = []
    with pytest.raises(ValidationError):
        NeuralJointPredictor(data)
