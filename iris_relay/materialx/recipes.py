"""把原生可变配置编译为可审计标准子图；回导重新生成并逐项比对实际实现。"""

import base64
import json
from copy import deepcopy
from hashlib import sha256

import MaterialX as mx

from ..dictionary import Dictionary
from ..model import ValidationError, fields, json_value, parse_json
from .editor import equivalent_computation

DEFAULT_RAMP = {
    "color_mode": "RGB",
    "hue_interpolation": "NEAR",
    "interpolation": "LINEAR",
    "elements": [
        {"position": 0.0, "color": [0.0, 0.0, 0.0, 1.0]},
        {"position": 1.0, "color": [1.0, 1.0, 1.0, 1.0]},
    ],
}


def ramp_configuration(value):
    """检查完整配置；未知模式和重复位置先阻断，不能借保存配置冒充可执行转换。"""
    config = parse_json(value.encode("utf-8")) if isinstance(value, str) else deepcopy(value)
    json_value(config)
    fields(config, {"color_mode", "hue_interpolation", "interpolation", "elements"})
    if (
        config["color_mode"] != "RGB"
        or config["hue_interpolation"] not in {"NEAR", "FAR", "CW", "CCW"}
        or config["interpolation"] not in {"LINEAR", "EASE", "CONSTANT", "B_SPLINE"}
    ):
        raise ValidationError("渐变模式尚无已核验标准实现", "MATERIALX_RECIPE_UNSUPPORTED")
    elements = config["elements"]
    if not isinstance(elements, list) or not 2 <= len(elements) <= 32:
        raise ValidationError("渐变需要 2 至 32 个控制点", "MATERIALX_RECIPE_UNSUPPORTED")
    previous = -1.0
    for element in elements:
        fields(element, {"position", "color"})
        p, color = element["position"], element["color"]
        if type(p) not in (float, int) or not 0 <= p <= 1 or p <= previous:
            raise ValidationError("渐变位置须严格递增且位于 [0,1]", "MATERIALX_RECIPE_UNSUPPORTED")
        if (
            not isinstance(color, list)
            or len(color) != 4
            or any(type(v) not in (int, float) for v in color)
            or not 0 <= color[3] <= 1
        ):
            raise ValidationError("渐变颜色必须包含有效 RGBA", "MATERIALX_RECIPE_UNSUPPORTED")
        previous = p
    return config


