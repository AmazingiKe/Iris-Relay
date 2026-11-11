"""Cinema 4D / Redshift 原生图创建；共享规则由公开核心字典提供。"""

import json
import math
from copy import deepcopy
from pathlib import Path

from iris_relay.native import decode_value, describe, encode_value, identify, resource_color_space
from iris_relay.protocol import ValidationError
from plugins.resources import image_format, image_header_channels

SPACE = "com.redshift3d.redshift4c4d.class.nodespace"
OUTPUT = "com.redshift3d.redshift4c4d.node.output"


def set_implementation_hints(material, values):
    """持久保存可校验的实现提示；不储存源文档，也不能替代实际节点和端口证据。"""
    import c4d

    description = c4d.GetCustomDataTypeDefault(c4d.DTYPE_STRING)
    description[c4d.DESC_NAME] = "Iris Relay 节点实现"
    identifier = material.AddUserData(description)
    material[identifier] = json.dumps(values, ensure_ascii=False)


def implementation_hints(material):
    """从当前材质读取提示；仍须交核心 identify 核对当前原生类型与配置。"""
    import c4d

    for identifier, description in material.GetUserDataContainer():
        if description[c4d.DESC_NAME] == "Iris Relay 节点实现":
            value = json.loads(material[identifier])
            if not isinstance(value, dict) or any(
                not isinstance(k, str) or not isinstance(v, str) for k, v in value.items()
            ):
                raise ValidationError("C4D 节点实现提示不是字符串映射", "NATIVE_HINT_MISMATCH")
            return value
    return {}


def renderer_ref():
    """查询实际宿主和渲染器版本，不把其他安装的目录当作支持证据。"""
    import c4d
    import redshift

    version = str(c4d.GetC4DVersion())
    return {
        "id": "redshift",
        "version": str(redshift.GetCoreVersion()),
        "host": "cinema4d",
        "host_version": f"{version[:4]}.{int(version[4:5])}.{int(version[5:])}",
        "node_system": "redshift.maxon",
    }


def json_value(value):
    """转换已核验的 Maxon 数据类型，未知类型明确报错，不用 repr 冒充数值。"""
    name = type(value).__name__
    if name == "Bool":
        literal = str(value).lower()
        if literal not in {"true", "false"}:
            raise ValidationError("未知 Maxon Bool 表示：" + literal)
        return literal == "true"
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if name.startswith(("Int", "UInt")):
        return int(value)
    if name.startswith("Float"):
        return float(value)
    if name == "Url":
        return str(value.GetSystemPath())
    if name in {"String", "Id"}:
        return str(value)
    if hasattr(value, "r"):
        result = [float(value.r), float(value.g), float(value.b)]
        return result + [float(value.a)] if hasattr(value, "a") else result
    if hasattr(value, "x") and hasattr(value, "y"):
        return [float(value.x), float(value.y)] + ([float(value.z)] if hasattr(value, "z") else [])
    raise ValidationError("未经核验的原生值类型：" + name, "C4D_VALUE_UNSUPPORTED")


def equal(a, b):
    """允许原生浮点精度，文本、布尔和列表长度保持严格相等。"""
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(equal(x, y) for x, y in zip(a, b, strict=True))
    if type(a) in (int, float) and type(b) in (int, float):
        return math.isclose(a, b, rel_tol=2e-6, abs_tol=2e-6)
    return a == b


def port(node, identifier, output=False):
    """按实机稳定 ID 定位端口，包括文件输入的子端口，不依赖显示名。"""
    result = node.GetOutputs() if output else node.GetInputs()
    for part in str(identifier).split("/"):
        result = result.FindChild(part)
    if result.IsNullValue() or not result.IsValid():
        raise ValidationError("原生端口不存在：" + str(identifier), "C4D_PORT_MISSING")
    return result


def child_nodes(parent):
    """仅枚举当前层计算节点，排除输入／输出集合。"""
    import maxon

    return [n for n in parent.GetChildren() if n.GetKind() == maxon.NODE_KIND.NODE]


