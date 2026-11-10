"""实机探测 Nodes 分组、可编辑接口及 Ungroup 的参数传播行为。"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    """只加载测试副本并写入新目录，保存当前端口值和实际层级证据。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    import c4d
    import maxon

    from plugins.cinema4d.adapter import SPACE, json_value, port

    document = c4d.documents.LoadDocument(str(args.source), c4d.SCENEFILTER_MATERIALS)
    try:
        material = document.GetFirstMaterial()
        graph = material.GetNodeMaterialReference().GetGraph(SPACE)
        nodes = {
            str(n.GetId()): n
            for n in graph.GetViewRoot().GetChildren()
            if n.GetKind() == maxon.NODE_KIND.NODE
        }
        prefix = "com.redshift3d.redshift4c4d.nodes.core.rsmathmul."
        with graph.BeginTransaction() as transaction:
            group = graph.MoveToGroup(
                maxon.GraphNode(), maxon.Id("math_group"), [nodes["multiply"], nodes["edited_add"]]
            )
            group.SetValue(maxon.NODE.BASE.NAME, maxon.String("可编辑数学组"))
            children = list(group.GetChildren())
            child = group.FindChild("multiply")
            assert not child.IsNullValue()
            control = group.GetInputs().AddPort(maxon.Id("gain"))
            control.SetPortValue(0.8)
            control.Connect(port(child, prefix + "input1"))
            transaction.Commit()
        evidence = {
            "group_id": str(group.GetId()),
            "children": [(str(n.GetId()), str(n.GetKind())) for n in children],
            "group_input": json_value(control.GetPortValue()),
            "child_input": json_value(port(child, prefix + "input1").GetPortValue()),
        }
        path = args.out / "grouped.c4d"
        assert c4d.documents.SaveDocument(
            document, str(path), c4d.SAVEDOCUMENTFLAGS_DONTADDTORECENTLIST, c4d.FORMAT_C4DEXPORT
        )
        from plugins.cinema4d.adapter import Cinema4DAdapter
        from plugins.core import create_service, export_material

        with create_service(ROOT) as service:
            adapter = Cinema4DAdapter(document, service)
            actual = adapter.read_material(material)
            evidence["group_current_read"] = next(
                n for n in actual["nodes"] if n["id"] == "multiply"
            )
            exported = export_material(
                adapter, service, material, args.out / "current.mtlx", accept_warnings=True
            )
            assert exported["saved_path"], exported
            assert abs(evidence["group_current_read"]["inputs"][prefix + "input1"] - 0.8) < 1e-6
        with graph.BeginTransaction() as transaction:
            mapping = graph.Ungroup(group)
            transaction.Commit()
        evidence["ungroup_mapping"] = [(str(a), str(b)) for a, b in mapping]
        evidence["root_children"] = [str(n.GetId()) for n in graph.GetViewRoot().GetChildren()]
        (args.out / "summary.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        child = next(n for n in graph.GetViewRoot().GetChildren() if "multiply" in str(n.GetId()))
        evidence["ungroup_input"] = json_value(port(child, prefix + "input1").GetPortValue())
        evidence["group_input_propagated"] = abs(evidence["ungroup_input"] - 0.8) < 1e-6
        (args.out / "summary.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(evidence, ensure_ascii=False), flush=True)
    finally:
        c4d.documents.KillDocument(document)


if __name__ == "__main__":
    main()
