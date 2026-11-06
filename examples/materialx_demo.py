"""通过公开 Python API 校验、保存自定义节点并转换为 Blender 材质数据。"""

import argparse
import json
import sys
from pathlib import Path

from iris_relay import RelayAPI

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="新的输出目录，不覆盖已有结果")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    service = RelayAPI.from_directory(ROOT / "dictionaries")
    document = (ROOT / "examples/materialx-example.mtlx").read_text(encoding="utf-8")

    def request(operation, payload):
        response = service.handle(
            {
                "api_version": "1",
                "request_id": operation,
                "operation": operation,
                "payload": payload,
            }
        )
        (args.out / (operation + ".json")).write_text(
            json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if response["status"] == "error":
            raise RuntimeError(response["error"])
        return response

    normalized = request("materialx.normalize", {"document": document})
    (args.out / "material.mtlx").write_text(normalized["result"]["document"], encoding="utf-8")
    converted = request(
        "materialx.convert",
        {
            "document": document,
            "target": {
                "id": "cycles",
                "version": "5.2.3",
                "host": "blender",
                "host_version": "5.2.3",
                "node_system": "blender.shader",
            },
        },
    )
    summary = {
        "status": converted["status"],
        "materialx_saved": True,
        "target_nodes": len(converted["result"]["material"]["nodes"]),
        "appearance_verified": False,
    }
    sys.stdout.buffer.write((json.dumps(summary, ensure_ascii=False) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
