"""通过公开 API 把已有 MaterialX 文档及贴图复制为便携资源包。"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from plugins.core import create_service  # noqa: E402
from plugins.resources import package_materialx  # noqa: E402


def main():
    """重定位清单为 URI 到现有文件的 JSON 映射，输出目录必须不存在。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--relinks", type=Path)
    args = parser.parse_args()
    relinks = json.loads(args.relinks.read_text("utf-8")) if args.relinks else None
    with create_service(ROOT) as service:
        result = package_materialx(service, args.source, args.out, relinks=relinks)
    print(
        json.dumps(
            {key: result[key] for key in ("document", "resource_count", "copied_files")},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
