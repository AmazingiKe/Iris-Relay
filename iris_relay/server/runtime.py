"""管理本机服务配置、核心实例和有界串行执行，不包含材质转换规则。"""

import ipaddress
import math
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path


def is_loopback(host):
    """只接受明确的本机名称或回环地址，不通过 DNS 猜测监听范围。"""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class ServerSettings:
    """启动时指定的配置；模型和字典路径不接受 HTTP 请求覆盖。"""

    dictionaries: Path = Path("dictionaries")
    model: Path | None = None
    host: str = "127.0.0.1"
    port: int = 8765
    read_timeout: float = 30.0

    def __post_init__(self):
        if not isinstance(self.host, str) or not is_loopback(self.host):
            raise ValueError("首版 HTTP 服务仅支持回环地址，不提供外网部署")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("监听端口必须为 1 到 65535 的整数")
        if (
            type(self.read_timeout) not in (int, float)
            or not math.isfinite(self.read_timeout)
            or self.read_timeout <= 0
        ):
            raise ValueError("请求读取超时必须为有限正数")


def build_service(settings):
    """启动时加载字典和可选 JSON 模型；配置无效时启动失败，不静默忽略模型。"""
    from ..api import RelayAPI

    predictor = None
    if settings.model is not None:
        from ..neural import NeuralJointPredictor

        predictor = NeuralJointPredictor.from_file(settings.model)
    return RelayAPI.from_directory(settings.dictionaries, predictor=predictor)


class ServiceBusyError(RuntimeError):
    """已有请求占用唯一核心执行槽；调用者应显式决定何时重新请求。"""


class ServiceRuntime:
    """复用一个核心和一个工作线程；无排队，客户端断开不提前释放执行槽。"""

    def __init__(self, settings, service=None):
        self.settings = settings
        self.service = service
        self._executor = None
        self._lock = threading.Lock()
        self._busy = False

    def start(self):
        """在应用 lifespan 中初始化一次，失败时不开放请求入口。"""
        if self._executor is not None:
            raise RuntimeError("服务已经启动")
        if self.service is None:
            self.service = build_service(self.settings)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="iris-relay")

    def submit(self, request):
        """立即占用唯一槽并提交；忙碌或关闭时拒绝，不建立隐式任务队列。"""
        with self._lock:
            if self._executor is None:
                raise RuntimeError("服务尚未启动或已经关闭")
            if self._busy:
                raise ServiceBusyError("服务正在处理另一个请求")
            self._busy = True
            try:
                return self._executor.submit(self._handle, request)
            except Exception:
                self._busy = False
                raise

    def _handle(self, request):
        """业务只通过公开 handle 执行；成功和失败都释放槽位。"""
        try:
            return self.service.handle(request)
        finally:
            with self._lock:
                self._busy = False

    def close(self):
        """关闭入口并等待已接受的调用结束，再释放核心和模型引用。"""
        with self._lock:
            executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=False)
        self.service = None
