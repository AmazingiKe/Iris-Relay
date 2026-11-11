"""Blender 导入导出流程：协调公开 API、文件与宿主，不实现材质转换规则。

只检查：check_material → adapter 读取 → materialx.export → materialx.validate。
保存文件：export_material → 检查路径 → check_material → 警告确认 → 排他写入。
导入文件：import_material → check_import → 警告确认 → adapter 创建新材质。
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from iris_relay.model import Material, ValidationError
from iris_relay.transport import JsonSessionService
from plugins.core import http_service_from_environment

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


@dataclass
class ImportResult:
    """导入报告只保存可序列化数据；created_material 是成功创建后的实际名称。"""

    status: str = "error"
    message: str = "文档尚未检查"
    issues: list = field(default_factory=list)
    source_path: str | None = None
    validation_response: dict | None = None
    conversion_response: dict | None = None
    target_material: dict | None = None
    created_material: str | None = None


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


def create_service(root=ROOT, *, transport=None, url=None):
    """显式 HTTP 或原有本地服务均通过能力查询，不在连接失败后切换后端。"""
    # 1. HTTP 为显式选择；未配置时保留仓库子进程优先的原有行为。
    if transport in (None, "AUTO"):
        service = http_service_from_environment()
    elif transport == "HTTP":
        from iris_relay.http_client import HttpRelayService

        service = HttpRelayService(url, timeout=60)
    elif transport == "LOCAL":
        service = None
    else:
        raise ValidationError("未知连接方式：" + str(transport))
    root = Path(root).resolve()
    python = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if service is None and python.is_file():
        service = JsonSessionService(
            [
                str(python),
                "-m",
                "iris_relay",
                "--dictionaries",
                str(root / "dictionaries"),
                "--stream",
            ],
            cwd=root,
        )
    elif service is None:
        from iris_relay import RelayAPI

        service = RelayAPI.from_directory(root / "dictionaries")
    # 2. 通过公开能力查询确认 SDK 版本，避免等到实际导出才发现环境不兼容。
    try:
        response = request(service, "capabilities")
        materialx = (response.get("result") or {}).get("materialx", {})
        if not materialx.get("available") or materialx.get("version") != "1.39.5":
            raise ValidationError(
                "核心需要 MaterialX 1.39.5，请检查所选服务环境的项目依赖；无需安装 Blender 插件",
                "MATERIALX_UNAVAILABLE",
            )
    except Exception:
        if hasattr(service, "close"):
            service.close()
        raise
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


def input_path(filepath):
    """验证输入为已存在的绝对 .mtlx 文件；拒绝 Blender 相对路径的歧义。"""
    raw = str(filepath).strip()
    path = Path(raw)
    if not raw or raw.startswith("//") or not path.is_absolute():
        raise ValidationError("请选择输入 .mtlx 文件的绝对路径", "INVALID_PATH")
    if path.suffix.lower() != ".mtlx" or not path.is_file():
        raise ValidationError("输入需要是已存在的 .mtlx 文件", "INVALID_PATH")
    return path


def _prepare_import_material(response, target, document_path):
    """校验核心目标协议并定位图片；相对图片路径以文档目录为基准，不改原响应。"""
    payload = response.get("result") or {}
    if not isinstance(payload.get("material"), dict):
        raise ValidationError("核心没有返回目标材质", "INVALID_RESPONSE")
    material = Material.parse(payload["material"]).to_dict()
    if material["renderer"] != target:
        raise ValidationError("核心返回的目标版本与请求不一致", "INVALID_RESPONSE")
    for resource in material["textures"].values():
        path = Path(resource["uri"])
        if not path.is_absolute():
            path = document_path.parent / path
        if not path.is_file():
            raise ValidationError(f"贴图文件不存在：{path}", "TEXTURE_NOT_FOUND")
        resource["uri"] = str(path.resolve())
    return material


def check_import(service, filepath, target):
    """只读检查文档、目标转换和贴图；保存完整响应，任何错误均不触碰宿主数据。"""
    result = ImportResult()
    try:
        # 1. 每次重新读取输入，UTF-8 BOM 可接受；限制读取量避免大文件耗尽宿主内存。
        path = input_path(filepath)
        result.source_path = str(path)
        with path.open("rb") as stream:
            content = stream.read(8 * 1024 * 1024 + 1)
        if len(content) > 8 * 1024 * 1024:
            raise ValidationError("输入文档超过 8 MiB 限制", "MATERIALX_LIMIT_EXCEEDED")
        document = content.decode("utf-8-sig")
        # 2. 由核心验证文档和转换能力；UI 与控制层不解析 XML 或猜测节点映射。
        result.validation_response = request(service, "materialx.validate", document=document)
        result.issues.extend(response_issues(result.validation_response))
        if (
            result.validation_response["status"] == "error"
            or (result.validation_response.get("result") or {}).get("valid") is not True
        ):
            return failure(result, "MATERIALX_INVALID", "文档校验失败，未创建材质")
        result.conversion_response = request(
            service, "materialx.convert", document=document, target=target
        )
        result.issues.extend(response_issues(result.conversion_response))
        if result.conversion_response["status"] == "error":
            result.message = "目标转换被阻断，未创建材质"
            return result
        # 3. 校验目标协议与资源；缺图时在宿主创建之前失败。
        result.target_material = _prepare_import_material(result.conversion_response, target, path)
        if any(i["severity"] == "error" for i in result.issues):
            result.message = "报告包含阻断问题，未创建材质"
            return result
        has_warning = any(i["severity"] == "warning" for i in result.issues) or any(
            response["status"] == "warning"
            for response in (result.validation_response, result.conversion_response)
        )
        result.status = "warning" if has_warning else "ok"
        result.message = (
            "导入检查完成，有警告；尚未创建材质" if has_warning else "检查通过，尚未创建材质"
        )
    except (ValidationError, OSError, ValueError) as exc:
        failure(result, getattr(exc, "code", "IMPORT_READ_FAILED"), str(exc))
    return result


def import_material(adapter, service, filepath, target, *, accept_warnings=False):
    """重新检查并创建独立材质；不替换绑定，失败清理由宿主适配器保证。"""
    # 1. 不复用预览结果，防止检查之后文件或引用图片发生变化。
    result = check_import(service, filepath, target)
    if result.status == "error":
        return result
    # 2. 近似警告默认阻止创建；允许警告不能绕过核心阻断或缺失图片。
    if result.status == "warning" and not accept_warnings:
        result.message = "尚未导入：请查看警告并选择是否允许带警告导入"
        return result
    # 3. 只有宿主创建成功才记录材质名，失败时保留核心报告供诊断。
    try:
        created = adapter.create_material(result.target_material)
        result.created_material = created.name
    except Exception as exc:
        return failure(result, getattr(exc, "code", "IMPORT_FAILED"), f"创建材质失败：{exc}")
    result.message = f"已导入为新材质：{result.created_material}"
    return result
