"""批量检查 .mtlx 对现有字典目标的转换，并可在独立 C4D 进程创建、保存和读回。"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from _scripts.native_fixture import renderer  # noqa: E402
from iris_relay import RelayAPI  # noqa: E402


def save(path, value):
    """保存可审阅的结果；调用方保证本次输出目录独立。"""
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def request(service, operation, **payload):
    """通过统一 API 检查文档和目标能力，保留警告与阻断结果。"""
    return service.handle(
        {"api_version": "1", "request_id": str(uuid4()), "operation": operation, "payload": payload}
    )


def prepare(service, documents, output, run_id):
    """逐文档检查三个已安装字典；成功的 C4D 目标进入实际创建任务。"""
    profiles = request(service, "capabilities")["result"]["renderers"]
    # 目标引用从 describe 所在的同一服务能力表获得，不假定其他宿主的版本。
    summary = {"run_id": run_id, "cases": [], "documents": {}}
    job = {"run_id": run_id, "host": "cinema4d", "output": str(output / "host.json"), "cases": []}
    for path in sorted(documents.glob("*.mtlx")):
        content = path.read_text("utf-8")
        summary["documents"][path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        validated = request(service, "materialx.validate", document=content)
        save(output / (path.stem + "-validation.json"), validated)
        for profile in profiles:
            target = renderer(profile)
            response = request(service, "materialx.convert", document=content, target=target)
            case_id = path.stem + "_" + target["id"]
            save(output / (case_id + ".json"), response)
            result = response.get("result") or {}
            summary["cases"].append(
                {
                    "id": case_id,
                    "document": path.name,
                    "target": target,
                    "status": response["status"],
                    "issues": result.get("report", {}).get("issues", []),
                    "error": response.get("error"),
                }
            )
            if response["status"] != "error" and target.get("host") == "cinema4d":
                job["cases"].append({"id": case_id, "material": result["material"]})
    return summary, job


def main():
    """组织核心矩阵和可选的 C4D 验证；宿主结果须同时通过退出码、运行 ID 和数量核对。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--c4dpy", type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    run_id = str(uuid4())
    summary, job = prepare(
        RelayAPI.from_directory(ROOT / "dictionaries"), args.documents, args.out, run_id
    )
    save(args.out / "summary.json", summary)
    if args.c4dpy and job["cases"]:
        job_path = args.out / "job.json"
        save(job_path, job)
        with (args.out / "host.log").open("wb") as stream:
            process = subprocess.run(
                [
                    str(args.c4dpy),
                    "-g_licenseModel=LICENSEMODEL::MAXONAPP",
                    str(ROOT / "_scripts/verify_native_host.py"),
                ],
                env=dict(os.environ, IRIS_RELAY_JOB=str(job_path), PYTHONUTF8="1"),
                stdout=stream,
                stderr=subprocess.STDOUT,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        report = (
            json.loads(Path(job["output"]).read_text("utf-8"))
            if Path(job["output"]).is_file()
            else {}
        )
        summary["host"] = {
            "exit_code": process.returncode,
            "requested": len(job["cases"]),
            "completed": len(report.get("cases", [])),
            "status": report.get("status", "missing"),
        }
        save(args.out / "summary.json", summary)
        if not (
            process.returncode == 0
            and report.get("run_id") == run_id
            and report.get("status") == "ok"
            and len(report["cases"]) == len(job["cases"])
        ):
            raise RuntimeError("C4D 创建或读回未通过；详见本次 host.json / host.log")
    print(
        json.dumps(
            {"run_id": run_id, "cases": len(summary["cases"]), "host": summary.get("host")},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
