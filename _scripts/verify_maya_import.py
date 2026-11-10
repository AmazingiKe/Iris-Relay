"""用 Maya 2022 mayapy 独立导入 .mtlx，保存重开核对原生节点、参数、贴图和连接。"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def verify(adapter, material, created):
    """直接查询 Maya 原生属性与连线；报告不读取源 Blender 快照。"""
    from maya import cmds

    from plugins.maya.adapter import encoded, equal

    nodes = created["nodes"]
    records = {n["id"]: n for n in material["nodes"]}
    connected = {(e["to"]["node"], e["to"]["socket"]) for e in material["links"]}
    count, resources = 0, 0
    for record in material["nodes"]:
        node = nodes[record["id"]]
        rule = adapter.profile["nodes"][record["type"]]
        assert cmds.nodeType(node) == rule["native"]["id"], node
        for attr, value in rule["native"]["properties"].items():
            actual = cmds.getAttr(node + "." + attr)
            if isinstance(value, list):
                actual = actual[0]
            assert equal(value, actual), (node, attr, value, actual)
        for port, value in record["inputs"].items():
            if value is None or (record["id"], port) in connected:
                continue
            binding = rule["inputs"][port]
            native = binding["native"]
            if binding["type"] == "texture":
                resource = material["textures"][value["texture"]]
                assert cmds.getAttr(node + "." + native["id"]) == resource["uri"]
                assert cmds.getAttr(node + "." + native["colorspace_id"]) == resource["color_space"]
                assert cmds.getAttr(node + "." + native["uv_id"]) == resource["coordinates"]["set"]
                resources += 1
            else:
                expected = encoded(value, binding)
                actual = cmds.getAttr(node + "." + native["id"])
                if isinstance(expected, list):
                    actual = actual[0]
                assert equal(expected, actual), (node, port, expected, actual)
            count += 1
    for link in material["links"]:
        source, target = link["from"], link["to"]
        source_rule = adapter.profile["nodes"][records[source["node"]]["type"]]["outputs"][
            source["socket"]
        ]
        target_rule = adapter.profile["nodes"][records[target["node"]]["type"]]["inputs"][
            target["socket"]
        ]
        start = nodes[source["node"]] + "." + source_rule["native"]["id"]
        for end in adapter.destination_plugs(nodes[target["node"]], target_rule):
            assert cmds.isConnected(start, end), (start, end)
    output = material["output"]
    rule = adapter.profile["nodes"][records[output["node"]]["type"]]["outputs"][output["socket"]]
    assert cmds.isConnected(
        nodes[output["node"]] + "." + rule["native"]["id"],
        created["shading_group"] + ".surfaceShader",
    )
    return {
        "nodes": len(nodes),
        "inputs": count,
        "links": len(material["links"]) + 1,
        "images": resources,
        "target_graph_verified": True,
    }


def verify_transactions(adapter, material, existing):
    """在独立验证场景检查撤销、重做和中途失败，保留已存在的绑定及选择。"""
    from maya import cmds

    from plugins.maya import adapter as host

    selection = cmds.ls(selection=True, long=True) or []
    sentinel = cmds.polyCube(name="IrisTransactionSentinel")[0]
    cmds.sets(sentinel, edit=True, forceElement=existing["shading_group"])
    members = cmds.sets(existing["shading_group"], query=True)
    shape = cmds.listRelatives(sentinel, shapes=True)[0]
    assert cmds.sets(shape, isMember=existing["shading_group"])
    cmds.select(sentinel, replace=True)
    before = set(cmds.ls())
    created = adapter.create_material(material)
    verify(adapter, material, created)
    assert cmds.ls(selection=True) == [sentinel]
    cmds.undo()
    assert set(cmds.ls()) == before
    cmds.redo()
    verify(adapter, material, created)
    cmds.undo()
    assert set(cmds.ls()) == before

    original, calls = host.set_value, []

    def fail_after_partial_creation(plug, value):
        """第二次写参数时注入失败，确保测试确实经过节点创建。"""
        calls.append(plug)
        if len(calls) == 2:
            raise RuntimeError("事务验证：模拟原生参数写入失败")
        return original(plug, value)

    host.set_value = fail_after_partial_creation
    try:
        try:
            adapter.create_material(material)
        except RuntimeError as exc:
            assert "事务验证" in str(exc)
        else:
            raise AssertionError("失败注入未生效")
    finally:
        host.set_value = original
    assert len(calls) == 2 and set(cmds.ls()) == before
    assert cmds.ls(selection=True) == [sentinel]
    assert cmds.sets(existing["shading_group"], query=True) == members
    assert cmds.sets(shape, isMember=existing["shading_group"])
    cmds.delete(sentinel)
    cmds.select(selection, replace=True) if selection else cmds.select(clear=True)
    return {"undo": True, "redo": True, "partial_failure_cleanup": True, "binding_preserved": True}


def verify_live_read(adapter, expected, created):
    """将真实读回的节点 ID 对齐目标图，逐条核对拓扑和未连接的有效输入。"""
    from plugins.maya.adapter import equal

    actual = adapter.read_material(created["material"])
    names = created["nodes"]
    reverse = {name: key for key, name in names.items()}
    nodes = {reverse[n["id"]]: n for n in actual["nodes"]}
    assert {key: n["type"] for key, n in nodes.items()} == {
        n["id"]: n["type"] for n in expected["nodes"]
    }
    edges = [
        {
            side: {"node": reverse[e[side]["node"]], "socket": e[side]["socket"]}
            for side in ("from", "to")
        }
        for e in actual["links"]
    ]
    assert sorted(edges, key=str) == sorted(expected["links"], key=str)
    assert {
        "node": reverse[actual["output"]["node"]],
        "socket": actual["output"]["socket"],
    } == expected["output"]
    connected = {(e["to"]["node"], e["to"]["socket"]) for e in expected["links"]}
    count = 0
    for record in expected["nodes"]:
        for key, value in record["inputs"].items():
            if (record["id"], key) in connected:
                continue
            found = nodes[record["id"]]["inputs"][key]
            if isinstance(value, dict) and "texture" in value:
                before, after = (
                    expected["textures"][value["texture"]],
                    actual["textures"][found["texture"]],
                )
                assert all(
                    before[field] == after[field] for field in ("uri", "color_space", "coordinates")
                )
            else:
                assert equal(value, found), (record["id"], key, value, found)
            count += 1
    return {
        "native_read_nodes": len(nodes),
        "native_read_inputs": count,
        "native_read_links": len(edges) + 1,
        "native_read_verified": True,
    }


def main():
    """在独立场景批处理；失败条目保留，任何校验失败都使进程非零退出。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--transactions", action="store_true")
    parser.add_argument("--export-documents", action="store_true", help="从重开后的真实节点再导出")
    parser.add_argument("--require-success", type=int, default=0)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    import maya.standalone

    maya.standalone.initialize(name="python")
    from maya import cmds

    from plugins.maya import controller
    from plugins.maya.adapter import MayaAdapter, ensure_environment

    target = ensure_environment()
    service = controller.create_service()
    adapter = MayaAdapter(service, target)
    summary = {"target": target, "appearance_verified": False, "cases": []}
    selection = cmds.ls(selection=True)
    color = cmds.colorManagementPrefs(query=True, renderingSpaceName=True)
    successful = []

    def save(name, data):
        """独立输出目录内更新本次报告。"""
        (args.out / name).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    for path in sorted(args.documents.glob("*.mtlx")):
        before = set(cmds.ls())
        result = controller.import_material(adapter, service, path, target, accept_warnings=True)
        case = {
            "id": path.stem,
            "status": result["status"],
            "message": result["message"],
            "created": result["created"],
        }
        if result["created"]:
            case.update(verify(adapter, result["material"], result["created"]))
            successful.append((case, result))
        else:
            assert set(cmds.ls()) == before, path
        summary["cases"].append(case)
        save(path.stem + ".json", result)
        save("summary.json", summary)
        print(
            json.dumps({k: v for k, v in case.items() if k != "created"}, ensure_ascii=False),
            flush=True,
        )
    assert cmds.ls(selection=True) == selection
    assert cmds.colorManagementPrefs(query=True, renderingSpaceName=True) == color
    if args.transactions:
        assert successful, "事务验证至少需要一个可导入材质"
        cmds.undoInfo(state=True)
        result = successful[0][1]
        summary["transactions"] = verify_transactions(
            adapter, result["material"], result["created"]
        )
    cmds.file(rename=str(args.out / "restored.ma"))
    cmds.file(save=True, type="mayaAscii", force=True)
    cmds.file(new=True, force=True)
    cmds.file(str(args.out / "restored.ma"), open=True, force=True, prompt=False)
    for case, result in successful:
        case.update(verify(adapter, result["material"], result["created"]), reopened=True)
    if args.export_documents:
        export_directory = args.out / "export"
        export_directory.mkdir(exist_ok=False)
        for case, result in successful:
            case.update(verify_live_read(adapter, result["material"], result["created"]))
            exported = controller.export_material(
                adapter,
                service,
                result["created"]["material"],
                export_directory / (case["id"] + ".mtlx"),
                accept_warnings=True,
            )
            save(case["id"] + "-export.json", exported)
            assert exported["saved_path"], exported
            case["native_reexported"] = True
    if len(successful) < args.require_success:
        raise RuntimeError("成功材质数量未达到要求：" + str(len(successful)))
    summary.update(
        scene_conditions_unchanged=True, successful=len(successful), completed=len(summary["cases"])
    )
    save("summary.json", summary)
    print(
        json.dumps({"completed": len(summary["cases"]), "successful": len(successful)}), flush=True
    )
    if hasattr(service, "close"):
        service.close()
    maya.standalone.uninitialize()


if __name__ == "__main__":
    main()