def validate_node_interface(node, rule):
    """核对实际资产的全部声明端口；常量类型由写入与提交后的回读继续核验。"""
    for identifier in rule["native"].get("properties", {}):
        port(node, identifier)
    for direction in ("inputs", "outputs"):
        for binding in rule[direction].values():
            native = binding["native"]
            projection = native.get("properties", {}).get("output_projection")
            # 投影输出属于按需创建的辅助资产，当前节点只核对其真实来源端口。
            identifier = projection["source"] if projection else native["id"]
            port(node, identifier, direction == "outputs")
            for key in ("colorspace_id", "uv_id"):
                if key in native:
                    port(node, native[key])


def flattened_copy(document, material):
    """在私有副本展开原生组，显式保留当前接口值和节点身份；调用者负责释放文档。"""
    import c4d
    import maxon

    staging = c4d.documents.BaseDocument()
    staging.SetDocumentPath(document.GetDocumentPath())
    cloned = material.GetClone(c4d.COPYFLAGS_0)
    staging.InsertMaterial(cloned)
    try:
        graph = cloned.GetNodeMaterialReference().GetGraph(SPACE)
        hints = implementation_hints(material)
        origins = {}

        def collect(parent, labels):
            """保存实际层级与显示名，原生参数及连接仍由展开后的 SDK 图读取。"""
            for node in child_nodes(parent):
                label = str(node.GetValue(maxon.NODE.BASE.NAME) or node.GetId())
                if child_nodes(node):
                    collect(node, labels + [label])
                else:
                    origins[str(node.GetPath())] = {
                        "id": str(node.GetId()),
                        "label": label,
                        "group_path": labels,
                        "hint": hints.get(str(node.GetId())),
                    }

        collect(graph.GetViewRoot(), [])
        with graph.BeginTransaction() as transaction:
            while True:
                group = next((n for n in child_nodes(graph.GetViewRoot()) if child_nodes(n)), None)
                if group is None:
                    break
                old_path = str(group.GetPath())
                # SDK Ungroup 不传播未连接接口的默认值，先写入实际内部目标。
                for socket in group.GetInputs().GetChildren():
                    if not socket.GetConnections(maxon.PORT_DIR.INPUT):
                        for destination, _ in socket.GetConnections(maxon.PORT_DIR.OUTPUT):
                            destination.SetPortValue(socket.GetPortValue())
                mapping = graph.Ungroup(group)
                for old_id, new_id in mapping:
                    prefix = old_path + "/" + str(old_id)
                    for path in list(origins):
                        if path == prefix or path.startswith(prefix + "/"):
                            origins[str(new_id) + path[len(prefix) :]] = origins.pop(path)
            transaction.Commit()
        return staging, cloned, origins
    except Exception:
        c4d.documents.KillDocument(staging)
        raise


def set_value(node, identifier, value):
    """依据实际端口类型写入；每次写入立即读回，宿主钳制不能被称为成功。"""
    import maxon

    socket = port(node, identifier)
    current = socket.GetPortValue()
    if isinstance(value, list):
        if len(value) == 2:
            if not hasattr(current, "x") or not hasattr(current, "y") or hasattr(current, "z"):
                raise ValidationError("原生端口不是已核验的二维向量", "C4D_VALUE_UNSUPPORTED")
            current.x, current.y = value
            value = current
        elif len(value) == 4:
            value = maxon.ColorA(*value)
        elif hasattr(current, "r"):
            value = maxon.Color(*value)
        else:
            value = maxon.Vector(*value)
    elif type(current).__name__.startswith(("Int", "UInt")):
        value = int(value)
    elif type(current).__name__ == "Url":
        value = maxon.Url(value)
    socket.SetPortValue(value)
    # Maxon 在事务中可能返回提交前的有效缓存值；提交后再核对全部有效常量。
    return socket, json_value(value)


