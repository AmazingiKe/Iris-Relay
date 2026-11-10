"""在独立 c4dpy 中导入 MaterialX，保存重开后逐节点、端口、贴图和连接核对。"""

import argparse
import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def verify(adapter, expected, name):
    """先独立核对真实分组，再在私有副本核对所有计算节点和边。"""
    import c4d

    from plugins.cinema4d.adapter import SPACE, Cinema4DAdapter, child_nodes, flattened_copy

    material = next(m for m in adapter.document.GetMaterials() if m.GetName() == name)
    graph = material.GetNodeMaterialReference().GetGraph(SPACE)
    groups = [n for n in child_nodes(graph.GetViewRoot()) if child_nodes(n)]
    if not groups:
        return verify_flat(adapter, expected, name)
    plans = expected.get("metadata", {}).get("editor", {}).get("groups", [])
    required = {
        "iris_function_" + str(i): g
        for i, g in enumerate(plans)
        if set(g["nodes"]) - {c["node"] for c in g["controls"]}
    }
    assert len(groups) == len(required)
    for group in groups:
        plan = required[str(group.GetId())]
        actual = {str(n.GetId()) for n in child_nodes(group)}
        assert set(plan["nodes"]) - {c["node"] for c in plan["controls"]} <= actual
    staging, cloned, origins = flattened_copy(adapter.document, material)
    try:
        result = verify_flat(Cinema4DAdapter(staging, adapter.service), expected, name, origins)
        result["native_function_groups"] = len(groups)
        return result
    finally:
        c4d.documents.KillDocument(staging)


def verify_flat(adapter, expected, name, origins=None):
    """独立读取实际图；不依赖源 Blender 快照或创建时的节点引用。"""
    import maxon

    from iris_relay.native import encode_value, resource_color_space
    from plugins.cinema4d.adapter import (
        OUTPUT,
        SPACE,
        equal,
        implementation_hints,
        json_value,
        port,
    )

    material = next(m for m in adapter.document.GetMaterials() if m.GetName() == name)
    assert implementation_hints(material) == {n["id"]: n["type"] for n in expected["nodes"]}
    graph = material.GetNodeMaterialReference().GetGraph(SPACE)
    origins = origins or {}

    def node_identifier(node):
        """副本重分配的 SDK ID 对齐当前原生组内身份，不读取参数缓存。"""
        return origins.get(str(node.GetId()), {}).get("id", str(node.GetId()))

    nodes = {
        node_identifier(n): n
        for n in graph.GetViewRoot().GetChildren()
        if n.GetKind() == maxon.NODE_KIND.NODE
    }
    projected = {}
    records = {n["id"]: n for n in expected["nodes"]}
    for edge in expected["links"]:
        start = edge["from"]
        native = adapter.profile["nodes"][records[start["node"]]["type"]]["outputs"][
            start["socket"]
        ]["native"]
        spec = native.get("properties", {}).get("output_projection")
        if spec:
            helper = start["node"] + "__" + start["socket"] + "_projection"
            projected[helper] = (start["node"], spec)
    assert set(nodes) == {n["id"] for n in expected["nodes"]} | {"material_output"} | set(projected)
    for helper, (_, spec) in projected.items():
        assert str(nodes[helper].GetValue(maxon.nodes.AssetId)[0]) == spec["asset"]
    connected = {(e["to"]["node"], e["to"]["socket"]) for e in expected["links"]}
    count, images = 0, 0
    records = {n["id"]: n for n in expected["nodes"]}
    for record in expected["nodes"]:
        node = nodes[record["id"]]
        rule = adapter.profile["nodes"][record["type"]]
        assert str(node.GetValue(maxon.nodes.AssetId)[0]) == rule["native"]["id"]
        for code, value in rule["native"].get("properties", {}).items():
            assert equal(json_value(port(node, code).GetPortValue()), value), (code, value)
        for key, value in record["inputs"].items():
            if value is None or (record["id"], key) in connected:
                continue
            binding = rule["inputs"][key]
            native = binding["native"]
            if binding["type"] == "texture":
                resource = expected["textures"][value["texture"]]
                assert Path(json_value(port(node, native["id"]).GetPortValue())) == Path(
                    resource["uri"]
                )
                assert json_value(
                    port(node, native["colorspace_id"]).GetPortValue()
                ) == resource_color_space(resource["color_space"], binding)
                assert (
                    json_value(port(node, native["uv_id"]).GetPortValue())
                    == resource["coordinates"]["set"]
                )
                images += 1
                plan = resource.get("metadata", {}).get("native_uv_target")
                if plan:
                    for key, identifier in native["properties"]["uv_sampler"].items():
                        assert equal(json_value(port(node, identifier).GetPortValue()), plan[key])
            else:
                assert equal(
                    json_value(port(node, native["id"]).GetPortValue()),
                    encode_value(value, binding),
                ), (record["id"], key, value)
            count += 1
    expected_connections = set()
    for edge in expected["links"]:
        start, end = edge["from"], edge["to"]
        a = adapter.profile["nodes"][records[start["node"]]["type"]]["outputs"][start["socket"]][
            "native"
        ]["id"]
        b = adapter.profile["nodes"][records[end["node"]]["type"]]["inputs"][end["socket"]][
            "native"
        ]["id"]
        expected_connections.add((start["node"], a, end["node"], b))
        native = adapter.profile["nodes"][records[start["node"]]["type"]]["outputs"][
            start["socket"]
        ]["native"]
        spec = native.get("properties", {}).get("output_projection")
        if spec:
            expected_connections.remove((start["node"], a, end["node"], b))
            helper = start["node"] + "__" + start["socket"] + "_projection"
            expected_connections.add((start["node"], spec["source"], helper, spec["input"]))
            expected_connections.add((helper, a, end["node"], b))
    output = expected["output"]
    a = adapter.profile["nodes"][records[output["node"]]["type"]]["outputs"][output["socket"]][
        "native"
    ]["id"]
    expected_connections.add((output["node"], a, "material_output", OUTPUT + ".surface"))
    actual_connections = set()
    for name, node in nodes.items():
        pending = list(node.GetInputs().GetChildren())
        while pending:
            socket = pending.pop()
            pending.extend(socket.GetChildren())
            for origin, _ in socket.GetConnections(maxon.PORT_DIR.INPUT):
                actual_connections.add(
                    (
                        node_identifier(origin.GetParent().GetParent()),
                        str(origin.GetId()),
                        name,
                        str(socket.GetId()),
                    )
                )
    assert actual_connections == expected_connections, (
        actual_connections - expected_connections,
        expected_connections - actual_connections,
    )
    return {
        "nodes": len(expected["nodes"]),
        "inputs": count,
        "images": images,
        "links": len(actual_connections),
        "target_graph_verified": True,
    }


