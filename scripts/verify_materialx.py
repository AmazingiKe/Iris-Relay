"""将受限 MaterialX 桥接结果在独立 Blender 中创建、保存、重开及读回。"""

import argparse
import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

from native_fixture import fixture, renderer
from verify_native import write_png

from iris_relay import RelayAPI

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blender", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if not args.blender.is_file():
        parser.error("Blender 程序不存在")
    output = args.out.resolve()
    output.mkdir(parents=True, exist_ok=False)
    run_id = str(uuid4())
    write_png(output / "fixture.png")
    profile = json.loads((ROOT / "dictionaries/blender-cycles-5.2.3.json").read_text("utf-8"))
    service = RelayAPI.from_directory(ROOT / "dictionaries")

    def request(operation, **payload):
        response = service.handle(
            {"api_version": "1", "request_id": run_id, "operation": operation, "payload": payload}
        )
        if response["status"] == "error":
            raise RuntimeError(response)
        return response["result"]

    job = {"run_id": run_id, "host": "blender", "output": str(output / "host.json"), "cases": []}
    for semantic in ("normal.tangent", "bump.height"):
        source = fixture(profile, str(output / "fixture.png"), normal_semantic=semantic)
        exported = request("materialx.export", material=source)
        (output / (semantic + ".mtlx")).write_text(exported["document"], encoding="utf-8")
        converted = request(
            "materialx.convert", document=exported["document"], target=renderer(profile)
        )
        (output / (semantic + ".json")).write_text(
            json.dumps(converted, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        job["cases"].append({"id": semantic, "material": converted["material"]})
    job_path = output / "job.json"
    job_path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    with (output / "blender.log").open("wb") as log:
        completed = subprocess.run(
            [
                str(args.blender),
                "--background",
                "--factory-startup",
                "--disable-autoexec",
                "--python-exit-code",
                "1",
                "--python",
                str(ROOT / "scripts/verify_native_host.py"),
            ],
            env=dict(os.environ, IRIS_RELAY_JOB=str(job_path), PYTHONUTF8="1"),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=300,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    host = (
        json.loads(Path(job["output"]).read_text("utf-8")) if Path(job["output"]).is_file() else {}
    )
    passed = (
        completed.returncode == 0 and host.get("run_id") == run_id and host.get("status") == "ok"
    )
    if passed:
        for case in host["cases"]:
            exported = request("materialx.export", material=case["material"])
            (output / (case["id"] + ".readback.mtlx")).write_text(
                exported["document"], encoding="utf-8"
            )
    summary = {
        "run_id": run_id,
        "status": "ok" if passed else "error",
        "exit_code": completed.returncode,
        "host": host,
        "appearance_verified": False,
        "scope": "MaterialX 桥接及 Blender 保存重开，不包含 Maya 或渲染验证",
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(summary["status"], run_id)
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
