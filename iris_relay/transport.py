"""Python 3.7 及以上宿主可用的公开 JSON 子进程服务；业务协议与核心相同。"""

import atexit
import json
import math
import queue
import subprocess
import threading

from .protocol import MAX_JSON_BYTES, ValidationError, parse_json


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


class JsonSessionService(JsonCommandService):
    """会话内复用一个 JSON 行协议核心进程；串行请求、有界响应，关闭后不重启。"""

    def __init__(self, command, *, timeout=60, cwd=None, env=None):
        super().__init__(command, timeout=timeout, cwd=cwd, env=env)
        self._process = None
        self._closed = False
        self._lock = threading.RLock()
        self._responses = queue.Queue(maxsize=1)
        atexit.register(self.close)

    @property
    def pid(self):
        """仅返回当前仍由本对象持有的进程 ID，便于验证和生命周期检查。"""
        return self._process.pid if self._process is not None else None

    def _read_responses(self, process):
        """后台只负责有界读取；超时由请求线程处理，不阻塞宿主主线程退出。"""
        try:
            while True:
                line = process.stdout.readline(MAX_JSON_BYTES + 1)
                self._responses.put(line, timeout=self._timeout)
                if not line or len(line) > MAX_JSON_BYTES or not line.endswith(b"\n"):
                    return
        except (OSError, ValueError, queue.Full):
            return

    def _write_request(self, process, content):
        """管道写入也可能阻塞，放入后台，使宿主等待同样受请求超时限制。"""
        try:
            process.stdin.write(content)
            process.stdin.flush()
        except (OSError, ValueError) as exc:
            try:
                self._responses.put(exc, timeout=self._timeout)
            except queue.Full:
                pass

    def handle(self, request):
        """一次请求对应一行响应；传输失败关闭本次子进程，不重放请求或复用残留输出。"""
        content = json.dumps(request, ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n"
        if len(content) > MAX_JSON_BYTES:
            raise ValidationError("请求超过传输大小限制", "TRANSPORT_ERROR")
        with self._lock:
            if self._closed:
                raise ValidationError("核心会话已关闭，请创建新服务", "TRANSPORT_ERROR")
            try:
                if self._process is None:
                    self._process = subprocess.Popen(
                        self._command,
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                        shell=False,
                        cwd=self._cwd,
                        env=self._env,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    threading.Thread(
                        target=self._read_responses, args=(self._process,), daemon=True
                    ).start()
                threading.Thread(
                    target=self._write_request, args=(self._process, content), daemon=True
                ).start()
                line = self._responses.get(timeout=self._timeout)
                if isinstance(line, Exception):
                    raise ValidationError("核心会话管道写入失败：" + str(line), "TRANSPORT_ERROR")
                if not line or len(line) > MAX_JSON_BYTES or not line.endswith(b"\n"):
                    raise ValidationError("核心会话响应不完整或超过限制", "TRANSPORT_ERROR")
                response = parse_json(line)
                if not isinstance(response, dict):
                    raise ValidationError("核心会话未返回 JSON 对象", "TRANSPORT_ERROR")
                return response
            except queue.Empty as exc:
                self.close()
                raise ValidationError("核心会话响应超时", "TRANSPORT_ERROR") from exc
            except (OSError, ValueError) as exc:
                self.close()
                raise ValidationError("核心会话传输失败：" + str(exc), "TRANSPORT_ERROR") from exc

    def close(self):
        """回收本对象的纯内存核心进程；先解除阻塞管道，不操作其他宿主或用户进程。"""
        with self._lock:
            self._closed = True
            process, self._process = self._process, None
            atexit.unregister(self.close)
            if process is None:
                return
            try:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2)
            finally:
                try:
                    process.stdin.close()
                except OSError:
                    pass
                process.stdout.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
