"""宿主共用的只读资源检查；文件通道与宿主扩展后的像素缓存通道分别处理。"""

import hashlib
import json
import shutil
import struct
from pathlib import Path

from iris_relay.native import request
from iris_relay.protocol import ValidationError

LIMIT = 1024 * 1024


def image_format(path):
    """依据文件魔数识别已核验的 RGBA 格式，不仅依赖扩展名。"""
    with Path(path).open("rb") as stream:
        header = stream.read(8)
    if header == b"\x89PNG\r\n\x1a\n":
        return "png"
    if header[:4] == b"\x76\x2f\x31\x01":
        return "exr"
    return None


def file_sha256(path):
    """流式校验资源；兼容宿主 Python 3.7，不读取全部大贴图到内存。"""
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(data)
    return digest.hexdigest()


def package_materialx(service, source, destination, *, relinks=None):
    """复制为独立便携包；显式重定位缺图，不覆盖任何现有目录或源资源。"""
    source = Path(source).expanduser().resolve()
    destination = Path(destination).expanduser()
    if not destination.is_absolute():
        raise ValidationError("资源包输出目录须为绝对路径", "INVALID_PATH")
    destination = destination.resolve()
    if destination.exists():
        raise FileExistsError(destination)
    if not destination.parent.is_dir():
        raise ValidationError("资源包父目录不存在", "INVALID_PATH")
    document = source.read_text("utf-8-sig")
    response = request(service, "materialx.resources", document=document)
    if response["status"] == "error":
        raise ValidationError("文档资源检查失败：" + response["error"]["message"])
    relinks = relinks or {}
    if not isinstance(relinks, dict):
        raise ValidationError("贴图重定位须为 URI 到本机文件的显式映射")
    planned = []
    used_relinks = set()
    # 1. 在创建输出前确认全部依赖；不访问网络或猜测同名资源。
    for resource in response["result"]["resources"]:
        replacement = next(
            (key for key in (resource["resolved_uri"], resource["uri"]) if key in relinks), None
        )
        if replacement is not None:
            path = Path(relinks[replacement]).expanduser().resolve()
            used_relinks.add(replacement)
        else:
            path = Path(resource["resolved_uri"])
            if not path.is_absolute():
                path = source.parent / path
            path = path.resolve()
        if not path.is_file():
            raise ValidationError(
                "找不到贴图，请显式重定位：" + resource["resolved_uri"], "TEXTURE_MISSING"
            )
        digest = file_sha256(path)
        relative = "textures/" + digest + path.suffix.lower()
        planned.append(
            dict(
                resource,
                source_file=str(path),
                sha256=digest,
                packaged_uri=relative,
                relinked=replacement is not None,
            )
        )
    if set(relinks) != used_relinks:
        raise ValidationError("重定位映射包含未使用的 URI", "RESOURCE_RELINK_UNUSED")
    created_files, created_directories = [], []
    try:
        destination.mkdir()
        created_directories.append(destination)
        textures = destination / "textures"
        if planned:
            textures.mkdir()
            created_directories.append(textures)
        copied = set()
        for resource in planned:
            target = destination / resource["packaged_uri"]
            if target in copied:
                continue
            with target.open("xb") as output:
                created_files.append(target)
                with Path(resource["source_file"]).open("rb") as incoming:
                    shutil.copyfileobj(incoming, output, 1024 * 1024)
            if file_sha256(target) != resource["sha256"]:
                raise ValidationError("贴图在复制期间改变，资源包已取消", "RESOURCE_CHANGED")
            copied.add(target)
        # 2. 改写由核心使用 SDK 完成；输出必须再次通过同一接口校验。
        rewritten = request(
            service,
            "materialx.rewrite_resources",
            document=document,
            resources={
                resource["path"]: {"expected_uri": resource["uri"], "uri": resource["packaged_uri"]}
                for resource in planned
            },
        )
        if rewritten["status"] == "error":
            raise ValidationError("资源文档改写失败：" + rewritten["error"]["message"])
        content = rewritten["result"]["document"]
        validation = request(service, "materialx.validate", document=content)
        if validation["status"] == "error":
            raise ValidationError("资源包文档未通过最终校验", "MATERIALX_INVALID")
        result = {
            "schema_version": "1",
            "document": source.name,
            "resource_count": len(planned),
            "copied_files": len(copied),
            "source_sha256": hashlib.sha256(document.encode("utf-8")).hexdigest(),
            "document_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "resources": planned,
            "appearance_verified": False,
        }
        for path, data in (
            (destination / source.name, content),
            (destination / "manifest.json", json.dumps(result, ensure_ascii=False, indent=2)),
        ):
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                created_files.append(path)
                stream.write(data)
        return result
    except Exception:
        # 仅处理排他创建成功后登记的路径，不递归删除，不触及其他现有文件。
        for path in reversed(created_files):
            path.unlink()
        for path in reversed(created_directories):
            path.rmdir()
        raise


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
