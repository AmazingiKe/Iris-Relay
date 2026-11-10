"""独立比较隔离材质族的真实源、基线、校正及重复渲染，不按测试结果重训模型。"""

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from _scripts.neural_data import comparison_mask, image_loss, read_exr  # noqa: E402
from _scripts.neural_experiment import save_json  # noqa: E402


def compare_split(rows, split):
    """按材质族聚合并重采样，四个参数变体不能充当四个独立材质族。"""
    selected = [r for r in rows if r["split"] == split]
    groups = defaultdict(list)
    for row in selected:
        groups[row["family"]].append(row)
    baseline = np.array(
        [np.mean([r["baseline_loss"] for r in values]) for values in groups.values()]
    )
    corrected = np.array(
        [np.mean([r["corrected_loss"] for r in values]) for values in groups.values()]
    )
    noise = np.array(
        [np.mean([r["repeat_metric_change"] for r in values]) for values in groups.values()]
    )
    rng = np.random.default_rng(20261111)
    indexes = rng.integers(0, len(groups), size=(10000, len(groups)))
    resampled_base, resampled_corrected = baseline[indexes].mean(1), corrected[indexes].mean(1)
    relative = 100 * (resampled_base - resampled_corrected) / np.maximum(resampled_base, 1e-12)
    interval = np.quantile(relative, [0.025, 0.975]).tolist()
    improvement = float(baseline.mean() - corrected.mean())
    return {
        "families": len(groups),
        "samples": len(selected),
        "baseline_mean_loss": float(baseline.mean()),
        "corrected_mean_loss": float(corrected.mean()),
        "relative_improvement_percent": float(100 * improvement / max(baseline.mean(), 1e-12)),
        "family_bootstrap_95_percent_interval": interval,
        "repeat_metric_change_mean": float(noise.mean()),
        "improvement_exceeds_measured_noise": bool(improvement > noise.mean()),
        "improvement_proven_in_this_experiment": bool(
            interval[0] > 0 and improvement > noise.mean()
        ),
        "outcomes": {
            name: sum(r["outcome"] == name for r in selected)
            for name in ("improved", "degraded", "within_noise")
        },
    }


def make_preview(job, rows, source, baseline, corrected, output):
    """预先固定展示每个测试族的 v2，三列使用同一 Standard sRGB 预览变换。"""
    chosen = [c for c in job["cases"] if c["split"] == "test" and c["id"].endswith("_v2")]
    canvas = Image.new("RGB", (700, 240 * len(chosen) + 36), (25, 25, 25))
    draw, font = ImageDraw.Draw(canvas), ImageFont.load_default(size=16)
    for index, label in enumerate(("Cycles source", "Arnold baseline", "Arnold corrected")):
        draw.text((index * 230 + 8, 8), label, fill="white", font=font)
    for row_index, case in enumerate(chosen):
        arrays = [
            read_exr(source / case["id"] / "beauty.exr"),
            read_exr(baseline / case["id"] / "baseline.exr"),
            read_exr(corrected / case["id"] / "corrected.exr"),
        ]
        metric = next(r for r in rows if r["id"] == case["id"])
        for column, array in enumerate(arrays):
            rgb = np.clip(array[:, :, :3], 0, 1)
            rgb = np.where(rgb <= 0.0031308, rgb * 12.92, 1.055 * rgb ** (1 / 2.4) - 0.055)
            image = Image.fromarray(np.round(rgb * 255).astype(np.uint8))
            canvas.paste(
                image.resize((192, 192), Image.Resampling.NEAREST),
                (column * 230 + 10, 40 + row_index * 240),
            )
        draw.text(
            (10, 236 + row_index * 240),
            case["id"] + "  " + metric["outcome"],
            fill="white",
            font=font,
        )
    canvas.save(output / "held-out-comparison.png")


def plot_history(training, output):
    """使用实际日志生成可单独导出的科研曲线，参数标签损失与渲染误差分开。"""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    with (training / "loss.csv").open(encoding="utf8") as stream:
        rows = list(csv.DictReader(stream))
    figure, axis = plt.subplots(figsize=(8, 4))
    epochs = [int(r["epoch"]) for r in rows]
    for key, label in (
        ("train_loss", "Train material families"),
        ("validation_loss", "Validation material families"),
    ):
        axis.plot(epochs, [float(r[key]) for r in rows], label=label)
    axis.set(
        xlabel="Epoch",
        ylabel="Joint parameter label MSE",
        title="First experiment: supervised render-search labels",
    )
    axis.legend()
    axis.grid(alpha=0.25)
    figure.tight_layout()
    figure.savefig(output / "training-loss.png", dpi=180)
    figure.savefig(output / "training-loss.pdf")
    plt.close(figure)


def main():
    """要求完整隔离集和重复渲染，保留退化样本，依据实际误差决定能否报告改善。"""
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("job", "source", "baseline", "corrected", "training", "out"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    job = json.loads(args.job.read_text("utf8"))
    state = json.loads((args.corrected / "summary.json").read_text("utf8"))
    selected = [c for c in job["cases"] if c["split"] != "train"]
    assert len(state["completed"]) == len(selected)
    assert (
        state["conditions_sha256"] == job["conditions_sha256"]
        and state["scene_conditions_unchanged"]
    )
    rows = []
    for case in selected:
        source = read_exr(args.source / case["id"] / "beauty.exr")
        baseline = read_exr(args.baseline / case["id"] / "baseline.exr")
        corrected = read_exr(args.corrected / case["id"] / "corrected.exr")
        source_repeat = read_exr(args.source / case["id"] / "beauty-repeat.exr")
        target_repeat = read_exr(args.corrected / case["id"] / "baseline-repeat.exr")
        mask = comparison_mask(source, baseline)
        comparison_mask(source, corrected)
        base_loss, corrected_loss = (
            image_loss(source, baseline, mask),
            image_loss(source, corrected, mask),
        )
        repeat_change = abs(image_loss(source_repeat, target_repeat, mask) - base_loss)
        delta = base_loss - corrected_loss
        rows.append(
            {
                "id": case["id"],
                "family": case["family"],
                "split": case["split"],
                "baseline_loss": base_loss,
                "corrected_loss": corrected_loss,
                "source_repeat_noise": image_loss(source, source_repeat, mask),
                "target_repeat_noise": image_loss(baseline, target_repeat, mask),
                "repeat_metric_change": repeat_change,
                "outcome": "improved"
                if delta > repeat_change
                else "degraded"
                if delta < -repeat_change
                else "within_noise",
            }
        )
    report = {
        "experiment_complete": True,
        "conditions_sha256": job["conditions_sha256"],
        "validation": compare_split(rows, "validation"),
        "test": compare_split(rows, "test"),
        "cases": rows,
        "model_scope": "仅本轮不透明 MULTI_GGX、固定单位球、均匀环境及声明参数域",
        "test_used_for_retraining": False,
        "preview_display": "所有列相同的线性 Rec.709 到 Standard sRGB；指标基于原始 EXR",
    }
    save_json(args.out / "evaluation.json", report)
    make_preview(job, rows, args.source, args.baseline, args.corrected, args.out)
    plot_history(args.training, args.out)
    print(json.dumps({"test": report["test"]}), flush=True)


if __name__ == "__main__":
    main()
