"""下载固定版本官方独立样本，通过公开 API 分别记录校验与目标转换。"""

import argparse
import hashlib
import json
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from _scripts.native_fixture import renderer  # noqa: E402
from iris_relay import RelayAPI  # noqa: E402
from plugins.core import request  # noqa: E402

VERSION = "v1.39.5"
PREFIX = "resources/Materials/Examples/StandardSurface/"
BASE = "https://raw.githubusercontent.com/AcademySoftwareFoundation/MaterialX/" + VERSION + "/"


def download(path):
    """取得固定版本原始内容；不改写标准材质以迎合目标支持。"""
    with urllib.request.urlopen(BASE + path, timeout=60) as response:
        return path, response.read()


def main():
    """新建独立目录，保留所有样本原文、来源、哈希及失败原因。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--source", type=Path, help="复用先前下载的官方原始文件，仍重新计算哈希")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    documents = args.out / "documents"
    documents.mkdir()
    tree_url = (
        "https://api.github.com/repos/AcademySoftwareFoundation/MaterialX/git/trees/"
        + VERSION
        + "?recursive=1"
    )
    if args.source:
        paths = [PREFIX + path.name for path in sorted(args.source.glob("*.mtlx"))]
    else:
        with urllib.request.urlopen(tree_url, timeout=60) as response:
            paths = [
                item["path"]
                for item in json.load(response)["tree"]
                if item["path"].startswith(PREFIX) and item["path"].endswith(".mtlx")
            ]
    api = RelayAPI.from_directory(ROOT / "dictionaries")
    capabilities = request(api, "capabilities")["result"]
    targets = [
        renderer(profile)
        for profile in capabilities["renderers"]
        if profile.get("host", {}).get("id") in {"blender", "maya"}
        or profile["renderer"]["id"] == "redshift"
    ]
    cases = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        loaded = (
            ((p, (args.source / Path(p).name).read_bytes()) for p in paths)
            if args.source
            else pool.map(download, sorted(paths))
        )
        for path, content in loaded:
            name = Path(path).name
            (documents / name).write_bytes(content)
            document = content.decode("utf-8")
            case = {
                "id": Path(name).stem,
                "source": BASE + path,
                "sha256": hashlib.sha256(content).hexdigest(),
                "modified": False,
                "validation": request(api, "materialx.validate", document=document),
                "targets": [],
            }
            for target in targets:
                result = request(api, "materialx.convert", document=document, target=target)
                case["targets"].append(
                    {"target": target, "response": result, "host_verified": False}
                )
            cases.append(case)
            (args.out / "summary.json").write_text(
                json.dumps(
                    {
                        "version": VERSION,
                        "cases": cases,
                        "completed": len(cases),
                        "path": "iris_relay_adapter",
                        "appearance_verified": False,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
    print(json.dumps({"completed": len(cases)}), flush=True)


if __name__ == "__main__":
    main()
