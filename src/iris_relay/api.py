"""所有插件共用的版本化服务接口；传输层不参与转换语义。"""

from pathlib import Path
from typing import Protocol

from .conversion import convert, transcode
from .correction import JointPredictor, calibrate
from .dictionary import Registry
from .model import Material, ValidationError, fields, json_value, load_json, renderer_ref, text


class RelayService(Protocol):
    """本地 SDK、子进程客户端或未来的远程客户端实现同一协议。"""

    def handle(self, request: dict) -> dict: ...


class RelayAPI:
    api_version = "1"

    def __init__(self, profiles=(), *, predictor: JointPredictor | None = None):
        self._registry = Registry(profiles)
        self._predictor = predictor

    @classmethod
    def from_directory(cls, path: str | Path, *, predictor: JointPredictor | None = None):
        directory = Path(path)
        if not directory.is_dir():
            raise ValidationError(f"字典目录不存在：{directory}")
        files = sorted(directory.glob("*.json"))
        if not files:
            raise ValidationError("字典目录中没有 JSON 字典")
        return cls([load_json(file) for file in files], predictor=predictor)

    def handle(self, request: dict) -> dict:
        response = {
            "api_version": "1",
            "request_id": None,
            "status": "error",
            "result": None,
            "error": None,
        }
        if isinstance(request, dict) and isinstance(request.get("request_id"), str):
            response["request_id"] = request["request_id"]
        try:
            json_value(request)
            fields(request, {"api_version", "request_id", "operation", "payload"})
            text(request["request_id"], "请求 ID")
            if request["api_version"] != "1":
                raise ValidationError("不支持的 API 版本", "UNSUPPORTED_API_VERSION")
            operation = text(request["operation"], "操作")
            payload = request["payload"]
            if operation == "capabilities":
                fields(payload, set())
                result = {
                    "renderers": self._registry.capabilities(),
                    "operations": ["capabilities", "validate", "convert"],
                    "material_schema_version": "1",
                    "calibration_available": self._predictor is not None,
                    "appearance_verified": False,
                }
                status = "ok"
            elif operation == "validate":
                fields(payload, {"material"})
                material = Material.parse(payload["material"])
                dictionary = self._registry.get(material.data["renderer"])
                issues = []
                transcode(
                    material.to_dict(),
                    dictionary,
                    encode=False,
                    renderer={"id": "iris.semantic", "version": "1"},
                    issues=issues,
                )
                result = {"valid": not issues, "issues": issues}
                status = "error" if issues else "ok"
            elif operation == "convert":
                fields(payload, {"material", "target"}, {"calibrate"})
                if type(payload.get("calibrate", False)) is not bool:
                    raise ValidationError("calibrate 必须为布尔值")
                material = Material.parse(payload["material"])
                target = renderer_ref(payload["target"])
                source_dictionary = self._registry.get(material.data["renderer"])
                target_dictionary = self._registry.get(target)
                result = convert(material, source_dictionary, target_dictionary, target)
                if payload.get("calibrate", False):
                    calibrate(result, target_dictionary, self._predictor)
                status = (
                    "error"
                    if result["material"] is None
                    else "warning"
                    if result["report"]["issues"]
                    else "ok"
                )
            else:
                raise ValidationError(f"不支持的操作：{operation}", "UNSUPPORTED_OPERATION")
            response.update(status=status, result=result)
            if status == "error":
                response["error"] = {
                    "code": "MATERIAL_REJECTED",
                    "message": "材质存在阻断问题，请查看报告",
                }
        except ValidationError as exc:
            response["error"] = {"code": exc.code, "message": str(exc)}
        return response