def main():
    """每份文件独立事务和诊断；终态报告只有保存重开全部复核后才标记完成。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--require-success", type=int, default=0)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    import c4d

    from plugins.cinema4d.adapter import Cinema4DAdapter
    from plugins.core import create_service, import_material

    def save(name, value):
        (args.out / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    summary = {"status": "running", "cases": [], "appearance_verified": False}
    document = c4d.documents.BaseDocument()
    with create_service() as service:
        try:
            adapter = Cinema4DAdapter(document, service)
            summary["target"] = adapter.target
            context = adapter.context()
            successful = []
            for path in sorted(args.documents.glob("*.mtlx")):
                before = list(document.GetMaterials())
                result = import_material(
                    adapter, service, path, adapter.target, accept_warnings=True
                )
                case = {
                    "id": path.stem,
                    "status": result["status"],
                    "message": result["message"],
                    "created": result["created"],
                }
                if result["created"]:
                    case.update(verify(adapter, result["material"], result["created"]["material"]))
                    successful.append((case, result["material"]))
                else:
                    assert list(document.GetMaterials()) == before
                summary["cases"].append(case)
                save(path.stem + ".json", result)
                save("summary.json", summary)
                print(
                    json.dumps(
                        {k: v for k, v in case.items() if k != "created"}, ensure_ascii=False
                    ),
                    flush=True,
                )
            assert adapter.context() == context
            scene = str(args.out / "restored.c4d")
            assert c4d.documents.SaveDocument(
                document, scene, c4d.SAVEDOCUMENTFLAGS_DONTADDTORECENTLIST, c4d.FORMAT_C4DEXPORT
            )
            c4d.documents.KillDocument(document)
            document = None
            document = c4d.documents.LoadDocument(scene, c4d.SCENEFILTER_MATERIALS, None)
            assert document is not None
            adapter = Cinema4DAdapter(document, service)
            for case, material in successful:
                case.update(verify(adapter, material, case["created"]["material"]), reopened=True)
            assert adapter.context() == context
            summary.update(
                status="complete",
                successful=len(successful),
                completed=len(summary["cases"]),
                scene_conditions_unchanged=True,
            )
            if len(successful) < args.require_success:
                raise RuntimeError(f"要求 {args.require_success} 份成功，实际 {len(successful)} 份")
        except Exception:
            summary.update(status="error", traceback=traceback.format_exc())
        finally:
            if document is not None:
                c4d.documents.KillDocument(document)
            save("summary.json", summary)
    if summary["status"] != "complete":
        raise RuntimeError(summary["traceback"])


if __name__ == "__main__":
    main()
