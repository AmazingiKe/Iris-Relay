"""用于独立宿主测试的材质图夹具，不代表用户材质。"""

from copy import deepcopy

from iris_relay.dictionary import Dictionary


def renderer(profile):
    """夹具使用实测环境版本；验证记录不是运行时字典的版本限制。"""
    environments = profile.get("evidence", {}).get("verified_environments", [])
    if environments:
        return deepcopy(environments[0])
    return {
        "id": profile["renderer"]["id"],
        "version": profile["renderer"]["versions"][0],
        "host": profile["host"]["id"],
        "host_version": profile["host"]["versions"][0],
        "node_system": profile["host"]["node_system"],
    }


def fixture(profile, texture="fixture.png", *, normal_semantic="normal.tangent"):
    if normal_semantic not in {"normal.tangent", "bump.height"}:
        raise ValueError("未知法线功能")
    nodes, meanings = [], {}
    included = {"surface.standard", "texture.image", normal_semantic}
    if normal_semantic == "bump.height":
        included.add("channel.red")
    dictionary = Dictionary.parse(profile)
    selected = {semantic: dictionary.target_node(semantic)[0] for semantic in included}
    for key, rule in profile["nodes"].items():
        if selected.get(rule["semantic"]) != key:
            continue
        node = {
            "id": rule["semantic"].replace(".", "_"),
            "type": key,
            "inputs": {port: deepcopy(value["default"]) for port, value in rule["inputs"].items()},
            "outputs": list(rule["outputs"]),
            "metadata": {},
        }
        for port, value in rule["inputs"].items():
            if value["semantic"] in {"metalness", "reflection.roughness", "coat.weight"}:
                node["inputs"][port] = {
                    "metalness": 0.25,
                    "reflection.roughness": 0.37,
                    "coat.weight": 0.2,
                }[value["semantic"]]
            if value["type"] == "texture":
                node["inputs"][port] = {"texture": "fixture"}
            if value["semantic"] in {"height", "height.scale"}:
                node["inputs"][port] = {"height": 0.4, "height.scale": 0.2}[value["semantic"]]
        nodes.append(node)
        meanings[rule["semantic"]] = (node, rule)

    def endpoint(semantic, direction, port_semantic):
        node, rule = meanings[semantic]
        return {
            "node": node["id"],
            "socket": next(
                key for key, value in rule[direction].items() if value["semantic"] == port_semantic
            ),
        }

    data = {
        "schema_version": "1",
        "name": "原生字典验证",
        "renderer": renderer(profile),
        "nodes": nodes,
        "links": [
            {
                "from": endpoint("texture.image", "outputs", "color"),
                "to": (
                    endpoint("channel.red", "inputs", "color")
                    if normal_semantic == "bump.height"
                    else endpoint("normal.tangent", "inputs", "normal.color")
                ),
            },
            {
                "from": endpoint(normal_semantic, "outputs", "normal"),
                "to": endpoint("surface.standard", "inputs", "normal"),
            },
        ],
        "output": endpoint("surface.standard", "outputs", "surface"),
        "textures": {
            "fixture": {
                "uri": texture,
                "color_space": "Raw",
                "coordinates": {"kind": "uv", "set": ""},
                "metadata": {},
            }
        },
        "metadata": {},
    }
    if normal_semantic == "bump.height":
        data["links"].append(
            {
                "from": endpoint("channel.red", "outputs", "value"),
                "to": endpoint("bump.height", "inputs", "height"),
            }
        )
    return data