class Cinema4DAdapter:
    """在明确所有者文档中创建独立材质；任意失败只移除本次新增材质。"""

    def __init__(self, document, service):
        self.document = document
        self.service = service
        self.target = renderer_ref()
        self.profile = describe(service, self.target)
        self.images = {}

    def context(self):
        """只读记录现有色彩设置，导入不更改场景渲染条件。"""
        import c4d

        return {
            name: json_value(self.document[getattr(c4d, name)])
            for name in (
                "DOCUMENT_COLOR_MANAGEMENT",
                "DOCUMENT_COLORPROFILE",
                "DOCUMENT_LINEARWORKFLOW",
            )
        }

    def read_material(self, material):
        """读取当前 Redshift 活动表面图；原生类型、配置、资源和每条边均从 SDK 查询。"""
        import c4d
        import maxon

        if material.GetDocument() != self.document:
            raise ValidationError("材质不属于当前文档", "C4D_MATERIAL_UNSUPPORTED")
        graph = material.GetNodeMaterialReference().GetGraph(maxon.Id(SPACE))
        if graph is None or graph.IsNullValue():
            raise ValidationError("材质没有当前 Redshift 节点图", "C4D_MATERIAL_UNSUPPORTED")
        if any(child_nodes(n) for n in child_nodes(graph.GetViewRoot())):
            staging, cloned, origins = flattened_copy(self.document, material)
            try:
                adapter = Cinema4DAdapter(staging, self.service)
                return adapter._read_flat_material(cloned, origins)
            finally:
                c4d.documents.KillDocument(staging)
        return self._read_flat_material(material)

    def _read_flat_material(self, material, origins=None):
        """从当前无组副本读取计算图；来源表只提供身份、名称和层级。"""
        import maxon

        graph = material.GetNodeMaterialReference().GetGraph(maxon.Id(SPACE))
        origins = origins or {}
        native_nodes = {
            str(n.GetId()): n
            for n in graph.GetViewRoot().GetChildren()
            if n.GetKind() == maxon.NODE_KIND.NODE
        }

        def asset(node):
            """读取当前资产 ID，显示名和保存的实现提示均不能替代它。"""
            return str(node.GetValue(maxon.nodes.AssetId)[0])

        roots = [n for n in native_nodes.values() if asset(n) == OUTPUT]
        if len(roots) != 1:
            raise ValidationError("需要唯一 Redshift 材质出口", "C4D_OUTPUT_UNSUPPORTED")

        def connections(node):
            """枚举嵌套输入；分量连线也必须被实际覆盖，不能只检查顶层端口。"""
            pending, result = list(node.GetInputs().GetChildren()), []
            while pending:
                socket = pending.pop()
                pending.extend(socket.GetChildren())
                result.extend(
                    (socket, origin) for origin, _ in socket.GetConnections(maxon.PORT_DIR.INPUT)
                )
            return result

        root_links = connections(roots[0])
        surface_port = port(roots[0], OUTPUT + ".surface")
        if len(root_links) != 1 or root_links[0][0] != surface_port:
            raise ValidationError("材质含未支持出口或缺少唯一表面", "C4D_OUTPUT_UNSUPPORTED")
        output_port = root_links[0][1]
        projection_nodes = set()

        def projected_origin(socket):
            """只合并字典明确声明且当前原生连接完全匹配的输出投影。"""
            owner = socket.GetParent().GetParent()
            for candidate in self.profile["nodes"].values():
                for key, binding in candidate["outputs"].items():
                    projection = binding["native"].get("properties", {}).get("output_projection")
                    if not projection or projection["asset"] != asset(owner):
                        continue
                    if str(socket.GetId()) != binding["native"]["id"]:
                        continue
                    inputs = connections(owner)
                    expected = port(owner, projection["input"])
                    if len(inputs) != 1 or inputs[0][0] != expected:
                        raise ValidationError(
                            "原生输出投影的输入已改变", "C4D_PROJECTION_UNSUPPORTED"
                        )
                    upstream = inputs[0][1]
                    primary = upstream.GetParent().GetParent()
                    if (
                        asset(primary) != candidate["native"]["id"]
                        or str(upstream.GetId()) != projection["source"]
                    ):
                        raise ValidationError(
                            "原生输出投影的来源不符", "C4D_PROJECTION_UNSUPPORTED"
                        )
                    projection_nodes.add(str(owner.GetId()))
                    return str(primary.GetId()), key
            return str(owner.GetId()), None

        def origin_id(socket):
            """由实际输出端口定位所属原生节点。"""
            return projected_origin(socket)[0]

        data = {
            "schema_version": "1",
            "renderer": self.target,
            "name": material.GetName(),
            "nodes": [],
            "links": [],
            "textures": {},
            "output": {},
            "metadata": {},
        }
        pending, rules, edges = [origin_id(output_port)], {}, []
        hints = implementation_hints(material)
        if origins:
            hints = {key: value["hint"] for key, value in origins.items() if value["hint"]}
        # 1. 当前图反向遍历，不使用导入时的源文档或常量缓存。
        while pending:
            name = pending.pop()
            if name in rules:
                continue
            if name not in native_nodes:
                raise ValidationError("尚未覆盖此原生图层级", "C4D_GROUP_UNSUPPORTED")
            node = native_nodes[name]
            native_id = asset(node)
            properties = {
                k
                for r in self.profile["nodes"].values()
                if r["native"]["id"] == native_id
                for k in r["native"].get("properties", {})
            }
            values = {k: json_value(port(node, k).GetPortValue()) for k in properties}
            match = identify(
                self.service,
                self.target,
                native_id,
                values,
                hints.get(name),
                prefer_source_default=name not in hints,
            )
            rule = self.profile["nodes"][match["implementation"]]
            validate_node_interface(node, rule)
            rules[name] = rule
            record = {
                "id": name,
                "type": match["implementation"],
                "inputs": {},
                "outputs": list(rule["outputs"]),
                "metadata": {
                    "editor": {
                        "label": origins.get(name, {}).get("label")
                        or str(node.GetValue(maxon.NODE.BASE.NAME) or "")
                        or name,
                        "group_path": origins.get(name, {}).get("group_path", []),
                        "generated": False,
                    }
                },
            }
            consumed = set()
            for key, binding in rule["inputs"].items():
                native = binding["native"]
                if binding["type"] == "texture":
                    resource = self.read_resource(node, native)
                    resource_id = "image_" + str(len(data["textures"]))
                    data["textures"][resource_id] = resource
                    record["inputs"][key] = {"texture": resource_id}
                    continue
                socket = port(node, native["id"])
                inputs = socket.GetConnections(maxon.PORT_DIR.INPUT)
                if inputs:
                    if len(inputs) != 1:
                        raise ValidationError("原生输入有多个来源", "C4D_INPUT_UNSUPPORTED")
                    origin = inputs[0][0]
                    pending.append(origin_id(origin))
                    edges.append((origin, {"node": name, "socket": key}))
                    consumed.add(str(socket.GetPath()))
                    record["inputs"][key] = deepcopy(binding.get("default"))
                else:
                    record["inputs"][key] = decode_value(json_value(socket.GetPortValue()), binding)
            extras = {str(p.GetPath()) for p, _ in connections(node)} - consumed
            if extras:
                raise ValidationError(
                    "存在字典未覆盖的原生输入连接：" + ", ".join(sorted(extras)),
                    "C4D_INPUT_UNSUPPORTED",
                )
            data["nodes"].append(record)

        # 2. 输出选择来自当前源端口，保持 RGB 通道和共享上游身份。
        def endpoint(origin):
            name = origin_id(origin)
            projected = projected_origin(origin)[1]
            if projected is not None:
                return {"node": name, "socket": projected}
            matches = [
                k
                for k, v in rules[name]["outputs"].items()
                if v["native"]["id"] == str(origin.GetId())
            ]
            if len(matches) != 1:
                raise ValidationError(
                    "原生输出不在字典中：" + str(origin.GetPath()), "C4D_OUTPUT_UNSUPPORTED"
                )
            return {"node": name, "socket": matches[0]}

        data["links"] = [
            {"from": endpoint(origin), "to": destination} for origin, destination in edges
        ]
        data["output"] = endpoint(output_port)
        data["metadata"]["unused_native_nodes"] = sorted(
            set(native_nodes) - set(rules) - {str(roots[0].GetId())} - projection_nodes
        )
        if origins:
            identifiers = {key: value["id"] for key, value in origins.items()}
            if len(set(identifiers.values())) != len(identifiers):
                identifiers = {
                    key: "/".join(value["group_path"] + [value["id"]])
                    for key, value in origins.items()
                }
            for record in data["nodes"]:
                record["id"] = identifiers[record["id"]]
            for edge in data["links"]:
                for side in ("from", "to"):
                    edge[side]["node"] = identifiers[edge[side]["node"]]
            data["output"]["node"] = identifiers[data["output"]["node"]]
        return data

    def read_resource(self, node, native):
        """读取实际文件和原生色彩／UV；未知通道或未核验变换明确阻断。"""
        import c4d

        path = Path(json_value(port(node, native["id"]).GetPortValue()))
        if not path.is_absolute():
            path = Path(self.document.GetDocumentPath()) / path
        path = path.resolve()
        if not path.is_file():
            raise ValidationError("原生图片不存在：" + str(path), "TEXTURE_MISSING")
        channels = image_header_channels(path)
        if channels is None:
            bitmap = c4d.bitmaps.BaseBitmap()
            try:
                status, _ = bitmap.InitWith(str(path))
                if status != c4d.IMAGERESULT_OK:
                    raise ValidationError("原生图片无法解码", "C4D_IMAGE_DECODE_FAILED")
                channels = (
                    4
                    if bitmap.GetInternalChannel() is not None
                    else 1
                    if bitmap.GetBt() in (8, 16)
                    else 3
                )
            finally:
                bitmap.FlushAll()
        image_binding = next(
            b
            for r in self.profile["nodes"].values()
            for b in r["inputs"].values()
            if b.get("native", {}).get("id") == native["id"]
        )
        sampler = native["properties"]["uv_sampler"]
        captured = {
            key: json_value(port(node, identifier).GetPortValue())
            for key, identifier in sampler.items()
        }
        return {
            "uri": path.as_posix(),
            "color_space": resource_color_space(
                json_value(port(node, native["colorspace_id"]).GetPortValue()),
                image_binding,
                encode=False,
            ),
            "coordinates": {
                "kind": "uv",
                "set": json_value(port(node, native["uv_id"]).GetPortValue()),
            },
            "metadata": {
                "image_alpha": {
                    "mode": "CHANNEL_PACKED" if channels == 4 else "STRAIGHT",
                    "channels": channels,
                },
                "native_uv_capture": captured,
            },
        }

    def preflight(self, material):
        """检查坐标边界和图片解码，并单独报告尚未渲染核验的 RGBA 采样假设。"""
        import c4d

        for resource in material["textures"].values():
            path = Path(resource["uri"])
            description = resource.get("metadata", {}).get("image_alpha", {})
            channels = image_header_channels(path)
            if channels is not None and channels != description.get("channels", channels):
                raise ValidationError(
                    "文件通道与文档描述不同：" + str(path), "C4D_IMAGE_CHANNELS_MISMATCH"
                )
            if description.get("channels", 3) not in self.profile["texture_image_channels"]:
                raise ValidationError(
                    "C4D 当前尚未核验四通道 Alpha 组合", "C4D_IMAGE_CHANNELS_UNSUPPORTED"
                )
            if (
                description.get("channels") == 4
                and image_format(path) not in self.profile["texture_alpha_behavior"]
            ):
                raise ValidationError(
                    "C4D 尚未核验此 RGBA 文件格式", "C4D_IMAGE_FORMAT_UNSUPPORTED"
                )
            if resource["coordinates"].get("transforms"):
                plan = resource.get("metadata", {}).get("native_uv_target")
                if not isinstance(plan, dict) or set(plan) != {"scale", "offset", "rotation"}:
                    raise ValidationError(
                        "C4D 坐标需要完整的核心二维采样计划", "C4D_UV_UNSUPPORTED"
                    )
            key = (str(path), path.stat().st_size, path.stat().st_mtime_ns)
            if key not in self.images:
                bitmap = c4d.bitmaps.BaseBitmap()
                try:
                    status, _movie = bitmap.InitWith(str(path))
                    if status != c4d.IMAGERESULT_OK or min(bitmap.GetSize()) <= 0:
                        raise ValidationError(
                            "C4D 无法解码图片：" + str(path), "C4D_IMAGE_DECODE_FAILED"
                        )
                    if bitmap.GetInternalChannel() is not None and description.get("channels") != 4:
                        raise ValidationError(
                            "解码图片含 Alpha，与受支持的通道描述不符：" + str(path),
                            "C4D_IMAGE_CHANNELS_UNSUPPORTED",
                        )
                    self.images[key] = {"size": list(bitmap.GetSize()), "bits": bitmap.GetBt()}
                finally:
                    bitmap.FlushAll()
        issues = [
            {
                "severity": "warning",
                "code": "TARGET_COLOR_CONTEXT",
                "message": "保留 C4D 当前色彩设置：" + str(self.context()) + "；未验证渲染外观",
            }
        ]
        if any(
            resource.get("metadata", {}).get("image_alpha", {}).get("channels") == 4
            for resource in material["textures"].values()
        ):
            issues.append(
                {
                    "severity": "warning",
                    "code": "RS_RGBA_SAMPLING_UNVERIFIED",
                    "message": "RGBA 颜色关联目前按字典声明保留，"
                    "尚无当前 Redshift 版本的实际采样渲染证据",
                }
            )
        return issues

    def create_material(self, material):
        """在 Nodes 事务中恢复完整目标图，默认不绑定对象；失败不留部分材质。"""
        import c4d
        import maxon

        self.preflight(material)
        created = c4d.BaseMaterial(c4d.Mmaterial)
        names = {m.GetName() for m in self.document.GetMaterials()}
        name = material["name"] + "_IrisRelay"
        suffix = 1
        while name in names:
            name = material["name"] + "_IrisRelay_" + str(suffix)
            suffix += 1
        created.SetName(name)
        # 图创建与用户数据会产生宿主内部历史；目标只记录最终新增材质。
        staging = c4d.documents.BaseDocument()
        staging.InsertMaterial(created)
        undo_open = False
        try:
            graph = maxon.GraphDescription.GetGraph(created, maxon.Id(SPACE), createEmpty=True)
            nodes, rules, written = {}, {}, []
            settings = maxon.DataDictionary()
            settings.Set(maxon.nodes.UndoMode, maxon.nodes.UNDO_MODE.NONE)
            with graph.BeginTransaction(settings) as transaction:
                # 1. 原生资产、默认配置和常量输入均来自核心返回的目标图及字典。
                for record in material["nodes"]:
                    rule = self.profile["nodes"][record["type"]]
                    node = graph.AddChild(maxon.Id(record["id"]), maxon.Id(rule["native"]["id"]))
                    if node.IsNullValue() or not node.IsValid():
                        raise ValidationError("原生资产无法创建：" + rule["native"]["id"])
                    nodes[record["id"]], rules[record["id"]] = node, rule
                    editor = record.get("metadata", {}).get("editor", {})
                    node.SetValue(
                        maxon.NODE.BASE.NAME, maxon.String(editor.get("label", record["id"]))
                    )
                    for code, value in rule["native"].get("properties", {}).items():
                        written.append(set_value(node, code, value))
                    validate_node_interface(node, rule)
                    for key, value in record["inputs"].items():
                        if value is None:
                            continue
                        binding = rule["inputs"][key]
                        native = binding["native"]
                        if binding["type"] == "texture":
                            resource = material["textures"][value["texture"]]
                            written.append(set_value(node, native["id"], resource["uri"]))
                            written.append(
                                set_value(
                                    node,
                                    native["colorspace_id"],
                                    resource_color_space(resource["color_space"], binding),
                                )
                            )
                            written.append(
                                set_value(node, native["uv_id"], resource["coordinates"]["set"])
                            )
                            plan = resource.get("metadata", {}).get("native_uv_target")
                            if plan:
                                for key, identifier in native["properties"]["uv_sampler"].items():
                                    written.append(set_value(node, identifier, plan[key]))
                        else:
                            written.append(
                                set_value(node, native["id"], encode_value(value, binding))
                            )
                # 2. 每条边及共享上游保持为原生连接，再连接唯一材质表面出口。
                projections = {}
                for edge in material["links"]:
                    start, end = edge["from"], edge["to"]
                    output_binding = rules[start["node"]]["outputs"][start["socket"]]["native"]
                    a = output_binding["id"]
                    b = rules[end["node"]]["inputs"][end["socket"]]["native"]["id"]
                    source_node = nodes[start["node"]]
                    projection = output_binding.get("properties", {}).get("output_projection")
                    if projection:
                        projection_id = start["node"] + "__" + start["socket"] + "_projection"
                        if projection_id not in projections:
                            helper = graph.AddChild(
                                maxon.Id(projection_id), maxon.Id(projection["asset"])
                            )
                            port(source_node, projection["source"], True).Connect(
                                port(helper, projection["input"])
                            )
                            projections[projection_id] = helper
                        source_node = projections[projection_id]
                    port(source_node, a, True).Connect(port(nodes[end["node"]], b))
                output = material["output"]
                node = graph.AddChild(maxon.Id("material_output"), maxon.Id(OUTPUT))
                binding = rules[output["node"]]["outputs"][output["socket"]]["native"]["id"]
                port(nodes[output["node"]], binding, True).Connect(port(node, OUTPUT + ".surface"))
                transaction.Commit()
            for socket, expected in written:
                if not socket.GetConnections(maxon.PORT_DIR.INPUT):
                    actual = json_value(socket.GetPortValue())
                    if not equal(actual, expected):
                        raise ValidationError(
                            f"原生参数提交后改变：{socket.GetPath()}；"
                            f"期望 {expected}，实际 {actual}",
                            "C4D_VALUE_CHANGED",
                        )
            set_implementation_hints(
                created, {record["id"]: record["type"] for record in material["nodes"]}
            )
            groups = self.create_editing_groups(graph, material, nodes, projections)
            created.Remove()
            self.document.StartUndo()
            undo_open = True
            self.document.InsertMaterial(created)
            self.document.AddUndo(c4d.UNDOTYPE_NEWOBJ, created)
            return {"material": name, "nodes": list(nodes), "groups": groups}
        except Exception:
            created.Remove()
            raise
        finally:
            if undo_open:
                self.document.EndUndo()
            c4d.documents.KillDocument(staging)

    def create_editing_groups(self, graph, material, nodes, projections):
        """创建原生功能组，共享控制节点保持单一并通过原生边界端口驱动组内计算。"""
        import maxon

        result = []
        with graph.BeginTransaction() as transaction:
            for index, spec in enumerate(
                material.get("metadata", {}).get("editor", {}).get("groups", [])
            ):
                controls = {c["node"] for c in spec["controls"]}
                members = [key for key in spec["nodes"] if key not in controls]
                selected = [nodes[key] for key in members]
                selected += [
                    node
                    for key, node in projections.items()
                    if any(key.startswith(name + "__") for name in members)
                ]
                if not selected:
                    continue
                group = graph.MoveToGroup(
                    maxon.GraphNode(), maxon.Id("iris_function_" + str(index)), selected
                )
                group.SetValue(maxon.NODE.BASE.NAME, maxon.String(spec["label"]))
                result.append(
                    {
                        "id": str(group.GetId()),
                        "label": spec["label"],
                        "nodes": members,
                        "controls": spec["controls"],
                    }
                )
            transaction.Commit()
        return result
