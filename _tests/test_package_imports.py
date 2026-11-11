"""目录分组不能让旧宿主或轻量工具提前加载 MaterialX 核心。"""

import subprocess
import sys


def test_feature_packages_and_naming_do_not_eagerly_load_sdk_or_api():
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            "import iris_relay.materialx, iris_relay.tools, sys; "
            "assert 'MaterialX' not in sys.modules; "
            "assert 'pypinyin' not in sys.modules; "
            "from iris_relay.tools.naming import node_name; "
            "assert callable(node_name); "
            "assert 'MaterialX' not in sys.modules; "
            "assert 'iris_relay.api' not in sys.modules",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert process.returncode == 0, process.stderr
