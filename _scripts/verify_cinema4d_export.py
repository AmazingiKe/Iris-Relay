"""独立加载已有 C4D 文档，从当前原生图导出 MaterialX；不使用导入缓存。"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    """每个材质单独报告，任何失败都不能计入已导出数量。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--require-success", type=int, default=0)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    import c4d

    from plugins.cinema4d.adapter import Cinema4DAdapter
    from plugins.core import create_service, export_material

    doc = c4d.documents.LoadDocument(
        str(args.source), c4d.SCENEFILTER_MATERIALS | c4d.SCENEFILTER_OBJECTS
    )
    assert doc is not None
    cases = []
    try:
        with create_service(ROOT) as service:
            adapter = Cinema4DAdapter(doc, service)
            for index, material in enumerate(doc.GetMaterials()):
                result = export_material(
                    adapter,
                    service,
                    material,
                    args.out / f"material_{index:04d}.mtlx",
                    accept_warnings=True,
                )
                case = {
                    "id": f"material_{index:04d}",
                    "name": material.GetName(),
                    "saved": bool(result["saved_path"]),
                    "status": result["status"],
                    "message": result["message"],
                }
                if result["saved_path"]:
                    case.update(
                        nodes=len(result["material"]["nodes"]),
                        links=len(result["material"]["links"]),
                        images=len(result["material"]["textures"]),
                    )
                (args.out / f"material_{index:04d}.json").write_text(
                    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8"
                )
                cases.append(case)
            summary = {
                "successful": sum(c["saved"] for c in cases),
                "completed": len(cases),
                "cases": cases,
                "appearance_verified": False,
            }
            (args.out / "summary.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf8"
            )
            print(json.dumps({k: v for k, v in summary.items() if k != "cases"}))
            if summary["successful"] < args.require_success:
                raise RuntimeError("原生导出未达到要求数量")
    finally:
        c4d.documents.KillDocument(doc)


if __name__ == "__main__":
    main()
