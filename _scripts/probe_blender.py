"""在 Blender 后台进程中提取当前版本的真实着色节点目录。"""

import json
import os
from pathlib import Path

import bpy


def json_value(value):
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    try:
        return list(value)
    except TypeError:
        return {"native_type": type(value).__name__}


def describe_socket(socket):
    return {
        "id": socket.identifier,
        "label": socket.name,
        "type": socket.type,
        "class": socket.bl_idname,
        "default": json_value(getattr(socket, "default_value", None)),
        "enabled": socket.enabled,
        "hide_value": socket.hide_value,
    }


def probe():
    result = {
        "catalog_version": "1",
        "host": {"id": "blender", "version": ".".join(map(str, bpy.app.version))},
        "renderer": {"id": "cycles", "version": ".".join(map(str, bpy.app.version))},
        "node_system": "blender.shader",
        "evidence": "runtime_instantiation",
        "scope": "当前安装版本的 ShaderNode 类型；端口为默认节点配置，未声明可转换全部节点",
        "nodes": {},
    }
    material = bpy.data.materials.new("IrisRelay_NodeProbe")
    material.use_nodes = True
    material.node_tree.nodes.clear()
    try:
        for name in dir(bpy.types):
            cls = getattr(bpy.types, name)
            if (
                not isinstance(cls, type)
                or not issubclass(cls, bpy.types.ShaderNode)
                or name == "ShaderNode"
            ):
                continue
            try:
                node = material.node_tree.nodes.new(name)
                properties = {}
                for prop in node.bl_rna.properties:
                    if (
                        prop.identifier in bpy.types.Node.bl_rna.properties.keys()
                        or prop.identifier.startswith("bl_")
                    ):
                        continue
                    if (
                        prop.type in {"ENUM", "BOOLEAN", "STRING", "INT", "FLOAT"}
                        and not prop.is_readonly
                    ):
                        record = {
                            "default": json_value(getattr(node, prop.identifier)),
                            "type": prop.type,
                        }
                        if prop.type == "ENUM":
                            record["items"] = [item.identifier for item in prop.enum_items]
                        properties[prop.identifier] = record
                result["nodes"][name] = {
                    "id": name,
                    "label": node.bl_label,
                    "status": "instantiated",
                    "inputs": [describe_socket(item) for item in node.inputs],
                    "outputs": [describe_socket(item) for item in node.outputs],
                    "properties": properties,
                }
                material.node_tree.nodes.remove(node)
            except Exception as exc:
                result["nodes"][name] = {"id": name, "status": "unavailable", "error": str(exc)}
    finally:
        bpy.data.materials.remove(material)
    return result


if __name__ == "__main__":
    target = Path(os.environ["IRIS_RELAY_OUTPUT"])
    with target.open("x", encoding="utf-8") as stream:
        json.dump(probe(), stream, ensure_ascii=False, indent=2, allow_nan=False)
