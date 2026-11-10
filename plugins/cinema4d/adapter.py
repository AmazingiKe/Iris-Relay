"""Cinema 4D 2025.3.2 / Redshift 原生图创建；规则由公开核心字典提供。"""

import json
import math
from pathlib import Path

from iris_relay.native import describe, encode_value, resource_color_space
from iris_relay.protocol import ValidationError
from plugins.resources import image_header_channels

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
    if hasattr(value, "x"):
        return [float(value.x), float(value.y), float(value.z)]
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


def set_value(node, identifier, value):
    """依据实际端口类型写入；每次写入立即读回，宿主钳制不能被称为成功。"""
    import maxon

    socket = port(node, identifier)
    current = socket.GetPortValue()
    if isinstance(value, list):
        if len(value) == 4:
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

    def preflight(self, material):
        """检查坐标边界和图片解码；Alpha 组合尚无当前宿主证据时明确阻断。"""
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
            if resource["coordinates"].get("transforms"):
                raise ValidationError("C4D 当前尚未还原坐标矩阵", "C4D_UV_UNSUPPORTED")
            key = (str(path), path.stat().st_size, path.stat().st_mtime_ns)
            if key not in self.images:
                bitmap = c4d.bitmaps.BaseBitmap()
                try:
                    status, _movie = bitmap.InitWith(str(path))
                    if status != c4d.IMAGERESULT_OK or min(bitmap.GetSize()) <= 0:
                        raise ValidationError(
                            "C4D 无法解码图片：" + str(path), "C4D_IMAGE_DECODE_FAILED"
                        )
                    if bitmap.GetInternalChannel() is not None:
                        raise ValidationError(
                            "解码图片含 Alpha，与受支持的通道描述不符：" + str(path),
                            "C4D_IMAGE_CHANNELS_UNSUPPORTED",
                        )
                    self.images[key] = {"size": list(bitmap.GetSize()), "bits": bitmap.GetBt()}
                finally:
                    bitmap.FlushAll()
        return [
            {
                "severity": "warning",
                "code": "TARGET_COLOR_CONTEXT",
                "message": "保留 C4D 当前色彩设置：" + str(self.context()) + "；未验证渲染外观",
            }
        ]

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
                    for code, value in rule["native"].get("properties", {}).items():
                        written.append(set_value(node, code, value))
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
                        else:
                            written.append(
                                set_value(node, native["id"], encode_value(value, binding))
                            )
                # 2. 每条边及共享上游保持为原生连接，再连接唯一材质表面出口。
                for edge in material["links"]:
                    start, end = edge["from"], edge["to"]
                    a = rules[start["node"]]["outputs"][start["socket"]]["native"]["id"]
                    b = rules[end["node"]]["inputs"][end["socket"]]["native"]["id"]
                    port(nodes[start["node"]], a, True).Connect(port(nodes[end["node"]], b))
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
            created.Remove()
            self.document.StartUndo()
            undo_open = True
            self.document.InsertMaterial(created)
            self.document.AddUndo(c4d.UNDOTYPE_NEWOBJ, created)
            return {"material": name, "nodes": list(nodes)}
        except Exception:
            created.Remove()
            raise
        finally:
            if undo_open:
                self.document.EndUndo()
            c4d.documents.KillDocument(staging)
