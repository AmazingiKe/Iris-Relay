"""Blender 材质读写。

读取入口：BlenderAdapter.read_material。
顺序：保存源快照 → 检查输出范围 → 追踪表面图 → 读取节点与贴图 → 校验协议。
本模块只处理宿主数据，节点转换规则通过公开 describe / identify API 获取。
"""

from dataclasses import dataclass
from pathlib import Path

import bpy

from iris_relay.model import Material, ValidationError
from iris_relay.native import decode_value, describe, encode_value, identify


def renderer_ref():
    """取得当前 Blender/Cycles 版本，供核心选择精确匹配的字典。"""
    version = ".".join(map(str, bpy.app.version))
    return {
        "id": "cycles",
        "version": version,
        "host": "blender",
        "host_version": version,
        "node_system": "blender.shader",
    }


def find_socket(sockets, identifier):
    """按稳定的端口 identifier 查找；不存在或重复时拒绝猜测。"""
    matches = [socket for socket in sockets if socket.identifier == identifier]
    if len(matches) != 1:
        raise ValidationError(f"原生端口不存在或不唯一：{identifier}")
    return matches[0]


def socket_value(socket):
    """把 Blender 端口默认值读成可写入 JSON 的标量或列表。"""
    value = getattr(socket, "default_value", None)
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return list(value)


def source_snapshot(material):
    """保存顶层宿主节点信息；即使读取被阻断，也能定位被排除的原始链路。"""
    # 1. 记录所有顶层节点，包含稍后可能排除或无法识别的节点。
    nodes = []
    for node in material.node_tree.nodes:
        properties = {}
        for prop in node.bl_rna.properties:
            if not prop.is_readonly and prop.type in {"BOOLEAN", "INT", "FLOAT", "STRING", "ENUM"}:
                value = getattr(node, prop.identifier)
                if isinstance(value, set):
                    value = sorted(value)
                elif getattr(prop, "is_array", False):
                    value = list(value)
                properties[prop.identifier] = value
        record = {
            "name": node.name,
            "type": node.bl_idname,
            "properties": properties,
            "inputs": {p.identifier: socket_value(p) for p in node.inputs},
            "outputs": [p.identifier for p in node.outputs],
        }
        image = getattr(node, "image", None)
        if image:
            record["image"] = {
                "name": image.name,
                "filepath": image.filepath,
                "resolved_path": bpy.path.abspath(image.filepath, library=image.library),
                "color_space": image.colorspace_settings.name,
                "source": image.source,
                "packed": bool(image.packed_file),
            }
        nodes.append(record)
    # 2. 单独保存原始连接；快照不跟随表面图的简化而改变。
    return {
        "name": material.name,
        "renderer": renderer_ref(),
        "nodes": nodes,
        "links": [
            {
                "from": {"node": link.from_node.name, "socket": link.from_socket.identifier},
                "to": {"node": link.to_node.name, "socket": link.to_socket.identifier},
            }
            for link in material.node_tree.links
        ],
    }


def linked_source(socket, structural):
    """沿宿主转接点查找实际输出，不创建或修改节点。"""
    seen = set()
    while True:
        links = list(socket.links)
        if len(links) != 1 or not links[0].is_valid:
            raise ValidationError("连接无效或转接点缺少唯一输入", "INVALID_LINK")
        source = links[0].from_socket
        node = source.node
        if node.mute:
            raise ValidationError(f"暂不转换静音节点：{node.name}", "MUTED_NODE")
        if node.bl_idname != "NodeReroute":
            return source
        if node.name in seen:
            raise ValidationError("转接点包含循环", "INVALID_LINK")
        seen.add(node.name)
        structural.add(node.name)
        socket = node.inputs[0]


