"""宿主内运行的字典集成验证器；仅创建独立测试文档，不读取用户场景。"""

import json
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "plugins"))

from iris_relay import RelayAPI  # noqa: E402
from iris_relay.model import Material  # noqa: E402
from iris_relay.native import decode_value, encode_value, identify  # noqa: E402


def equal(actual, expected):
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return isinstance(actual, (int, float)) and abs(actual - expected) < 2e-5
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(equal(a, b) for a, b in zip(actual, expected, strict=True))
        )
    return actual == expected


def run_blender(job, api):
    import bpy
    from blender import BlenderAdapter

    cases = []
    for case in job["cases"]:
        material = BlenderAdapter(None, api).create_material(case["material"])
        material.name = case["id"]
        material.use_fake_user = True
        cases.append((case, material.name))
    path = str(Path(job["output"]).with_suffix(".blend"))
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)
    results = []
    for case, name in cases:
        actual = BlenderAdapter(bpy.data.materials[name], api).read_material()
        expected = case["material"]
        assert actual["links"] == expected["links"], (actual["links"], expected["links"])
        assert actual["output"] == expected["output"]
        checks = 0
        for node in expected["nodes"]:
            found = next(n for n in actual["nodes"] if n["id"] == node["id"])
            assert found["type"] == node["type"], (found["type"], node["type"])
            for port, value in node["inputs"].items():
                if isinstance(value, dict) and "texture" in value:
                    resource = actual["textures"][found["inputs"][port]["texture"]]
                    assert resource == expected["textures"][value["texture"]]
                else:
                    assert equal(found["inputs"][port], value), (
                        node["id"],
                        port,
                        found["inputs"][port],
                        value,
                    )
                checks += 1
        results.append(
            {
                "id": case["id"],
                "status": "ok",
                "checked_inputs": checks,
                "checked_links": len(actual["links"]) + 1,
                "material": actual,
            }
        )
    # 确认新建目标不修改源，并验证资源错误的失败清理。
    source = bpy.data.materials[cases[0][1]]
    before = BlenderAdapter(source, api).read_material()
    copy = BlenderAdapter(None, api).create_material(before)
    assert BlenderAdapter(source, api).read_material() == before
    bpy.data.materials.remove(copy)
    invalid = json.loads(json.dumps(before))
    next(iter(invalid["textures"].values()))["uri"] = str(ROOT / "__iris_missing_texture__.png")
    material_count, image_count = len(bpy.data.materials), len(bpy.data.images)
    try:
        BlenderAdapter(None, api).create_material(invalid)
    except ValueError:
        pass
    else:
        raise AssertionError("缺失资源必须阻断")
    assert (len(bpy.data.materials), len(bpy.data.images)) == (material_count, image_count)
    return {
        "status": "ok",
        "version": bpy.app.version_string,
        "saved_and_reopened": path,
        "cases": results,
        "source_unchanged": True,
        "failure_cleanup": True,
    }


