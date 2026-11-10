"""Maya 导入控制层，兼容 Python 3.7；所有转换通过公开 RelayService。"""

import os
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from iris_relay.protocol import MAX_JSON_BYTES, ValidationError
from iris_relay.transport import JsonCommandService

ROOT = Path(__file__).resolve().parents[2]


def request(service, operation, **payload):
    """核对公开响应版本与请求 ID，避免将无效响应应用到场景。"""
    identifier = str(uuid4())
    response = service.handle(
        {"api_version": "1", "request_id": identifier, "operation": operation, "payload": payload}
    )
    if (
        not isinstance(response, dict)
        or response.get("api_version") != "1"
        or response.get("request_id") != identifier
        or response.get("status") not in {"ok", "warning", "error"}
    ):
        raise ValidationError("核心响应协议或请求 ID 不匹配", "INVALID_RESPONSE")
    return response


def create_service(root=ROOT):
    """复用公开子进程服务，Maya 3.7 不加载 3.11 核心或安装 MaterialX。"""
    root = Path(root).resolve()
    python = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file():
        raise ValidationError("仓库缺少 .venv，请先在仓库运行 uv sync --extra dev")
    environment = dict(os.environ)
    for key in ("PYTHONHOME", "PYTHONPATH"):
        environment.pop(key, None)
    environment["PYTHONUTF8"] = "1"
    return JsonCommandService(
        [str(python), "-m", "iris_relay", "--dictionaries", str(root / "dictionaries")],
        cwd=str(root),
        env=environment,
    )


def check_import(adapter, service, path, target):
    """读取单份文档，核心转换后解析资源；检查过程不创建 Maya 节点。"""
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
        material = deepcopy(payload["material"])
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
    except (OSError, ValueError, RuntimeError) as exc:
        result["message"] = str(exc)
        result["issues"].append(
            {
                "severity": "error",
                "code": getattr(exc, "code", "IMPORT_CHECK_FAILED"),
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
    except (ValueError, RuntimeError) as exc:
        result.update(status="error", message=str(exc))
        result["issues"].append(
            {"severity": "error", "code": "HOST_CREATE_FAILED", "message": str(exc)}
        )
    return result
