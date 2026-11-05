"""用于独立宿主测试的材质图夹具，不代表用户材质。"""

from copy import deepcopy


def renderer(profile):
    return {
        "id": profile["renderer"]["id"],
        "version": profile["renderer"]["versions"][0],
        "host": profile["host"]["id"],
        "host_version": profile["host"]["versions"][0],
        "node_system": profile["host"]["node_system"],
    }


def fixture(profile, texture="fixture.png"):
    nodes, meanings = [], {}
    for key, rule in profile["nodes"].items():
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

    return {
        "schema_version": "1",
        "name": "原生字典验证",
        "renderer": renderer(profile),
        "nodes": nodes,
        "links": [
            {
                "from": endpoint("texture.image", "outputs", "color"),
                "to": endpoint("surface.standard", "inputs", "base.color"),
            },
            {
                "from": endpoint("normal.tangent", "outputs", "normal"),
                "to": endpoint("surface.standard", "inputs", "normal"),
            },
        ],
        "output": endpoint("surface.standard", "outputs", "surface"),
        "textures": {
            "fixture": {
                "uri": texture,
                "color_space": "sRGB",
                "coordinates": {"kind": "uv", "set": ""},
                "metadata": {},
            }
        },
        "metadata": {},
    }
