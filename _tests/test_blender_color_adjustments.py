"""颜色调整按 Cycles 公式核对，尤其 Gamma=0 与非正颜色的不同分支。"""

import pytest

from _tests.test_materialx_hsv import evaluate, profile
from iris_relay.materialx.core import MaterialXCore


@pytest.mark.parametrize("gamma", [0.0, 0.5, 1.0, 2.2, -1.0])
def test_gamma_uses_cycles_zero_and_negative_color_branches(gamma):
    data = profile()
    assert "ShaderNodeGamma" in data["nodes"]
    doc = MaterialXCore().read(data["materialx_extensions"])
    graph = doc.getNodeGraph("NG_iris_blender_gamma_color3")
    color = [-0.3, 0.0, 0.8]
    expected = [1.0] * 3 if gamma == 0 else [c**gamma if c > 0 else c for c in color]
    assert evaluate(graph, {"color": color, "gamma": gamma}) == pytest.approx(expected)


@pytest.mark.parametrize("bright,contrast", [(0, 0), (0.1, 0.5), (-1, -2), (2, 3)])
def test_brightness_contrast_preserves_lower_clamp(bright, contrast):
    data = profile()
    assert "ShaderNodeBrightContrast" in data["nodes"]
    doc = MaterialXCore().read(data["materialx_extensions"])
    graph = doc.getNodeGraph("NG_iris_blender_brightcontrast_color3")
    color = [-0.4, 0.2, 1.4]
    expected = [max(0, (1 + contrast) * c + bright - contrast * 0.5) for c in color]
    assert evaluate(
        graph, {"color": color, "bright": bright, "contrast": contrast}
    ) == pytest.approx(expected)
