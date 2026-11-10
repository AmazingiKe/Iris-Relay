"""批量报告遇到 Windows 短暂文件占用时可恢复，永久失败保留最后有效进度。"""

import json
from pathlib import Path

import pytest

from _scripts.verify_blender_batch import write_json


def test_progress_retries_temporary_access_denial(tmp_path, monkeypatch):
    path = tmp_path / "summary.json"
    path.write_text('{"processed": 1}', "utf-8")
    replace = Path.replace
    attempts = []

    def busy_once(self, target):
        attempts.append(str(target))
        if len(attempts) == 1:
            raise PermissionError("测试：读者尚未释放旧文件")
        return replace(self, target)

    monkeypatch.setattr(Path, "replace", busy_once)
    write_json(path, {"processed": 2})
    assert len(attempts) == 2
    assert json.loads(path.read_text("utf-8")) == {"processed": 2}
    assert not path.with_suffix(".json.tmp").exists()


def test_progress_keeps_last_valid_file_when_retries_exhausted(tmp_path, monkeypatch):
    path = tmp_path / "summary.json"
    path.write_text('{"processed": 1}', "utf-8")
    attempts = []

    def busy(self, target):
        attempts.append(str(target))
        raise PermissionError("测试：持续拒绝替换")

    monkeypatch.setattr(Path, "replace", busy)
    with pytest.raises(PermissionError):
        write_json(path, {"processed": 2})
    assert 1 < len(attempts) <= 8
    assert json.loads(path.read_text("utf-8")) == {"processed": 1}
