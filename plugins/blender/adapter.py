"""Blender 材质读写。

读取入口：BlenderAdapter.read_material。
顺序：保存源快照 → 检查输出范围 → 追踪表面图 → 读取节点与贴图 → 校验协议。
本模块只处理宿主数据，节点转换规则通过公开 describe / identify API 获取。
"""

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import bpy

from iris_relay.model import Material, ValidationError
from iris_relay.native import decode_value, describe, encode_value, identify
from plugins.resources import image_header_channels

from .coordinates import read_uv_coordinates
from .groups import ExpandedGraph


def read_property(node, binding):
    """读取原生复合属性为协议数据；这里只保存配置，不实现渐变计算。"""
    output = binding.get("properties", {}).get("output_socket")
    if output:
        value = socket_value(find_socket(node.outputs, output))
        return decode_value(value, {"native": binding})
    value = getattr(node, binding["id"])
    if binding.get("codec") == "color_ramp":
        return json.dumps(
            {
                "color_mode": value.color_mode,
                "hue_interpolation": value.hue_interpolation,
                "interpolation": value.interpolation,
                "elements": [
                    {"position": e.position, "color": list(e.color)} for e in value.elements
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    return value


def write_property(node, binding, value):
    """按核心已验证的配置创建原生复合属性；不从快照猜测节点功能。"""
    output = binding.get("properties", {}).get("output_socket")
    if output:
        find_socket(node.outputs, output).default_value = encode_value(value, {"native": binding})
        return
    if binding.get("codec") != "color_ramp":
        setattr(node, binding["id"], value)
        return
    data = json.loads(value)
    ramp = getattr(node, binding["id"])
    ramp.color_mode = data["color_mode"]
    ramp.hue_interpolation = data["hue_interpolation"]
    ramp.interpolation = data["interpolation"]
    while len(ramp.elements) > 2:
        ramp.elements.remove(ramp.elements[-1])
    for index, record in enumerate(data["elements"]):
        element = ramp.elements[index] if index < 2 else ramp.elements.new(record["position"])
        element.position = record["position"]
        element.color = record["color"]


def renderer_ref():
    """记录当前 Blender/Cycles 环境；共享字典按宿主、渲染器和节点系统选择。"""
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


def validate_node_interface(node, rule):
    """检查当前模式下的实际插口类型及属性存在性，避免跨版本时隐式误接端口。"""
    types = {
        "float": {"VALUE", "INT"},
        "color": {"RGBA", "VECTOR"},
        "color4": {"RGBA"},
        "vector": {"VECTOR"},
        "shader": {"SHADER"},
        "bool": {"BOOLEAN"},
        "string": {"STRING"},
    }
    for direction in ("inputs", "outputs"):
        for binding in rule[direction].values():
            native = binding["native"]
            output = native.get("properties", {}).get("output_socket")
            if output:
                socket = find_socket(node.outputs, output)
            elif native.get("kind", "socket") != "socket":
                if not hasattr(node, native["id"]):
                    raise ValidationError(
                        "原生属性不存在：" + native["id"], "NATIVE_INTERFACE_MISMATCH"
                    )
                continue
            else:
                socket = find_socket(getattr(node, direction), native["id"])
            if socket.type not in types.get(binding["type"], set()):
                raise ValidationError(
                    "原生插口类型不匹配：" + native["id"] + "（" + socket.type + "）",
                    "NATIVE_INTERFACE_MISMATCH",
                )


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
                "alpha_mode": image.alpha_mode,
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


def create_uv_coordinates(tree, image_node, coordinates):
    """在新材质中重建命名 UV 与 POINT Mapping；不可分解的矩阵明确失败。"""
    import math

    from mathutils import Matrix

    if (
        not isinstance(coordinates, dict)
        or set(coordinates) - {"kind", "set", "transforms"}
        or coordinates.get("kind") != "uv"
        or not isinstance(coordinates.get("set"), str)
    ):
        raise ValidationError("不支持该图片坐标描述", "UV_UNSUPPORTED")
    matrices = coordinates.get("transforms", [])
    if not isinstance(matrices, list) or len(matrices) > 64:
        raise ValidationError("坐标矩阵须为至多 64 项的列表", "UV_UNSUPPORTED")
    if not coordinates["set"] and not matrices:
        return
    uv = tree.nodes.new("ShaderNodeUVMap" if coordinates["set"] else "ShaderNodeTexCoord")
    if coordinates["set"]:
        uv.uv_map = coordinates["set"]
    output = uv.outputs["UV"]
    for values in matrices:
        if (
            not isinstance(values, list)
            or len(values) != 16
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in values)
            or any(values[i] != 0 for i in (3, 7, 11))
            or values[15] != 1
        ):
            raise ValidationError("坐标需要有限仿射 matrix44", "UV_UNSUPPORTED")
        matrix = Matrix([values[i : i + 4] for i in (0, 4, 8, 12)]).transposed()
        location, rotation, scale = matrix.decompose()
        euler = rotation.to_euler("XYZ")
        restored = Matrix.LocRotScale(location, euler, scale)
        if min(abs(v) for v in scale) < 1e-12 or any(
            not math.isclose(matrix[i][j], restored[i][j], rel_tol=1e-5, abs_tol=2e-5)
            for i in range(4)
            for j in range(4)
        ):
            raise ValidationError("POINT Mapping 不能还原剪切或退化矩阵", "UV_UNSUPPORTED")
        mapping = tree.nodes.new("ShaderNodeMapping")
        mapping.vector_type = "POINT"
        mapping.inputs["Location"].default_value = location
        mapping.inputs["Rotation"].default_value = euler
        mapping.inputs["Scale"].default_value = scale
        tree.links.new(output, mapping.inputs["Vector"])
        output = mapping.outputs["Vector"]
    tree.links.new(output, image_node.inputs["Vector"])


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
    uv_images: dict[str, dict]
    needs_color_cast: bool


@lru_cache(maxsize=512)
def image_file_channels(filepath, size, modified):
    """在临时 Image 中探测文件色彩通道，随即释放；缓存键包含文件大小和修改时间。

    PNG／EXR 优先读取文件头；Image.channels 和 depth 都可能反映缓存展开格式。
    其他已接受格式使用临时 Image 探测解码色彩模式。
    PNG 灰度加 Alpha 的两通道文件尚未实现标准采样对应，明确拒绝。
    """
    probed = image_header_channels(filepath)
    if probed is not None:
        return probed
    with Path(filepath).open("rb") as stream:
        header = stream.read(26)
    if header.startswith(b"\x89PNG\r\n\x1a\n") and len(header) == 26 and header[25] == 4:
        raise ValidationError("暂不转换灰度加 Alpha 的双通道 PNG", "MATERIALX_RESOURCE_UNSUPPORTED")
    temporary = bpy.data.images.load(filepath, check_existing=False)
    try:
        depth = temporary.depth
        channels = depth // (32 if temporary.is_float else 8)
        if temporary.type != "IMAGE" or channels not in {1, 3, 4}:
            raise ValidationError("图片解码通道或类型尚未核验", "MATERIALX_RESOURCE_UNSUPPORTED")
        return channels
    finally:
        bpy.data.images.remove(temporary)


class BlenderAdapter:
    """宿主接入入口：读取源材质，或根据核心返回数据创建新材质。"""

    def __init__(self, material, service, *, surface_only=False, uv_context=None):
        """保存读取对象、公开服务和输出范围；不立即访问或修改材质。"""
        self.material = material
        self.service = service
        self.surface_only = surface_only
        self.read_issues = []
        self.source_snapshot = None
        self.uv_context = uv_context

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

        # 2. 向核心查询当前接入身份的共享节点及端口绑定。
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
        """从表面出口向上游遍历，解析转接点与 UV 链，报告未使用节点。"""
        # 1. 以真实表面节点为起点；structural 记录已解析的转接点/坐标节点。
        expanded = ExpandedGraph(tree)
        output = expanded.node(output)
        structural, active, uv_images = set(), {}, {}
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
                    coordinates = read_uv_coordinates(socket, structural)
                    uv_images[node.name] = coordinates
                    default = coordinates == {"kind": "uv", "set": ""}
                    self.note(
                        "DEFAULT_UV_RESOLVED" if default else "UV_COORDINATES_PRESERVED",
                        "默认 UV 和恒等坐标表达式已解析"
                        if default
                        else "UV 名称及静态表达式的各步矩阵保留于图片坐标；导入重建可编辑坐标链",
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

        # 3. 矩阵参数不能冻结同时驱动材质属性的计算节点，否则回导会丢失共享依赖。
        shared = sorted(set(active) & structural)
        if shared:
            raise ValidationError(
                "坐标表达式同时驱动其他材质分支，尚不能拆开其共享连接：" + "、".join(shared),
                "UV_SHARED_EXPRESSION_UNSUPPORTED",
            )
        # 4. 未参与所选表面的节点只保留在源快照，明确列入诊断。
        unused = [
            n.name
            for n in tree.nodes
            if n.name not in active
            and n.name not in structural | expanded.root_groups | expanded.root_reroutes
            and n.bl_idname not in {"NodeFrame", "ShaderNodeOutputMaterial"}
        ]
        if unused:
            self.note("UNUSED_NODES", "未参与所选表面的节点仅保留于源快照：" + "、".join(unused))
        if expanded.scopes:
            self.note(
                "NODE_GROUPS_EXPANDED",
                f"已按实例展开 {len(expanded.scopes)} 个节点组，保留输入值与连线；"
                "导入创建可编辑功能节点，组层级和布局不随标准图重建",
            )
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
        if isinstance(material.get("irisDeferredDetails"), str):
            result["metadata"]["deferred_details"] = json.loads(material["irisDeferredDetails"])
        failures = []
        for node in graph.nodes.values():
            try:
                result["nodes"].append(
                    self._read_node(
                        node, schema, result["textures"], graph.uv_images.get(node.name)
                    )
                )
            except ValidationError as exc:
                failures.append(exc)
                self.note(exc.code, str(exc), node.name, severity="error")
        if failures:
            raise ValidationError(
                f"当前活动图有 {len(failures)} 个节点读取问题；" + str(failures[0]),
                failures[0].code,
            )
        records = {record["id"]: record for record in result["nodes"]}

        def mapped(endpoint, direction):
            """协议端口键可以不同于宿主插口 ID，连接必须使用公开绑定映射。"""
            rule = schema["nodes"][records[endpoint["node"]]["type"]]
            matches = [
                key
                for key, value in rule[direction].items()
                if value["native"]["id"] == endpoint["socket"]
            ]
            if len(matches) != 1:
                raise ValidationError(
                    "字典未覆盖原生连接端口：" + endpoint["socket"], "UNSUPPORTED_SOCKET"
                )
            return {"node": endpoint["node"], "socket": matches[0]}

        result["links"] = [
            {"from": mapped(edge["from"], "outputs"), "to": mapped(edge["to"], "inputs")}
            for edge in result["links"]
        ]
        result["output"] = mapped(result["output"], "outputs")
        identities = {}
        for name, node in graph.nodes.items():
            saved = node.native.get("irisNodeId") if not node.scope.path else None
            identities[name] = saved if isinstance(saved, str) and saved else name
        if len(set(identities.values())) != len(identities):
            self.note(
                "NODE_ID_HINT_COLLISION",
                "重复的节点身份提示已停用，使用当前原生名称区分节点",
                severity="warning",
            )
            identities = {name: name for name in graph.nodes}
        for record in result["nodes"]:
            if identities[record["id"]] != record["id"]:
                record["metadata"]["native_name"] = record["id"]
                record["id"] = identities[record["id"]]
        for edge in result["links"]:
            for endpoint in edge.values():
                endpoint["node"] = identities[endpoint["node"]]
        result["output"]["node"] = identities[result["output"]["node"]]
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
                f"节点「{node.diagnostic_name}」({node.bl_idname})：{exc}", exc.code
            ) from exc
        implementation = match["implementation"]
        return implementation, schema["nodes"][implementation]

    def _read_node(self, node, schema, textures, resolved_uv):
        """读取一个节点：先识别配置，再依照绑定读取图片资源或端口值。"""
        implementation, rule = self._identify_node(node, schema)
        validate_node_interface(node, rule)
        record = {
            "id": node.name,
            "type": implementation,
            "inputs": {},
            "outputs": list(rule["outputs"]),
            "metadata": {},
        }
        raw_editor = node.native.get("irisEditor")
        editor = json.loads(raw_editor) if isinstance(raw_editor, str) else {}
        if "irisSourceName" in node.native:
            record["metadata"]["source_name"] = node.native["irisSourceName"]
        editor["label"] = node.label or node.diagnostic_name
        position = list(node.location)
        parent = node.parent
        frames = []
        while parent is not None:
            position = [position[i] + parent.location[i] for i in range(2)]
            frames.insert(0, parent.label or parent.name)
            parent = parent.parent
        if frames or "group_path" in editor:
            editor["group_path"] = frames
        editor.update(source_position=position, generated=False)
        record["metadata"]["editor"] = editor
        if node.source_group_path:
            record["metadata"]["source_group_path"] = node.source_group_path
        if node.bl_idname == "ShaderNodeNormalMap" and node.uv_map:
            owners = self.uv_context
            if owners is None:
                owners = []
                for obj in bpy.data.objects:
                    if not any(slot.material == self.material for slot in obj.material_slots):
                        continue
                    layers = getattr(obj.data, "uv_layers", [])
                    owners.append(
                        {
                            "mesh": obj.data.name_full if obj.data else obj.name_full,
                            "render_default": next(
                                (layer.name for layer in layers if layer.active_render), None
                            ),
                            "uv_sets": [layer.name for layer in layers],
                        }
                    )
            record["metadata"]["uv_binding"] = {"name": node.uv_map, "owners": owners}
        for port, port_rule in rule["inputs"].items():
            binding = port_rule["native"]
            if binding["kind"] == "resource":
                value = self._read_image_resource(node.image, textures, resolved_uv)
            elif binding["kind"] == "property":
                value = read_property(node, binding)
            else:
                value = decode_value(
                    socket_value(find_socket(node.inputs, binding["id"])), port_rule
                )
                if resolved_uv is not None and binding["id"] == "Vector":
                    # 坐标链已保存于资源，不把被连接覆盖的插口默认值当作额外变换。
                    value = port_rule["default"]
            record["inputs"][port] = value
        return record

    def _read_image_resource(self, image, textures, coordinates=None):
        """验证外部图片，将路径与色彩解释加入资源表，返回纹理引用。"""
        if image is None:
            return None
        if image.packed_file or image.source != "FILE":
            raise ValidationError(
                "首版图片适配只接受已存在的外部单张图像；不静默替代内嵌、序列或 UDIM"
            )
        if image.is_dirty:
            raise ValidationError(
                "图片有未保存的像素修改，不能用磁盘文件替代", "MATERIALX_RESOURCE_UNSUPPORTED"
            )
        path = Path(bpy.path.abspath(image.filepath, library=image.library)).resolve()
        if not path.is_file():
            raise ValidationError(f"贴图文件不存在：{path}")
        resource_id = "image_" + str(len(textures))
        space = image.colorspace_settings.name
        stat = path.stat()
        description = {
            "mode": image.alpha_mode,
            "channels": image_file_channels(str(path), stat.st_size, stat.st_mtime_ns),
        }
        textures[resource_id] = {
            "uri": str(path),
            "color_space": "Raw" if space == "Non-Color" else space,
            "coordinates": coordinates or {"kind": "uv", "set": ""},
            "metadata": {"image_alpha": description},
        }
        return {"texture": resource_id}

    def create_material(self, data):
        """根据核心返回数据新建材质；失败时清理本次创建的材质和无用户图片。"""
        # 1. 校验接入身份，按实际运行环境查询规则；来源版本不同不阻断共享绑定。
        data = Material.parse(data).to_dict()
        current = renderer_ref()
        if any(data["renderer"].get(key) != current[key] for key in ("id", "host", "node_system")):
            raise ValidationError("目标宿主、渲染器或节点系统不匹配")
        schema = describe(self.service, current)
        material = bpy.data.materials.new(data["name"] + "_IrisRelay")
        created_images = []
        try:
            # 2. 创建目标节点和输入值；图片单独加载，避免更改源图片的色彩空间。
            archive = data.get("metadata", {}).get("deferred_details")
            if archive is not None:
                material["irisDeferredDetails"] = json.dumps(archive, ensure_ascii=True)
            material.use_nodes = True
            tree = material.node_tree
            tree.nodes.clear()
            nodes, image_coordinates = {}, []
            for record in data["nodes"]:
                rule = schema["nodes"][record["type"]]
                node = tree.nodes.new(rule["native"]["id"])
                node.name = record["id"]
                node["irisSourceName"] = record.get("metadata", {}).get("source_name", record["id"])
                if node.name != record["id"]:
                    node["irisNodeId"] = record["id"]
                editor = record.get("metadata", {}).get("editor", {})
                if editor:
                    node.label = editor.get("label", record["id"])
                    node["irisEditor"] = json.dumps(editor, ensure_ascii=False)
                    if "position" in editor:
                        node.location = editor["position"]
                nodes[record["id"]] = node
                for prop, value in rule["native"].get("properties", {}).items():
                    setattr(node, prop, value)
                validate_node_interface(node, rule)
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
                        description = resource.get("metadata", {}).get("image_alpha")
                        if description is not None:
                            stat = path.stat()
                            if (
                                image_file_channels(str(path), stat.st_size, stat.st_mtime_ns)
                                != description["channels"]
                            ):
                                raise ValidationError(
                                    "目标图片通道与 MaterialX 描述不符",
                                    "MATERIALX_RESOURCE_UNSUPPORTED",
                                )
                            image.alpha_mode = description["mode"]
                        image.colorspace_settings.name = (
                            "Non-Color"
                            if resource["color_space"] == "Raw"
                            else resource["color_space"]
                        )
                        node.image = image
                        image_coordinates.append((node, resource["coordinates"]))
                    elif binding["kind"] == "property":
                        write_property(node, binding, value)
                    elif value is not None:
                        find_socket(node.inputs, binding["id"]).default_value = encode_value(
                            value, port_rule
                        )
            # 3. 原生功能节点名称确定后再建坐标节点，避免辅助节点抢占协议 ID。
            for node, coordinates in image_coordinates:
                create_uv_coordinates(tree, node, coordinates)
            for index, group in enumerate(
                data.get("metadata", {}).get("editor", {}).get("groups", [])
            ):
                frame = tree.nodes.new("NodeFrame")
                frame.name = "Iris_Function_" + str(index)
                frame.label = group["label"]
                for identifier in group["nodes"]:
                    node = nodes[identifier]
                    position = node.location.copy()
                    node.parent = frame
                    node.location = position
            # 4. 恢复节点连接，并将核心指定的表面出口接到新材质输出。
            records = {record["id"]: record for record in data["nodes"]}
            for link in data["links"]:
                source, target = link["from"], link["to"]
                source_record = records[source["node"]]
                target_record = records[target["node"]]
                source_binding = schema["nodes"][source_record["type"]]["outputs"][
                    source["socket"]
                ]["native"]
                target_binding = schema["nodes"][target_record["type"]]["inputs"][target["socket"]][
                    "native"
                ]
                tree.links.new(
                    find_socket(nodes[source["node"]].outputs, source_binding["id"]),
                    find_socket(nodes[target["node"]].inputs, target_binding["id"]),
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
            # 5. 任意一步失败只清理本次新增数据，保留源材质与源图片。
            bpy.data.materials.remove(material)
            for image in created_images:
                if image.users == 0:
                    bpy.data.images.remove(image)
            raise
