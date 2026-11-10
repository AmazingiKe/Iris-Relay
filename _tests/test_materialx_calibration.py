"""仅验证真实 MaterialX 链路的原子校正协议；这些测试不是神经训练证据。"""

import json
from pathlib import Path

import pytest

from _scripts.native_fixture import renderer
from _tests.test_materialx_bridge import call
from iris_relay import RelayAPI
from iris_relay.correction import CorrectionProposal, InputChange
from iris_relay.dictionary import Dictionary
from iris_relay.model import ValidationError

DOCUMENT = """<materialx version="1.39"><standard_surface name="surface" type="surfaceshader"/>
<surfacematerial name="material" type="material">
<input name="surfaceshader" type="surfaceshader" nodename="surface"/>
</surfacematerial></materialx>"""


def profile():
    path = Path(__file__).resolve().parents[1] / "dictionaries/maya-arnold-7.2.5.3.json"
    data = json.loads(path.read_text("utf8"))
    for name, limits in [("specularRoughness", [0.05, 0.95]), ("specular", [0, 1])]:
        data["nodes"]["standard_surface"]["inputs"][name].update(
            adjustable=True, adjustment_range=limits
        )
    return data


class ProtocolPredictor:
    """两端口联合提案的协议夹具，禁止称为训练模型。"""

    model_id = "contract-only"

    def predict(self, context):
        assert context["intermediate"]["nodes"]
        assert context["material_context"]["spatial"] == [0.2, 0.5]
        return CorrectionProposal(
            [
                InputChange("surface", "specularRoughness", 0.4),
                InputChange("surface", "specular", 0.7),
            ]
        )


def test_materialx_calibration_uses_context_and_both_changes_are_applied():
    data = profile()
    result = call(
        RelayAPI([data], predictor=ProtocolPredictor()),
        "materialx.convert",
        document=DOCUMENT,
        target=renderer(data),
        calibrate=True,
        calibration_context={"spatial": [0.2, 0.5]},
    )
    assert result["result"]["report"]["calibration"]["status"] == "applied", result
    assert len(result["result"]["report"]["calibration"]["changes"]) == 2


def test_out_of_adjustment_domain_rejects_entire_proposal_and_keeps_baseline():
    class Outside(ProtocolPredictor):
        def predict(self, context):
            return CorrectionProposal(
                [
                    InputChange("surface", "specular", 0.5),
                    InputChange("surface", "specularRoughness", 0.99),
                ]
            )

    data = profile()
    result = call(
        RelayAPI([data], predictor=Outside()),
        "materialx.convert",
        document=DOCUMENT,
        target=renderer(data),
        calibrate=True,
    )
    baseline = call(RelayAPI([data]), "materialx.convert", document=DOCUMENT, target=renderer(data))
    assert result["result"]["report"]["calibration"]["status"] == "rejected", result
    assert result["result"]["material"] == baseline["result"]["material"]


def test_missing_model_keeps_base_conversion_and_reports_unavailable():
    data = profile()
    result = call(
        RelayAPI([data]),
        "materialx.convert",
        document=DOCUMENT,
        target=renderer(data),
        calibrate=True,
    )
    assert result["result"]["material"]
    assert result["result"]["report"]["calibration"]["status"] == "unavailable"


def test_adjustment_range_does_not_restrict_base_material_but_requires_adjustable_numeric_port():
    data = profile()
    Dictionary.parse(data)
    data["nodes"]["standard_surface"]["inputs"]["specularRoughness"]["adjustable"] = False
    with pytest.raises(ValidationError):
        Dictionary.parse(data)
