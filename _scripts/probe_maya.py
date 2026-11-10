"""在独立 Maya 2022 中探测节点属性、默认值和可写性，不修改软件配置。"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    """实机创建并读取目标节点；未知节点记录失败，不伪造端口。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    import maya.standalone

    maya.standalone.initialize(name="python")
    from maya import cmds

    from plugins.maya.adapter import ensure_environment

    target = ensure_environment()
    old = json.loads((ROOT / "dictionaries/catalogs/maya-arnold-7.2.5.3.json").read_text("utf-8"))
    result = {k: v for k, v in old.items() if k != "nodes"}
    result["nodes"] = {}
    result["runtime"] = target
    for kind in list(old["nodes"]) + [
        "aiUvTransform",
        "aiUserDataVector",
        "aiUtility",
        "place2dTexture",
        "file",
        "aiCompare",
        "aiSwitchRgba",
    ]:
        try:
            node = cmds.createNode(kind)
            attrs = {}
            for name in cmds.listAttr(node, read=True) or []:
                try:
                    attrs[name] = {
                        "type": cmds.getAttr(node + "." + name, type=True),
                        "value": cmds.getAttr(node + "." + name),
                        "enum": cmds.attributeQuery(name, node=node, listEnum=True),
                        "writable": cmds.attributeQuery(name, node=node, writable=True),
                        "connectable": cmds.attributeQuery(name, node=node, connectable=True),
                        "parent": cmds.attributeQuery(name, node=node, listParent=True),
                    }
                except (ValueError, RuntimeError):
                    continue
            result["nodes"][kind] = {
                "status": "instantiated",
                "actual_type": cmds.nodeType(node),
                "attributes": attrs,
            }
            cmds.delete(node)
        except RuntimeError as exc:
            result["nodes"][kind] = {"status": "unavailable", "error": str(exc)}
    with args.out.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    maya.standalone.uninitialize()


if __name__ == "__main__":
    main()
