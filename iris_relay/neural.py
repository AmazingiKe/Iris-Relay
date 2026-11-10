"""受限联合模型的纯 Python 推理；权重和真实效果由独立实验提供，不随核心分发。"""

import json
import math
from copy import deepcopy
from pathlib import Path

from .correction import CorrectionProposal, InputChange
from .model import ValidationError, fields, json_value, text

FEATURE_SCHEMA = "albedo4x4-roughness4x4-params4/v1"
PARAMETERS = ("specularRoughness", "specular", "specularIOR", "metalness")


def numeric_vector(values, length):
    """拒绝非有限、错误维数和布尔，避免坏数据进入联合计算。"""
    if (
        not isinstance(values, list)
        or len(values) != length
        or any(type(v) not in (int, float) or not math.isfinite(v) for v in values)
    ):
        raise ValidationError("联合模型的数值维数或有限性无效", "MODEL_INPUT_UNSUPPORTED")
    return values


def material_features(context):
    """组合实际空间采样与当前目标参数；本轮只接受不透明、无额外叶瓣的表面。"""
    target = context["target"]
    root_id = target["output"]["node"]
    root = next((n for n in target["nodes"] if n["id"] == root_id), None)
    if root is None or root["type"] != "standard_surface":
        raise ValidationError("首轮模型只接受已声明的 Arnold 标准表面", "MODEL_INPUT_UNSUPPORTED")
    if any(
        e["to"]["node"] == root_id and e["to"]["socket"] != "baseColor" for e in target["links"]
    ):
        raise ValidationError(
            "首轮模型不接受连接驱动的粗糙度、法线或其他表面参数", "MODEL_INPUT_UNSUPPORTED"
        )
    values = root["inputs"]
    if values.get("specularColor", [1, 1, 1]) != [1, 1, 1] or any(
        values.get(k, 0) != 0
        for k in ("diffuseRoughness", "specularAnisotropy", "specularRotation")
    ):
        raise ValidationError("首轮模型尚未训练反射染色或各向异性", "MODEL_INPUT_UNSUPPORTED")
    if any(values.get(k, 0) != 0 for k in ("coat", "emission", "sheen")) or values.get(
        "opacity"
    ) != [1, 1, 1]:
        raise ValidationError("首轮模型尚未训练透明、涂层、发光或 sheen", "MODEL_INPUT_UNSUPPORTED")
    parameters = numeric_vector([values.get(p) for p in PARAMETERS], 4)
    bounds = ((0.05, 0.95), (0, 1), (1, 3), (0, 1))
    if any(not low <= v <= high for v, (low, high) in zip(parameters, bounds, strict=True)):
        raise ValidationError("参数超出本轮已声明实验域", "MODEL_INPUT_UNSUPPORTED")
    sampled = context.get("material_context", {})
    if sampled.get("feature_schema") != FEATURE_SCHEMA:
        raise ValidationError("缺少本轮真实空间采样上下文", "MODEL_INPUT_UNSUPPORTED")
    spatial = numeric_vector(sampled.get("spatial"), 64)
    if any(not 0 <= x <= 16 for x in spatial[:48]) or any(not 0 <= x <= 1 for x in spatial[48:]):
        raise ValidationError("空间采样超出本轮实验域", "MODEL_INPUT_UNSUPPORTED")
    return spatial + parameters


class NeuralJointPredictor:
    """一次前向联合输出粗糙度和反射权重提案；最终有效性仍由公开校正入口原子核验。"""

    def __init__(self, data):
        json_value(data)
        fields(
            data,
            {
                "format_version",
                "model_id",
                "feature_schema",
                "source_shading_model",
                "target",
                "dictionary_version",
                "conditions_sha256",
                "input_mean",
                "input_std",
                "layers",
                "delta_scale",
            },
            {"training", "metrics"},
        )
        if data["format_version"] != "1" or data["feature_schema"] != FEATURE_SCHEMA:
            raise ValidationError("不支持此模型格式或空间特征", "MODEL_FORMAT_UNSUPPORTED")
        if data["source_shading_model"] != "cycles.principled.MULTI_GGX":
            raise ValidationError("首轮模型尚未训练此源着色模型", "MODEL_INPUT_UNSUPPORTED")
        self.model_id = text(data["model_id"], "模型标识")
        self.data = deepcopy(data)
        numeric_vector(data["input_mean"], 68)
        deviations = numeric_vector(data["input_std"], 68)
        if any(s < 1e-8 for s in deviations):
            raise ValidationError("模型归一化尺度无效")
        scales = numeric_vector(data["delta_scale"], 2)
        if any(s <= 0 or s > maximum for s, maximum in zip(scales, (0.12, 0.25), strict=True)):
            raise ValidationError("模型变化幅度超出本轮允许范围")
        width, total = 68, 0
        layers = data["layers"]
        if not isinstance(layers, list) or not 1 <= len(layers) <= 5:
            raise ValidationError("模型层数超出已验证范围")
        for layer in layers:
            fields(layer, {"weights", "bias", "activation"})
            if not isinstance(layer["weights"], list) or not 1 <= len(layer["weights"]) <= 256:
                raise ValidationError("模型层宽无效")
            numeric_vector(layer["bias"], len(layer["weights"]))
            for row in layer["weights"]:
                numeric_vector(row, width)
                total += width
            if total > 100000 or layer["activation"] not in {"relu", "tanh"}:
                raise ValidationError("模型规模或激活类型不支持")
            width = len(layer["weights"])
        if width != 2 or layers[-1]["activation"] != "tanh":
            raise ValidationError("联合输出必须为两个有界数值")

    @classmethod
    def from_file(cls, path):
        """仅加载调用者显式提供的有界 JSON 权重，不执行 pickle 或任意模型代码。"""
        path = Path(path)
        if path.stat().st_size > 8 * 1024 * 1024:
            raise ValidationError("模型文件超过 8 MiB")
        return cls(json.loads(path.read_text("utf-8")))

    def predict(self, context):
        """验证渲染条件与输入域，按共享隐藏层生成两个关联变化；未知上下文拒绝提案。"""
        data = self.data
        if (
            context.get("material_context", {}).get("source_shading_model")
            != data["source_shading_model"]
        ):
            raise ValidationError("缺少已核验的源着色模型上下文", "MODEL_INPUT_UNSUPPORTED")
        if (
            context["target"]["renderer"] != data["target"]
            or context["dictionary_versions"]["target"] != data["dictionary_version"]
        ):
            raise ValidationError("模型尚未验证当前目标版本", "MODEL_INPUT_UNSUPPORTED")
        if (
            context.get("material_context", {}).get("conditions_sha256")
            != data["conditions_sha256"]
        ):
            raise ValidationError("模型尚未验证当前渲染条件", "MODEL_INPUT_UNSUPPORTED")
        features = material_features(context)
        values = [
            (x - mean) / std
            for x, mean, std in zip(features, data["input_mean"], data["input_std"], strict=True)
        ]
        for layer in data["layers"]:
            computed = [
                sum(w * x for w, x in zip(row, values, strict=True)) + bias
                for row, bias in zip(layer["weights"], layer["bias"], strict=True)
            ]
            numeric_vector(computed, len(computed))
            values = [
                max(0, x) if layer["activation"] == "relu" else math.tanh(x) for x in computed
            ]
        node = context["target"]["output"]["node"]
        changes = []
        for index, (low, high) in enumerate(((0.05, 0.95), (0, 1))):
            value = max(
                low, min(high, features[64 + index] + values[index] * data["delta_scale"][index])
            )
            changes.append(InputChange(node, PARAMETERS[index], value))
        return CorrectionProposal(changes)
