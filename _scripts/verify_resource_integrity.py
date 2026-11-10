"""记录或对比测试文档引用资源的 SHA-256；不修改源材质和图片。"""

import argparse
import hashlib
import json
from pathlib import Path
from xml.etree import ElementTree as ET


def main():
    """仅检查明确文档中的引用和额外指定文件；对比不一致时返回非零退出。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", type=Path)
    parser.add_argument("--additional", nargs="*", type=Path, default=[])
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    paths = set(args.additional)
    if args.documents:
        for path in args.documents.glob("*.mtlx"):
            for port in ET.fromstring(path.read_text("utf-8")).iter("input"):
                if port.get("type") == "filename" and port.get("value"):
                    image = Path(port.get("value"))
                    paths.add(image if image.is_absolute() else path.parent / image)
    before = json.loads(args.compare.read_text("utf-8"))["files"] if args.compare else None
    if before is not None:
        paths.update(Path(p) for p in before)
    files = {}
    for path in sorted({p.resolve() for p in paths}):
        value = None
        if path.is_file():
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            value = digest.hexdigest()
        files[path.as_posix()] = value
    mismatches = [p for p in (before or {}) if files.get(p) != before[p]]
    result = {
        "files": files,
        "mismatches": mismatches,
        "unchanged": not mismatches if before is not None else None,
    }
    with args.out.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"files": len(files), "mismatches": len(mismatches)}))
    if mismatches:
        raise RuntimeError("源文件或资源哈希不同，详见报告")


if __name__ == "__main__":
    main()
