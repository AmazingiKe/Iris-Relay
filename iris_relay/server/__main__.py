"""通过 python -m iris_relay.server 启动可选的本机 HTTP 服务。"""

import argparse
import sys
from pathlib import Path

from .runtime import ServerSettings


def main(argv=None):
    """解析本机启动配置；缺少可选依赖或不安全监听地址时明确退出。"""
    parser = argparse.ArgumentParser(description="Iris Relay 本机 HTTP 服务")
    parser.add_argument("--dictionaries", type=Path, default=Path("dictionaries"))
    parser.add_argument("--model", type=Path, help="可选实验模型 JSON；未指定时禁用神经预测器")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    try:
        settings = ServerSettings(**vars(args))
    except ValueError as exc:
        parser.error(str(exc))
    try:
        import uvicorn

        from .app import create_app
    except ImportError:
        print("HTTP 服务依赖未安装，请执行 uv sync --extra http", file=sys.stderr)
        return 2
    uvicorn.run(
        create_app(settings=settings),
        host=settings.host,
        port=settings.port,
        workers=1,
        proxy_headers=False,
        server_header=False,
        access_log=False,
        limit_concurrency=32,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
