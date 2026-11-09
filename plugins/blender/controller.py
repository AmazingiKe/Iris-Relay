"""Blender 导出流程：协调公开 API 和文件保存，不实现材质转换规则。

只检查：check_material → adapter 读取 → materialx.export → materialx.validate。
保存文件：export_material → 检查路径 → check_material → 警告确认 → 排他写入。
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from iris_relay import RelayAPI
from iris_relay.model import ValidationError
from iris_relay.plugin import JsonCommandService

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class ExportResult:
    """本次操作的统一结果，供 UI 显示诊断、复制报告及判断是否已保存。"""

    status: str = "error"
    message: str = "材质尚未检查"
    issues: list = field(default_factory=list)
    export_response: dict | None = None
    validation_response: dict | None = None
    saved_path: str | None = None
    source_snapshot: dict | None = None


def request(service, operation, **payload):
    """通过公开 API 发送请求，并校验响应版本、请求 ID 和状态。"""
    request_id = str(uuid4())
    response = service.handle(
        {
            "api_version": "1",
            "request_id": request_id,
            "operation": operation,
            "payload": payload,
        }
    )
    if (
        not isinstance(response, dict)
        or response.get("api_version") != "1"
        or response.get("request_id") != request_id
        or response.get("status") not in {"ok", "warning", "error"}
    ):
        raise ValidationError("核心响应协议或请求 ID 不匹配", "INVALID_RESPONSE")
    return response


def create_service(root=ROOT):
    """优先使用仓库环境，避免向 Blender 安装依赖或替换它自带的 SDK。"""
    # 1. 选择仓库虚拟环境的 JSON 子进程服务，或回退到当前进程服务。
    root = Path(root).resolve()
    python = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if python.is_file():
        service = JsonCommandService(
            [str(python), "-m", "iris_relay", "--dictionaries", str(root / "dictionaries")],
            cwd=root,
        )
    else:
        service = RelayAPI.from_directory(root / "dictionaries")
    # 2. 通过公开能力查询确认 SDK 版本，避免等到实际导出才发现环境不兼容。
    response = request(service, "capabilities")
    materialx = (response.get("result") or {}).get("materialx", {})
    if not materialx.get("available") or materialx.get("version") != "1.39.5":
        raise ValidationError(
            "核心需要 MaterialX 1.39.5，请检查仓库 .venv 的项目依赖；无需安装 Blender 插件",
            "MATERIALX_UNAVAILABLE",
        )
    return service


def failure(result, code, message):
    """在保留已有报告的基础上追加错误，并将操作状态标记为失败。"""
    result.status = "error"
    result.message = message
    result.issues.append(
        {"severity": "error", "code": code, "message": message, "node": None, "port": None}
    )
    return result


def response_issues(response):
    """统一提取核心响应中的诊断；顶层错误没有对应条目时补入列表。"""
    payload = response.get("result") or {}
    issues = list(payload.get("report", {}).get("issues", payload.get("issues", [])))
    if response.get("error") and not any(i["severity"] == "error" for i in issues):
        issues.append(dict(response["error"], severity="error", node=None, port=None))
    return issues


def check_material(adapter, service):
    """读取当前源图，导出并再次校验文档；任何诊断和源快照均留在结果中。"""
    result = ExportResult()
    try:
        # 1. 读取当前宿主材质；即使读取失败，也保留已有快照和诊断。
        source = _read_source_with_diagnostics(adapter, result)

        # 2. 请求核心生成 MaterialX 文本；核心报告阻断时立即返回。
        result.export_response = request(service, "materialx.export", material=source)
        result.issues.extend(response_issues(result.export_response))
        if result.export_response["status"] == "error":
            result.message = "材质导出被阻断，请查看诊断"
            return result
        # 3. 确认拿到了文档，并通过独立校验请求再次验证。
        document = (result.export_response.get("result") or {}).get("document")
        if not isinstance(document, str) or not document.strip():
            return failure(result, "INVALID_RESPONSE", "核心没有返回 MaterialX 文档")
        result.validation_response = request(service, "materialx.validate", document=document)
        result.issues.extend(response_issues(result.validation_response))
        valid = (result.validation_response.get("result") or {}).get("valid")
        if result.validation_response["status"] == "error" or valid is not True:
            return failure(result, "MATERIALX_INVALID", "MaterialX 文档校验失败，未保存文件")
        # 4. 合并宿主与核心诊断；检查通过只表示可以继续选择是否保存。
        if any(i["severity"] == "error" for i in result.issues):
            result.message = "报告包含阻断问题，未保存文件"
            return result
        has_warning = any(i["severity"] == "warning" for i in result.issues)
        result.status = "warning" if has_warning else "ok"
        result.message = "检查完成，有警告；尚未保存" if has_warning else "检查通过，尚未保存"
    except (ValidationError, OSError, ValueError) as exc:
        failure(result, getattr(exc, "code", "READ_FAILED"), str(exc))
    return result


def _read_source_with_diagnostics(adapter, result):
    """调用宿主读取并返回原生图；finally 确保读取失败时也能显示来源问题。"""
    try:
        return adapter.read_material()
    finally:
        result.source_snapshot = getattr(adapter, "source_snapshot", None)
        result.issues.extend(getattr(adapter, "read_issues", []))


def output_path(filepath):
    """验证输出为尚不存在的绝对 .mtlx 路径，返回 Path；不创建任何文件。"""
    raw = str(filepath).strip()
    if not raw:
        raise ValidationError("请选择输出文件路径", "INVALID_PATH")
    path = Path(raw)
    if raw.startswith("//") or not path.is_absolute():
        raise ValidationError("请选择输出文件的绝对路径", "INVALID_PATH")
    if path.suffix.lower() != ".mtlx":
        raise ValidationError("输出文件扩展名需要为 .mtlx", "INVALID_PATH")
    if not path.parent.is_dir():
        raise ValidationError("输出目录不存在，请重新选择", "INVALID_PATH")
    if path.exists():
        raise ValidationError("目标路径已存在，请选择新的文件名", "FILE_EXISTS")
    return path


def export_material(adapter, service, filepath, *, accept_warnings=False):
    """检查并保存新 .mtlx；错误或未接受的警告不会写入，已有文件不会覆盖。"""
    # 1. 先验证路径，路径无效时不读取材质、不调用核心转换。
    result = ExportResult()
    try:
        path = output_path(filepath)
    except (ValidationError, OSError, ValueError) as exc:
        return failure(result, getattr(exc, "code", "INVALID_PATH"), str(exc))
    # 2. 重新读取和检查，避免使用上一次 GUI 检查留下的过期结果。
    result = check_material(adapter, service)
    if result.status == "error":
        return result
    # 3. 检查警告接受选项；即使文档合法，也不能自动接受近似或位移排除。
    if result.status == "warning" and not accept_warnings:
        result.message = "尚未保存：请查看警告并选择是否允许带警告导出"
        return result
    # 4. 排他写入新文件；出错时只清理本次已创建的残留。
    created = False
    try:
        # 排他创建同时处理检查之后发生的同名文件竞争；不覆盖现有文件。
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            created = True
            stream.write(result.export_response["result"]["document"])
    except (OSError, ValueError) as exc:
        if created:
            try:
                path.unlink(missing_ok=True)
            except OSError as cleanup_error:
                return failure(
                    result, "WRITE_FAILED", f"保存失败，残留文件需手动清理：{cleanup_error}"
                )
        return failure(result, "WRITE_FAILED", f"保存失败：{exc}")
    # 5. 只有写入完成后才设置 saved_path，让 UI 明确区分检查通过与已保存。
    result.saved_path = str(path)
    result.message = "已导出 .mtlx（含警告）" if result.status == "warning" else "已导出 .mtlx"
    return result
