"""用实际训练权重核对公开 API 的超域回退和整组撤回，不制造模型有效性结论。"""

import argparse
import hashlib
import json
import sys
import xml.etree.ElementTree as ET
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from _scripts.native_fixture import renderer  # noqa: E402
from _scripts.neural_data import calibration_context  # noqa: E402
from _scripts.neural_experiment import save_json  # noqa: E402
from iris_relay import RelayAPI  # noqa: E402
from iris_relay.correction import CorrectionProposal, InputChange  # noqa: E402
from iris_relay.neural import NeuralJointPredictor  # noqa: E402
from plugins.core import request  # noqa: E402


class InvalidSecondChange:
    """保留真实网络的首项变化，仅故意破坏第二项来证明不会部分应用；只用于失败注入。"""

    def __init__(self, predictor):
        self.predictor = predictor
        self.model_id = predictor.model_id + "/fault-injection"

    def predict(self, context):
        proposal = self.predictor.predict(context)
        node = context["target"]["output"]["node"]
        return CorrectionProposal([proposal.changes[0], InputChange(node, "specular", 1.25)])


def main():
    """源文档、实际特征和权重均只读；每个负例必须保留完整基础图并报告未应用。"""
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("job", "source", "training", "out"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    job = json.loads(args.job.read_text("utf8"))
    dataset = json.loads((args.training / "dataset.json").read_text("utf8"))
    case = next(row for row in dataset["cases"] if row["split"] == "test")
    document = (args.source / "documents" / (case["id"] + ".mtlx")).read_text("utf8")
    context = calibration_context(job, case["spatial"])
    model_path = args.training / "model.json"
    digest = hashlib.sha256(model_path.read_bytes()).hexdigest()
    predictor = NeuralJointPredictor.from_file(model_path)
    plain = RelayAPI.from_directory(ROOT / "dictionaries")
    modeled = RelayAPI.from_directory(ROOT / "dictionaries", predictor=predictor)
    invalid = RelayAPI.from_directory(
        ROOT / "dictionaries", predictor=InvalidSecondChange(predictor)
    )
    cases = []

    def verify(name, api, candidate, material_context, target, expected):
        baseline = request(plain, "materialx.convert", document=candidate, target=target)
        response = request(
            api,
            "materialx.convert",
            document=candidate,
            target=target,
            calibrate=True,
            calibration_context=material_context,
        )
        assert baseline["status"] != "error" and response["status"] != "error", response
        calibration = response["result"]["report"]["calibration"]
        assert calibration["status"] == expected and not calibration["changes"], response
        assert response["result"]["material"] == baseline["result"]["material"]
        cases.append({"name": name, "calibration": calibration, "baseline_unchanged": True})

    verify("missing_model", plain, document, context, job["target"], "unavailable")
    for name, changed in (
        ("unknown_conditions", {"conditions_sha256": "0" * 64}),
        ("unknown_source_model", {"source_shading_model": "cycles.principled.GGX"}),
        ("missing_spatial", {"spatial": []}),
        ("invalid_spatial", {"spatial": [-1.0] * 64}),
    ):
        verify(name, modeled, document, {**context, **changed}, job["target"], "rejected")
    profiles = request(plain, "capabilities")["result"]["renderers"]
    blender = renderer(next(p for p in profiles if p.get("host", {}).get("id") == "blender"))
    verify("unknown_target", modeled, document, context, blender, "rejected")

    tree = ET.fromstring(document)
    surface = next(node for node in tree if node.tag == "standard_surface")
    port = next(p for p in surface if p.get("name") == "specular_roughness")
    port.attrib.pop("value", None)
    port.set("nodename", "probe_connected_roughness")
    control = ET.SubElement(
        tree,
        "constant",
        {"name": "probe_connected_roughness", "type": "float", "nodedef": "ND_constant_float"},
    )
    ET.SubElement(control, "input", {"name": "value", "type": "float", "value": "0.36"})
    verify(
        "connected_roughness",
        modeled,
        ET.tostring(tree, encoding="unicode"),
        context,
        job["target"],
        "rejected",
    )
    verify("invalid_second_change", invalid, document, deepcopy(context), job["target"], "rejected")
    assert hashlib.sha256(model_path.read_bytes()).hexdigest() == digest
    save_json(
        args.out / "summary.json",
        {"model_sha256": digest, "cases": cases, "all_passed": True, "appearance_verified": False},
    )
    print(json.dumps({"cases": len(cases), "all_passed": True}), flush=True)


if __name__ == "__main__":
    main()
