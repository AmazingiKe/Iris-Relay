"""Maya / MtoA 的原生创建、资源核对和失败清理；按实际节点接口接入。"""

import json
import math
import re
from copy import deepcopy
from pathlib import Path

from iris_relay.native import decode_value, describe, encode_value, identify
from iris_relay.protocol import ValidationError
from plugins.resources import image_format


def renderer_ref():
    """读取实际 Maya、Arnold 与 MtoA 版本，拒绝套用其他宿主字典。"""
    import arnold

    from maya import cmds

    version = ".".join(
        v.decode() if isinstance(v, bytes) else str(v) for v in arnold.AiGetVersion()
    )
    return {
        "id": "arnold",
        "version": version,
        "host": "maya",
        "host_version": cmds.about(version=True),
        "node_system": "mtoa",
    }


def ensure_environment():
    """仅加载本次 Maya 的 MtoA；加载失败给出诊断，不修改系统安装或全局配置。"""
    from maya import cmds

    if not cmds.pluginInfo("mtoa", query=True, loaded=True):
        cmds.loadPlugin("mtoa", quiet=True)
    return renderer_ref()


def validate_node_interface(node, rule):
    """按当前原生属性核对绑定与类型；软件版本不同不阻断，接口变化必须显式失败。"""
    from maya import cmds

    types = {
        "float": {
            "float",
            "double",
            "doubleLinear",
            "doubleAngle",
            "long",
            "short",
            "byte",
            "enum",
        },
        "color": {"float3", "double3"},
        "vector": {"float3", "double3"},
        "shader": {"float3", "double3"},
        "bool": {"bool"},
        "string": {"string"},
        "texture": {"string"},
    }

    def check(attribute, expected=None):
        if not cmds.attributeQuery(attribute, node=node, exists=True):
            raise ValidationError(
                "原生属性不存在：" + node + "." + attribute, "NATIVE_INTERFACE_MISMATCH"
            )
        if expected is not None:
            actual = cmds.getAttr(node + "." + attribute, type=True)
            if actual not in types.get(expected, set()):
                raise ValidationError(
                    "原生属性类型不匹配：" + node + "." + attribute + "（" + str(actual) + "）",
                    "NATIVE_INTERFACE_MISMATCH",
                )

    for attribute in rule["native"].get("properties", {}):
        check(attribute)
    for direction in ("inputs", "outputs"):
        for binding in rule[direction].values():
            native = binding["native"]
            expected = "color" if native.get("codec") == "gray" else binding["type"]
            check(native["id"], expected)
            for key in ("colorspace_id", "uv_id"):
                if key in native:
                    check(native[key], "string")


def equal(a, b):
    """比较宿主浮点回读；非数值必须严格相等。"""
    if isinstance(a, (tuple, list)) and isinstance(b, (tuple, list)):
        return len(a) == len(b) and all(equal(x, y) for x, y in zip(a, b))
    if type(a) in (float, int) and type(b) in (float, int):
        return math.isclose(a, b, rel_tol=2e-6, abs_tol=2e-6)
    return a == b


def set_value(plug, value):
    """按实际 Maya 属性类型写入，并立即核验没有被宿主钳制或改变。"""
    from maya import cmds

    kind = cmds.getAttr(plug, type=True)
    if kind == "string":
        cmds.setAttr(plug, value, type="string")
    elif kind in {"float2", "double2", "float3", "double3"}:
        cmds.setAttr(plug, *value, type=kind)
    elif kind == "matrix":
        cmds.setAttr(plug, *value, type="matrix")
    else:
        cmds.setAttr(plug, value)
    actual = cmds.getAttr(plug)
    if kind in {"float2", "double2", "float3", "double3"}:
        actual = actual[0]
    if not equal(actual, value):
        raise ValidationError("原生参数写入后不一致：" + plug)


