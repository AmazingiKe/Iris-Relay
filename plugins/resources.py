"""宿主共用的只读资源检查；文件通道与宿主扩展后的像素缓存通道分别处理。"""

import struct
from pathlib import Path

from iris_relay.protocol import ValidationError

LIMIT = 1024 * 1024


def _cstring(data, offset):
    """读取有界 EXR 头字符串，截断或非 ASCII 字段明确报错。"""
    end = data.find(b"\0", offset)
    if end < 0 or end - offset > 255:
        raise ValidationError("EXR 头字符串截断或超限", "IMAGE_HEADER_UNSUPPORTED")
    return data[offset:end].decode("ascii"), end + 1


def _exr_channels(data):
    """读取普通单部件 EXR 的 chlist，拒绝深度、多部件和未知分层通道。"""
    if len(data) < 8 or struct.unpack_from("<I", data, 4)[0] & (0x800 | 0x1000):
        raise ValidationError("EXR 深度或多部件头尚未支持", "IMAGE_HEADER_UNSUPPORTED")
    offset = 8
    while offset < len(data):
        name, offset = _cstring(data, offset)
        if not name:
            break
        kind, offset = _cstring(data, offset)
        if offset + 4 > len(data):
            break
        size = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        if size > LIMIT or offset + size > len(data):
            break
        if name == "channels" and kind == "chlist":
            content = data[offset : offset + size]
            position, names = 0, []
            while position < len(content):
                channel, position = _cstring(content, position)
                if not channel:
                    if names == ["Y"]:
                        return 1
                    if len(names) == len(set(names)) and set(names) in (
                        {"R", "G", "B"},
                        {"R", "G", "B", "A"},
                    ):
                        return len(names)
                    raise ValidationError(
                        "EXR 仅核验 Y、RGB 或 RGBA 通道", "IMAGE_HEADER_UNSUPPORTED"
                    )
                if position + 16 > len(content):
                    break
                names.append(channel)
                position += 16
            break
        offset += size
    raise ValidationError("EXR 通道头截断、缺失或超限", "IMAGE_HEADER_UNSUPPORTED")


def image_header_channels(path):
    """返回已核验 PNG/EXR 的解码通道数；其他格式返回 None，由宿主解码器核验。"""
    with Path(path).open("rb") as stream:
        data = stream.read(32)
        if data.startswith(b"\x76\x2f\x31\x01"):
            data += stream.read(LIMIT - len(data))
            return _exr_channels(data)
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return _png_channels(stream, data)
    return None


def _png_channels(stream, data):
    """读取 IDAT 之前的透明度声明，不读取像素块，也不把调色板索引当灰度。"""
    if len(data) < 26 or data[12:16] != b"IHDR":
        raise ValidationError("PNG 头截断或缺失", "IMAGE_HEADER_UNSUPPORTED")
    kind = data[25]
    if kind == 6:
        return 4
    if kind == 4:
        raise ValidationError("灰度加 Alpha 双通道 PNG 尚未支持", "IMAGE_HEADER_UNSUPPORTED")
    if kind not in (0, 2, 3):
        raise ValidationError("PNG 颜色类型未知", "IMAGE_HEADER_UNSUPPORTED")
    offset, alpha = 8, False
    stream.seek(offset)
    while offset + 8 <= LIMIT:
        header = stream.read(8)
        if len(header) != 8:
            break
        size, chunk = struct.unpack(">I4s", header)
        if chunk in (b"IDAT", b"IEND"):
            if kind == 0 and alpha:
                raise ValidationError(
                    "灰度透明色解码为双通道，尚未支持", "IMAGE_HEADER_UNSUPPORTED"
                )
            return 4 if alpha else 1 if kind == 0 else 3
        if offset + size + 12 > LIMIT:
            break
        alpha |= chunk == b"tRNS"
        stream.seek(size + 4, 1)
        offset += size + 12
    raise ValidationError("PNG 透明度头截断或超限", "IMAGE_HEADER_UNSUPPORTED")
