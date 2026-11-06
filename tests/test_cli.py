import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from iris_relay.cli import main
from iris_relay.model import ValidationError, load_json
from iris_relay.plugin import JsonCommandService, convert_material


@pytest.fixture
def files(tmp_path, profiles, request_data):
    dictionaries = tmp_path / "字典"
    dictionaries.mkdir()
    for i, profile in enumerate(profiles):
        (dictionaries / f"{i}.json").write_text(json.dumps(profile), encoding="utf-8")
    request = tmp_path / "请求.json"
    request.write_text(json.dumps(request_data, ensure_ascii=False), encoding="utf-8")
    return dictionaries, request


def test_cli_writes_response_and_never_overwrites(files, tmp_path, capsys):
    dictionaries, request = files
    output = tmp_path / "输出.json"
    args = ["--dictionaries", str(dictionaries), "--request", str(request), "--output", str(output)]
    assert main(args) == 0
    original = output.read_bytes()
    assert json.loads(original)["status"] == "ok"
    assert main(args) == 2
    assert output.read_bytes() == original
    assert "已存在" in capsys.readouterr().err


def test_cli_protects_request_file(files):
    dictionaries, request = files
    original = request.read_bytes()
    assert (
        main(
            [
                "--dictionaries",
                str(dictionaries),
                "--request",
                str(request),
                "--output",
                str(request),
            ]
        )
        == 2
    )
    assert request.read_bytes() == original


def test_cli_returns_structured_invalid_json_error(files, capsys):
    dictionaries, request = files
    request.write_text('{"bad":', encoding="utf-8")
    assert main(["--dictionaries", str(dictionaries), "--request", str(request)]) == 2
    response = json.loads(capsys.readouterr().out)
    assert response["status"] == "error"
    assert response["error"]["code"] == "INVALID_REQUEST"


def test_cli_subprocess_json_transport(files):
    dictionaries, request = files
    env = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).parents[1]),
        "PYTHONIOENCODING": "utf-8",
    }
    result = subprocess.run(
        [sys.executable, "-m", "iris_relay", "--dictionaries", str(dictionaries)],
        input=request.read_bytes(),
        capture_output=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8")
    response = json.loads(result.stdout)
    assert response["status"] == "ok"
    assert response["result"]["material"]["name"] == "磨损金属"
    assert not result.stderr


@pytest.mark.parametrize("content", ['{"a":1,"a":2}', '{"value":NaN}', '{"value":Infinity}'])
def test_json_loader_rejects_ambiguous_values(tmp_path, content):
    path = tmp_path / "bad.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValidationError):
        load_json(path)


def test_json_loader_accepts_utf8_bom(tmp_path):
    path = tmp_path / "bom.json"
    path.write_text('{"名称":"材质"}', encoding="utf-8-sig")
    assert load_json(path) == {"名称": "材质"}


def test_plugin_can_use_the_same_api_in_a_separate_process(files, material):
    dictionaries, _ = files

    class Adapter:
        def read_material(self):
            return material

        def create_material(self, data):
            assert data["nodes"][0]["inputs"]["roughness"] == pytest.approx(0.2)
            return "cross-process-material"

    service = JsonCommandService(
        [
            sys.executable,
            "-m",
            "iris_relay",
            "--dictionaries",
            str(dictionaries),
        ]
    )
    outcome = convert_material(Adapter(), service, {"id": "reference.rough", "version": "1.0"})
    assert outcome.created == "cross-process-material"


def test_command_service_rejects_shell_string():
    with pytest.raises(ValueError):
        JsonCommandService("python -m iris_relay")


def test_command_service_reports_failed_process():
    service = JsonCommandService([sys.executable, "-c", "raise SystemExit(7)"])
    with pytest.raises(ValueError, match="退出码"):
        service.handle({})


def test_command_service_timeout_is_bounded():
    service = JsonCommandService([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.01)
    with pytest.raises(ValueError, match="超时"):
        service.handle({})


@pytest.mark.parametrize("transport", ["python", "process"])
def test_reference_plugin_example_runs_with_utf8_output(transport, tmp_path):
    root = Path(__file__).parents[1]
    result = subprocess.run(
        [sys.executable, str(root / "examples" / "plugin_demo.py"), "--transport", transport],
        capture_output=True,
        check=False,
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout.decode("utf-8"))
    assert summary["状态"] == "ok"
    assert summary["源材质保持不变"] is True


def test_core_imports_from_repository_without_site_packages():
    root = Path(__file__).resolve().parents[1]
    code = (
        "from pathlib import Path; import iris_relay; "
        "assert Path(iris_relay.__file__).resolve() == "
        "Path.cwd() / 'iris_relay' / '__init__.py'"
    )
    result = subprocess.run(
        [sys.executable, "-S", "-c", code], cwd=root, capture_output=True, check=False
    )
    assert result.returncode == 0, result.stderr
