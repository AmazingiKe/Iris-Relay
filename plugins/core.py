"""宿主共用的 MaterialX 文件流程；兼容 Python 3.7，仅依赖公开 RelayService。"""

import os
from pathlib import Path

from iris_relay.model import Material
from iris_relay.native import request as request
from iris_relay.protocol import MAX_JSON_BYTES, ValidationError
from iris_relay.transport import JsonSessionService

ROOT = Path(__file__).resolve().parents[1]


def create_service(root=ROOT):
    """隔离宿主与核心 Python，清理仅传给核心子进程的宿主环境变量。"""
    root = Path(root).resolve()
    python = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file():
        raise ValidationError("仓库缺少 .venv，请先在仓库运行 uv sync --extra dev")
    environment = dict(os.environ)
    for key in ("PYTHONHOME", "PYTHONPATH"):
        environment.pop(key, None)
    environment["PYTHONUTF8"] = "1"
    return JsonSessionService(
        [str(python), "-m", "iris_relay", "--dictionaries", str(root / "dictionaries"), "--stream"],
        cwd=str(root),
        env=environment,
    )


def check_import(adapter, service, path, target):
    """读取单份文档，核心转换后解析资源；检查过程不创建宿主节点。"""
    path = Path(path).expanduser().resolve()
    result = {
        "status": "error",
        "source_path": str(path),
        "message": "尚未检查",
        "issues": [],
        "response": None,
        "material": None,
        "created": None,
        "appearance_verified": False,
    }
    try:
        # 1. 每次读取当前文件并验证，禁止沿用上次检查结果创建材质。
        with path.open("rb") as stream:
            content = stream.read(MAX_JSON_BYTES + 1)
        if len(content) > MAX_JSON_BYTES:
            raise ValidationError("MaterialX 文件超过 16 MiB")
        response = request(
            service, "materialx.convert", document=content.decode("utf-8-sig"), target=target
        )
        result["response"] = response
        payload = response.get("result") or {}
        result["issues"] = list(payload.get("report", {}).get("issues", []))
        if response["status"] == "error" or payload.get("material") is None:
            result["message"] = (response.get("error") or {}).get("message", "核心阻断转换")
            return result
        # 2. 资源相对路径以文档目录为准，只引用现有文件，不复制或生成贴图。
        try:
            material = Material.parse(payload["material"]).to_dict()
        except ValidationError as exc:
            raise ValidationError("核心返回了非法材质图：" + str(exc), "INVALID_RESPONSE") from exc
        if material.get("renderer") != target:
            raise ValidationError("核心返回的目标版本与请求不同", "INVALID_RESPONSE")
        for resource in material["textures"].values():
            image = Path(resource["uri"])
            if not image.is_absolute():
                image = path.parent / image
            image = image.resolve()
            if not image.is_file():
                raise ValidationError("找不到贴图：" + str(image), "TEXTURE_MISSING")
            resource["uri"] = image.as_posix()
        result["issues"].extend(adapter.preflight(material))
        result.update(
            material=material,
            status="warning" if any(i["severity"] == "warning" for i in result["issues"]) else "ok",
            message="可导入为新材质",
        )
    except Exception as exc:
        result["message"] = str(exc)
        result["issues"].append(
            {
                "severity": "error",
                "code": getattr(exc, "code", "IMPORT_CHECK_FAILED"),
                "message": str(exc),
            }
        )
    return result


def export_material(adapter, service, surface, path, *, accept_warnings=False):
    """重新读取当前宿主图并排他保存；转换与文档校验均通过统一 API。"""
    result = {
        "status": "error",
        "issues": [],
        "message": "尚未导出",
        "saved_path": None,
        "material": None,
        "response": None,
        "appearance_verified": False,
    }
    try:
        # 1. 验证新输出位置，再从当前节点读取，禁止使用导入时的缓存文档。
        path = Path(path).expanduser()
        if not path.is_absolute() or path.suffix.lower() != ".mtlx":
            raise ValidationError("输出须为绝对路径的 .mtlx 文件", "INVALID_PATH")
        if path.exists():
            raise ValidationError("输出文件已存在，原文件未修改", "FILE_EXISTS")
        result["material"] = adapter.read_material(surface)
        response = request(service, "materialx.export", material=result["material"])
        result["response"] = response
        payload = response.get("result") or {}
        result["issues"] = list(payload.get("report", {}).get("issues", []))
        if response["status"] == "error" or not isinstance(payload.get("document"), str):
            result["message"] = "当前材质不能完整导出，详见诊断"
            return result
        validation = request(service, "materialx.validate", document=payload["document"])
        result["validation"] = validation
        if validation["status"] == "error" or not (validation.get("result") or {}).get("valid"):
            raise ValidationError("导出文档未通过校验", "MATERIALX_INVALID")
        result["status"] = response["status"]
        if result["status"] == "warning" and not accept_warnings:
            result["message"] = "存在警告；明确允许后才保存"
            return result
        # 2. 排他写入，只有本次确实打开的文件才能在写入异常后清理。
        opened = False
        try:
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                opened = True
                stream.write(payload["document"])
        except OSError:
            if opened:
                path.unlink()
            raise
        result.update(saved_path=str(path), message="已导出：" + str(path))
    except Exception as exc:
        result.update(status="error", message=str(exc))
        result["issues"].append(
            {
                "severity": "error",
                "code": getattr(exc, "code", "EXPORT_FAILED"),
                "message": str(exc),
            }
        )
    return result


def import_material(adapter, service, path, target, *, accept_warnings=False):
    """检查通过后创建可撤销的新材质；警告默认需用户明确允许。"""
    result = check_import(adapter, service, path, target)
    if result["status"] == "error":
        return result
    if result["status"] == "warning" and not accept_warnings:
        result["message"] = "存在警告；勾选允许带警告导入后重试"
        return result
    try:
        result["created"] = adapter.create_material(result["material"])
        result["message"] = "已创建：" + result["created"]["material"]
    except Exception as exc:
        result.update(status="error", message=str(exc))
        result["issues"].append(
            {
                "severity": "error",
                "code": getattr(exc, "code", "HOST_CREATE_FAILED"),
                "message": str(exc),
            }
        )
    return result