def ramp_library(core, value):
    """每个配置生成独立多输出 NodeDef；颜色和 Alpha 都由官方 float 节点精确表达。"""
    config = ramp_configuration(value)
    serialized = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = sha256(serialized.encode("utf-8")).hexdigest()
    name = "iris_blender_color_ramp_" + digest
    doc = mx.createDocument()
    doc.setDataLibrary(core.library)
    nd = doc.addNodeDef("ND_" + name, "multioutput", name)
    for output in list(nd.getOutputs()):
        nd.removeOutput(output.getName())
    nd.addInput("factor", "float").setValue(0.5)
    nd.addOutput("color", "color3")
    nd.addOutput("alpha", "float")
    nd.setAttribute("iris_recipe", "blender.color_ramp")
    nd.setAttribute(
        "iris_recipe_config", base64.b64encode(serialized.encode("utf-8")).decode("ascii")
    )
    graph = doc.addNodeGraph("NG_" + name)
    graph.setNodeDefString(nd.getName())
    graph.setAttribute("iris_recipe", "blender.color_ramp")

    def node(category, datatype="float", **values):
        standard = core.library.getNodeDef("ND_" + category + "_" + datatype)
        result = graph.addNodeInstance(standard, graph.createValidChildName(category))
        for key, value in values.items():
            port = result.addInput(key, standard.getActiveInput(key).getType())
            if isinstance(value, str) and value == "__factor":
                port.setInterfaceName("factor")
            elif isinstance(value, mx.Node):
                port.setConnectedNode(value)
            else:
                port.setValueString(str(value))
        return result

    points, mode = deepcopy(config["elements"]), config["interpolation"]
    factor = "__factor"
    if mode == "B_SPLINE":
        # 样条会越过首尾控制点继续插值；补齐 0/1 的重复颜色而非提前返回端点颜色。
        if points[0]["position"] > 0:
            points.insert(0, dict(points[0], position=0.0))
        if points[-1]["position"] < 1:
            points.append(dict(points[-1], position=1.0))
        factor = node("clamp", **{"in": factor, "low": 0, "high": 1})
    segments = []
    for index in range(len(points) - 1):
        left, right = points[index : index + 2]
        t, weights = None, None
        if mode != "CONSTANT":
            t = node(
                "clamp",
                **{
                    "in": node(
                        "divide",
                        in1=node("subtract", in1=factor, in2=left["position"]),
                        in2=right["position"] - left["position"],
                    ),
                    "low": 0,
                    "high": 1,
                },
            )
        if mode == "EASE":
            t = node(
                "multiply",
                in1=node("multiply", in1=t, in2=t),
                in2=node("subtract", in1=3, in2=node("multiply", in1=2, in2=t)),
            )
        if mode == "B_SPLINE":
            t2 = node("multiply", in1=t, in2=t)
            t3 = node("multiply", in1=t2, in2=t)
            u = node("subtract", in1=1, in2=t)
            weights = [
                node(
                    "divide", in1=node("multiply", in1=node("multiply", in1=u, in2=u), in2=u), in2=6
                ),
                node(
                    "divide",
                    in1=node(
                        "add",
                        in1=node("subtract", in1=4, in2=node("multiply", in1=6, in2=t2)),
                        in2=node("multiply", in1=3, in2=t3),
                    ),
                    in2=6,
                ),
                node(
                    "divide",
                    in1=node(
                        "subtract",
                        in1=node(
                            "add",
                            in1=node("add", in1=1, in2=node("multiply", in1=3, in2=t)),
                            in2=node("multiply", in1=3, in2=t2),
                        ),
                        in2=node("multiply", in1=3, in2=t3),
                    ),
                    in2=6,
                ),
                node("divide", in1=t3, in2=6),
            ]
        segments.append((index, left, right, t, weights))
    channels = []
    # 1. 各分量共用同一因子和控制点顺序；Alpha 独立保留，不能从亮度推测。
    for channel in range(4):
        result = points[-1]["color"][channel]
        for index, left, right, t, weights in reversed(segments):
            if mode == "CONSTANT":
                segment = left["color"][channel]
                result = node(
                    "ifgreater",
                    value1=right["position"],
                    value2=factor,
                    in1=segment,
                    in2=result,
                )
                continue
            if weights is not None:
                controls = [
                    points[max(0, index - 1)],
                    left,
                    right,
                    points[min(len(points) - 1, index + 2)],
                ]
                terms = [
                    node("multiply", in1=p["color"][channel], in2=w)
                    for p, w in zip(controls, weights, strict=True)
                ]
                segment = node(
                    "clamp",
                    **{
                        "in": node(
                            "add",
                            in1=node("add", in1=terms[0], in2=terms[1]),
                            in2=node("add", in1=terms[2], in2=terms[3]),
                        ),
                        "low": 0,
                        "high": 1,
                    },
                )
            else:
                segment = node(
                    "add",
                    in1=node(
                        "multiply", in1=left["color"][channel], in2=node("subtract", in1=1, in2=t)
                    ),
                    in2=node("multiply", in1=right["color"][channel], in2=t),
                )
            result = node(
                "ifgreater", value1=factor, value2=right["position"], in1=result, in2=segment
            )
        channels.append(result if isinstance(result, mx.Node) else node("add", in1=result, in2=0))
    color = node("combine3", "color3", **{f"in{i + 1}": v for i, v in enumerate(channels[:3])})
    graph.addOutput("color", "color3").setConnectedNode(color)
    graph.addOutput("alpha", "float").setConnectedNode(channels[3])
    return doc, nd.getName(), serialized, digest


def normal_library(core, config):
    """编译已证实的默认 UV 别名，显式写出世界空间第零切线／副切线基底。"""
    fields(config, {"uv_set", "basis"})
    if (
        not isinstance(config["uv_set"], str)
        or not config["uv_set"]
        or config["basis"] != "render_default"
    ):
        raise ValidationError("法线 UV 配置不受支持", "MATERIALX_RECIPE_UNSUPPORTED")
    serialized = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = sha256(serialized.encode("utf-8")).hexdigest()
    name = "iris_blender_normal_map_" + digest
    doc = mx.createDocument()
    doc.setDataLibrary(core.library)
    nd = doc.addNodeDef("ND_" + name, "vector3", name)
    nd.addInput("color", "color3").setValueString("0.5, 0.5, 1.0")
    nd.addInput("strength", "float").setValue(1.0)
    nd.setAttribute("iris_recipe", "blender.normal_map")
    nd.setAttribute(
        "iris_recipe_config", base64.b64encode(serialized.encode("utf-8")).decode("ascii")
    )
    graph = doc.addNodeGraph("NG_" + name)
    graph.setNodeDefString(nd.getName())
    graph.setAttribute("iris_recipe", "blender.normal_map")
    color = graph.addNodeInstance(
        core.library.getNodeDef("ND_convert_color3_vector3"), "color_to_vector"
    )
    color.addInput("in", "color3").setInterfaceName("color")
    normal = graph.addNodeInstance(core.library.getNodeDef("ND_normalmap_float"), "normal_map")
    normal.addInput("in", "vector3").setConnectedNode(color)
    normal.addInput("scale", "float").setInterfaceName("strength")
    for category in ("tangent", "bitangent"):
        basis = graph.addNodeInstance(
            core.library.getNodeDef("ND_" + category + "_vector3"), category
        )
        basis.addInput("space", "string").setValue("world")
        basis.addInput("index", "integer").setValue(0)
        normal.addInput(category, "vector3").setConnectedNode(basis)
    graph.addOutput("out", "vector3").setConnectedNode(normal)
    return doc, nd.getName(), serialized, digest


