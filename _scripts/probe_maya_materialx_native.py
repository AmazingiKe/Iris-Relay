"""直接调用本机 MtoA 菜单使用的原生导入命令，不通过 Iris Relay 适配器。"""

import argparse
import json
from pathlib import Path
from xml.etree import ElementTree as ET


def main():
    """在独立新场景中逐份导入，记录实际节点和错误；不将文件引用当成可编辑展开。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    import maya.standalone

    maya.standalone.initialize(name="python")
    from maya import cmds

    cmds.loadPlugin("mtoa")
    import arnold as ai

    cases = []
    try:
        for path in sorted(args.documents.glob("*.mtlx")):
            cmds.file(new=True, force=True)
            before = set(cmds.ls())
            case = {"id": path.stem, "command": "arnoldImportAss", "error": None}
            try:
                cmds.arnoldImportAss(f=str(path), mask=ai.AI_NODE_SHADER)
                added = sorted(set(cmds.ls()) - before)
                case["nodes"] = [{"name": n, "type": cmds.nodeType(n)} for n in added]
                case["created_nodes"] = len(added)
                case["imported_graph"] = any(cmds.nodeType(n) == "aiStandardSurface" for n in added)
                checks = []
                names = {
                    "base": "base",
                    "base_color": "baseColor",
                    "metalness": "metalness",
                    "specular": "specular",
                    "specular_roughness": "specularRoughness",
                    "specular_IOR": "specularIOR",
                    "coat": "coat",
                    "coat_color": "coatColor",
                    "coat_roughness": "coatRoughness",
                }
                for surface in ET.parse(str(path)).getroot().findall("standard_surface"):
                    node = surface.get("name")
                    if not cmds.objExists(node):
                        continue
                    for port in surface.findall("input"):
                        if port.get("name") not in names or port.get("value") is None:
                            continue
                        attribute = names[port.get("name")]
                        expected = [float(x) for x in port.get("value").split(",")]
                        actual = cmds.getAttr(node + "." + attribute)
                        actual = list(actual[0]) if isinstance(actual, list) else [actual]
                        checks.append(
                            {
                                "node": node,
                                "port": attribute,
                                "expected": expected,
                                "actual": actual,
                                "matched": len(expected) == len(actual)
                                and all(abs(a - b) < 2e-5 for a, b in zip(actual, expected)),
                            }
                        )
                case["constant_checks"] = checks
                case["constants_verified"] = bool(checks) and all(c["matched"] for c in checks)
                case["topology_verified"] = False
            except Exception as exc:
                case["error"] = str(exc)
                case["imported_graph"] = False
            cases.append(case)
            (args.out / "summary.json").write_text(
                json.dumps(
                    {"path": "mtoa_native_importer", "cases": cases, "appearance_verified": False},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            print(json.dumps(case, ensure_ascii=False), flush=True)
    finally:
        maya.standalone.uninitialize()


if __name__ == "__main__":
    main()
