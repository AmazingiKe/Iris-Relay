"""通过 Cinema 4D 标准文档导入入口探测 .mtlx，不调用项目转换器或猜测命令 ID。"""

import argparse
import json
from pathlib import Path


def main():
    """对每个外部样本建立独立文档，报告实际导入结果及本机加载器目录。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    import c4d
    import redshift

    loaders = [
        {"id": p.GetID(), "name": p.GetName()}
        for p in c4d.plugins.FilterPluginList(c4d.PLUGINTYPE_SCENELOADER, True)
    ]
    cases = []
    for path in sorted(args.documents.glob("*.mtlx")):
        document = c4d.documents.BaseDocument()
        case = {"id": path.stem, "entry": "MergeDocument", "error": None}
        try:
            case["returned"] = bool(
                c4d.documents.MergeDocument(document, str(path), c4d.SCENEFILTER_MATERIALS)
            )
            case["materials"] = [
                {"name": m.GetName(), "type": m.GetType()} for m in document.GetMaterials()
            ]
            case["imported_materials"] = bool(case["materials"])
        except Exception as exc:
            case.update(error=str(exc), imported_materials=False)
        finally:
            c4d.documents.KillDocument(document)
        cases.append(case)
    report = {
        "path": "c4d_native_document_importer",
        "version": c4d.GetC4DVersion(),
        "redshift": str(redshift.GetCoreVersion()),
        "loaders": loaders,
        "redshift_materialx_api_names": [n for n in dir(redshift) if "materialx" in n.lower()],
        "cases": cases,
        "appearance_verified": False,
    }
    (args.out / "summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps({"tested": len(cases), "imported": sum(c["imported_materials"] for c in cases)}),
        flush=True,
    )


if __name__ == "__main__":
    main()