FUNCTION_RECIPES = {
    "color_ramp": "ND_iris_color_ramp",
    "normal_map": "ND_iris_tangent_normal",
}


def recipe_interfaces():
    """声明渐变及命名法线的可变配置接口；不包含公式或宿主参数名称。"""
    doc = mx.createDocument()
    for recipe, name in FUNCTION_RECIPES.items():
        ramp = recipe == "color_ramp"
        nd = doc.addNodeDef(name, "multioutput" if ramp else "vector3", name.removeprefix("ND_"))
        nd.setAttribute("iris_function_version", "1")
        nd.addInput("configuration", "string").setValueString("{}")
        if ramp:
            for port in list(nd.getOutputs()):
                nd.removeOutput(port.getName())
            nd.addInput("factor", "float").setValue(0.5)
            nd.addOutput("color", "color3")
            nd.addOutput("alpha", "float")
        else:
            nd.addInput("color", "color3").setValueString("0.5, 0.5, 1")
            nd.addInput("strength", "float").setValue(1.0)
    return doc


def prepare_recipes(core, dictionary, source=None, document=None, deferred_errors=None):
    """只编译字典明确授权的配方；目标折叠须验证完整定义及子图，返回原生类型恢复表。"""
    recipes = dictionary.data.get("materialx_recipes", {})
    functional = dictionary.data.get("materialx_function_recipes", {})
    if not recipes and not functional:
        return source, dictionary, {}
    data, source = deepcopy(dictionary.data), deepcopy(source)
    data.pop("materialx_recipes", None)
    data.pop("materialx_function_recipes", None)
    originals = {key: deepcopy(data["nodes"][key]) for key in recipes}
    for key, recipe in recipes.items():
        if recipe == "blender.color_ramp":
            data["nodes"].pop(key)
    library = mx.createDocument()
    if data.get("materialx_extensions"):
        mx.readFromXmlString(library, data["materialx_extensions"])
    restore = {}

    def install(key, config, actual=None):
        recipe = recipes[key]
        generated, nd_name, encoded, digest = (
            ramp_library(core, config)
            if recipe == "blender.color_ramp"
            else normal_library(core, config)
        )
        if actual is not None:
            # 2. 只认可本核心从配置重新产生的计算，禁止通过自定义名称伪装为原生节点。
            for expected in generated.getChildren():
                observed = document.getChild(expected.getName())
                if observed is None or not equivalent_computation(observed, expected):
                    raise ValidationError(
                        "渐变配置与实际标准子图不一致", "MATERIALX_BINDING_INVALID"
                    )
            if actual.getName() != nd_name:
                raise ValidationError("渐变定义身份不匹配", "MATERIALX_BINDING_INVALID")
        runtime_key = key + "." + digest
        if runtime_key not in data["nodes"]:
            rule = deepcopy(originals[key])
            rule["semantic"] = "materialx." + nd_name
            if recipe == "blender.color_ramp":
                rule["inputs"]["color_ramp"]["default"] = encoded
            else:
                rule["inputs"]["uv_map"]["default"] = config["uv_set"]
                rule["inputs"]["Color"]["semantic"] = "color"
                rule["inputs"]["Strength"]["semantic"] = "strength"
                rule["outputs"]["Normal"]["semantic"] = "out"
            data["nodes"][runtime_key] = rule
            library.importLibrary(generated)
        restore[runtime_key] = key
        function = FUNCTION_RECIPES[recipe.removeprefix("blender.")]
        if function in functional:
            inputs = (
                {"factor": "factor"}
                if recipe == "blender.color_ramp"
                else {
                    "color": "color",
                    "strength": "strength",
                }
            )
            data.setdefault("materialx_function_exports", {})[nd_name] = {
                "definition": function,
                "inputs": inputs,
                "constants": {"configuration": encoded},
            }
        return runtime_key, encoded

    def prepare_function(node, function, specification):
        """按目标授权编译配置；值进入工作库，输入文件始终只携带接口和配置。"""
        port = node.getInput("configuration") or node.getNodeDef().getInput("configuration")
        if port is None or port.getType() != "string":
            raise ValidationError("功能配置缺少字符串接口", "MATERIALX_BINDING_INVALID")
        if any(port.getAttribute(k) for k in ("nodename", "nodegraph", "interfacename")):
            raise ValidationError(
                "功能配置需要静态数据，不能以连接代替", "MATERIALX_RECIPE_UNSUPPORTED"
            )
        config = parse_json(port.getValueString().encode("utf-8"))
        recipe = specification["recipe"]
        generated, nd_name, encoded, _ = (
            ramp_library(core, config) if recipe == "color_ramp" else normal_library(core, config)
        )
        # JSON 空白及键顺序不构成语义差异；只在工作副本规范化条件值。
        actual = node.getInput("configuration") or node.addInput("configuration", "string")
        actual.setValueString(encoded)
        native = specification.get("native")
        if native:
            install(native, config)
        else:
            library.importLibrary(generated)
        inputs = (
            {"factor": "factor"}
            if recipe == "color_ramp"
            else {
                "color": "color",
                "strength": "strength",
            }
        )
        entry = {
            "definition": nd_name,
            "inputs": inputs,
            "when": {"configuration": encoded},
            "kind": "native" if native else "recipe",
            "evidence": (
                "目标字典授权的配置配方；只在工作图编译。法线仍要求对应的默认 UV 基底，外观未验证"
            ),
        }
        choices = data.setdefault("materialx_function_implementations", {}).setdefault(function, [])
        if entry not in choices:
            choices.append(entry)
            if native:
                choices.append(dict(entry, kind="recipe"))

    if source is not None:
        linked = {(e["to"]["node"], e["to"]["socket"]) for e in source["links"]}
        for record in source["nodes"]:
            if record["type"] not in recipes:
                continue
            key = record["type"]
            if recipes[key] == "blender.normal_map":
                uv = record["inputs"].get("uv_map", "")
                if not uv:
                    continue
                binding = record.get("metadata", {}).get("uv_binding", {})
                owners = binding.get("owners")
                if (
                    binding.get("name") != uv
                    or not isinstance(owners, list)
                    or not owners
                    or any(
                        not isinstance(o, dict)
                        or o.get("render_default") != uv
                        or uv not in o.get("uv_sets", [])
                        for o in owners
                    )
                ):
                    raise ValidationError(
                        "命名法线 UV 尚无所有网格的渲染默认集证据", "NORMAL_UV_CONTEXT_REQUIRED"
                    )
                if (record["id"], "uv_map") in linked:
                    raise ValidationError(
                        "法线 UV 名称不能动态连接", "MATERIALX_RECIPE_UNSUPPORTED"
                    )
                record["type"], _ = install(key, {"uv_set": uv, "basis": "render_default"})
                continue
            if (record["id"], "color_ramp") in linked:
                raise ValidationError("渐变配置不能是动态连接", "MATERIALX_RECIPE_UNSUPPORTED")
            value = record["inputs"].get(
                "color_ramp", originals[key]["inputs"]["color_ramp"]["default"]
            )
            record["type"], encoded = install(key, value)
            record["inputs"]["color_ramp"] = encoded
    if document is not None:
        for nd in document.getNodeDefs():
            if (
                nd.getDocument() != document
                or nd.getAttribute("iris_recipe") not in recipes.values()
            ):
                continue
            try:
                value = base64.b64decode(
                    nd.getAttribute("iris_recipe_config"), validate=True
                ).decode("utf-8")
            except (ValueError, UnicodeError) as exc:
                raise ValidationError("渐变配置编码无效", "MATERIALX_BINDING_INVALID") from exc
            for key, recipe in recipes.items():
                if recipe == nd.getAttribute("iris_recipe"):
                    install(key, json.loads(value), nd)
        for node in document.getNodes():
            nd = node.getNodeDef()
            if nd is not None and nd.getName() in functional:
                try:
                    prepare_function(node, nd.getName(), functional[nd.getName()])
                except ValidationError as exc:
                    if deferred_errors is None:
                        raise
                    # 细节支路可能在功能映射时被归档；只有仍被主要输入使用才阻断。
                    deferred_errors[node.getNamePath()] = exc
    data["materialx_extensions"] = mx.writeToXmlString(library)
    return source, Dictionary.parse(data), restore
