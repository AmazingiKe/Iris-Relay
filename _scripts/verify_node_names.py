"""在独立宿主中核对统一节点名、同名导入和保存重开后的原名溯源。"""

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def check_readback(service, actual, expected):
    """检查当前原生图及再次导出的备份；连接和节点数量须保持，原名不作显示。"""
    from plugins.core import request

    assert len(actual["links"]) == len(expected["links"])
    originals = {n["metadata"]["source_name"] for n in expected["nodes"]}
    expected_names = {n["metadata"]["source_name"]: n["id"] for n in expected["nodes"]}
    assert {n["metadata"]["source_name"] for n in actual["nodes"]} == originals, (
        [(n["id"], n["metadata"]["source_name"]) for n in actual["nodes"]],
        originals,
    )
    for node in actual["nodes"]:
        current = node["metadata"].get("native_name", node["id"])
        assert re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", current), current
        if expected_names[node["metadata"]["source_name"]] != node["metadata"]["source_name"]:
            assert (
                node["metadata"].get("editor", {}).get("label") != node["metadata"]["source_name"]
            )
    exported = request(service, "materialx.export", material=actual)
    assert exported["result"]["document"], exported
    converted = request(
        service,
        "materialx.convert",
        document=exported["result"]["document"],
        target=expected["renderer"],
    )
    material = converted["result"]["material"]
    assert material, converted
    assert {n["metadata"]["source_name"] for n in material["nodes"]} == originals
    assert len(material["links"]) == len(expected["links"])
    return exported["result"]["document"]


def run_host(job, service):
    """新建隔离场景，连续导入两次，再保存重开并读取实际原生节点。"""
    from plugins.core import request

    out = Path(job["out"])
    host = job["host"]
    document = None
    if host == "blender":
        import bpy

        from plugins.blender.adapter import BlenderAdapter, renderer_ref

        target = renderer_ref()
        adapter = BlenderAdapter(None, service)
    elif host == "maya":
        from maya import cmds

        from plugins.maya.adapter import MayaAdapter, ensure_environment

        cmds.file(new=True, force=True)
        target = ensure_environment()
        adapter = MayaAdapter(service, target)
    else:
        import c4d

        from plugins.cinema4d.adapter import Cinema4DAdapter

        document = c4d.documents.BaseDocument()
        adapter = Cinema4DAdapter(document, service)
        target = adapter.target
    created = []
    try:
        for path in job["documents"]:
            response = request(
                service, "materialx.convert", document=Path(path).read_text("utf-8"), target=target
            )
            expected = response["result"]["material"]
            assert expected, response
            for _ in range(2):
                result = adapter.create_material(expected)
                name = result.name if host == "blender" else result["material"]
                assert name not in {item[0] for item in created}
                created.append((name, expected))
        if host == "blender":
            scene = str(out / "names.blend")
            bpy.ops.wm.save_as_mainfile(filepath=scene)
            bpy.ops.wm.open_mainfile(filepath=scene)
        elif host == "maya":
            scene = str(out / "names.ma")
            cmds.file(rename=scene)
            cmds.file(save=True, type="mayaAscii", force=True)
            cmds.file(new=True, force=True)
            cmds.file(scene, open=True, force=True, prompt=False)
        else:
            scene = str(out / "names.c4d")
            assert c4d.documents.SaveDocument(
                document, scene, c4d.SAVEDOCUMENTFLAGS_DONTADDTORECENTLIST, c4d.FORMAT_C4DEXPORT
            )
            c4d.documents.KillDocument(document)
            document = c4d.documents.LoadDocument(scene, c4d.SCENEFILTER_MATERIALS, None)
            assert document is not None
            adapter = Cinema4DAdapter(document, service)
        for index, (name, expected) in enumerate(created):
            if host == "blender":
                actual = BlenderAdapter(bpy.data.materials[name], service).read_material()
            elif host == "maya":
                actual = adapter.read_material(name)
            else:
                material = next(m for m in document.GetMaterials() if m.GetName() == name)
                actual = adapter.read_material(material)
            (out / (str(index) + "-read.json")).write_text(
                json.dumps({"actual": actual, "expected": expected}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            xml = check_readback(service, actual, expected)
            (out / (str(index) + ".mtlx")).write_text(xml, encoding="utf-8")
        return {
            "target": target,
            "materials": len(created),
            "saved_reopened": True,
            "source_names_preserved": True,
            "appearance_verified": False,
        }
    finally:
        if document is not None:
            c4d.documents.KillDocument(document)


def main():
    """作业由环境指定；输出目录必须新建，不写入用户已打开的场景。"""
    job = json.loads(Path(os.environ["IRIS_NAMING_JOB"]).read_text("utf-8"))
    out = Path(job["out"])
    out.mkdir(parents=True, exist_ok=False)
    if job["host"] == "maya":
        import maya.standalone

        maya.standalone.initialize(name="python")
    from plugins.core import create_service

    with create_service(transport="LOCAL") as service:
        result = run_host(job, service)
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
    if job["host"] == "maya":
        maya.standalone.uninitialize()


if __name__ == "__main__":
    main()