def default_uv(socket, structural):
    """仅接受默认 UV 及 POINT 恒等 Mapping，不把其他坐标近似成默认值。"""
    seen = set()
    while True:
        source = linked_source(socket, structural)
        node = source.node
        if node.name in seen:
            raise ValidationError("坐标链包含循环", "INVALID_LINK")
        seen.add(node.name)
        structural.add(node.name)
        # 到达默认 UV 即完成；实例 UV 的含义不同，继续阻断。
        if node.bl_idname == "ShaderNodeTexCoord" and source.identifier == "UV":
            if node.from_instancer:
                raise ValidationError("尚不支持实例 UV", "UV_UNSUPPORTED")
            return
        # 只穿过没有任何变换、且变换参数未被连接驱动的 Mapping。
        if node.bl_idname == "ShaderNodeMapping" and node.vector_type == "POINT":
            expected = {"Location": [0, 0, 0], "Rotation": [0, 0, 0], "Scale": [1, 1, 1]}
            if all(
                not node.inputs[p].is_linked and socket_value(node.inputs[p]) == v
                for p, v in expected.items()
            ):
                socket = node.inputs["Vector"]
                continue
        raise ValidationError(f"坐标链暂不支持节点或变换：{node.name}", "UV_UNSUPPORTED")


def color_to_float_context():
    """读取 Cycles 当前工作空间的亮度系数；依据 5.2.3 的 XYZ 初始化过程。"""
    import PyOpenColorIO as ocio
    from mathutils import Matrix

    # 1. 确认宿主当前工作空间属于已核验范围，不修改其色彩设置。
    config = ocio.GetCurrentConfig()
    colorspace = bpy.data.colorspace
    if (
        colorspace.is_missing_opencolorio_config
        or colorspace.working_space_interop_id != "lin_rec709_scene"
        or not config.hasRole("aces_interchange")
    ):
        raise ValidationError(
            "颜色转标量目前只核验了 Linear Rec.709 工作空间", "MATERIALX_COLORSPACE_UNSUPPORTED"
        )
    # 2. 只接受线性矩阵，防止将非线性色彩处理误读成固定系数。
    processor = config.getProcessor("aces_interchange", colorspace.working_space)
    transforms = processor.createGroupTransform()
    if any(
        not isinstance(transform, ocio.MatrixTransform)
        or any(transform.getOffset())
        or any(transform.getMatrix()[i] != 0 for i in (3, 7, 11))
        for transform in transforms
    ):
        raise ValidationError(
            "工作空间变换不是已核验的线性矩阵", "MATERIALX_COLORSPACE_UNSUPPORTED"
        )
    processor = processor.getDefaultCPUProcessor()
    # 3. 按 Cycles 的 AP0 → XYZ D65 过程取得 RGB → Y 系数，交给核心建图。
    basis = [processor.applyRGB(v) for v in ([1, 0, 0], [0, 1, 0], [0, 0, 1])]
    matrix = Matrix(basis).transposed()
    ap0_to_xyz = Matrix(
        (
            (0.938280, -0.004451, 0.016628),
            (0.337369, 0.729522, -0.066890),
            (0.001174, -0.003711, 1.091595),
        )
    )
    xyz_to_rgb = matrix @ ap0_to_xyz.inverted()
    coefficients = list(xyz_to_rgb.inverted()[1])
    return {"working_space": colorspace.working_space_interop_id, "coefficients": coefficients}


@dataclass
class _SurfaceGraph:
    """读取中间结果：有效表面出口、可达节点、连接及已解析的 UV 信息。"""

    surface: bpy.types.NodeSocket
    nodes: dict
    links: list
    uv_images: set[str]
    needs_color_cast: bool


