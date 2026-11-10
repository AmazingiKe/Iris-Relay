"""Python 3.7 及以上宿主可用的公开 JSON 子进程服务；业务协议与核心相同。"""

import json
import math
import subprocess

from .protocol import ValidationError, parse_json


class JsonCommandService:
    """通过无 shell 的子进程调用同一 API，隔离 DCC 内置 Python 与核心环境。"""

    def __init__(self, command, *, timeout: float = 60, cwd=None, env=None):
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
        self._cwd = cwd
        self._env = None if env is None else dict(env)

    def handle(self, request: dict) -> dict:
        """发送 UTF-8 请求并解析完整响应；超时、启动或协议失败时抛出校验错误。"""
        content = json.dumps(request, ensure_ascii=False, allow_nan=False).encode("utf-8")
        try:
            completed = subprocess.run(
                self._command,
                input=content,
                capture_output=True,
                timeout=self._timeout,
                check=False,
                shell=False,
                cwd=self._cwd,
                env=self._env,
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
