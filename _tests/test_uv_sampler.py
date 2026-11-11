"""二维采样器变换的声明式矩阵投影，拒绝剪切和超出二维的变换。"""

import pytest

from iris_relay.materialx.coordinates import sampler_matrix, sampler_parameters
from iris_relay.model import ValidationError


@pytest.mark.parametrize(
    "scale,offset,rotation",
    [
        ([1, 1], [0, 0], 0),
        ([2, 0.75], [0.125, -0.25], 20),
        ([-2, 1.5], [-1, 2], -45),
    ],
)
def test_sampler_parameters_reconstruct_declared_matrix(scale, offset, rotation):
    spec = {
        "rotation_unit": "degrees",
        "pivot": [0.5, 0.5],
        "order": "scale_rotate_translate",
        "verified": False,
    }
    matrix = sampler_matrix({"scale": scale, "offset": offset, "rotation": rotation}, spec)
    parameters = sampler_parameters([matrix], spec)
    assert sampler_matrix(parameters, spec) == pytest.approx(matrix, abs=1e-6)


def test_unrepresentable_sampler_transform_is_not_silently_projected():
    spec = {
        "rotation_unit": "degrees",
        "pivot": [0.5, 0.5],
        "order": "scale_rotate_translate",
        "verified": False,
    }
    for matrix in (
        [1, 1, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
        [1, 0, 1, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
    ):
        with pytest.raises(ValidationError):
            sampler_parameters([matrix], spec)
