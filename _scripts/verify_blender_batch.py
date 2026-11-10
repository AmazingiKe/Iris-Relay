"""在独立 Blender 中逐材质导出或导入，逐项落盘报告，失败不终止其他材质。"""

import argparse
import json
import sys
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from _scripts.verify_native_host import equal  # noqa: E402
from plugins.blender import controller  # noqa: E402
from plugins.blender.adapter import BlenderAdapter, renderer_ref  # noqa: E402


class CachedService:
    """缓存一次批处理内的只读绑定查询；转换仍逐次通过公开服务执行。"""

    def __init__(self, service):
        self.service = service
        self.cache = {}

    def handle(self, request):
        if request["operation"] not in {"describe", "identify", "capabilities"}:
            return self.service.handle(request)
        key = json.dumps([request["operation"], request["payload"]], sort_keys=True)
        if key not in self.cache:
            self.cache[key] = self.service.handle(request)
        response = deepcopy(self.cache[key])
        response["request_id"] = request["request_id"]
        return response


def write_json(path, data):
    """保存本次任务自身的进度文件，中文问题保持可读。"""
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def export_all(args, service):
    """只附加源文件的材质，逐一导出；保留每个失败报告及已成功的文档。"""
    # 1. 附加材质及其依赖，避免加载和求值与材质检查无关的整场景几何体。
    with bpy.data.libraries.load(str(args.source), link=False) as (source, target):
        target.materials = list(source.materials)
    materials = [material for material in target.materials if material is not None]
    print(json.dumps({"loaded_materials": len(materials)}, ensure_ascii=False), flush=True)
    if args.material_cache:
        if args.material_cache.exists():
            raise FileExistsError(args.material_cache)
        bpy.data.libraries.write(
            str(args.material_cache), set(materials), path_remap="ABSOLUTE", fake_user=True
        )
    summary = {"source": str(args.source), "version": bpy.app.version_string, "cases": []}
    for index, material in enumerate(materials):
        case_id = f"material_{index:04d}"
        path = args.out / (case_id + ".mtlx")
        adapter = BlenderAdapter(material, service)
        result = controller.export_material(adapter, service, path, accept_warnings=True)
        write_json(args.out / (case_id + ".json"), asdict(result))
        case = {
            "id": case_id,
            "name": material.name_full,
            "status": result.status,
            "exported": bool(result.saved_path),
            "message": result.message,
            "issues": result.issues,
        }
        summary["cases"].append(case)
        write_json(args.out / "summary.json", summary)
        if index % 100 == 0 or index + 1 == len(materials):
            print(
                json.dumps(
                    {
                        "processed": index + 1,
                        "total": len(materials),
                        "exported": sum(c["exported"] for c in summary["cases"]),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    return summary


def import_all(args, service):
    """从独立目录的文档还原新材质，输入只包含 .mtlx 与其资源。"""
    summary = {"version": bpy.app.version_string, "cases": []}
    created = []
    for path in sorted(args.documents.glob("*.mtlx")):
        result = controller.import_material(
            BlenderAdapter(None, service), service, path, renderer_ref(), accept_warnings=True
        )
        write_json(args.out / (path.stem + ".json"), asdict(result))
        case = {
            "id": path.stem,
            "status": result.status,
            "created_material": result.created_material,
            "message": result.message,
        }
        summary["cases"].append(case)
        if result.created_material:
            created.append((case, result.target_material))
        write_json(args.out / "summary.json", summary)
        print(json.dumps(case, ensure_ascii=False), flush=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(args.out / "restored.blend"))
    bpy.ops.wm.open_mainfile(filepath=str(args.out / "restored.blend"))
    # 保存重开后按核心目标图核对原生结果；源图语义核对需另看转换报告。
    for case, expected in created:
        try:
            actual = BlenderAdapter(
                bpy.data.materials[case["created_material"]], service
            ).read_material()
            case.update(compare_target_graph(expected, actual), reopened=True)
            write_json(args.out / (case["id"] + "-readback.json"), actual)
        except Exception as exc:
            case.update(status="error", reopened=False, verification_error=repr(exc))
        write_json(args.out / "summary.json", summary)
    return summary


def compare_target_graph(expected, actual):
    """核对目标图中的全部节点、输入、连接和图片解释，资源 ID 可重新编号。"""
    assert sorted(map(json.dumps, expected["links"])) == sorted(map(json.dumps, actual["links"]))
    assert expected["output"] == actual["output"]
    nodes = {node["id"]: node for node in actual["nodes"]}
    assert set(nodes) == {node["id"] for node in expected["nodes"]}
    count = 0
    for node in expected["nodes"]:
        found = nodes[node["id"]]
        assert found["type"] == node["type"], node["id"]
        for name, value in node["inputs"].items():
            actual_value = found["inputs"][name]
            if isinstance(value, dict) and "texture" in value:
                actual_resource = actual["textures"][actual_value["texture"]]
                expected_resource = expected["textures"][value["texture"]]
                assert equal(actual_resource, expected_resource), (
                    node["id"],
                    name,
                    expected_resource,
                    actual_resource,
                )
            else:
                assert equal(actual_value, value), (node["id"], name, value, actual_value)
            count += 1
    return {
        "checked_inputs": count,
        "checked_links": len(expected["links"]) + 1,
        "target_graph_verified": True,
        "appearance_verified": False,
    }


def main():
    """只允许后台独立任务；输出目录必须新建，防止混用旧结果。"""
    if not bpy.app.background or "--factory-startup" not in sys.argv:
        raise RuntimeError("必须使用独立后台 Blender 和 --factory-startup")
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--source", type=Path)
    mode.add_argument("--documents", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--material-cache", type=Path, help="另存仅含材质依赖的测试缓存")
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    args.out.mkdir(parents=True, exist_ok=False)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    service = CachedService(controller.create_service())
    summary = export_all(args, service) if args.source else import_all(args, service)
    print(json.dumps({"completed": len(summary["cases"])}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
