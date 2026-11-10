"""Maya 2022 / MtoA 的原生创建、资源核对和失败清理；不实现语义转换。"""

import json
import math
import re
from copy import deepcopy
from pathlib import Path

from iris_relay.native import decode_value, describe, encode_value, identify
from iris_relay.protocol import ValidationError


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

    if cmds.about(version=True) != "2022":
        raise ValidationError("本入口当前仅验证 Maya 2022")
    if not cmds.pluginInfo("mtoa", query=True, loaded=True):
        cmds.loadPlugin("mtoa", quiet=True)
    if cmds.pluginInfo("mtoa", query=True, version=True) != "5.3.5.3":
        raise ValidationError("当前字典要求 MtoA 5.3.5.3，请保留实际版本信息重新探测")
    return renderer_ref()


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
            if actual not in self.profile["texture_image_channels"] or (
                expected is not None and expected != actual
            ):
                raise ValidationError(
                    "图片通道暂不支持或与文档不同"
                    f"（仅核验 1/3 通道；文件={actual}，文档={expected}）：" + str(path),
                    "MAYA_IMAGE_CHANNELS_UNSUPPORTED",
                )
            if resource["coordinates"].get("transforms"):
                raise ValidationError("Maya 暂未实现坐标矩阵还原", "MAYA_UV_UNSUPPORTED")
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
        created, nodes = [], {}
        cmds.undoInfo(openChunk=True, chunkName="Iris Relay 导入材质")
        try:
            # 1. 创建原生节点并写入已由核心确定的属性和输入。
            prefix = "Iris_" + re.sub(r"[^A-Za-z0-9_]", "_", material["name"])[:40]
            for index, record in enumerate(material["nodes"]):
                rule = self.profile["nodes"][record["type"]]
                node = cmds.shadingNode(
                    rule["native"]["id"],
                    asShader=rule["semantic"] == "surface.standard",
                    asUtility=rule["semantic"] != "surface.standard",
                    name=prefix + "_" + str(index) + "_" + record["type"],
                )
                created.append(node)
                nodes[record["id"]] = node
                cmds.addAttr(node, longName="irisImplementation", dataType="string")
                set_value(node + ".irisImplementation", record["type"])
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
                source = nodes[start["node"]] + "." + source_rule["native"]["id"]
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
            return {
                "material": surface,
                "shading_group": group,
                "nodes": nodes,
                "created_nodes": created,
            }
        except Exception:
            surviving = [node for node in created if cmds.objExists(node)]
            if surviving:
                cmds.delete(surviving)
            raise
        finally:
            cmds.select(before, replace=True) if before else cmds.select(clear=True)
            cmds.undoInfo(closeChunk=True)

    def read_material(self, surface):
        """读取当前原生上游图；提示须经核心核验，未知节点或多余连接明确阻断。"""
        from maya import cmds

        if not cmds.objExists(surface) or cmds.nodeType(surface) != "aiStandardSurface":
            raise ValidationError("请选择 aiStandardSurface 表面节点", "MAYA_SURFACE_UNSUPPORTED")
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
        pending, rules, origins = [surface], {}, []
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
            match = identify(self.service, self.target, native_id, values, hint)
            rule = self.profile["nodes"][match["implementation"]]
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
            consumed = set()
            for name, binding in rule["inputs"].items():
                native = binding["native"]
                if binding["type"] == "texture":
                    resource = self.read_resource(node, native)
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
                    origins.append((sources[0], {"node": node, "socket": name}))
                    pending.append(sources[0].split(".", 1)[0])
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

    def read_resource(self, node, native):
        """读取当前图片文件、通道、色彩和 UV；附加审计属性不作为资源真值。"""
        import arnold

        from maya import cmds

        path = Path(cmds.getAttr(node + "." + native["id"]))
        if not path.is_file():
            raise ValidationError("图片不存在：" + str(path), "TEXTURE_MISSING")
        channels = arnold.AiTextureGetNumChannels(str(path))
        if channels not in self.profile["texture_image_channels"]:
            raise ValidationError(
                "Maya 图片导出目前只核验 1/3 通道", "MAYA_IMAGE_CHANNELS_UNSUPPORTED"
            )
        return {
            "uri": path.resolve().as_posix(),
            "color_space": cmds.getAttr(node + "." + native["colorspace_id"]),
            "coordinates": {"kind": "uv", "set": cmds.getAttr(node + "." + native["uv_id"])},
            "metadata": {"image_alpha": {"mode": "STRAIGHT", "channels": channels}},
        }
