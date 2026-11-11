"""Python 3.7 宿主可用的 HTTP 服务客户端；不加载核心或服务端依赖。"""

import json
import math
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .protocol import MAX_JSON_BYTES, ValidationError, json_value, parse_json


class _NoRedirect(HTTPRedirectHandler):
    """不向重定向地址重新发送材质请求，也不把 POST 自动转换为 GET。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _endpoint(base_url):
    """只接受 HTTP(S) 服务根地址；拒绝凭据、路径与隐含控制字符。"""
    if not isinstance(base_url, str) or not base_url:
        raise ValidationError("HTTP 服务地址必须为非空字符串")
    if any(ord(char) <= 32 or ord(char) == 127 for char in base_url):
        raise ValidationError("HTTP 服务地址不能包含空白或控制字符")
    try:
        parts = urlsplit(base_url)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.path not in {"", "/"}
            or "?" in base_url
            or "#" in base_url
            or parts.port == 0
        ):
            raise ValueError("服务地址格式不支持")
    except ValueError as exc:
        raise ValidationError("请使用无凭据、路径、查询和片段的 HTTP(S) 服务根地址") from exc
    return base_url.rstrip("/") + "/v1/handle"


def _validate_response(response, request_id):
    """核对 API 信封和请求关联，业务阻断及其详细报告保持原样。"""
    keys = {"api_version", "request_id", "status", "result", "error"}
    if not isinstance(response, dict) or set(response) != keys:
        raise ValidationError("HTTP 响应缺少统一 API 信封", "INVALID_RESPONSE")
    if response["api_version"] != "1" or response["request_id"] != request_id:
        raise ValidationError("HTTP 响应的 API 版本或请求 ID 不匹配", "INVALID_RESPONSE")
    status = response["status"]
    result = response["result"]
    error = response["error"]
    if status not in ("ok", "warning", "error"):
        raise ValidationError("HTTP 响应状态不受支持", "INVALID_RESPONSE")
    if status == "error":
        if (
            not isinstance(error, dict)
            or set(error) != {"code", "message"}
            or any(not isinstance(error[key], str) or not error[key] for key in error)
            or (result is not None and not isinstance(result, dict))
        ):
            raise ValidationError("HTTP 错误响应格式不合法", "INVALID_RESPONSE")
    elif error is not None or not isinstance(result, dict):
        raise ValidationError("HTTP 成功响应格式不合法", "INVALID_RESPONSE")
    return response


def _http_error_message(incoming, request_id):
    """有界读取可信格式的传输诊断；重定向或损坏内容仅显示 HTTP 状态。"""
    generic = "HTTP 服务返回状态码 " + str(incoming.code)
    if incoming.code < 400 or incoming.headers.get_content_type() != "application/json":
        return generic
    # 错误说明不需要材质大图；较小上限避免错误路径再次占用大量内存。
    limit = min(MAX_JSON_BYTES, 16 * 1024)
    try:
        length = incoming.headers.get("Content-Length")
        if length is not None and not 0 <= int(length) <= limit:
            return generic
        if incoming.headers.get("Content-Encoding", "identity").lower() != "identity":
            return generic
        body = incoming.read(limit + 1)
        if len(body) > limit or (length is not None and len(body) != int(length)):
            return generic
        value = parse_json(body)
        if not isinstance(value, dict) or value.get("request_id") not in (None, request_id):
            return generic
        _validate_response(value, value.get("request_id"))
        if value["status"] != "error":
            return generic
        code, message = value["error"]["code"], value["error"]["message"]
        if len(code) > 128 or len(message) > 2048:
            return generic
        detail = generic + "（" + code + "）：" + message
        detail.encode("utf-8")
        return detail
    except (ValueError, OSError, HTTPException, UnicodeError):
        return generic


class HttpRelayService:
    """通过 HTTP 调用同一 API；只发送一次，失败不会重试或回退到其他服务。"""

    def __init__(self, base_url, *, timeout=60):
        self._url = _endpoint(base_url)
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ValidationError("timeout 必须为有限正数")
        self._timeout = timeout
        # 宿主可能继承代理环境；材质请求只发送到用户明确配置的服务地址。
        self._opener = build_opener(ProxyHandler({}), _NoRedirect())

    def handle(self, request):
        """发送有界 UTF-8 JSON 并返回业务响应；网络或协议失败抛出稳定错误码。"""
        # 1. 只检查可传输性，具体业务字段仍由统一核心校验。
        if not isinstance(request, dict):
            raise ValidationError("HTTP 请求必须为 JSON 对象")
        try:
            json_value(request)
            content = json.dumps(request, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (ValueError, TypeError, RecursionError, UnicodeError) as exc:
            raise ValidationError("HTTP 请求不是有效的 UTF-8 JSON") from exc
        if len(content) > MAX_JSON_BYTES:
            raise ValidationError("HTTP 请求超过传输大小限制")
        outbound = Request(
            self._url,
            data=content,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json",
            },
            method="POST",
        )
        # 2. 限制响应体积，关闭连接后再检查业务协议，不信任网络返回值。
        try:
            with self._opener.open(outbound, timeout=self._timeout) as incoming:
                if incoming.status != 200:
                    raise ValidationError("HTTP 服务返回非成功状态", "TRANSPORT_ERROR")
                if incoming.headers.get_content_type() != "application/json":
                    raise ValidationError("HTTP 服务未返回 JSON 内容", "INVALID_RESPONSE")
                encoding = incoming.headers.get("Content-Encoding", "identity").lower()
                if encoding != "identity":
                    raise ValidationError("HTTP 响应使用了不支持的内容编码", "INVALID_RESPONSE")
                declared_length = incoming.headers.get("Content-Length")
                if declared_length is not None:
                    try:
                        declared_length = int(declared_length)
                    except ValueError as exc:
                        raise ValidationError("HTTP 响应长度不合法", "INVALID_RESPONSE") from exc
                    if declared_length < 0 or declared_length > MAX_JSON_BYTES:
                        raise ValidationError("HTTP 响应超过传输大小限制", "INVALID_RESPONSE")
                body = incoming.read(MAX_JSON_BYTES + 1)
                if len(body) > MAX_JSON_BYTES:
                    raise ValidationError("HTTP 响应超过传输大小限制", "INVALID_RESPONSE")
                if declared_length is not None and len(body) != declared_length:
                    raise ValidationError("HTTP 响应内容不完整", "INVALID_RESPONSE")
        except HTTPError as exc:
            try:
                message = _http_error_message(exc, request.get("request_id"))
            finally:
                exc.close()
            raise ValidationError(message, "TRANSPORT_ERROR") from exc
        except (URLError, OSError, HTTPException, UnicodeError) as exc:
            raise ValidationError("HTTP 服务连接失败、超时或响应中断", "TRANSPORT_ERROR") from exc
        try:
            response = parse_json(body)
        except ValueError as exc:
            raise ValidationError("HTTP 服务返回了非法 JSON", "INVALID_RESPONSE") from exc
        request_id = request.get("request_id")
        return _validate_response(response, request_id if isinstance(request_id, str) else None)
