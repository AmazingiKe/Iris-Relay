"""汇集标准导出与未知节点结构保存；两类文档状态分别报告，不能混记转换成功。"""

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from iris_relay import RelayAPI  # noqa: E402
from plugins.blender.preservation import preservation_snapshot  # noqa: E402
from plugins.core import request  # noqa: E402


def main():
    """标准成功项逐字节复制；其余仅保存原生结构，保留原阻断及完整盘点来源。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--exports", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    inventory = json.loads(args.inventory.read_text("utf-8"))
    exports = json.loads((args.exports / "summary.json").read_text("utf-8"))
    originals = {m["name"]: m for m in inventory["materials"]}
    api = RelayAPI([])
    summary = {"cases": [], "appearance_verified": False}
    for case in exports["cases"]:
        name = case["id"] + ".mtlx"
        if case["exported"]:
            shutil.copyfile(args.exports / name, args.out / name)
            status = {
                "id": case["id"],
                "name": case["name"],
                "document_saved": True,
                "standard_exported": True,
                "preservation_only": False,
            }
        else:
            response = request(
                api,
                "materialx.preserve",
                snapshot=preservation_snapshot(originals[case["name"]], inventory),
            )
            result = response.get("result") or {}
            document = result.get("document")
            if document:
                (args.out / name).write_text(document, encoding="utf-8")
            status = {
                "id": case["id"],
                "name": case["name"],
                "document_saved": bool(document),
                "standard_exported": False,
                "preservation_only": bool(document),
                "conversion_supported": False,
                "original_issues": case["issues"],
                "error": response.get("error"),
                "preservation_issues": result.get("report", {}).get("issues", []),
            }
        summary["cases"].append(status)
        (args.out / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    summary.update(
        documents=sum(c["document_saved"] for c in summary["cases"]),
        standard=sum(c["standard_exported"] for c in summary["cases"]),
        preserved=sum(c["preservation_only"] for c in summary["cases"]),
    )
    (args.out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in summary.items() if k != "cases"}))


if __name__ == "__main__":
    main()
