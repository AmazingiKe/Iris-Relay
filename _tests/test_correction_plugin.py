from copy import deepcopy

import pytest

from iris_relay.api import RelayAPI
from iris_relay.correction import CorrectionProposal, InputChange
from iris_relay.plugin import convert_material


class TestPredictor:
    """只验证联合提案协议，不是神经模型或视觉效果证据。"""

    __test__ = False
    model_id = "contract-test/1"

    def predict(self, context):
        assert context["source"]["textures"]
        assert context["intermediate"]["links"]
        assert context["target"]["nodes"]
        return CorrectionProposal(
            [
                InputChange("surface", "roughness", 0.25),
                InputChange("surface", "metalness", 0.85),
            ]
        )


def test_joint_proposal_is_applied_as_one_material_change(profiles, request_data):
    request_data["payload"]["calibrate"] = True
    result = RelayAPI(profiles, predictor=TestPredictor()).handle(request_data)["result"]
    assert result["material"]["nodes"][0]["inputs"]["roughness"] == 0.25
    assert result["material"]["nodes"][0]["inputs"]["metalness"] == 0.85
    assert result["report"]["calibration"]["status"] == "applied"
    assert len(result["report"]["calibration"]["changes"]) == 2


@pytest.mark.parametrize(
    "bad_change",
    [
        InputChange("surface", "metalness", 5),
        InputChange("surface", "exposure", 1),
        InputChange("missing", "roughness", 0.2),
        InputChange("surface", "base_color", [0.1, 0.1, 0.1]),
        InputChange("surface", "roughness", 0.9),
    ],
)
def test_rejected_proposal_keeps_whole_baseline(profiles, request_data, bad_change):
    class InvalidPredictor(TestPredictor):
        def predict(self, context):
            context["target"]["nodes"][0]["inputs"]["roughness"] = 0.99
            return CorrectionProposal([InputChange("surface", "roughness", 0.25), bad_change])

    request_data["payload"]["calibrate"] = True
    result = RelayAPI(profiles, predictor=InvalidPredictor()).handle(request_data)["result"]
    assert result["report"]["calibration"]["status"] == "rejected"
    assert result["material"]["nodes"][0]["inputs"]["roughness"] == pytest.approx(0.2)


def test_predictor_failure_returns_baseline(profiles, request_data):
    class BrokenPredictor(TestPredictor):
        def predict(self, context):
            raise RuntimeError("预测失败")

    request_data["payload"]["calibrate"] = True
    response = RelayAPI(profiles, predictor=BrokenPredictor()).handle(request_data)
    assert response["status"] == "warning"
    assert response["result"]["report"]["calibration"]["status"] == "rejected"


def test_plugin_uses_only_public_api_and_creates_new_material(profiles, material):
    class Adapter:
        def __init__(self):
            self.original = deepcopy(material)
            self.created = []
            self.scene = {"exposure": 0, "lights": ["key"]}

        def read_material(self):
            return deepcopy(self.original)

        def create_material(self, data):
            self.created.append(deepcopy(data))
            return "new-material-id"

    adapter = Adapter()
    outcome = convert_material(
        adapter, RelayAPI(profiles), {"id": "reference.rough", "version": "1.0"}
    )
    assert outcome.created == "new-material-id"
    assert adapter.original == material
    assert adapter.scene == {"exposure": 0, "lights": ["key"]}
    assert len(adapter.created) == 1


def test_plugin_does_not_apply_blocked_conversion(profiles, material):
    class Adapter:
        def read_material(self):
            return material

        def create_material(self, data):
            pytest.fail("不能应用失败方案")

    outcome = convert_material(Adapter(), RelayAPI(profiles), {"id": "unknown", "version": "1"})
    assert outcome.created is None
    assert outcome.response["status"] == "error"


def test_plugin_requires_explicit_acceptance_of_warnings(profiles, material):
    class Adapter:
        def read_material(self):
            return material

        def create_material(self, data):
            return "created"

    args = (Adapter(), RelayAPI(profiles), {"id": "reference.rough", "version": "1.0"})
    assert convert_material(*args, calibrate=True).created is None
    assert convert_material(*args, calibrate=True, accept_warnings=True).created == "created"


@pytest.mark.parametrize("mode", ["request_id", "renderer", "empty"])
def test_plugin_rejects_inconsistent_service_response(profiles, material, mode):
    class Service:
        def handle(self, request):
            response = RelayAPI(profiles).handle(request)
            if mode == "request_id":
                response["request_id"] = "wrong"
            elif mode == "renderer":
                response["result"]["material"]["renderer"]["id"] = "wrong"
            else:
                response["result"] = None
            return response

    class Adapter:
        def read_material(self):
            return material

        def create_material(self, data):
            pytest.fail("不可应用不一致的响应")

    with pytest.raises(ValueError):
        convert_material(Adapter(), Service(), {"id": "reference.rough", "version": "1.0"})
