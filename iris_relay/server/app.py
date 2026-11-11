"""HTTP 传输适配：有界接收 JSON，再委托统一服务处理原始请求。"""

import asyncio
import json
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from starlette.exceptions import HTTPException
from starlette.requests import ClientDisconnect
from starlette.responses import JSONResponse, Response

from ..protocol import MAX_JSON_BYTES, ValidationError, json_value, parse_json
from .runtime import ServerSettings, ServiceBusyError, ServiceRuntime, is_loopback

# 仅用于 OpenAPI 展示；运行时业务校验仍完全由 RelayAPI 执行。
REQUEST_SCHEMA = {
    "type": "object",
    "required": ["api_version", "request_id", "operation", "payload"],
    "additionalProperties": False,
    "properties": {
        "api_version": {"type": "string", "const": "1"},
        "request_id": {"type": "string"},
        "operation": {"type": "string"},
        "payload": {"type": "object", "description": "具体字段遵循统一 API 的 operation 定义"},
    },
}
RESPONSE_SCHEMA = {
    "type": "object",
    "required": ["api_version", "request_id", "status", "result", "error"],
    "properties": {
        "api_version": {"type": "string", "const": "1"},
        "request_id": {"type": ["string", "null"]},
        "status": {"type": "string", "enum": ["ok", "warning", "error"]},
        "result": {"description": "核心原始结果；业务阻断时也可包含诊断报告"},
        "error": {"type": ["object", "null"]},
    },
}


def error_response(status, code, message, request_id=None):
    """使用统一响应信封表达传输失败，HTTP 状态与业务响应分开。"""
    # 非法的巨大请求 ID 不应再次放大传输错误响应；正常业务响应保持原值。
    if isinstance(request_id, str) and len(request_id) > 1024:
        request_id = None
    if isinstance(request_id, str):
        try:
            request_id.encode("utf-8")
        except UnicodeEncodeError:
            request_id = None
    return JSONResponse(
        {
            "api_version": "1",
            "request_id": request_id,
            "status": "error",
            "result": None,
            "error": {"code": code, "message": message},
        },
        status_code=status,
        headers={"Cache-Control": "no-store"},
    )


async def read_body(request, timeout):
    """无论是否声明 Content-Length，都在累积前检查每段数据的上限。"""
    body = bytearray()
    async with asyncio.timeout(timeout):
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_JSON_BYTES:
                raise OverflowError("请求超过大小限制")
            body.extend(chunk)
    return bytes(body)


def allowed_host(value):
    """直接校验原始 Host，避免 URL 包装器对畸形 Host 回退到监听地址。"""
    if not value or len(value) > 256 or any(c.isspace() for c in value):
        return False
    try:
        parsed = urlsplit("//" + value)
        return (
            not parsed.path
            and not parsed.query
            and not parsed.fragment
            and parsed.username is None
            and parsed.password is None
            and (parsed.port is None or 1 <= parsed.port <= 65535)
            and is_loopback(parsed.hostname or "")
        )
    except ValueError:
        return False


def create_app(service=None, settings=None):
    """创建独立应用；可注入同一 RelayService，默认在 lifespan 内加载正式核心。"""
    settings = settings or ServerSettings()
    runtime = ServiceRuntime(settings, service)

    @asynccontextmanager
    async def lifespan(app):
        await asyncio.to_thread(runtime.start)
        try:
            yield
        finally:
            await asyncio.to_thread(runtime.close)

    app = FastAPI(title="Iris Relay", version="1", lifespan=lifespan)
    app.state.runtime = runtime

    @app.middleware("http")
    async def local_transport(request: Request, call_next):
        # Host 白名单阻断 DNS 重绑定；Origin 拒绝浏览器跨站发起调用。
        if "origin" in request.headers or not allowed_host(request.headers.get("host", "")):
            return error_response(403, "TRANSPORT_FORBIDDEN", "仅接受本机 DCC 客户端请求")
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(HTTPException)
    async def routing_error(request, exc):
        return error_response(exc.status_code, "HTTP_ERROR", "HTTP 路径或方法不受支持")

    @app.get("/health")
    async def health():
        """只报告服务就绪，不读取或泄露字典、模型和本机路径。"""
        if runtime.service is None:
            return error_response(503, "SERVICE_UNAVAILABLE", "服务尚未就绪")
        return {"status": "ok", "api_version": "1"}

    @app.post(
        "/v1/handle",
        summary="调用统一材质 API",
        responses={
            code: {
                "description": "核心业务响应" if code == 200 else "传输失败",
                "content": {"application/json": {"schema": RESPONSE_SCHEMA}},
            }
            for code in (200, 400, 403, 408, 413, 415, 500, 503)
        },
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": REQUEST_SCHEMA}},
            }
        },
    )
    async def handle(request: Request):
        """校验传输、读取原始 JSON，再串行调用核心；不重写业务参数或结果。"""
        # 1. 检查传输格式，压缩体和浏览器表单不进入业务层。
        if (
            request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            != "application/json"
        ):
            return error_response(415, "UNSUPPORTED_MEDIA_TYPE", "请求必须使用 application/json")
        if request.headers.get("content-encoding", "identity").lower() != "identity":
            return error_response(415, "UNSUPPORTED_ENCODING", "不接受压缩请求体")
        length = request.headers.get("content-length")
        if length is not None:
            if not length.isascii() or not length.isdecimal():
                return error_response(400, "INVALID_REQUEST", "Content-Length 无效")
            normalized_length = length.lstrip("0") or "0"
            if len(normalized_length) > 10 or int(normalized_length) > MAX_JSON_BYTES:
                return error_response(413, "REQUEST_TOO_LARGE", "请求超过大小限制")

        # 2. 流式限制大小及读取时间；JSON 严格校验共用现有协议实现。
        try:
            payload = parse_json(await read_body(request, settings.read_timeout))
        except OverflowError:
            return error_response(413, "REQUEST_TOO_LARGE", "请求超过大小限制")
        except TimeoutError:
            return error_response(408, "REQUEST_TIMEOUT", "请求体读取超时")
        except (ValidationError, ValueError, ClientDisconnect):
            return error_response(400, "INVALID_REQUEST", "请求不是有效的 UTF-8 JSON")
        request_id = payload.get("request_id") if isinstance(payload, dict) else None
        if not isinstance(request_id, str):
            request_id = None

        # 3. 忙请求立即拒绝；客户端取消不会让仍在执行的核心与新请求并发。
        try:
            future = runtime.submit(payload)
            result = await asyncio.shield(asyncio.wrap_future(future))
            json_value(result)
            # JSON 可包含转义的孤立 surrogate；保留核心接受的值，避免 UTF-8 编码崩溃。
            content = json.dumps(result, ensure_ascii=True, allow_nan=False).encode("ascii")
            if len(content) > MAX_JSON_BYTES:
                return error_response(500, "RESPONSE_TOO_LARGE", "响应超过大小限制", request_id)
            return Response(content, media_type="application/json")
        except ServiceBusyError:
            return error_response(503, "SERVICE_BUSY", "服务正在处理另一个请求", request_id)
        except Exception:
            return error_response(
                500, "INTERNAL_ERROR", "核心调用失败，请检查服务端配置", request_id
            )

    return app
