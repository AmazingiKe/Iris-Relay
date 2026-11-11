"""宿主共用的 MaterialX 文件流程；兼容 Python 3.7，仅依赖公开 RelayService。"""

import os
from pathlib import Path

from iris_relay.model import Material
from iris_relay.native import request as request
from iris_relay.protocol import MAX_JSON_BYTES, ValidationError
from iris_relay.transport import JsonSessionService

ROOT = Path(__file__).resolve().parents[1]


def http_service_from_environment():
    """显式配置 HTTP 时创建轻量客户端；未配置返回 None，配置错误不回退。"""
    url = os.environ.get("IRIS_RELAY_HTTP_URL")
    if url is None:
        return None
    if not url.strip():
        raise ValidationError("IRIS_RELAY_HTTP_URL 不能为空；使用默认调用时请移除此变量")
    from iris_relay.http_client import HttpRelayService

    return HttpRelayService(url.strip(), timeout=60)


def create_service(root=ROOT, *, transport=None, url=None):
    """显式选择 HTTP，否则隔离宿主 Python 并清理核心子进程的宿主环境。"""
    if transport not in (None, "AUTO", "LOCAL", "HTTP"):
        raise ValidationError("不支持的服务连接方式")
    service = None
    if transport == "HTTP":
        from iris_relay.http_client import HttpRelayService

        service = HttpRelayService(url, timeout=60)
    elif transport in (None, "AUTO"):
        service = http_service_from_environment()
    if service is not None:
        return service
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


def check_export(adapter, service, surface):
    """只读当前原生图并检查导出文档；不创建文件或修改材质。"""
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
        # 1. 每次读取当前原生图，不沿用上一次检查或导入缓存。
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
        result["message"] = (
            "存在警告，请查看报告" if response["status"] == "warning" else "检查通过，可导出"
        )
    except Exception as exc:
        result.update(status="error", message=str(exc))
        result["issues"].append(
            {
                "severity": "error",
                "code": getattr(exc, "code", "EXPORT_CHECK_FAILED"),
                "message": str(exc),
            }
        )
    return result


def export_material(adapter, service, surface, path, *, accept_warnings=False):
    """检查当前材质后排他保存；输出冲突先拒绝，不沿用界面的旧检查结果。"""
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
        # 1. 先保护输出文件，再重新读取和校验当前材质。
        path = Path(path).expanduser()
        if not path.is_absolute() or path.suffix.lower() != ".mtlx":
            raise ValidationError("输出须为绝对路径的 .mtlx 文件", "INVALID_PATH")
        if path.exists():
            raise ValidationError("输出文件已存在，原文件未修改", "FILE_EXISTS")
        result = check_export(adapter, service, surface)
        if result["status"] == "error":
            return result
        if result["status"] == "warning" and not accept_warnings:
            result["message"] = "存在警告；明确允许后才保存"
            return result
        # 2. 排他写入，只有本次确实打开的文件才能在写入异常后清理。
        opened = False
        try:
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                opened = True
                stream.write(result["response"]["result"]["document"])
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
