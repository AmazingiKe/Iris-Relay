"""将 Blender 只读盘点数据装入公开原生保存协议，不猜测未知节点的着色含义。"""

import json
import math
from copy import deepcopy

SOCKET_TYPES = {
    "VALUE": "float",
    "INT": "integer",
    "BOOLEAN": "boolean",
    "RGBA": "color4",
    "VECTOR": "vector3",
    "STRING": "string",
    "SHADER": "surfaceshader",
}


def _json_safe(value):
    """非有限原生数值使用显式标签保存；不将其伪造为可执行的有限参数。"""
    if isinstance(value, float) and not math.isfinite(value):
        return {
            "iris_nonfinite_float": "nan" if math.isnan(value) else "+inf" if value > 0 else "-inf"
        }
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    return value


def _finite(value):
    """检查声明端口常量是否能直接写入标准数值；完整原值仍保留在元数据中。"""
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_finite(item) for item in value)
    return True


def preservation_snapshot(material, inventory):
    """保留顶层图、递归组定义、图片描述及属性；这里只转换宿主数据类型和端点格式。"""
    nodes, groups, images = [], {}, set()
    pending = [n.get("group") for n in material.get("nodes", []) if n.get("group")]
    while pending:
        name = pending.pop()
        if name in groups:
            continue
        group = inventory.get("groups", {}).get(name)
        groups[name] = deepcopy(group)
        if group:
            pending.extend(n["group"] for n in group.get("nodes", []) if n.get("group"))
            images.update(n["image"] for n in group.get("nodes", []) if n.get("image"))
    for source in material.get("nodes", []):
        node = {
            "id": source["name"],
            "type": source["type"],
            "inputs": [],
            "outputs": [],
            "properties": deepcopy(source.get("properties", {})),
            "metadata": deepcopy(source),
        }
        for direction in ("inputs", "outputs"):
            for socket in source.get(direction, []):
                datatype = SOCKET_TYPES.get(socket["type"], "string")
                entry = {"id": socket["id"], "type": datatype, "metadata": deepcopy(socket)}
                value = deepcopy(socket.get("value"))
                if value is not None and direction == "inputs":
                    if not _finite(value):
                        entry["metadata"]["value_encoding"] = "iris_nonfinite_float"
                        value = None
                    if datatype == "string" and not isinstance(value, str):
                        value = json.dumps(value, ensure_ascii=False)
                    if datatype == "integer" and value is not None:
                        value = int(value)
                    entry["value"] = value
                node[direction].append(entry)
        nodes.append(node)
        if source.get("image"):
            images.add(source["image"])
    if not nodes:
        # 非节点材质依然保存原始宿主记录，不把它伪装成已定义的 Principled。
        nodes = [
            {
                "id": "native_material",
                "type": "BlenderMaterial",
                "inputs": [],
                "outputs": [],
                "properties": {"diffuse_color": material.get("diffuse_color")},
            }
        ]
    return _json_safe(
        {
            "name": material["name"],
            "host": {"id": "blender", "version": inventory["blender_version"]},
            "nodes": nodes,
            "links": [
                {
                    "from": {"node": e["from"][0], "socket": e["from"][1]},
                    "to": {"node": e["to"][0], "socket": e["to"][1]},
                }
                for e in material.get("links", [])
            ],
            "metadata": {
                "material": deepcopy(material),
                "groups": groups,
                "images": [deepcopy(i) for i in inventory.get("images", []) if i["name"] in images],
                "color_context": inventory.get("color_context", {}),
            },
        }
    )
