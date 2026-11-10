"""训练首轮真实渲染联合模型，导出纯 JSON 推理权重并通过公开 API 生成隔离集提案。"""

import argparse
import copy
import csv
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import torch  # noqa: E402
from torch import nn  # noqa: E402

from _scripts.neural_data import assemble, calibration_context  # noqa: E402
from _scripts.neural_experiment import FEATURE_SCHEMA, fingerprint, save_json  # noqa: E402
from iris_relay import RelayAPI  # noqa: E402
from iris_relay.neural import NeuralJointPredictor  # noqa: E402
from plugins.core import request  # noqa: E402


def main():
    """先审计链路与数据，再仅用训练／验证集合选模型，最后准备隔离集真实渲染。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--gates", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    gates = json.loads(args.gates.read_text("utf8"))
    if any(
        gates.get(key, {}).get("complete") is not True for key in ("C1", "C2", "C3", "C4", "C5")
    ):
        raise RuntimeError("五项交换链路尚未完成，不能开始训练")
    args.out.mkdir(parents=True, exist_ok=False)
    job = json.loads(args.job.read_text("utf8"))
    assert fingerprint(job["conditions"]) == job["conditions_sha256"]
    rows = assemble(job, args.source, args.target)
    save_json(
        args.out / "dataset.json",
        {
            "cases": rows,
            "conditions_sha256": job["conditions_sha256"],
            "gates_sha256": hashlib.sha256(args.gates.read_bytes()).hexdigest(),
        },
    )
    split = {
        name: [i for i, r in enumerate(rows) if r["split"] == name]
        for name in ("train", "validation", "test")
    }
    if any(not indexes for indexes in split.values()):
        raise ValueError("训练、验证与测试必须各有独立材质族")
    spec = job["conditions"]
    torch.manual_seed(spec["train_seed"])
    np.random.seed(spec["train_seed"])
    torch.set_num_threads(4)
    torch.use_deterministic_algorithms(True)
    device = torch.device("cpu")
    features = np.asarray([r["features"] for r in rows], dtype=np.float32)
    mean, std = features[split["train"]].mean(0), features[split["train"]].std(0)
    std[std < 1e-6] = 1
    inputs = torch.tensor((features - mean) / std, device=device)
    labels = torch.tensor(
        np.asarray([r["label_delta"] for r in rows], dtype=np.float32) / [0.12, 0.25],
        dtype=torch.float32,
        device=device,
    )
    net = nn.Sequential(
        nn.Linear(68, 32), nn.ReLU(), nn.Linear(32, 16), nn.ReLU(), nn.Linear(16, 2), nn.Tanh()
    ).to(device)
    optimizer = torch.optim.AdamW(
        net.parameters(), lr=spec["learning_rate"], weight_decay=spec["weight_decay"]
    )
    criterion = nn.MSELoss()
    best_loss, best_epoch, best_state = float("inf"), -1, None
    history = []
    started = time.perf_counter()
    with (args.out / "loss.csv").open("w", encoding="utf8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["epoch", "train_loss", "validation_loss"])
        for epoch in range(spec["epochs"]):
            net.train()
            order = torch.tensor(split["train"])[torch.randperm(len(split["train"]))]
            for batch in order.split(spec["batch_size"]):
                optimizer.zero_grad()
                loss = criterion(net(inputs[batch]), labels[batch])
                loss.backward()
                optimizer.step()
            net.eval()
            with torch.no_grad():
                train_loss = criterion(net(inputs[split["train"]]), labels[split["train"]]).item()
                validation_loss = criterion(
                    net(inputs[split["validation"]]), labels[split["validation"]]
                ).item()
            writer.writerow([epoch, train_loss, validation_loss])
            history.append(
                {"epoch": epoch, "train_loss": train_loss, "validation_loss": validation_loss}
            )
            if validation_loss < best_loss - 1e-8:
                best_loss, best_epoch, best_state = (
                    validation_loss,
                    epoch,
                    copy.deepcopy(net.state_dict()),
                )
            if epoch % 50 == 0:
                stream.flush()
                print(json.dumps(history[-1]), flush=True)
            if epoch - best_epoch >= spec["patience"]:
                break
    net.load_state_dict(best_state)
    net.eval()
    torch.save(
        {
            "model_state": best_state,
            "input_mean": mean.tolist(),
            "input_std": std.tolist(),
            "seed": spec["train_seed"],
            "architecture": spec["architecture"],
        },
        args.out / "weights.pt",
    )
    layers = []
    for index, activation in zip((0, 2, 4), spec["activations"], strict=True):
        layer = net[index]
        layers.append(
            {
                "weights": layer.weight.detach().cpu().tolist(),
                "bias": layer.bias.detach().cpu().tolist(),
                "activation": activation,
            }
        )
    model_id = "iris.n1/20261111/" + fingerprint(layers)[:16]
    model = {
        "format_version": "1",
        "model_id": model_id,
        "feature_schema": FEATURE_SCHEMA,
        "source_shading_model": job["source_shading_model"],
        "target": job["target"],
        "dictionary_version": job["dictionary_version"],
        "conditions_sha256": job["conditions_sha256"],
        "input_mean": mean.tolist(),
        "input_std": std.tolist(),
        "layers": layers,
        "delta_scale": [0.12, 0.25],
        "training": {
            "framework": torch.__version__,
            "python": sys.version,
            "device": str(device),
            "seed": spec["train_seed"],
            "epochs": len(history),
            "best_epoch": best_epoch,
            "validation_loss": best_loss,
            "elapsed_seconds": time.perf_counter() - started,
            "dataset_sha256": hashlib.sha256((args.out / "dataset.json").read_bytes()).hexdigest(),
            "test_used_for_model_selection": False,
        },
    }
    save_json(args.out / "model.json", model)
    predictor = NeuralJointPredictor.from_file(args.out / "model.json")
    api = RelayAPI.from_directory(ROOT / "dictionaries", predictor=predictor)
    predictions = args.out / "predictions"
    predictions.mkdir()
    cases, max_error = [], 0.0
    with torch.no_grad():
        inferred = net(inputs).cpu().numpy()
    for index, row in enumerate(rows):
        if row["split"] == "train":
            continue
        path = args.source / "documents" / (row["id"] + ".mtlx")
        response = request(
            api,
            "materialx.convert",
            document=path.read_text("utf8"),
            target=job["target"],
            calibrate=True,
            calibration_context=calibration_context(job, row["spatial"]),
        )
        result = response.get("result") or {}
        calibration = result.get("report", {}).get("calibration", {})
        if calibration.get("status") not in {"applied", "unchanged"}:
            raise RuntimeError(response)
        root = next(
            n
            for n in result["material"]["nodes"]
            if n["id"] == result["material"]["output"]["node"]
        )
        values = [root["inputs"][p] for p in ("specularRoughness", "specular")]
        expected = np.clip(
            np.asarray(row["features"][-4:-2]) + inferred[index] * [0.12, 0.25],
            [0.05, 0],
            [0.95, 1],
        )
        max_error = max(max_error, float(np.abs(expected - values).max()))
        save_json(predictions / (row["id"] + ".json"), response)
        cases.append(
            {
                "id": row["id"],
                "split": row["split"],
                "parameters": values,
                "calibration": calibration,
            }
        )
    if max_error > 2e-6:
        raise RuntimeError("纯 Python 与真实 PyTorch 推理不一致：" + str(max_error))
    metrics = {
        "model_id": model_id,
        "training": model["training"],
        "samples": {k: len(v) for k, v in split.items()},
        "families": {
            name: len({r["family"] for r in rows if r["split"] == name}) for name in split
        },
        "torch_json_max_error": max_error,
        "predictions": cases,
        "held_out_render_evaluation": "pending",
        "appearance_improvement_proven": False,
    }
    save_json(args.out / "training-summary.json", metrics)
    print(
        json.dumps(
            {
                "model_id": model_id,
                "epochs": len(history),
                "best_epoch": best_epoch,
                "validation_loss": best_loss,
                "torch_json_max_error": max_error,
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
