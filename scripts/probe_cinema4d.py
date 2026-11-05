"""在独立 c4dpy 进程中核对 Redshift / C4DtoA 的真实节点和端口。"""

import json
import os
import re
import sys
from pathlib import Path

import c4d
import maxon
import redshift


def json_value(value):
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    name = type(value).__name__
    if name.startswith("Float"):
        return float(value)
    if name.startswith(("Int", "UInt")):
        return int(value)
    if name == "Bool":
        return bool(value)
    if name in {"String", "Id", "Url"}:
        return str(value)
    if hasattr(value, "r") and hasattr(value, "g") and hasattr(value, "b"):
        result = [float(value.r), float(value.g), float(value.b)]
        if hasattr(value, "a"):
            result.append(float(value.a))
        return result
    if hasattr(value, "x") and hasattr(value, "y") and hasattr(value, "z"):
        return [float(value.x), float(value.y), float(value.z)]
    return {"native_type": name, "unreadable": True}


def describe_ports(parent, prefix=""):
    result = []
    for port in parent.GetChildren():
        name = str(port.GetId())
        path = prefix + name
        children = list(port.GetChildren())
        try:
            value = json_value(port.GetPortValue())
        except Exception:
            value = {"native_type": "unreadable", "unreadable": True}
        try:
            label = str(port.GetValue(maxon.NODE.BASE.NAME) or name.rsplit(".", 1)[-1])
        except Exception:
            label = name.rsplit(".", 1)[-1]
        result.append({"id": path, "label": label, "default": value, "compound": bool(children)})
        result.extend(describe_ports(port, path + "/"))
    return result


def arnold_message(material, action, integers=()):
    message = c4d.BaseContainer()
    message.SetInt32(1000, action)
    for key, value in integers:
        message.SetInt32(key, value)
    material.Message(c4d.MSG_BASECONTAINER, message)
    return message


