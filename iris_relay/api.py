"""所有插件共用的版本化服务接口；传输层不参与转换语义。"""

from copy import deepcopy
from pathlib import Path
from typing import Protocol

from .conversion import convert, status_for, transcode
from .correction import JointPredictor, calibrate
from .dictionary import Registry
from .model import Material, ValidationError, fields, json_value, load_json, renderer_ref, text
from .semantics import describe_semantics


class RelayService(Protocol):
    """本地 SDK、子进程客户端或未来的远程客户端实现同一协议。"""

    def handle(self, request: dict) -> dict: ...


class RelayAPI:
    api_version = "1"

    def __init__(self, profiles=(), *, predictor: JointPredictor | None = None):
        self._registry = Registry(profiles)
        self._predictor = predictor
        self._materialx = None

    @property
    def _mx(self):
        if self._materialx is None:
            try:
                from .materialx_core import MaterialXCore
            except ImportError as exc:
                raise ValidationError(
                    "当前 Python 缺少可加载的 MaterialX SDK，请安装项目依赖",
                    "MATERIALX_UNAVAILABLE",
                ) from exc
            self._materialx = MaterialXCore()
        return self._materialx

    def _materialx_capabilities(self):
        try:
            return self._mx.capabilities()
        except ValidationError as exc:
            if exc.code != "MATERIALX_UNAVAILABLE":
                raise
            return {"available": False, "reason": str(exc)}

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
                    "operations": [
                        "capabilities",
                        "semantics",
                        "identify",
                        "describe",
                        "validate",
                        "convert",
                        "materialx.definitions",
                        "materialx.validate",
                        "materialx.normalize",
                        "materialx.export",
                        "materialx.preserve",
                        "materialx.convert",
                    ],
                    "semantic_schema_version": "1",
                    "material_schema_version": "1",
                    "calibration_available": self._predictor is not None,
                    "appearance_verified": False,
                    "materialx": self._materialx_capabilities(),
                }
                status = "ok"
            elif operation == "materialx.definitions":
                result = self._mx.definitions(payload)
                status = "ok"
            elif operation in {"materialx.validate", "materialx.normalize"}:
                result = self._mx.process(payload, normalize=operation == "materialx.normalize")
                status = status_for(result["issues"])
            elif operation == "materialx.export":
                core = self._mx
                from .materialx_bridge import export_material

                fields(payload, {"material"})
                material = Material.parse(payload["material"])
                dictionary = self._registry.get(material.data["renderer"])
                result = export_material(core, material, dictionary)
                status = status_for(result["report"]["issues"])
            elif operation == "materialx.preserve":
                from .materialx_preserve import preserve_snapshot

                fields(payload, {"snapshot"})
                result = preserve_snapshot(self._mx, payload["snapshot"])
                status = status_for(result["report"]["issues"])
            elif operation == "materialx.convert":
                core = self._mx
                from .materialx_bridge import convert_document

                fields(payload, {"document", "target"}, {"material_name"})
                target = renderer_ref(payload["target"])
                name = (
                    text(payload["material_name"], "材质名称")
                    if "material_name" in payload
                    else None
                )
                dictionary = self._registry.get(target)
                result = convert_document(core, payload["document"], dictionary, target, name)
                status = status_for(result["report"]["issues"])
            elif operation == "semantics":
                fields(payload, set())
                result = describe_semantics()
                status = "ok"
            elif operation == "identify":
                fields(payload, {"renderer", "native_id", "properties"}, {"implementation_hint"})
                dictionary = self._registry.get(renderer_ref(payload["renderer"]))
                result = dictionary.identify(
                    payload["native_id"], payload["properties"], payload.get("implementation_hint")
                )
                status = "ok"
            elif operation == "describe":
                fields(payload, {"renderer"})
                dictionary = self._registry.get(renderer_ref(payload["renderer"]))
                result = {
                    "dictionary": deepcopy(dictionary.data),
                    "fingerprint": dictionary.fingerprint,
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
                valid = not any(item["severity"] == "error" for item in issues)
                result = {"valid": valid, "issues": issues}
                status = (
                    "error"
                    if not valid
                    else "warning"
                    if any(item["severity"] == "warning" for item in issues)
                    else "ok"
                )
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
                    if any(item["severity"] == "warning" for item in result["report"]["issues"])
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
