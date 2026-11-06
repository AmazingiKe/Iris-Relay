"""JSON 命令行传输：不同语言插件可调用同一核心，不需要嵌入 Python。"""

import argparse
import json
import sys
from pathlib import Path

from .api import RelayAPI
from .model import MAX_JSON_BYTES, ValidationError, load_json, parse_json


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Iris Relay 统一材质转换 API（JSON 传输）")
    parser.add_argument("--dictionaries", required=True, help="渲染器字典目录")
    parser.add_argument("--request", default="-", help="UTF-8 请求文件；- 表示标准输入")
    parser.add_argument(
        "--output", default="-", help="UTF-8 响应文件；- 表示标准输出，不覆盖已有文件"
    )
    args = parser.parse_args(argv)
    try:
        request = (
            parse_json(sys.stdin.buffer.read(MAX_JSON_BYTES + 1))
            if args.request == "-"
            else load_json(args.request)
        )
        api = RelayAPI.from_directory(args.dictionaries)
        response = api.handle(request)
    except (ValidationError, OSError) as exc:
        response = {
            "api_version": "1",
            "request_id": None,
            "status": "error",
            "result": None,
            "error": {"code": getattr(exc, "code", "IO_ERROR"), "message": str(exc)},
        }
    encoded = json.dumps(response, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    try:
        if args.output == "-":
            # 真实 CLI 强制 UTF-8；测试或宿主提供的文本流使用其已有编码。
            if hasattr(sys.stdout, "buffer"):
                sys.stdout.buffer.write(encoded.encode("utf-8"))
                sys.stdout.buffer.flush()
            else:
                sys.stdout.write(encoded)
        else:
            output = Path(args.output)
            with output.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(encoded)
    except FileExistsError:
        print("输出文件已存在，请选择新路径；原文件未修改", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"无法写入输出：{exc}", file=sys.stderr)
        return 2
    return 2 if response["status"] == "error" else 0
