"""将受限 MaterialX 桥接结果在独立 Blender 中创建、保存、重开及读回。"""

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

from native_fixture import fixture, renderer  # noqa: E402
from verify_native import write_png  # noqa: E402

from iris_relay import RelayAPI  # noqa: E402


def parse_args():
    """读取执行路径；输出目录必须新建，输入文档可省略以运行既有夹具。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blender", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--input", type=Path, help="已有 .mtlx；图片需使用本机绝对路径")
    args = parser.parse_args()
    if not args.blender.is_file():
        parser.error("Blender 程序不存在")
    if args.input and (not args.input.is_file() or args.input.suffix.lower() != ".mtlx"):
        parser.error("输入必须是存在的 .mtlx 文件")
    return args


def request(service, operation, **payload):
    """通过公开 API 取得结果；验证器接受并记录警告，阻断错误立即终止。"""
    response = service.handle(
        {"api_version": "1", "request_id": str(uuid4()), "operation": operation, "payload": payload}
    )
    if response["status"] == "error":
        raise RuntimeError(response)
    return response["result"]


def write_json(path, data):
    """将本轮报告写入新建的输出目录，保留中文诊断。"""
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def input_documents(service, profile, output, source_path):
    """读取指定文档，或生成法线／高度夹具；不修改外部输入。"""
    if source_path:
        return {"imported": source_path.read_text(encoding="utf-8")}
    write_png(output / "fixture.png")
    documents = {}
    for semantic in ("normal.tangent", "bump.height"):
        source = fixture(profile, str(output / "fixture.png"), normal_semantic=semantic)
        documents[semantic] = request(service, "materialx.export", material=source)["document"]
    return documents


def prepare_cases(service, profile, documents, output):
    """将文档转为目标材质协议，保存转换报告并收集需要保护的纹理文件。"""
    cases, resources = [], set()
    for name, document in documents.items():
        (output / (name + ".mtlx")).write_text(document, encoding="utf-8")
        converted = request(
            service, "materialx.convert", document=document, target=renderer(profile)
        )
        write_json(output / (name + ".json"), converted)
        for resource in converted["material"]["textures"].values():
            path = Path(resource["uri"])
            if not path.is_absolute() or not path.is_file():
                raise ValueError(f"验证器要求存在的本机绝对图片路径：{path}")
            resources.add(path)
        cases.append({"id": name, "material": converted["material"]})
    return cases, resources


def fingerprints(paths):
    """流式计算输入文件哈希，用于检测宿主验证是否改动了外部资源。"""
    result = {}
    for path in sorted(paths):
        with path.open("rb") as stream:
            result[str(path.resolve())] = hashlib.file_digest(stream, "sha256").hexdigest()
    return result


def run_host(blender, output, job):
    """在独立后台 Blender 中创建、保存和重开材质；返回退出码及本轮报告。"""
    job_path = output / "job.json"
    write_json(job_path, job)
    with (output / "blender.log").open("wb") as log:
        completed = subprocess.run(
            [
                str(blender),
                "--background",
                "--factory-startup",
                "--disable-autoexec",
                "--python-exit-code",
                "1",
                "--python",
                str(ROOT / "_scripts/verify_native_host.py"),
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
    return completed.returncode, host


def main():
    """验证入口：准备独立输入 → 调用宿主 → 检查读回和资源保护 → 输出报告。"""
    # 1. 新建输出目录，只通过公开 API 准备目标材质。
    args = parse_args()
    output = args.out.resolve()
    output.mkdir(parents=True, exist_ok=False)
    run_id = str(uuid4())
    profile = json.loads((ROOT / "dictionaries/blender-cycles.json").read_text("utf-8"))
    service = RelayAPI.from_directory(ROOT / "dictionaries")
    source_paths = {args.input.resolve()} if args.input else set()
    before = fingerprints(source_paths)
    documents = input_documents(service, profile, output, args.input)
    cases, resources = prepare_cases(service, profile, documents, output)
    before.update(fingerprints(resources - source_paths))

    # 2. 在独立宿主创建新材质；实际节点与目标协议的比对由宿主验证器执行。
    job = {"run_id": run_id, "host": "blender", "output": str(output / "host.json"), "cases": cases}
    exit_code, host = run_host(args.blender, output, job)
    after = fingerprints(source_paths | resources)
    passed = (
        exit_code == 0
        and host.get("run_id") == run_id
        and host.get("status") == "ok"
        and before == after
    )
    # 3. 将真实读回材质再次导出并校验，外观验证仍单独标记为未完成。
    if passed:
        for case in host["cases"]:
            exported = request(service, "materialx.export", material=case["material"])
            (output / (case["id"] + ".readback.mtlx")).write_text(
                exported["document"], encoding="utf-8"
            )
            write_json(output / (case["id"] + ".readback.json"), exported)
            request(service, "materialx.validate", document=exported["document"])
    summary = {
        "run_id": run_id,
        "status": "ok" if passed else "error",
        "exit_code": exit_code,
        "host": host,
        "input_files_unchanged": before == after,
        "fingerprints_before": before,
        "fingerprints_after": after,
        "appearance_verified": False,
        "scope": "MaterialX 桥接及 Blender 保存重开，不包含 Maya 或渲染验证",
    }
    write_json(output / "summary.json", summary)
    print(summary["status"], run_id)
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
