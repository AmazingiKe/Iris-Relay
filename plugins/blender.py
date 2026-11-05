"""Blender Cycles 薄适配器：节点标识及端口绑定均来自核心 describe API。"""

from pathlib import Path

import bpy

from iris_relay.model import Material, ValidationError
from iris_relay.native import decode_value, describe, encode_value, identify


def renderer_ref():
    version = ".".join(map(str, bpy.app.version))
    return {
        "id": "cycles",
        "version": version,
        "host": "blender",
        "host_version": version,
        "node_system": "blender.shader",
    }


def find_socket(sockets, identifier):
    matches = [socket for socket in sockets if socket.identifier == identifier]
    if len(matches) != 1:
        raise ValidationError(f"原生端口不存在或不唯一：{identifier}")
    return matches[0]


def socket_value(socket):
    value = getattr(socket, "default_value", None)
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return list(value)


class BlenderAdapter:
    def __init__(self, material, service):
        self.material = material
        self.service = service

    def read_material(self):
        material = self.material
        if material is None or not material.use_nodes:
            raise ValidationError("请选择使用节点的 Blender 材质")
        schema = describe(self.service, renderer_ref())
        tree = material.node_tree
        outputs = [
            node
            for node in tree.nodes
            if node.bl_idname == "ShaderNodeOutputMaterial" and node.is_active_output
        ]
        if len(outputs) != 1:
            raise ValidationError("需要唯一的活动材质输出")
        output = outputs[0]
        surface_links = list(output.inputs["Surface"].links)
        if (
            len(surface_links) != 1
            or output.inputs["Volume"].is_linked
            or output.inputs["Displacement"].is_linked
        ):
            raise ValidationError("首版适配器需要单一表面输出，暂不转换体积或位移输出")
        result = {
            "schema_version": "1",
            "name": material.name,
            "renderer": renderer_ref(),
            "nodes": [],
            "links": [],
            "textures": {},
            "output": {
                "node": surface_links[0].from_node.name,
                "socket": surface_links[0].from_socket.identifier,
            },
            "metadata": {},
        }
        for node in tree.nodes:
            if node.bl_idname == "ShaderNodeOutputMaterial":
                continue
            if node.mute:
                raise ValidationError("暂不转换静音节点，必须显式解析旁路语义")
            property_names = {
                prop
                for rule in schema["nodes"].values()
                if rule["native"]["id"] == node.bl_idname
                for prop in rule["native"].get("properties", {})
            }
            match = identify(
                self.service,
                renderer_ref(),
                node.bl_idname,
                {prop: getattr(node, prop) for prop in property_names},
            )
            implementation = match["implementation"]
            rule = schema["nodes"][implementation]
            record = {
                "id": node.name,
                "type": implementation,
                "inputs": {},
                "outputs": list(rule["outputs"]),
                "metadata": {},
            }
            for port, port_rule in rule["inputs"].items():
                binding = port_rule["native"]
                if binding["kind"] == "resource":
                    image = node.image
                    if image is None:
                        value = None
                    else:
                        if image.packed_file or image.source != "FILE":
                            raise ValidationError(
                                "首版图片适配只接受已存在的外部单张图像；"
                                "不静默替代内嵌、序列或 UDIM"
                            )
                        path = Path(bpy.path.abspath(image.filepath)).resolve()
                        if not path.is_file():
                            raise ValidationError(f"贴图文件不存在：{path}")
                        resource_id = "image_" + str(len(result["textures"]))
                        space = image.colorspace_settings.name
                        result["textures"][resource_id] = {
                            "uri": str(path),
                            "color_space": "Raw" if space == "Non-Color" else space,
                            "coordinates": {"kind": "uv", "set": ""},
                            "metadata": {},
                        }
                        value = {"texture": resource_id}
                else:
                    value = decode_value(
                        socket_value(find_socket(node.inputs, binding["id"])), port_rule
                    )
                record["inputs"][port] = value
            result["nodes"].append(record)
        for link in tree.links:
            if link.to_node.bl_idname == "ShaderNodeOutputMaterial":
                continue
            result["links"].append(
                {
                    "from": {"node": link.from_node.name, "socket": link.from_socket.identifier},
                    "to": {"node": link.to_node.name, "socket": link.to_socket.identifier},
                }
            )
        return Material.parse(result).to_dict()

    def create_material(self, data):
        data = Material.parse(data).to_dict()
        if data["renderer"] != renderer_ref():
            raise ValidationError("目标宿主或 Blender/Cycles 版本不匹配")
        schema = describe(self.service, data["renderer"])
        material = bpy.data.materials.new(data["name"] + "_IrisRelay")
        created_images = []
        try:
            material.use_nodes = True
            tree = material.node_tree
            tree.nodes.clear()
            nodes = {}
            for record in data["nodes"]:
                rule = schema["nodes"][record["type"]]
                node = tree.nodes.new(rule["native"]["id"])
                node.name = record["id"]
                nodes[record["id"]] = node
                for prop, value in rule["native"].get("properties", {}).items():
                    setattr(node, prop, value)
                for port, value in record["inputs"].items():
                    port_rule = rule["inputs"][port]
                    binding = port_rule["native"]
                    if binding["kind"] == "resource":
                        if value is None:
                            continue
                        resource = data["textures"][value["texture"]]
                        path = Path(resource["uri"])
                        if not path.is_file():
                            raise ValidationError(f"贴图文件不存在：{path}")
                        # 不复用已有 Image，避免修改源材质使用的色彩空间。
                        image = bpy.data.images.load(str(path), check_existing=False)
                        created_images.append(image)
                        image.colorspace_settings.name = (
                            "Non-Color"
                            if resource["color_space"] == "Raw"
                            else resource["color_space"]
                        )
                        node.image = image
                        if resource["coordinates"] != {"kind": "uv", "set": ""}:
                            raise ValidationError("首版仅支持默认 UV 集，不静默忽略坐标变换")
                    elif value is not None:
                        find_socket(node.inputs, binding["id"]).default_value = encode_value(
                            value, port_rule
                        )
            for link in data["links"]:
                source, target = link["from"], link["to"]
                tree.links.new(
                    find_socket(nodes[source["node"]].outputs, source["socket"]),
                    find_socket(nodes[target["node"]].inputs, target["socket"]),
                )
            output = tree.nodes.new("ShaderNodeOutputMaterial")
            endpoint = data["output"]
            tree.links.new(
                find_socket(nodes[endpoint["node"]].outputs, endpoint["socket"]),
                output.inputs["Surface"],
            )
            return material
        except Exception:
            bpy.data.materials.remove(material)
            for image in created_images:
                if image.users == 0:
                    bpy.data.images.remove(image)
            raise