class BlenderAdapter:
    """宿主接入入口：读取源材质，或根据核心返回数据创建新材质。"""

    def __init__(self, material, service, *, surface_only=False):
        """保存读取对象、公开服务和输出范围；不立即访问或修改材质。"""
        self.material = material
        self.service = service
        self.surface_only = surface_only
        self.read_issues = []
        self.source_snapshot = None

    def note(self, code, message, node=None, port=None, severity="info"):
        """收集宿主侧诊断，稍后由 controller 合并进完整报告。"""
        self.read_issues.append(
            dict(code=code, message=message, node=node, port=port, severity=severity)
        )

    def read_material(self):
        """读取当前材质并返回原生材质协议；失败时保留已有快照和诊断。"""
        # 1. 重置本次读取状态，并在任何筛选之前保存原始顶层图。
        self.read_issues = []
        self.source_snapshot = None
        material = self.material
        if material is None or not material.use_nodes:
            raise ValidationError("请选择使用节点的 Blender 材质")
        self.source_snapshot = source_snapshot(material)

        # 2. 向核心查询此 Blender 版本的节点及端口绑定。
        schema = describe(self.service, renderer_ref())
        tree = material.node_tree

        # 3. 检查表面、体积和位移范围，再追踪真正参与表面的上游图。
        output = self._select_surface_output(tree)
        graph = self._collect_surface_graph(tree, output)

        # 4. 读取节点参数、贴图资源及必要的宿主色彩上下文。
        result = self._read_native_graph(material, schema, graph)

        # 5. 校验节点、连接及资源引用，只返回合法的原生材质协议。
        return Material.parse(result).to_dict()

    def _select_surface_output(self, tree):
        """返回唯一活动输出；拒绝未支持的输出，或按明确选项报告排除位移。"""
        # 先确定唯一出口，再检查出口上每个会影响材质含义的分支。
        outputs = [
            node
            for node in tree.nodes
            if node.bl_idname == "ShaderNodeOutputMaterial" and node.is_active_output
        ]
        if len(outputs) != 1:
            raise ValidationError("需要唯一的活动材质输出")
        output = outputs[0]
        thickness = output.inputs.get("Thickness")
        if thickness and (thickness.is_linked or socket_value(thickness) != 0):
            raise ValidationError("暂不转换材质输出的 Thickness", "OUTPUT_UNSUPPORTED")
        surface_links = list(output.inputs["Surface"].links)
        if len(surface_links) != 1 or output.inputs["Volume"].is_linked:
            raise ValidationError("需要单一表面输出，暂不转换体积输出")
        if output.inputs["Displacement"].is_linked or socket_value(
            output.inputs["Displacement"]
        ) != [0, 0, 0]:
            if not self.surface_only:
                raise ValidationError(
                    "材质含位移输出；当前仅支持表面，可明确选择“仅导出表面”后重试",
                    "DISPLACEMENT_UNSUPPORTED",
                )
            self.note(
                "DISPLACEMENT_EXCLUDED",
                "按所选范围排除位移输出；位移会影响外观，原链路保留于源快照",
                output.name,
                "Displacement",
                "warning",
            )
        return output

    def _collect_surface_graph(self, tree, output):
        """从表面出口向上游遍历，解析转接点与默认 UV，报告未使用节点。"""
        # 1. 以真实表面节点为起点；structural 记录已解析的转接点/坐标节点。
        structural, active, uv_images = set(), {}, set()
        surface = linked_source(output.inputs["Surface"], structural)
        pending = [surface.node]
        links = []
        needs_color_cast = False

        # 2. 收集可达节点和实际连接；坐标链单独校验，保留标量转换需求。
        while pending:
            node = pending.pop()
            if node.name in active:
                continue
            active[node.name] = node
            for socket in node.inputs:
                if not socket.is_linked:
                    continue
                if node.bl_idname == "ShaderNodeTexImage" and socket.identifier == "Vector":
                    default_uv(socket, structural)
                    uv_images.add(node.name)
                    self.note(
                        "DEFAULT_UV_RESOLVED",
                        "默认 UV、恒等 Mapping 与转接点已解析",
                        node.name,
                        "Vector",
                    )
                    continue
                upstream = linked_source(socket, structural)
                needs_color_cast |= upstream.type == "RGBA" and socket.type == "VALUE"
                pending.append(upstream.node)
                links.append(
                    {
                        "from": {"node": upstream.node.name, "socket": upstream.identifier},
                        "to": {"node": node.name, "socket": socket.identifier},
                    }
                )

        # 3. 未参与所选表面的节点只保留在源快照，明确列入诊断。
        unused = [
            n.name
            for n in tree.nodes
            if n.name not in active
            and n.name not in structural
            and n.bl_idname not in {"NodeFrame", "ShaderNodeOutputMaterial"}
        ]
        if unused:
            self.note("UNUSED_NODES", "未参与所选表面的节点仅保留于源快照：" + "、".join(unused))
        return _SurfaceGraph(surface, active, links, uv_images, needs_color_cast)

    def _read_native_graph(self, material, schema, graph):
        """将宿主图装入原生协议；节点内容交由 _read_node 逐个读取。"""
        result = {
            "schema_version": "1",
            "name": material.name,
            "renderer": renderer_ref(),
            "nodes": [],
            "links": graph.links,
            "textures": {},
            "output": {
                "node": graph.surface.node.name,
                "socket": graph.surface.identifier,
            },
            "metadata": {},
        }
        if graph.needs_color_cast:
            result["metadata"]["color_to_float"] = color_to_float_context()
        for node in graph.nodes.values():
            result["nodes"].append(
                self._read_node(node, schema, result["textures"], node.name in graph.uv_images)
            )
        return result

    def _identify_node(self, node, schema):
        """读取字典要求的原生属性，让核心 identify 决定具体实现配置。"""
        if node.mute:
            raise ValidationError("暂不转换静音节点，必须显式解析旁路语义")
        property_names = {
            prop
            for rule in schema["nodes"].values()
            if rule["native"]["id"] == node.bl_idname
            for prop in rule["native"].get("properties", {})
        }
        try:
            match = identify(
                self.service,
                renderer_ref(),
                node.bl_idname,
                {prop: getattr(node, prop) for prop in property_names},
            )
        except ValidationError as exc:
            raise ValidationError(
                f"节点「{node.name}」({node.bl_idname})：{exc}", exc.code
            ) from exc
        implementation = match["implementation"]
        return implementation, schema["nodes"][implementation]

    def _read_node(self, node, schema, textures, resolved_uv):
        """读取一个节点：先识别配置，再依照绑定读取图片资源或端口值。"""
        implementation, rule = self._identify_node(node, schema)
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
                value = self._read_image_resource(node.image, textures)
            else:
                value = decode_value(
                    socket_value(find_socket(node.inputs, binding["id"])), port_rule
                )
                if resolved_uv and binding["id"] == "Vector":
                    # 坐标链已确认等价于默认 UV，不把被连接覆盖的插口默认值当作变换。
                    value = port_rule["default"]
            record["inputs"][port] = value
        return record

    def _read_image_resource(self, image, textures):
        """验证外部图片，将路径与色彩解释加入资源表，返回纹理引用。"""
        if image is None:
            return None
        if image.packed_file or image.source != "FILE":
            raise ValidationError(
                "首版图片适配只接受已存在的外部单张图像；不静默替代内嵌、序列或 UDIM"
            )
        path = Path(bpy.path.abspath(image.filepath, library=image.library)).resolve()
        if not path.is_file():
            raise ValidationError(f"贴图文件不存在：{path}")
        resource_id = "image_" + str(len(textures))
        space = image.colorspace_settings.name
        textures[resource_id] = {
            "uri": str(path),
            "color_space": "Raw" if space == "Non-Color" else space,
            "coordinates": {"kind": "uv", "set": ""},
            "metadata": {},
        }
        return {"texture": resource_id}

    def create_material(self, data):
        """根据核心返回数据新建材质；失败时清理本次创建的材质和无用户图片。"""
        # 1. 校验原生协议与宿主版本，再查询目标节点绑定。
        data = Material.parse(data).to_dict()
        if data["renderer"] != renderer_ref():
            raise ValidationError("目标宿主或 Blender/Cycles 版本不匹配")
        schema = describe(self.service, data["renderer"])
        material = bpy.data.materials.new(data["name"] + "_IrisRelay")
        created_images = []
        try:
            # 2. 创建目标节点和输入值；图片单独加载，避免更改源图片的色彩空间。
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
            # 3. 恢复节点连接，并将核心指定的表面出口接到新材质输出。
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
            # 新建材质默认不绑定对象，仍需保留以供 .blend 保存和后续手动选择。
            material.use_fake_user = True
            return material
        except Exception:
            # 4. 任意一步失败只清理本次新增数据，保留源材质与源图片。
            bpy.data.materials.remove(material)
            for image in created_images:
                if image.users == 0:
                    bpy.data.images.remove(image)
            raise
