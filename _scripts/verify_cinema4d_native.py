"""直接创建无 Iris 提示的 Redshift 原生图，验证编辑、重开和当前图导出。"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    """独立材质只改变自身；当前原生图变化必须反映到导出节点、值和边。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    import c4d
    import maxon

    from plugins.cinema4d.adapter import (
        OUTPUT,
        SPACE,
        Cinema4DAdapter,
        implementation_hints,
        port,
        set_value,
    )
    from plugins.core import create_service, export_material

    prefix = "com.redshift3d.redshift4c4d.nodes.core."
    document = c4d.documents.BaseDocument()
    try:
        material = c4d.BaseMaterial(c4d.Mmaterial)
        material.SetName("独立原生材质")
        document.InsertMaterial(material)
        graph = maxon.GraphDescription.GetGraph(material, maxon.Id(SPACE), createEmpty=True)
        with graph.BeginTransaction() as transaction:
            surface = graph.AddChild(maxon.Id("surface"), maxon.Id(prefix + "standardmaterial"))
            color = graph.AddChild(maxon.Id("color"), maxon.Id(prefix + "rscolorconstant"))
            multiply = graph.AddChild(maxon.Id("multiply"), maxon.Id(prefix + "rsmathmul"))
            output = graph.AddChild(maxon.Id("output"), maxon.Id(OUTPUT))
            set_value(color, prefix + "rscolorconstant.color", [0.2, 0.4, 0.8, 1])
            set_value(multiply, prefix + "rsmathmul.input1", 0.6)
            set_value(multiply, prefix + "rsmathmul.input2", 0.7)
            port(color, prefix + "rscolorconstant.outcolor", True).Connect(
                port(surface, prefix + "standardmaterial.base_color")
            )
            port(multiply, prefix + "rsmathmul.out", True).Connect(
                port(surface, prefix + "standardmaterial.refl_roughness")
            )
            port(surface, prefix + "standardmaterial.outcolor", True).Connect(
                port(output, OUTPUT + ".surface")
            )
            transaction.Commit()
        assert implementation_hints(material) == {}
        with create_service(ROOT) as service:
            adapter = Cinema4DAdapter(document, service)
            first = export_material(
                adapter, service, material, args.out / "before.mtlx", accept_warnings=True
            )
            assert first["saved_path"], first
            assert len(first["material"]["nodes"]) == 3 and len(first["material"]["links"]) == 2
            with graph.BeginTransaction() as transaction:
                added = graph.AddChild(maxon.Id("edited_add"), maxon.Id(prefix + "rsmathadd"))
                set_value(added, prefix + "rsmathadd.input2", 0.1)
                set_value(color, prefix + "rscolorconstant.color", [0.4, 0.2, 0.7, 1])
                destination = port(surface, prefix + "standardmaterial.refl_roughness")
                origin = port(multiply, prefix + "rsmathmul.out", True)
                maxon.GraphModelHelper.RemoveConnection(origin, destination)
                origin.Connect(port(added, prefix + "rsmathadd.input1"))
                port(added, prefix + "rsmathadd.out", True).Connect(destination)
                transaction.Commit()
            path = args.out / "edited.c4d"
            assert c4d.documents.SaveDocument(
                document, str(path), c4d.SAVEDOCUMENTFLAGS_DONTADDTORECENTLIST, c4d.FORMAT_C4DEXPORT
            )
            c4d.documents.KillDocument(document)
            document = c4d.documents.LoadDocument(str(path), c4d.SCENEFILTER_MATERIALS)
            assert document is not None
            material = document.GetFirstMaterial()
            adapter = Cinema4DAdapter(document, service)
            second = export_material(
                adapter, service, material, args.out / "after.mtlx", accept_warnings=True
            )
            assert second["saved_path"], second
            assert len(second["material"]["nodes"]) == 4 and len(second["material"]["links"]) == 3
            # 未支持节点从当前图读取，不能因已有成功记录而被省略。
            graph = material.GetNodeMaterialReference().GetGraph(SPACE)
            surface = graph.GetViewRoot().FindChild("surface")
            added = graph.GetViewRoot().FindChild("edited_add")
            with graph.BeginTransaction() as transaction:
                unknown = graph.AddChild(maxon.Id("unknown_sin"), maxon.Id(prefix + "rsmathsin"))
                destination = port(surface, prefix + "standardmaterial.refl_roughness")
                maxon.GraphModelHelper.RemoveConnection(
                    port(added, prefix + "rsmathadd.out", True), destination
                )
                port(unknown, prefix + "rsmathsin.out", True).Connect(destination)
                transaction.Commit()
            try:
                adapter.read_material(material)
            except Exception as exc:
                assert getattr(exc, "code", None) == "UNSUPPORTED_NODE", repr(exc)
            else:
                raise AssertionError("未知原生节点被静默省略")
            (args.out / "details.json").write_text(
                json.dumps({"before": first, "after": second}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            (args.out / "summary.json").write_text(
                json.dumps(
                    {
                        "before_nodes": 3,
                        "after_nodes": 4,
                        "before_links": 2,
                        "after_links": 3,
                        "reopened": True,
                        "unmarked_native_graph": True,
                        "unknown_node_blocked": True,
                        "appearance_verified": False,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
    finally:
        if document is not None:
            c4d.documents.KillDocument(document)


if __name__ == "__main__":
    main()
