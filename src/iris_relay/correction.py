"""联合校正扩展协议与原子应用；本模块不提供训练好的模型。"""

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Protocol

from .conversion import issue
from .dictionary import Dictionary, validate_value
from .model import Material, ValidationError, text


@dataclass(frozen=True)
class InputChange:
    node: str
    port: str
    value: Any


@dataclass(frozen=True)
class CorrectionProposal:
    changes: list[InputChange]


class JointPredictor(Protocol):
    """一次接收完整材质上下文，返回一组相互关联的修改。"""

    model_id: str

    def predict(self, context: dict) -> CorrectionProposal: ...


def calibrate(result: dict, dictionary: Dictionary, predictor: JointPredictor | None) -> None:
    report = result["report"]
    if result["material"] is None:
        report["calibration"]["status"] = "skipped"
        return
    if predictor is None:
        report["calibration"]["status"] = "unavailable"
        report["issues"].append(
            issue("MODEL_UNAVAILABLE", "未配置模型，保留字典基础转换结果", severity="warning")
        )
        return
    state = {"status": "rejected", "model_id": None, "changes": []}
    report["calibration"] = state
    try:
        state["model_id"] = text(predictor.model_id, "模型标识")
        context = deepcopy(
            {
                "source": result["source"],
                "intermediate": result["intermediate"],
                "target": result["material"],
                "dictionary_versions": report["dictionary_versions"],
            }
        )
        proposal = predictor.predict(context)
        if not isinstance(proposal, CorrectionProposal) or not isinstance(proposal.changes, list):
            raise ValidationError("模型必须返回 CorrectionProposal")
        candidate = deepcopy(result["material"])
        nodes = {node["id"]: node for node in candidate["nodes"]}
        linked = {(link["to"]["node"], link["to"]["socket"]) for link in candidate["links"]}
        seen, changes = set(), []
        for change in proposal.changes:
            if not isinstance(change, InputChange):
                raise ValidationError("提案包含不支持的操作")
            text(change.node, "校正节点 ID")
            text(change.port, "校正端口")
            key = (change.node, change.port)
            if key in seen or key in linked:
                raise ValidationError("校正端口重复或由连接驱动，不能修改默认值冒充有效校正")
            seen.add(key)
            if change.node not in nodes:
                raise ValidationError("校正节点不存在")
            node = nodes[change.node]
            rule = dictionary.nodes[node["type"]]["inputs"].get(change.port)
            if rule is None or not rule.get("adjustable", False):
                raise ValidationError("校正端口不在字典允许范围内")
            validate_value(change.value, rule)
            if node["inputs"][change.port] != change.value:
                changes.append(
                    {
                        "node": change.node,
                        "port": change.port,
                        "before": deepcopy(node["inputs"][change.port]),
                        "after": deepcopy(change.value),
                    }
                )
                node["inputs"][change.port] = deepcopy(change.value)
        candidate = Material.parse(candidate).to_dict()
        # 所有修改验证成功后一次替换；失败时保留完整基础方案。
        result["material"] = candidate
        state.update(status="applied" if changes else "unchanged", changes=changes)
    except Exception as exc:
        # 模型属于可选扩展，异常不能破坏可用的基础转换；不暴露扩展内部堆栈。
        reason = str(exc) if isinstance(exc, ValidationError) else "模型预测失败"
        report["issues"].append(issue("CALIBRATION_REJECTED", reason, severity="warning"))