def run_cinema4d(job, api):
    import c4d
    import maxon
    import redshift
    from probe_cinema4d import json_value

    space = maxon.Id("com.redshift3d.redshift4c4d.class.nodespace")
    output_asset = "com.redshift3d.redshift4c4d.node.output"

    def port(node, code, output=False):
        value = node.GetOutputs() if output else node.GetInputs()
        for segment in str(code).split("/"):
            value = value.FindChild(segment)
        assert value.IsValid(), code
        return value

    def set_rs(node, code, value):
        socket = port(node, code)
        old = socket.GetPortValue()
        if isinstance(value, list):
            if hasattr(old, "a"):
                value = maxon.ColorA(*value)
            elif hasattr(old, "r") or old is None:
                value = maxon.Color(*value)
            else:
                value = maxon.Vector(*value)
        elif type(old).__name__.startswith(("Int", "UInt")):
            value = int(value)
        socket.SetPortValue(value)

    def message(material, action, values):
        container = c4d.BaseContainer()
        container.SetInt32(1000, action)
        for key, value in values.items():
            if isinstance(value, c4d.BaseList2D):
                container.SetLink(key, value)
            else:
                container.SetInt32(key, value)
        material.Message(c4d.MSG_BASECONTAINER, container)
        return container

    def schema_for(data):
        response = api.handle(
            {
                "api_version": "1",
                "request_id": "native",
                "operation": "describe",
                "payload": {"renderer": data["renderer"]},
            }
        )
        assert response["status"] == "ok", response
        return response["result"]["dictionary"]

    def create(document, case):
        data = case["material"]
        schema = schema_for(data)
        is_rs = data["renderer"]["id"] == "redshift"
        material = c4d.BaseMaterial(c4d.Mmaterial if is_rs else 1033991)
        material.SetName(case["id"])
        document.InsertMaterial(material)
        graph = (
            maxon.GraphDescription.GetGraph(material, space, createEmpty=True) if is_rs else None
        )
        nodes = {}

        def write():
            for record in data["nodes"]:
                rule = schema["nodes"][record["type"]]
                if is_rs:
                    node = graph.AddChild(maxon.Id(record["id"]), maxon.Id(rule["native"]["id"]))
                else:
                    node = message(material, 1029, {2002: rule["native"]["id"]}).GetLink(
                        2011, document
                    )
                    assert node is not None, record["type"]
                    node.SetName(record["id"])
                nodes[record["id"]] = node

                def assign(code, value, node=node):
                    if is_rs:
                        set_rs(node, code, value)
                    else:
                        code = int(code)
                        old = node[code]
                        if isinstance(value, list):
                            value = c4d.Vector(*value)
                        elif isinstance(old, int) and not isinstance(old, bool):
                            value = int(value)
                        node[code] = value

                for code, value in rule["native"].get("properties", {}).items():
                    assign(code, value)
                for name, value in record["inputs"].items():
                    binding = rule["inputs"][name]["native"]
                    if value is None:
                        continue
                    if binding["kind"] == "resource":
                        resource = data["textures"][value["texture"]]
                        assert Path(resource["uri"]).is_file()
                        assert resource["coordinates"] == {"kind": "uv", "set": ""}
                        assign(binding["id"], resource["uri"])
                        assign(binding["colorspace_id"], resource["color_space"])
                        assign(binding["uv_id"], "")
                    else:
                        assign(binding["id"], encode_value(value, rule["inputs"][name]))
            by_id = {n["id"]: n for n in data["nodes"]}

            def binding(endpoint, direction):
                return schema["nodes"][by_id[endpoint["node"]]["type"]][direction][
                    endpoint["socket"]
                ]["native"]["id"]

            for link in data["links"]:
                source, target = link["from"], link["to"]
                if is_rs:
                    port(nodes[source["node"]], binding(source, "outputs"), True).Connect(
                        port(nodes[target["node"]], binding(target, "inputs"))
                    )
                else:
                    response = message(
                        material,
                        1031,
                        {
                            2001: nodes[source["node"]],
                            2002: binding(source, "outputs"),
                            2003: nodes[target["node"]],
                            2004: binding(target, "inputs"),
                        },
                    )
                    assert response.GetBool(2011), link
            endpoint = data["output"]
            if is_rs:
                output = graph.AddChild(maxon.Id("material_output"), maxon.Id(output_asset))
                port(nodes[endpoint["node"]], binding(endpoint, "outputs"), True).Connect(
                    port(output, output_asset + ".surface")
                )
            else:
                assert message(
                    material,
                    1033,
                    {
                        2001: nodes[endpoint["node"]],
                        2002: binding(endpoint, "outputs"),
                        2003: 537905099,
                    },
                ).GetBool(2011)

        if is_rs:
            with graph.BeginTransaction() as transaction:
                write()
                transaction.Commit()
        else:
            write()

    def verify(document, case):
        data = case["material"]
        schema = schema_for(data)
        material = next(m for m in document.GetMaterials() if m.GetName() == case["id"])
        is_rs = data["renderer"]["id"] == "redshift"
        if is_rs:
            graph = material.GetNodeMaterialReference().GetGraph(space)
            nodes = {
                str(n.GetId()): n
                for n in graph.GetViewRoot().GetChildren()
                if n.GetKind() == maxon.NODE_KIND.NODE
            }
        else:
            network = message(material, 1028, {})
            nodes = {}
            for index in range(network.GetInt32(2011)):
                node = network.GetLink(10000 + index, document)
                nodes[node.GetName()] = node
        checks = 0
        actual = {
            "schema_version": "1",
            "renderer": data["renderer"],
            "name": material.GetName(),
            "nodes": [],
            "links": [],
            "textures": {},
            "output": {},
        }
        bindings = {}
        # 直接读取原生类型和模式；不以预期的实现键代替识别证据。
        for name, node in nodes.items():
            native_id = (
                str(node.GetValue(maxon.nodes.AssetId)[0]) if is_rs else node[200]
            )  # C4DtoA gvarnoldshader.h: C4DAI_GVSHADER_TYPE
            if is_rs and native_id == output_asset:
                continue
            properties = {
                key
                for rule in schema["nodes"].values()
                if rule["native"]["id"] == native_id
                for key in rule["native"].get("properties", {})
            }

            def read(code, node=node):
                return json_value(port(node, code).GetPortValue() if is_rs else node[int(code)])

            match = identify(
                api, data["renderer"], native_id, {key: read(key) for key in properties}
            )
            rule = schema["nodes"][match["implementation"]]
            bindings[name] = rule
            record = {
                "id": name,
                "type": match["implementation"],
                "inputs": {},
                "outputs": list(rule["outputs"]),
                "metadata": {},
            }
            for key, definition in rule["inputs"].items():
                binding = definition["native"]
                if binding["kind"] == "resource":
                    uri = read(binding["id"])
                    if uri:
                        resource_id = "image_" + str(len(actual["textures"]))
                        actual["textures"][resource_id] = {
                            "uri": uri,
                            "color_space": read(binding["colorspace_id"]),
                            "coordinates": {"kind": "uv", "set": read(binding["uv_id"])},
                            "metadata": {},
                        }
                        record["inputs"][key] = {"texture": resource_id}
                    else:
                        record["inputs"][key] = None
                else:
                    record["inputs"][key] = decode_value(read(binding["id"]), definition)
            actual["nodes"].append(record)

        def endpoint_for(name, code, direction):
            rule = bindings[name]
            keys = [
                key
                for key, definition in rule[direction].items()
                if str(definition["native"]["id"]) == str(code)
            ]
            assert len(keys) == 1, (name, code, direction)
            return {"node": name, "socket": keys[0]}

        if is_rs:
            from probe_cinema4d import describe_ports

            for name, node in nodes.items():
                for socket in describe_ports(node.GetInputs()):
                    origins = port(node, socket["id"]).GetConnections(maxon.PORT_DIR.INPUT)
                    for origin, _wires in origins:
                        source_name = str(origin.GetParent().GetParent().GetId())
                        source = endpoint_for(source_name, str(origin.GetId()), "outputs")
                        if name not in bindings:
                            assert socket["id"] == output_asset + ".surface"
                            assert not actual["output"], "多个材质表面输出"
                            actual["output"] = source
                        else:
                            actual["links"].append(
                                {"from": source, "to": endpoint_for(name, socket["id"], "inputs")}
                            )
        else:
            for index in range(network.GetInt32(2012)):
                connection = network.GetContainer(20000 + index)
                actual["links"].append(
                    {
                        "from": endpoint_for(
                            connection.GetLink(0, document).GetName(),
                            connection.GetInt32(1),
                            "outputs",
                        ),
                        "to": endpoint_for(
                            connection.GetLink(2, document).GetName(),
                            connection.GetInt32(3),
                            "inputs",
                        ),
                    }
                )
            output_node = network.GetLink(2013, document)
            assert output_node is not None
            # 当前支持的表面节点只有一个已核验输出。
            output_name = output_node.GetName()
            assert len(bindings[output_name]["outputs"]) == 1
            actual["output"] = {
                "node": output_name,
                "socket": next(iter(bindings[output_name]["outputs"])),
            }
        actual = Material.parse(actual).to_dict()
        assert sorted(actual["links"], key=str) == sorted(data["links"], key=str)
        assert actual["output"] == data["output"]
        assert {n["id"]: n["type"] for n in actual["nodes"]} == {
            n["id"]: n["type"] for n in data["nodes"]
        }
        exported = actual
        for record in data["nodes"]:
            node = nodes[record["id"]]
            rule = schema["nodes"][record["type"]]
            for code, expected in rule["native"].get("properties", {}).items():
                actual = json_value(port(node, code).GetPortValue() if is_rs else node[int(code)])
                assert equal(actual, expected), (code, actual, expected)
            for name, expected in record["inputs"].items():
                binding = rule["inputs"][name]["native"]

                def read(code, node=node):
                    return json_value(port(node, code).GetPortValue() if is_rs else node[code])

                if binding["kind"] == "resource":
                    if expected is not None:
                        resource = data["textures"][expected["texture"]]
                        assert Path(read(binding["id"])) == Path(resource["uri"])
                        assert read(binding["colorspace_id"]) == resource["color_space"]
                        assert read(binding["uv_id"]) == ""
                else:
                    actual = decode_value(read(binding["id"]), rule["inputs"][name])
                    assert equal(actual, expected), (case["id"], name, actual, expected)
                checks += 1
        if is_rs:
            for link in data["links"]:
                source, target = link["from"], link["to"]
                origins = port(nodes[target["node"]], target["socket"]).GetConnections(
                    maxon.PORT_DIR.INPUT
                )
                assert any(
                    origin == port(nodes[source["node"]], source["socket"], True)
                    for origin, wires in origins
                ), link
            output_origins = port(
                nodes["material_output"], output_asset + ".surface"
            ).GetConnections(maxon.PORT_DIR.INPUT)
            assert any(
                origin == port(nodes[data["output"]["node"]], data["output"]["socket"], True)
                for origin, wires in output_origins
            )
        else:
            assert network.GetInt32(2012) == len(data["links"]), network.GetInt32(2012)
            assert network.GetLink(2013, document) == nodes[data["output"]["node"]]
            by_id = {n["id"]: n for n in data["nodes"]}
            actual_links = []
            for index in range(network.GetInt32(2012)):
                connection = network.GetContainer(20000 + index)
                actual_links.append(
                    (
                        connection.GetLink(0, document).GetName(),
                        connection.GetInt32(1),
                        connection.GetLink(2, document).GetName(),
                        connection.GetInt32(3),
                    )
                )
            for link in data["links"]:
                source, target = link["from"], link["to"]
                target_code = schema["nodes"][by_id[target["node"]]["type"]]["inputs"][
                    target["socket"]
                ]["native"]["id"]
                assert (
                    source["node"],
                    int(source["socket"]),
                    target["node"],
                    target_code,
                ) in actual_links, actual_links
        return {
            "id": case["id"],
            "status": "ok",
            "checked_inputs": checks,
            "checked_links": len(data["links"]) + 1,
            "material": exported,
        }

    document = c4d.documents.BaseDocument()
    path = str(Path(job["output"]).with_suffix(".c4d"))
    try:
        versions = message(document.FindSceneHook(1032309), 1040, {})
        runtime = {
            "redshift": str(redshift.GetCoreVersion()),
            "arnold": versions.GetString(2012),
            "c4dtoa": versions.GetString(2011),
        }
        c4d_version = str(c4d.GetC4DVersion())
        host_version = f"{c4d_version[:4]}.{int(c4d_version[4:5])}.{int(c4d_version[5:])}"
        for case in job["cases"]:
            expected = case["material"]["renderer"]
            assert expected["host_version"] == host_version, (expected, host_version)
            assert expected["version"] == runtime[expected["id"]], (expected, runtime)
        context = {
            name: json_value(document[getattr(c4d, name)])
            for name in dir(c4d)
            if name
            in {"DOCUMENT_COLOR_MANAGEMENT", "DOCUMENT_COLORPROFILE", "DOCUMENT_LINEARWORKFLOW"}
        }
        assert context == {
            "DOCUMENT_COLOR_MANAGEMENT": 0,
            "DOCUMENT_COLORPROFILE": 0,
            "DOCUMENT_LINEARWORKFLOW": 1,
        }, "当前验证限定 Basic / sRGB 输入 / Linear Workflow，不能推断 OCIO 场景"
        for case in job["cases"]:
            create(document, case)
        assert c4d.documents.SaveDocument(
            document, path, c4d.SAVEDOCUMENTFLAGS_DONTADDTORECENTLIST, c4d.FORMAT_C4DEXPORT
        )
    finally:
        c4d.documents.KillDocument(document)
    document = c4d.documents.LoadDocument(path, c4d.SCENEFILTER_MATERIALS, None)
    assert document is not None
    try:
        results = [verify(document, case) for case in job["cases"]]
    finally:
        c4d.documents.KillDocument(document)
    return {
        "status": "ok",
        "version": c4d.GetC4DVersion(),
        "color_context": context,
        "renderer_versions": runtime,
        "saved_and_reopened": path,
        "cases": results,
    }


def main():
    job = json.loads(Path(os.environ["IRIS_RELAY_JOB"]).read_text(encoding="utf-8"))
    try:
        api = RelayAPI.from_directory(ROOT / "dictionaries")
        result = run_blender(job, api) if job["host"] == "blender" else run_cinema4d(job, api)
    except Exception:
        result = {"status": "error", "traceback": traceback.format_exc()}
    result["run_id"] = job["run_id"]
    with Path(job["output"]).open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
