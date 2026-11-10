"""从真实 EXR 组装实验特征和联合搜索标签；只在独立训练环境导入数值依赖。"""

import json

import numpy as np
import OpenEXR
from scipy.ndimage import binary_erosion

from _scripts.neural_experiment import FEATURE_SCHEMA, validate_split
from iris_relay.neural import material_features


def read_exr(path):
    """要求有限的实际 RGBA 图像；不使用预览图、缺失帧或合成值回填。"""
    channels = OpenEXR.File(str(path)).channels()
    if "RGBA" not in channels:
        raise ValueError("实验要求实际 RGBA EXR：" + str(path))
    image = channels["RGBA"].pixels.astype(np.float64)
    if image.shape != (64, 64, 4) or not np.isfinite(image).all():
        raise ValueError("实验图像维数或有限性无效：" + str(path))
    if image[:, :, :3].min() < -1e-5:
        raise ValueError("图像包含本轮尚未支持的负能量：" + str(path))
    return image


def comparison_mask(source, target):
    """独立核对几何覆盖后排除抗锯齿边缘，不能靠裁掉错位区域掩盖场景问题。"""
    difference = np.count_nonzero((source[:, :, 3] > 0.5) != (target[:, :, 3] > 0.5))
    if difference > 32:
        raise ValueError("源／目标几何覆盖不匹配：" + str(difference))
    mask = binary_erosion((source[:, :, 3] > 0.999) & (target[:, :, 3] > 0.999), iterations=2)
    if mask.sum() < 1000:
        raise ValueError("有效比较区域不足")
    return mask


def image_loss(source, target, mask):
    """在线性 Rec.709 的实际 RGB 上计算固定 log1p MSE，不调整曝光或灯光。"""
    delta = np.log1p(np.maximum(source[:, :, :3], 0)) - np.log1p(np.maximum(target[:, :, :3], 0))
    return float(np.square(delta[mask]).mean())


def grid_values(image, color=True):
    """提取实际底色／粗糙度的四乘四空间分布，空角保持明确的零值。"""
    result = []
    for y in range(4):
        for x in range(4):
            tile = image[y * 16 : (y + 1) * 16, x * 16 : (x + 1) * 16]
            mask = tile[:, :, 3] > 0.999
            value = tile[:, :, :3][mask].mean(0) if mask.any() else np.zeros(3)
            result.extend(np.maximum(value, 0).tolist() if color else [max(0.0, float(value[0]))])
    return result


def calibration_context(job, spatial):
    """只提供已冻结的条件身份与实际空间样本，当前参数由推理器从目标图读取。"""
    return {
        "feature_schema": FEATURE_SCHEMA,
        "source_shading_model": job["source_shading_model"],
        "conditions_sha256": job["conditions_sha256"],
        "spatial": spatial,
    }


def assemble(job, source_root, target_root):
    """核对完整渲染批次和隔离清单，再为每个样本选择真实搜索结果；不制造偏移标签。"""
    validate_split(job["source_materials"], job["cases"])
    for directory in (source_root, target_root):
        summary = json.loads((directory / "summary.json").read_text("utf8"))
        if (
            len(summary["completed"]) != len(job["cases"])
            or summary["conditions_sha256"] != job["conditions_sha256"]
        ):
            raise ValueError("源／目标批次未完整结束或条件不同：" + str(directory))
    rows, observed = [], {}
    for case in job["cases"]:
        source_dir, target_dir = source_root / case["id"], target_root / case["id"]
        source = read_exr(source_dir / "beauty.exr")
        albedo, roughness = (
            read_exr(source_dir / "albedo.exr"),
            read_exr(source_dir / "roughness.exr"),
        )
        baseline = read_exr(target_dir / "baseline.exr")
        mask = comparison_mask(source, baseline)
        spatial = grid_values(albedo) + grid_values(roughness, color=False)
        material = json.loads((target_dir / "material.json").read_text("utf8"))
        context = {"target": material, "material_context": calibration_context(job, spatial)}
        features = material_features(context)
        signature = np.asarray(features, dtype=np.float32).tobytes()
        if signature in observed and observed[signature] != case["split"]:
            raise ValueError("相同实际输入特征跨越集合，须在训练前修正材质族划分")
        observed[signature] = case["split"]
        candidates = json.loads((target_dir / "candidates.json").read_text("utf8"))
        losses = []
        for candidate in candidates:
            image = read_exr(target_dir / (candidate["id"] + ".exr"))
            comparison_mask(source, image)
            losses.append(image_loss(source, image, mask))
        best = int(np.argmin(losses))
        threshold = job["conditions"]["search_min_relative_improvement"]
        if losses[best] >= losses[0] * (1 - threshold):
            best = 0
        changes = [
            candidates[best]["parameters"][i] - candidates[0]["parameters"][i] for i in range(2)
        ]
        rows.append(
            {
                "id": case["id"],
                "asset": case["asset"],
                "family": case["family"],
                "split": case["split"],
                "features": features,
                "spatial": spatial,
                "label_delta": changes,
                "baseline_loss": losses[0],
                "selected_loss": losses[best],
                "selected_candidate": candidates[best]["id"],
                "candidate_losses": losses,
            }
        )
    return rows
