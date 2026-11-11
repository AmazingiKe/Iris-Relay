"""在独立 mayapy 或后台 Blender 中验证 HTTP 创建、保存重开及原生再导出。"""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "_scripts"))

from iris_relay.http_client import HttpRelayService  # noqa: E402
from iris_relay.native import request  # noqa: E402


def write_json(path, data):
    """仅在本轮新建目录保存报告，包含全部核心诊断。"""
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def fingerprints(paths):
    """分块计算输入文档和贴图哈希，兼容 Maya 的 Python 3.7。"""
    result = {}
    for path in sorted(paths):
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        result[str(path)] = digest.hexdigest()
    return result


def source_files(service, documents):
    """由公开资源 API 枚举引用，并在客户端核对本机文件。"""
    paths = set(documents)
    for document in documents:
        response = request(
            service, "materialx.resources", document=document.read_text(encoding="utf-8-sig")
        )
        assert response["status"] != "error", response
        for resource in response["result"]["resources"]:
            path = Path(resource["resolved_uri"])
            if not path.is_absolute():
                path = document.parent / path
            path = path.resolve()
            assert path.is_file(), path
            paths.add(path)
    return paths


def run_maya(args, documents):
    """复用已有原生核验器，只在本次独立 Maya 进程创建材质。"""
    import maya.standalone

    maya.standalone.initialize(name="python")
    try:
        from maya import cmds
        from verify_maya_import import verify, verify_live_read

        from plugins.maya import controller
        from plugins.maya.adapter import MayaAdapter, ensure_environment

        target = ensure_environment()
        service = controller.create_service()
        assert isinstance(service, HttpRelayService)
        adapter = MayaAdapter(service, target)
        paths = source_files(service, documents)
        before = fingerprints(paths)
        color = cmds.colorManagementPrefs(query=True, renderingSpaceName=True)
        selection = cmds.ls(selection=True)
        cases = []
        for document in documents:
            result = controller.import_material(
                adapter, service, document, target, accept_warnings=True
            )
            write_json(args.out / (document.stem + ".json"), result)
            assert result["created"], result
            verify(adapter, result["material"], result["created"])
            cases.append((document, result))
        scene = args.out / "restored.ma"
        cmds.file(rename=str(scene))
        cmds.file(save=True, type="mayaAscii", force=True)
        cmds.file(new=True, force=True)
        cmds.file(str(scene), open=True, force=True, prompt=False)
        reports = []
        for document, result in cases:
            report = dict(id=document.stem, reopened=True)
            report.update(verify(adapter, result["material"], result["created"]))
            report.update(verify_live_read(adapter, result["material"], result["created"]))
            exported = controller.export_material(
                adapter,
                service,
                result["created"]["material"],
                args.out / "export" / document.name,
                accept_warnings=True,
            )
            write_json(args.out / (document.stem + "-export.json"), exported)
            assert exported["saved_path"], exported
            report["native_reexported"] = True
            reports.append(report)
        assert cmds.colorManagementPrefs(query=True, renderingSpaceName=True) == color
        assert cmds.ls(selection=True) == selection
        return reports, before, fingerprints(paths), True
    finally:
        maya.standalone.uninitialize()


def run_blender(args, documents):
    """复用 Blender 完整图核验器，以真实 HTTP 服务完成读写及再导出。"""
    import bpy
    from verify_native_host import run_blender as verify_blender

    from plugins.blender import controller
    from plugins.blender.adapter import renderer_ref

    assert bpy.app.background, "仅允许独立后台 Blender，不能在用户当前场景运行"
    service = controller.create_service()
    assert isinstance(service, HttpRelayService)
    paths = source_files(service, documents)
    before = fingerprints(paths)
    cases = []
    for document in documents:
        checked = controller.check_import(service, str(document), renderer_ref())
        assert checked.status != "error", checked
        write_json(args.out / (document.stem + ".json"), checked.__dict__)
        cases.append({"id": document.stem, "material": checked.target_material})
    result = verify_blender({"cases": cases, "output": str(args.out / "restored.json")}, service)
    reports = []
    for case in result["cases"]:
        exported = request(service, "materialx.export", material=case["material"])
        assert exported["status"] != "error", exported
        destination = args.out / "export" / (case["id"] + ".mtlx")
        with destination.open("x", encoding="utf-8") as stream:
            stream.write(exported["result"]["document"])
        write_json(args.out / (case["id"] + "-export.json"), exported)
        reports.append(dict(case, reopened=True, native_reexported=True))
    return reports, before, fingerprints(paths), None


def main():
    """仅接收显式服务地址；输出目录必须不存在，不修改源文档或源贴图。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=("maya", "blender"), required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else sys.argv[1:]
    args = parser.parse_args(argv)
    documents = sorted(args.documents.resolve().glob("*.mtlx"))
    if not documents:
        parser.error("文档目录至少需要一份 .mtlx")
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / "export").mkdir()
    os.environ["IRIS_RELAY_HTTP_URL"] = args.url
    reports, before, after, unchanged = (
        run_maya(args, documents) if args.host == "maya" else run_blender(args, documents)
    )
    assert before == after, "源文档或贴图被修改"
    summary = {
        "host": args.host,
        "transport": "http",
        "status": "ok",
        "cases": reports,
        "successful": len(reports),
        "input_files_unchanged": True,
        "fingerprints_before": before,
        "fingerprints_after": after,
        "scene_conditions_unchanged": unchanged,
        "appearance_verified": False,
    }
    write_json(args.out / "summary.json", summary)
    print(json.dumps({"host": args.host, "successful": len(reports), "status": "ok"}), flush=True)


if __name__ == "__main__":
    main()