def probe():
    startup = Path(sys.executable).parent
    host_version = str(c4d.GetC4DVersion())
    host_version = (
        host_version[:4] + "." + str(int(host_version[4:5])) + "." + str(int(host_version[5:]))
    )
    host = {"id": "cinema4d", "version": host_version}
    result = {}
    document = c4d.documents.BaseDocument()
    try:
        material = c4d.BaseMaterial(c4d.Mmaterial)
        document.InsertMaterial(material)
        graph = maxon.GraphDescription.GetGraph(
            material, maxon.Id("com.redshift3d.redshift4c4d.class.nodespace"), createEmpty=True
        )
        schema = json.loads(
            (
                startup
                / "plugins/Redshift/res/nodes/com.redshift3d.redshift4c4d.nodes.module.db.json"
            ).read_text(encoding="utf-8-sig")
        )
        descriptions = schema["content"]["_descriptions"]
        assets = [
            key
            for key in descriptions
            if isinstance(key, str)
            and (
                key.startswith("com.redshift3d.redshift4c4d.nodes.core.")
                or key == "com.redshift3d.redshift4c4d.node.output"
            )
        ]
        repository = maxon.AssetInterface.GetUserPrefsRepository()
        discovered = repository.FindAssets(
            maxon.Id("net.maxon.node.assettype.nodetemplate"),
            maxon.Id(),
            maxon.Id(),
            maxon.ASSET_FIND_MODE.LATEST,
        )
        assets.extend(
            str(asset.GetId())
            for asset in discovered
            if str(asset.GetId()).startswith("com.redshift3d.redshift4c4d.nodes.core.")
        )
        rs = {
            "catalog_version": "1",
            "host": host,
            "renderer": {"id": "redshift", "version": str(redshift.GetCoreVersion())},
            "node_system": "redshift.maxon",
            "evidence": "installed_schema_and_runtime_instantiation",
            "scope": "安装的 Redshift 核心节点资产及输出；端口为默认配置",
            "nodes": {},
        }
        for index, asset in enumerate(sorted(set(assets))):
            try:
                with graph.BeginTransaction() as transaction:
                    node = graph.AddChild(maxon.Id("probe_" + str(index)), maxon.Id(asset))
                    if node.IsNullValue() or not node.IsValid():
                        raise ValueError("节点不可创建")
                    rs["nodes"][asset] = {
                        "id": asset,
                        "label": asset.rsplit(".", 1)[-1],
                        "status": "instantiated",
                        "inputs": describe_ports(node.GetInputs()),
                        "outputs": describe_ports(node.GetOutputs()),
                    }
                    node.Remove()
                    transaction.Commit()
            except Exception as exc:
                rs["nodes"][asset] = {"id": asset, "status": "unavailable", "error": str(exc)}
        result["redshift"] = rs
        material = c4d.BaseMaterial(1033991)
        if material is None:
            raise RuntimeError("本机 Arnold 材质插件不可用")
        document.InsertMaterial(material)
        hook = document.FindSceneHook(1032309)
        versions = arnold_message(hook, 1040)
        arnold = {
            "catalog_version": "1",
            "host": host,
            "renderer": {"id": "arnold", "version": versions.GetString(2012)},
            "integration_version": versions.GetString(2011),
            "node_system": "arnold.gvnodes",
            "evidence": "installed_headers_and_runtime_instantiation",
            "scope": "C4DtoA 头文件列出的内置 shader 和 custom shader；不含第三方程序生成节点",
            "nodes": {},
        }
        resources = startup / "plugins/C4DtoA/res"
        content = (resources / "arnold_node_ids.h").read_text(encoding="utf-8")
        shader_section = content.split("// shader\n", 1)[1].split("// shape\n", 1)[0]
        for symbol, identifier in re.findall(r"#define C4DAIN_(\w+)\s+(\d+)", shader_section):
            name, code = symbol.lower(), int(identifier)
            record = {"id": name, "code": code, "label": name, "status": "unavailable"}
            arnold["nodes"][name] = record
            try:
                response = arnold_message(material, 1029, [(2002, code)])
                node = response.GetLink(2011, document)
                if node is None:
                    raise RuntimeError("插件未返回节点")
                header = resources / "description" / ("ainode_" + name + ".h")
                parameters = (
                    re.findall(
                        r"C4DAIP_" + symbol + r"_(\w+)\s*=\s*(\d+)",
                        header.read_text(encoding="utf-8"),
                    )
                    if header.exists()
                    else []
                )
                descriptions = {}
                for bc, did, _group in node.GetDescription(c4d.DESCFLAGS_DESC_0):
                    if did.GetDepth() == 1:
                        descriptions[did[0].id] = {
                            "label": bc.GetString(c4d.DESC_NAME),
                            "datatype": did[0].dtype,
                        }
                inputs = []
                for parameter, param_id in parameters:
                    if "__" in parameter:
                        continue
                    param_code = int(param_id)
                    try:
                        default = json_value(node[param_code])
                    except Exception:
                        default = {"native_type": "unreadable", "unreadable": True}
                    inputs.append(
                        {
                            "id": parameter.lower(),
                            "code": param_code,
                            "default": default,
                            **descriptions.get(param_code, {}),
                        }
                    )
                outputs = [
                    {"id": str(port.GetMainID()), "code": port.GetMainID()}
                    for port in node.GetOutPorts()
                ]
                record.update(status="instantiated", inputs=inputs, outputs=outputs)
            except Exception as exc:
                record["error"] = str(exc)
        result["arnold"] = arnold
        context = {
            "color_management": document[c4d.DOCUMENT_COLOR_MANAGEMENT],
            "color_profile": document[c4d.DOCUMENT_COLORPROFILE],
            "linear_workflow": document[c4d.DOCUMENT_LINEARWORKFLOW],
        }
        rs["color_context"] = context
        arnold["color_context"] = context
    finally:
        c4d.documents.KillDocument(document)
    return result


if __name__ == "__main__":
    target = Path(os.environ["IRIS_RELAY_OUTPUT"])
    with target.open("x", encoding="utf-8") as stream:
        json.dump(probe(), stream, ensure_ascii=False, indent=2, allow_nan=False)
