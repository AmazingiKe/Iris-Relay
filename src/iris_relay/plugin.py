"""薄插件接入约定；只使用公开 API，不直接访问字典或转换实现。"""

import json
import math
import subprocess
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import uuid4

from .api import RelayService
from .model import Material, ValidationError, parse_json


class JsonCommandService:
    """通过无 shell 的子进程调用同一 API，隔离 DCC 内置 Python 与核心环境。"""

    def __init__(self, command: list[str], *, timeout: float = 60):
        if (
            not isinstance(command, list)
            or not command
            or any(not isinstance(arg, str) or not arg for arg in command)
        ):
            raise ValidationError("command 必须为非空参数列表，不能是 shell 命令字符串")
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ValidationError("timeout 必须为有限正数")
        self._command = list(command)
        self._timeout = timeout

    def handle(self, request: dict) -> dict:
        content = json.dumps(request, ensure_ascii=False, allow_nan=False).encode("utf-8")
        try:
            completed = subprocess.run(
                self._command,
                input=content,
                capture_output=True,
                timeout=self._timeout,
                check=False,
                shell=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired as exc:
            raise ValidationError("核心子进程响应超时", "TRANSPORT_ERROR") from exc
        except OSError as exc:
            raise ValidationError(f"无法启动核心子进程：{exc}", "TRANSPORT_ERROR") from exc
        if completed.returncode not in (0, 2):
            raise ValidationError(
                f"核心子进程异常退出，退出码：{completed.returncode}", "TRANSPORT_ERROR"
            )
        response = parse_json(completed.stdout)
        if not isinstance(response, dict):
            raise ValidationError("核心子进程未返回 JSON 对象", "TRANSPORT_ERROR")
        return response


class MaterialAdapter(Protocol):
    def read_material(self) -> dict:
        """读取选中材质，按统一协议导出完整图信息。"""
        ...

    def create_material(self, material: dict) -> Any:
        """新建材质并返回宿主句柄；由适配器保证失败回滚与源材质保留。"""
        ...


@dataclass(frozen=True)
class PluginResult:
    response: dict
    created: Any = None


def convert_material(
    adapter: MaterialAdapter,
    service: RelayService,
    target: dict,
    *,
    calibrate: bool = False,
    accept_warnings: bool = False,
) -> PluginResult:
    """默认只应用无警告方案；不会自动替换场景中的材质绑定。"""
    request = {
        "api_version": "1",
        "request_id": str(uuid4()),
        "operation": "convert",
        "payload": {
            "material": deepcopy(adapter.read_material()),
            "target": deepcopy(target),
            "calibrate": calibrate,
        },
    }
    response = service.handle(request)
    if response.get("api_version") != "1" or response.get("request_id") != request["request_id"]:
        raise ValidationError("服务返回的 API 版本或请求 ID 不匹配")
    allowed = {"ok", "warning"} if accept_warnings else {"ok"}
    if response.get("status") not in allowed:
        return PluginResult(response)
    result = response.get("result")
    if not isinstance(result, dict) or result.get("material") is None:
        raise ValidationError("转换响应缺少目标材质")
    material = Material.parse(result["material"]).to_dict()
    if material["renderer"] != target:
        raise ValidationError("服务返回的目标渲染器与请求不一致")
    return PluginResult(response, adapter.create_material(material))
