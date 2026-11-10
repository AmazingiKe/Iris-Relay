"""以独立进程验证真实字典；命令行必须指定本机软件路径，结果写入新目录。"""

import argparse
import json
import os
import struct
import subprocess
import sys
import zlib
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from _scripts.native_fixture import fixture, renderer  # noqa: E402
from iris_relay import RelayAPI  # noqa: E402


def write_png(path):
    """自行生成 2×2 RGB 棋盘，外部贴图引用的最小测试资源。"""

    def chunk(kind, data):
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    pixels = bytes([0, 220, 120, 40, 40, 120, 220, 0, 40, 120, 220, 220, 120, 40])
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(pixels))
        + chunk(b"IEND", b"")
    )


def main():
    parser = argparse.ArgumentParser(description="真实宿主节点字典验证（独立测试文件）")
    parser.add_argument("--blender", type=Path, required=True)
    parser.add_argument("--c4dpy", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    for executable in (args.blender, args.c4dpy):
        if not executable.is_file():
            parser.error(f"软件不存在：{executable}")
    output = args.out.resolve()
    output.mkdir(parents=True, exist_ok=False)
    run_id = str(uuid4())
    texture = output / "fixture.png"
    write_png(texture)
    api = RelayAPI.from_directory(ROOT / "dictionaries")
    profiles = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((ROOT / "dictionaries").glob("*.json"))
    ]
    excluded = [p["host"]["id"] for p in profiles if p["host"]["id"] not in {"blender", "cinema4d"}]
    profiles = [p for p in profiles if p["host"]["id"] in {"blender", "cinema4d"}]

    def new_jobs(phase):
        return {
            host: {
                "run_id": run_id,
                "host": host,
                "output": str(output / f"{phase}_{host}_result.json"),
                "cases": [],
            }
            for host in ("blender", "cinema4d")
        }

    jobs = new_jobs("source")
    for source in profiles:
        for semantic in ("normal.tangent", "bump.height"):
            case_id = source["renderer"]["id"] + "_" + semantic
            jobs[source["host"]["id"]]["cases"].append(
                {"id": case_id, "material": fixture(source, str(texture), normal_semantic=semantic)}
            )
    summary = {
        "run_id": run_id,
        "excluded_hosts": excluded,
        "scope": (
            "原生夹具保存重开并通过统一 API 识别读回，"
            "再跨宿主转换、创建、保存重开核对；不证明外观一致"
        ),
        "phases": {},
    }
    worker = str(ROOT / "_scripts/verify_native_host.py")

    def execute(jobs, phase):
        reports = {}
        for host, job in jobs.items():
            job_path = output / f"{phase}_{host}_job.json"
            job_path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
            command = (
                [
                    str(args.blender),
                    "--background",
                    "--factory-startup",
                    "--disable-autoexec",
                    "--python-exit-code",
                    "1",
                    "--python",
                    worker,
                ]
                if host == "blender"
                else [str(args.c4dpy), "-g_licenseModel=LICENSEMODEL::MAXONAPP", worker]
            )
            with (output / f"{phase}_{host}.log").open("wb") as log:
                completed = subprocess.run(
                    command,
                    env=dict(os.environ, IRIS_RELAY_JOB=str(job_path), PYTHONUTF8="1"),
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    timeout=300,
                    check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            report_path = Path(job["output"])
            report = (
                json.loads(report_path.read_text(encoding="utf-8"))
                if report_path.is_file()
                else {"status": "error", "message": "宿主没有生成完成报告"}
            )
            reports[host] = {"exit_code": completed.returncode, "report": report}
            print(phase, host, report["status"], flush=True)
        return reports

    def passed(reports):
        return all(
            item["exit_code"] == 0
            and item["report"].get("run_id") == run_id
            and item["report"]["status"] == "ok"
            for item in reports.values()
        )

    source_reports = execute(jobs, "source")
    summary["phases"]["source"] = source_reports
    if passed(source_reports):
        jobs = new_jobs("conversion")
        for host_result in source_reports.values():
            for case in host_result["report"]["cases"]:
                for target in profiles:
                    case_id = case["id"] + "_to_" + target["renderer"]["id"]
                    result = api.handle(
                        {
                            "api_version": "1",
                            "request_id": case_id,
                            "operation": "convert",
                            "payload": {"material": case["material"], "target": renderer(target)},
                        }
                    )
                    (output / f"{case_id}_conversion.json").write_text(
                        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    if result["status"] == "error":
                        raise RuntimeError(f"读回后的核心转换失败：{case_id}")
                    jobs[target["host"]["id"]]["cases"].append(
                        {"id": case_id, "material": result["result"]["material"]}
                    )
        summary["phases"]["conversion"] = execute(jobs, "conversion")
    summary["status"] = (
        "ok"
        if len(summary["phases"]) == 2
        and all(passed(reports) for reports in summary["phases"].values())
        else "error"
    )
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(output / "summary.json")
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
