"""在独立 Blender 进程只读盘点全部材质、节点组和图片，输出可追溯的 JSON。"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import bpy


def serializable(value):
    """将 RNA 值转换为报告数据；数据块引用只记录名称，不遍历场景对象。"""
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, bpy.types.ID):
        return {"datablock": value.name_full}
    try:
        return [serializable(item) for item in value]
    except TypeError:
        return str(value)


def tree_record(tree):
    """读取一个节点树的原生属性、全部插口和连接，节点组通过名称关联。"""
    nodes = []
    for node in tree.nodes:
        properties = {}
        for prop in node.bl_rna.properties:
            if not prop.is_readonly and prop.type in {"BOOLEAN", "INT", "FLOAT", "STRING", "ENUM"}:
                try:
                    properties[prop.identifier] = serializable(getattr(node, prop.identifier))
                except (AttributeError, TypeError, ValueError) as exc:
                    properties[prop.identifier] = {"read_error": str(exc)}
        record = {"name": node.name, "type": node.bl_idname, "properties": properties}
        for direction in ("inputs", "outputs"):
            record[direction] = [
                {
                    "id": socket.identifier,
                    "name": socket.name,
                    "type": socket.type,
                    "enabled": socket.enabled,
                    "linked": socket.is_linked,
                    "value": serializable(getattr(socket, "default_value", None)),
                }
                for socket in getattr(node, direction)
            ]
        if getattr(node, "image", None):
            record["image"] = node.image.name_full
        if getattr(node, "node_tree", None):
            record["group"] = node.node_tree.name_full
        if hasattr(node, "color_ramp"):
            ramp = node.color_ramp
            record["color_ramp"] = {
                "interpolation": ramp.interpolation,
                "color_mode": ramp.color_mode,
                "hue_interpolation": ramp.hue_interpolation,
                "elements": [
                    {"position": e.position, "color": list(e.color)} for e in ramp.elements
                ],
            }
        nodes.append(record)
    return {
        "nodes": nodes,
        "links": [
            {
                "from": [link.from_node.name, link.from_socket.identifier],
                "to": [link.to_node.name, link.to_socket.identifier],
                "valid": link.is_valid,
            }
            for link in tree.links
        ],
    }


def image_record(image):
    """记录图片资源位置和打包状态，不加载像素、不解包也不修改文件。"""
    path = bpy.path.abspath(image.filepath, library=image.library)
    return {
        "name": image.name_full,
        "filepath": image.filepath,
        "resolved_path": path,
        "exists": Path(path).is_file() if path else False,
        "source": image.source,
        "color_space": image.colorspace_settings.name,
        "alpha_mode": image.alpha_mode,
        "packed": bool(image.packed_file),
        "packed_files": len(image.packed_files),
        "users": image.users,
    }


def main():
    """盘点所有材质及引用的递归节点组，排他写入报告；不保存源场景。"""
    if not bpy.app.background:
        raise RuntimeError("请在独立后台 Blender 进程运行")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    report = inspect_loaded()
    with args.out.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(
        json.dumps(
            {
                "materials": len(report["materials"]),
                "groups": len(report["groups"]),
                "nodes": report["node_counts"],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


def inspect_loaded(materials=None):
    """返回已加载材质清单；不访问 image.size，以免隐式解码大量贴图像素。"""
    selected = list(bpy.data.materials if materials is None else materials)
    # 1. 不依赖对象绑定，枚举文件中加载的全部材质。
    materials, groups, counts = [], {}, Counter()
    pending = []
    for material in selected:
        record = {
            "name": material.name_full,
            "library": material.library.filepath if material.library else None,
            "users": material.users,
            "use_nodes": material.use_nodes,
            "diffuse_color": list(material.diffuse_color),
        }
        if material.node_tree:
            record.update(tree_record(material.node_tree))
            counts.update(n["type"] for n in record["nodes"])
            pending.extend(n.node_tree for n in material.node_tree.nodes if n.type == "GROUP")
        materials.append(record)
    # 2. 按数据块去重读取节点组，保留共享和嵌套关系。
    while pending:
        tree = pending.pop()
        if tree is None or tree.name_full in groups:
            continue
        groups[tree.name_full] = tree_record(tree)
        pending.extend(n.node_tree for n in tree.nodes if n.type == "GROUP")
    return {
        "source": bpy.data.filepath,
        "blender_version": bpy.app.version_string,
        "color_context": {
            "working_space": bpy.data.colorspace.working_space,
            "working_space_interop_id": bpy.data.colorspace.working_space_interop_id,
            "missing_config": bpy.data.colorspace.is_missing_opencolorio_config,
        },
        "materials": materials,
        "groups": groups,
        "images": [image_record(image) for image in bpy.data.images],
        "libraries": [
            {"filepath": lib.filepath, "resolved_path": bpy.path.abspath(lib.filepath)}
            for lib in bpy.data.libraries
        ],
        "node_counts": dict(counts.most_common()),
    }


if __name__ == "__main__":
    main()
