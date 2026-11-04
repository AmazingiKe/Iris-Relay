"""参考插件：演示统一 API 接入，不依赖或冒充真实 DCC。"""

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path

from iris_relay import RelayAPI
from iris_relay.plugin import JsonCommandService, convert_material


class ReferenceAdapter:
    """宿主适配层只负责读写材质，不包含转换规则。"""

    def __init__(self, material):
        self.source = deepcopy(material)
        self.created = []

    def read_material(self):
        return deepcopy(self.source)

    def create_material(self, material):
        self.created.append(deepcopy(material))
        return f"new-material-{len(self.created)}"


def main():
    parser = argparse.ArgumentParser(description="演示插件通过统一 API 转换材质")
    parser.add_argument("--transport", choices=["python", "process"], default="python")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    request = json.loads((root / "convert-request.json").read_text(encoding="utf-8"))
    adapter = ReferenceAdapter(request["payload"]["material"])
    if args.transport == "process":
        service = JsonCommandService(
            [
                sys.executable,
                "-m",
                "iris_relay",
                "--dictionaries",
                str(root / "dictionaries"),
            ]
        )
    else:
        service = RelayAPI.from_directory(root / "dictionaries")
    result = convert_material(adapter, service, request["payload"]["target"])
    summary = {
        "传输方式": args.transport,
        "状态": result.response["status"],
        "新材质": result.created,
        "源材质保持不变": adapter.source == request["payload"]["material"],
        "目标材质": adapter.created[0] if adapter.created else None,
        "说明": "参考字典只用于协议验证；没有执行真实渲染或神经校正。",
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if result.created is not None else 2


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
