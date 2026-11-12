"""自动阻止核心依赖宿主、宿主交叉引用，以及插件绕过公开协议调用转换实现。"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from _scripts.check_dependency_boundaries import check_repository, check_source

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "path,source",
    [
        ("iris_relay/materialx/example.py", "import bpy"),
        ("iris_relay/example.py", "from maya import cmds"),
        ("iris_relay/example.py", "from plugins.blender import BlenderAdapter"),
        (
            "iris_relay/example.py",
            "from _scripts.materialx_function_library import blend_implementation",
        ),
        ("plugins/blender/adapter.py", "from iris_relay.materialx.bridge import export_material"),
        ("plugins/blender/adapter.py", "from iris_relay import conversion"),
        ("plugins/blender/adapter.py", "from ..maya import adapter"),
        ("plugins/presentation.py", "from .blender import adapter"),
        ("plugins/blender/adapter.py", "import maya.cmds"),
        ("plugins/core.py", "import bpy"),
        ("plugins/blender/adapter.py", "importlib.import_module('iris_relay.dictionary')"),
        ("iris_relay/example.py", "__import__('c4d')"),
        ("iris_relay/example.py", "from importlib import import_module as load; load('bpy')"),
        (
            "plugins/blender/adapter.py",
            "importlib.import_module('..maya.adapter', 'plugins.blender')",
        ),
        (
            "plugins/blender/adapter.py",
            "importlib.import_module('.materialx.bridge', package='iris_relay')",
        ),
    ],
)
def test_rejects_runtime_and_conversion_dependency_leaks(path, source):
    violations = check_source(Path(path), source)
    assert violations
    assert all(v["line"] >= 1 and v["module"] and v["rule"] for v in violations)


@pytest.mark.parametrize(
    "path,source",
    [
        ("plugins/blender/adapter.py", "from iris_relay.native import identify"),
        ("plugins/maya/controller.py", "from plugins.core import check_import"),
        ("plugins/maya/controller.py", "from .. import presentation"),
        ("plugins/blender/ui.py", "from . import adapter"),
        ("plugins/blender/adapter.py", "import bpy"),
        ("plugins/cinema4d/adapter.py", "import maxon; import c4d"),
        ("plugins/maya/adapter.py", "from maya import cmds; import arnold"),
        ("plugins/core.py", "from iris_relay import RelayAPI, RelayService"),
        ("iris_relay/materialx/example.py", "from ..dictionary import Dictionary"),
        ("iris_relay/example.py", "import MaterialX"),
        ("iris_relay/example.py", "message = 'import bpy'"),
    ],
)
def test_allows_public_interfaces_and_host_local_implementation(path, source):
    assert check_source(Path(path), source) == []


def test_current_repository_preserves_dependency_boundaries():
    assert check_repository(ROOT) == []


def test_cli_detects_violation_without_importing_host_code(tmp_path):
    core = tmp_path / "iris_relay"
    core.mkdir()
    (core / "invalid.py").write_text("import bpy\n", "utf-8")
    process = subprocess.run(
        [
            sys.executable,
            str(ROOT / "_scripts/check_dependency_boundaries.py"),
            "--root",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert process.returncode == 1
    report = json.loads(process.stdout)
    assert report["violations"][0]["module"] == "bpy"


def test_syntax_error_is_a_failed_check_instead_of_an_unchecked_file(tmp_path):
    path = tmp_path / "iris_relay"
    path.mkdir()
    (path / "invalid.py").write_text("if :\n", "utf-8")
    violations = check_repository(tmp_path)
    assert violations[0]["rule"] == "syntax"


def test_empty_root_cannot_be_reported_as_a_successful_architecture_check(tmp_path):
    assert check_repository(tmp_path)[0]["rule"] == "missing_runtime_sources"