def encoded(value, rule):
    """复用公开数值编码；额外的枚举索引由字典明确列出，不按名称猜测。"""
    values = rule["native"].get("properties", {}).get("enum_values")
    if values is not None:
        if type(value) not in (int, float) or int(value) != value or not 0 <= value < len(values):
            raise ValidationError("原生枚举索引不受支持")
        return values[int(value)]
    return encode_value(value, rule)


class MayaAdapter:
    """创建独立节点网络与 shadingEngine，默认不修改对象材质绑定。"""

    def __init__(self, service, target):
        self.service = service
        self.profile = describe(service, target)
        self.target = target
        self.channels = {}

    def preflight(self, material):
        """核对贴图通道与坐标支持；未经验证的四通道 Alpha 不能当作已还原。"""
        import arnold

        from maya import cmds

        for resource in material["textures"].values():
            path = Path(resource["uri"])
            key = (str(path), path.stat().st_size, path.stat().st_mtime_ns)
            if key not in self.channels:
                self.channels[key] = arnold.AiTextureGetNumChannels(str(path))
            actual = self.channels[key]
            expected = resource.get("metadata", {}).get("image_alpha", {}).get("channels")
            if actual == 4 and expected is None:
                raise ValidationError(
                    "四通道图片缺少明确 Alpha 解释，不能猜测原生关联行为",
                    "MAYA_IMAGE_ALPHA_REQUIRED",
                )
            if actual not in self.profile["texture_image_channels"] or (
                expected is not None and expected != actual
            ):
                raise ValidationError(
                    f"图片通道暂不支持或与文档不同（文件={actual}，文档={expected}）：" + str(path),
                    "MAYA_IMAGE_CHANNELS_UNSUPPORTED",
                )
            if actual == 4 and image_format(path) not in self.profile["texture_alpha_behavior"]:
                raise ValidationError(
                    "Maya 尚未核验此 RGBA 文件格式", "MAYA_IMAGE_FORMAT_UNSUPPORTED"
                )
            if actual == 4 and image_format(path) == "exr" and resource["color_space"] != "Raw":
                raise ValidationError(
                    "RGBA EXR 的非 Raw 色彩采样尚未通过探测", "MAYA_IMAGE_COLORSPACE_UNSUPPORTED"
                )
        for link in material["links"]:
            record = next(n for n in material["nodes"] if n["id"] == link["to"]["node"])
            rule = self.profile["nodes"][record["type"]]["inputs"][link["to"]["socket"]]
            if rule["native"].get("properties", {}).get("enum_values") is not None:
                raise ValidationError(
                    "Maya 枚举索引暂不支持动态连接", "MAYA_ENUM_CONNECTION_UNSUPPORTED"
                )
        space = cmds.colorManagementPrefs(query=True, renderingSpaceName=True)
        return [
            {
                "severity": "warning",
                "code": "TARGET_COLOR_CONTEXT",
                "message": "当前 Maya 工作空间为 "
                + space
                + "；保留现有设置，节点与贴图解释已写入，外观未验证",
            },
            {
                "severity": "warning",
                "code": "MAYA_MATH_LIMITS",
                "message": (
                    "标准数学图展开为可编辑节点；floor 使用 Arnold 32 位整数输出，超大数值未验证"
                ),
            },
        ]

    def destination_plugs(self, node, rule):
        """标量到原生三分量端口按字典 gray 编码广播，其他连接保持单端口。"""
        from maya import cmds

        attr = rule["native"]["id"]
        if rule["native"].get("codec") == "gray":
            children = cmds.attributeQuery(attr, node=node, listChildren=True)
            if len(children or []) != 3:
                raise ValidationError("gray 编码要求三个原生子端口")
            return [node + "." + child for child in children]
        return [node + "." + attr]

    def create_material(self, material):
        """在单个撤销块新建网络；任一失败仅删除本次节点并恢复选择。"""
        from maya import cmds

        self.preflight(material)
        before = cmds.ls(selection=True, long=True) or []
        created, nodes, sampled, wrappers = [], {}, {}, {}
        cmds.undoInfo(openChunk=True, chunkName="Iris Relay 导入材质")
        try:
            # 1. 创建原生节点并写入已由核心确定的属性和输入。
            prefix = "Iris_" + re.sub(r"[^A-Za-z0-9_]", "_", material["name"])[:40]
            for record in material["nodes"]:
                rule = self.profile["nodes"][record["type"]]
                node = cmds.shadingNode(
                    rule["native"]["id"],
                    asShader=rule["semantic"] == "surface.standard",
                    asUtility=rule["semantic"] != "surface.standard",
                    name=record["id"],
                )
                created.append(node)
                validate_node_interface(node, rule)
                nodes[record["id"]] = node
                cmds.addAttr(node, longName="irisImplementation", dataType="string")
                set_value(node + ".irisImplementation", record["type"])
                cmds.addAttr(node, longName="irisSourceName", dataType="string")
                set_value(
                    node + ".irisSourceName",
                    json.dumps(
                        record.get("metadata", {}).get("source_name", record["id"]),
                        ensure_ascii=True,
                    ),
                )
                editor = record.get("metadata", {}).get("editor", {})
                if editor:
                    cmds.addAttr(node, longName="irisEditor", dataType="string")
                    set_value(node + ".irisEditor", json.dumps(editor, ensure_ascii=False))
                    if not cmds.attributeQuery("notes", node=node, exists=True):
                        cmds.addAttr(node, longName="notes", dataType="string")
                    set_value(node + ".notes", editor.get("label", record["id"]))
                for attr, value in rule["native"].get("properties", {}).items():
                    set_value(node + "." + attr, value)
                for port, value in record["inputs"].items():
                    binding = rule["inputs"][port]
                    if value is None:
                        continue
                    if binding["type"] == "texture":
                        resource = material["textures"][value["texture"]]
                        native = binding["native"]
                        set_value(node + "." + native["id"], resource["uri"])
                        set_value(node + "." + native["colorspace_id"], resource["color_space"])
                        set_value(node + "." + native["uv_id"], resource["coordinates"]["set"])
                        cmds.addAttr(node, longName="irisResource", dataType="string")
                        set_value(node + ".irisResource", json.dumps(resource, ensure_ascii=False))
                    else:
                        set_value(node + "." + binding["native"]["id"], encoded(value, binding))
                for port, value in record["inputs"].items():
                    binding = rule["inputs"][port]
                    if binding["type"] == "texture" and value is not None:
                        resource = material["textures"][value["texture"]]
                        outputs, helpers = self.create_sampling_wrappers(
                            node, resource, binding["native"], created
                        )
                        sampled[record["id"]] = outputs
                        wrappers[record["id"]] = helpers
            # 2. 恢复每条连接和共享上游；不使用源快照、不自动绑定对象。
            records = {n["id"]: n for n in material["nodes"]}
            for link in material["links"]:
                start, end = link["from"], link["to"]
                source_rule = self.profile["nodes"][records[start["node"]]["type"]]["outputs"][
                    start["socket"]
                ]
                target_rule = self.profile["nodes"][records[end["node"]]["type"]]["inputs"][
                    end["socket"]
                ]
                source = sampled.get(start["node"], {}).get(
                    source_rule["semantic"],
                    nodes[start["node"]] + "." + source_rule["native"]["id"],
                )
                for destination in self.destination_plugs(nodes[end["node"]], target_rule):
                    cmds.connectAttr(source, destination)
            output = material["output"]
            surface = nodes[output["node"]]
            group = cmds.sets(
                renderable=True, noSurfaceShader=True, empty=True, name=prefix + "_SG"
            )
            created.append(group)
            rule = self.profile["nodes"][records[output["node"]]["type"]]["outputs"][
                output["socket"]
            ]
            cmds.connectAttr(surface + "." + rule["native"]["id"], group + ".surfaceShader")
            cmds.addAttr(surface, longName="irisMaterialName", dataType="string")
            set_value(surface + ".irisMaterialName", material["name"])
            containers = self.create_editing_groups(material, nodes, wrappers, created, prefix)
            return {
                "material": surface,
                "shading_group": group,
                "nodes": nodes,
                "created_nodes": created,
                "resource_wrappers": wrappers,
                "containers": containers,
            }
        except Exception:
            surviving = [node for node in created if cmds.objExists(node)]
            if surviving:
                cmds.delete(surviving)
            raise
        finally:
            cmds.select(before, replace=True) if before else cmds.select(clear=True)
            cmds.undoInfo(closeChunk=True)

    def create_editing_groups(self, material, nodes, wrappers, created, prefix):
        """创建原生容器并发布实际控制属性；不改变着色连线和共享计算。"""
        from maya import cmds

        result = []
        for index, group in enumerate(
            material.get("metadata", {}).get("editor", {}).get("groups", [])
        ):
            container = cmds.container(name=prefix + "_Function_" + str(index))
            created.append(container)
            members = [nodes[key] for key in group["nodes"]]
            for key in group["nodes"]:
                members.extend(wrappers.get(key, []))
            cmds.container(container, edit=True, addNode=members, force=True)
            cmds.addAttr(container, longName="irisLabel", dataType="string")
            set_value(container + ".irisLabel", group["label"])
            published = []
            for control_index, control in enumerate(group["controls"]):
                record = next(n for n in material["nodes"] if n["id"] == control["node"])
                binding = self.profile["nodes"][record["type"]]["inputs"][control["port"]]
                plug = nodes[control["node"]] + "." + binding["native"]["id"]
                label = re.sub(r"[^A-Za-z0-9_]", "_", control["label"])
                label = (
                    "parameter_" + str(control_index)
                    if not label or not label[0].isalpha()
                    else label
                )
                alias = label + "_" + str(control_index)
                cmds.container(container, edit=True, publishAndBind=[plug, alias])
                published.append(
                    {"alias": alias, "plug": plug, "node": control["node"], "port": control["port"]}
                )
            result.append(
                {
                    "node": container,
                    "label": group["label"],
                    "members": members,
                    "published": published,
                }
            )
        return result

    def create_sampling_wrappers(self, node, resource, native, created):
        """用原生 UV 上下文节点逐步保留矩阵；颜色和 Alpha 共用完全相同的上下文。"""
        from maya import cmds

        matrices = resource["coordinates"].get("transforms", [])
        if not matrices:
            return {}, []
        spec = native.get("properties", {}).get("coordinate_wrapper")
        if not spec:
            raise ValidationError("缺少已核验的原生坐标绑定", "MAYA_UV_UNSUPPORTED")
        rule = next(
            r for r in self.profile["nodes"].values() if r["native"]["id"] == cmds.nodeType(node)
        )
        outputs = {p["semantic"]: node + "." + p["native"]["id"] for p in rule["outputs"].values()}
        set_value(node + "." + native["uv_id"], "")
        helpers = []
        for index in reversed(range(len(matrices))):
            helper = cmds.shadingNode(
                spec["asset"], asUtility=True, name=node + "_UV_" + str(index)
            )
            created.append(helper)
            helpers.insert(0, helper)
            for key, value in spec["fixed"].items():
                set_value(helper + "." + key, value)
            set_value(helper + "." + spec["matrix"], matrices[index])
            set_value(
                helper + "." + spec["uv"], resource["coordinates"]["set"] if index == 0 else ""
            )
            for semantic, attribute in spec["inputs"].items():
                cmds.connectAttr(outputs[semantic], helper + "." + attribute)
            outputs = {
                semantic: helper + "." + attribute
                for semantic, attribute in spec["outputs"].items()
            }
        return outputs, helpers

    def sampling_origin(self, plug, contexts):
        """核验当前 UV 包装链后返回真实图片输出，任何不受支持的配置或分叉均阻断。"""
        from maya import cmds

        image_rule = next(
            r for r in self.profile["nodes"].values() if r["semantic"] == "texture.image"
        )
        resource_binding = next(p for p in image_rule["inputs"].values() if p["type"] == "texture")[
            "native"
        ]
        spec = resource_binding.get("properties", {}).get("coordinate_wrapper")
        original, matrices, helpers, uv = plug, [], [], None
        while spec and cmds.nodeType(plug.split(".", 1)[0]) == spec["asset"]:
            node, attribute = plug.split(".", 1)
            semantics = [key for key, value in spec["outputs"].items() if value == attribute]
            if len(semantics) != 1 or node in helpers:
                raise ValidationError("未知或循环的 UV 上下文输出", "MAYA_UV_UNSUPPORTED")
            for key, value in spec["fixed"].items():
                if not equal(self.read_value(node + "." + key), value):
                    raise ValidationError(
                        "UV 上下文含未支持参数：" + node + "." + key, "MAYA_UV_UNSUPPORTED"
                    )
            connections = (
                cmds.listConnections(
                    node, source=True, destination=False, plugs=True, connections=True
                )
                or []
            )
            destinations = set(connections[::2])
            if destinations != {node + "." + value for value in spec["inputs"].values()}:
                raise ValidationError("UV 上下文含缺失或额外输入连接", "MAYA_UV_UNSUPPORTED")
            sources = {
                key: cmds.connectionInfo(node + "." + value, sourceFromDestination=True)
                for key, value in spec["inputs"].items()
            }
            source_nodes = {value.split(".", 1)[0] for value in sources.values()}
            if len(source_nodes) != 1:
                raise ValidationError("UV 的颜色与 Alpha 来自不同采样", "MAYA_UV_UNSUPPORTED")
            upstream = next(iter(source_nodes))
            if cmds.nodeType(upstream) == spec["asset"]:
                expected = spec["outputs"]
            elif cmds.nodeType(upstream) == image_rule["native"]["id"]:
                expected = {
                    value["semantic"]: value["native"]["id"]
                    for value in image_rule["outputs"].values()
                }
            else:
                expected = {}
            if any(sources[key] != upstream + "." + expected.get(key, "") for key in sources):
                raise ValidationError("UV 包装链没有完整传递图片通道", "MAYA_UV_UNSUPPORTED")
            name = cmds.getAttr(node + "." + spec["uv"])
            if helpers and name:
                raise ValidationError(
                    "嵌套 UV 上下文重新选择命名集，尚未支持", "MAYA_UV_UNSUPPORTED"
                )
            if uv is None:
                uv = name
            matrices.append(self.read_value(node + "." + spec["matrix"]))
            helpers.append(node)
            plug = sources[semantics[0]]
        node, attribute = plug.split(".", 1)
        if cmds.nodeType(node) == image_rule["native"]["id"]:
            image_uv = cmds.getAttr(node + "." + resource_binding["uv_id"])
            if helpers and image_uv:
                raise ValidationError("图片覆盖了外层 UV 上下文", "MAYA_UV_UNSUPPORTED")
            coordinates = {"kind": "uv", "set": uv if helpers else image_uv}
            if matrices:
                coordinates["transforms"] = matrices
            if node in contexts and not equal(contexts[node]["coordinates"], coordinates):
                raise ValidationError(
                    "同一图片的输出使用不同坐标上下文", "MAYA_SPLIT_SAMPLING_CONTEXT_UNSUPPORTED"
                )
            contexts[node] = {"coordinates": coordinates, "helpers": helpers}
        return plug if helpers else original

    def read_material(self, surface):
        """读取当前原生上游图；提示须经核心核验，未知节点或多余连接明确阻断。"""
        from maya import cmds

        if not cmds.objExists(surface) or cmds.nodeType(surface) != "aiStandardSurface":
            raise ValidationError("请选择 aiStandardSurface 表面节点", "MAYA_SURFACE_UNSUPPORTED")
        for group in (
            cmds.listConnections(
                surface + ".outColor", source=False, destination=True, type="shadingEngine"
            )
            or []
        ):
            for attribute in ("volumeShader", "displacementShader"):
                if cmds.connectionInfo(group + "." + attribute, sourceFromDestination=True):
                    raise ValidationError(
                        "材质含未支持的体积或位移出口：" + group + "." + attribute,
                        "MAYA_OUTPUT_UNSUPPORTED",
                    )
        data = {
            "schema_version": "1",
            "renderer": self.target,
            "name": surface,
            "nodes": [],
            "links": [],
            "textures": {},
            "output": {},
            "metadata": {},
        }
        if cmds.attributeQuery("irisMaterialName", node=surface, exists=True):
            data["name"] = cmds.getAttr(surface + ".irisMaterialName")
        pending, rules, origins, contexts = [surface], {}, [], {}
        # 1. 按当前连线遍历，读取当前属性，不用导入时的文档或材质快照。
        while pending:
            node = pending.pop()
            if node in rules:
                continue
            native_id = cmds.nodeType(node)
            hint = (
                cmds.getAttr(node + ".irisImplementation")
                if cmds.attributeQuery("irisImplementation", node=node, exists=True)
                else None
            )
            properties = {
                key
                for rule in self.profile["nodes"].values()
                if rule["native"]["id"] == native_id
                for key in rule["native"].get("properties", {})
            }
            values = {key: self.read_value(node + "." + key) for key in properties}
            match = identify(
                self.service,
                self.target,
                native_id,
                values,
                hint,
                prefer_source_default=hint is None,
            )
            rule = self.profile["nodes"][match["implementation"]]
            validate_node_interface(node, rule)
            rules[node] = rule
            if cmds.getAttr(node + ".nodeState") != 0:
                raise ValidationError(
                    "节点处于旁路或阻断状态：" + node, "MAYA_NODE_STATE_UNSUPPORTED"
                )
            record = {
                "id": node,
                "type": match["implementation"],
                "inputs": {},
                "outputs": list(rule["outputs"]),
                "metadata": {},
            }
            editor = (
                json.loads(cmds.getAttr(node + ".irisEditor"))
                if cmds.attributeQuery("irisEditor", node=node, exists=True)
                else {}
            )
            if cmds.attributeQuery("irisSourceName", node=node, exists=True):
                # Maya ASCII 保存非 BMP 字符可能变成问号；备份用 JSON 转义传递。
                record["metadata"]["source_name"] = json.loads(
                    cmds.getAttr(node + ".irisSourceName")
                )
            if cmds.attributeQuery("notes", node=node, exists=True):
                label = cmds.getAttr(node + ".notes")
                if label:
                    editor["label"] = label
            editor.setdefault("label", node)
            container = cmds.container(query=True, findContainer=[node])
            if container:
                editor["group_path"] = [
                    cmds.getAttr(container + ".irisLabel")
                    if cmds.attributeQuery("irisLabel", node=container, exists=True)
                    else container
                ]
            elif "group_path" in editor:
                editor["group_path"] = []
            editor["generated"] = False
            record["metadata"]["editor"] = editor
            consumed = set()
            for name, binding in rule["inputs"].items():
                native = binding["native"]
                if binding["type"] == "texture":
                    resource = self.read_resource(node, native, contexts.get(node))
                    key = "image_" + str(len(data["textures"]))
                    data["textures"][key] = resource
                    record["inputs"][name] = {"texture": key}
                    continue
                destinations = self.destination_plugs(node, binding)
                sources = [
                    cmds.connectionInfo(plug, sourceFromDestination=True) for plug in destinations
                ]
                if any(sources):
                    if not all(sources) or len(set(sources)) != 1:
                        raise ValidationError(
                            "不完整或不同来源的复合输入：" + node + "." + name,
                            "MAYA_PARTIAL_CONNECTION_UNSUPPORTED",
                        )
                    for plug in destinations:
                        consumed.add(cmds.connectionInfo(plug, getExactDestination=True))
                    source = self.sampling_origin(sources[0], contexts)
                    origins.append((source, {"node": node, "socket": name}))
                    pending.append(source.split(".", 1)[0])
                    record["inputs"][name] = deepcopy(binding.get("default"))
                else:
                    value = self.read_value(node + "." + native["id"])
                    enums = native.get("properties", {}).get("enum_values")
                    if enums is not None:
                        if value not in enums:
                            raise ValidationError("原生枚举不在字典范围：" + node + "." + name)
                        value = enums.index(value)
                    record["inputs"][name] = decode_value(value, binding)
            connections = (
                cmds.listConnections(
                    node, source=True, destination=False, plugs=True, connections=True
                )
                or []
            )
            # Maya 自动连接的色彩上下文属于宿主状态，只核对，不改动或当作材质计算分支。
            context = {
                "colorManagementEnabled": "defaultColorMgtGlobals.cmEnabled",
                "colorManagementConfigFileEnabled": "defaultColorMgtGlobals.configFileEnabled",
                "colorManagementConfigFilePath": "defaultColorMgtGlobals.configFilePath",
                "workingSpace": "defaultColorMgtGlobals.workingSpaceName",
            }
            for destination, source in zip(connections[::2], connections[1::2]):
                if context.get(destination.split(".", 1)[1]) == source:
                    consumed.add(destination)
            extra = set(connections[::2]) - consumed
            if extra:
                raise ValidationError(
                    "存在字典未覆盖的输入连接：" + ", ".join(sorted(extra)),
                    "MAYA_INPUT_UNSUPPORTED",
                )
            data["nodes"].append(record)
        # 2. 输出端口也从实际来源核对；未知分量不替换为其他通道。
        for plug, destination in origins:
            node, attr = plug.split(".", 1)
            outputs = [
                key
                for key, value in rules[node]["outputs"].items()
                if value["native"]["id"] == attr
            ]
            if len(outputs) != 1:
                raise ValidationError("字典未覆盖原生输出：" + plug, "MAYA_OUTPUT_UNSUPPORTED")
            data["links"].append({"from": {"node": node, "socket": outputs[0]}, "to": destination})
        output = next(
            key
            for key, value in rules[surface]["outputs"].items()
            if value["semantic"] == "surface"
        )
        data["output"] = {"node": surface, "socket": output}
        return data

    @staticmethod
    def read_value(plug):
        """将 Maya 的复合属性转换为公开协议的列表，保留其实际数值。"""
        from maya import cmds

        value = cmds.getAttr(plug)
        if isinstance(value, (tuple, list)):
            if len(value) == 1 and isinstance(value[0], (tuple, list)):
                value = value[0]
            return list(value)
        return value

    def read_resource(self, node, native, context=None):
        """读取当前图片文件、通道、色彩和 UV；附加审计属性不作为资源真值。"""
        import arnold

        from maya import cmds

        path = Path(cmds.getAttr(node + "." + native["id"]))
        if not path.is_file():
            raise ValidationError("图片不存在：" + str(path), "TEXTURE_MISSING")
        channels = arnold.AiTextureGetNumChannels(str(path))
        if channels not in self.profile["texture_image_channels"]:
            raise ValidationError("Maya 图片通道尚未核验", "MAYA_IMAGE_CHANNELS_UNSUPPORTED")
        description = {
            "mode": "CHANNEL_PACKED" if channels == 4 else "STRAIGHT",
            "channels": channels,
        }
        kind = image_format(path)
        if channels == 4:
            behavior = self.profile["texture_alpha_behavior"].get(kind)
            if behavior is None:
                raise ValidationError(
                    "Maya 尚未核验此 RGBA 文件格式", "MAYA_IMAGE_FORMAT_UNSUPPORTED"
                )
            if behavior == "premultiplied":
                description["association"] = "premultiplied"
        return {
            "uri": path.resolve().as_posix(),
            "color_space": cmds.getAttr(node + "." + native["colorspace_id"]),
            "coordinates": deepcopy(context["coordinates"])
            if context
            else {"kind": "uv", "set": cmds.getAttr(node + "." + native["uv_id"])},
            "metadata": {"image_alpha": description, "image_format": kind},
        }
