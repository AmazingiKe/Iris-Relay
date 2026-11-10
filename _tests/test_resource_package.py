"""便携资源包必须复制现有文件、使用相对路径，并在失败后仅清理本次产物。"""

import hashlib
import shutil

import pytest

from iris_relay import RelayAPI
from plugins.resources import package_materialx


def source_document(tmp_path, uri="source.png"):
    path = tmp_path / "source.mtlx"
    path.write_text(
        '<materialx version="1.39"><image name="img" type="color3" nodedef="ND_image_color3">'
        '<input name="file" type="filename" value="'
        + uri
        + '" colorspace="raw"/></image></materialx>',
        encoding="utf-8",
    )
    return path


def test_package_can_move_without_original_resource_and_preserves_hash(tmp_path):
    image = tmp_path / "source.png"
    image.write_bytes(b"fixture image bytes")
    document = source_document(tmp_path)
    original = document.read_bytes()
    result = package_materialx(RelayAPI(), document, tmp_path / "bundle")
    assert result["resource_count"] == 1
    moved = tmp_path / "moved bundle"
    shutil.copytree(tmp_path / "bundle", moved)
    image.unlink()
    doc = (moved / "source.mtlx").read_text("utf-8")
    assert "textures/" in doc and str(tmp_path) not in doc
    copied = next((moved / "textures").iterdir())
    assert hashlib.sha256(copied.read_bytes()).hexdigest() == result["resources"][0]["sha256"]
    assert document.read_bytes() == original


def test_missing_resource_relink_is_explicit_and_existing_output_is_protected(tmp_path):
    document = source_document(tmp_path, "missing.png")
    replacement = tmp_path / "found.png"
    replacement.write_bytes(b"replacement")
    with pytest.raises(Exception, match="贴图"):
        package_materialx(RelayAPI(), document, tmp_path / "missing-bundle")
    assert not (tmp_path / "missing-bundle").exists()
    package_materialx(
        RelayAPI(), document, tmp_path / "bundle", relinks={"missing.png": replacement}
    )
    before = list((tmp_path / "bundle").iterdir())
    with pytest.raises(FileExistsError):
        package_materialx(
            RelayAPI(), document, tmp_path / "bundle", relinks={"missing.png": replacement}
        )
    assert list((tmp_path / "bundle").iterdir()) == before


def test_core_rewrite_failure_removes_only_owned_files(tmp_path):
    document = source_document(tmp_path)
    (tmp_path / "source.png").write_bytes(b"source")
    sentinel = tmp_path / "keep.txt"
    sentinel.write_text("keep")

    class FailedService:
        def handle(self, request):
            if request["operation"] == "materialx.rewrite_resources":
                return {
                    "api_version": "1",
                    "request_id": request["request_id"],
                    "status": "error",
                    "error": {"message": "模拟改写失败"},
                    "result": None,
                }
            return RelayAPI().handle(request)

    with pytest.raises(Exception, match="改写"):
        package_materialx(FailedService(), document, tmp_path / "bundle")
    assert not (tmp_path / "bundle").exists()
    assert sentinel.read_text() == "keep"
