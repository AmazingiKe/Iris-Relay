"""保留图关系的双阶段转换；无法完整表达时返回阻断报告。"""

from copy import deepcopy

from .dictionary import Dictionary, transform, validate_value
from .model import Material, ValidationError


def issue(
    code: str,
    message: str,
    node: str | None = None,
    port: str | None = None,
    severity: str = "error",
) -> dict:
    return {"code": code, "message": message, "node": node, "port": port, "severity": severity}


def remap_endpoint(value: dict, maps: dict) -> dict:
    node, socket = value["node"], value["socket"]
    return {"node": node, "socket": maps.get(node, {}).get(socket, socket)}


def transcode(
    material: dict, dictionary: Dictionary, *, encode: bool, renderer: dict, issues: list
) -> dict:
    result = deepcopy(material)
    result["renderer"] = deepcopy(renderer)
    input_maps, output_maps, port_types = {}, {}, {}
    linked = {(link["to"]["node"], link["to"]["socket"]) for link in material["links"]}
    used_outputs = {(link["from"]["node"], link["from"]["socket"]) for link in material["links"]}
    used_outputs.add((material["output"]["node"], material["output"]["socket"]))
    for node in result["nodes"]:
        node_id = node["id"]
        if encode:
            try:
                entry = dictionary.target_node(node["type"])
            except ValidationError as exc:
                issues.append(issue(exc.code, str(exc), node_id))
                continue
        else:
            rule = dictionary.nodes.get(node["type"])
            entry = (rule["semantic"], rule) if rule else None
        if entry is None:
            issues.append(
                issue("UNSUPPORTED_NODE", f"字典无法表达节点类型：{node['type']}", node_id)
            )
            continue
        target_type, rule = entry
        if rule.get("approximation") and material["renderer"] != renderer:
            issues.append(
                issue(
                    "SHADING_MODEL_APPROXIMATION",
                    rule["approximation"],
                    node_id,
                    severity="warning",
                )
            )
        node["type"] = target_type
        input_maps[node_id], output_maps[node_id] = {}, {}
        source_inputs = deepcopy(node["inputs"])
        node["inputs"] = {}
        expected = set()
        for native, port_rule in rule["inputs"].items():
            source_name = port_rule["semantic"] if encode else native
            target_name = native if encode else port_rule["semantic"]
            expected.add(source_name)
            input_maps[node_id][source_name] = target_name
            semantic_type = (
                "color" if port_rule.get("transform") == "scalar_to_color" else port_rule["type"]
            )
            port_types[(node_id, "in", source_name)] = (
                semantic_type if encode else port_rule["type"]
            )
            if (
                not encode
                and port_rule.get("omit_if_default", False)
                and (node_id, source_name) not in linked
                and source_inputs.get(source_name, port_rule["default"]) == port_rule["default"]
            ):
                issues.append(
                    issue(
                        "NATIVE_DEFAULT_OMITTED",
                        "仅适用于源渲染器的原生默认值保留在源快照中",
                        node_id,
                        source_name,
                        "info",
                    )
                )
                continue
            if source_name in source_inputs:
                value = source_inputs[source_name]
                try:
                    if encode:
                        # 先验证语义值的类型，再执行变换，避免错误类型参与算术。
                        validate_value(
                            value,
                            {"type": semantic_type, "nullable": port_rule.get("nullable", False)},
                        )
                        value = transform(value, port_rule, encode=True)
                        validate_value(value, port_rule)
                    else:
                        validate_value(value, port_rule)
                        value = transform(value, port_rule)
                except ValidationError as exc:
                    issues.append(issue(exc.code, str(exc), node_id, source_name))
                    value = deepcopy(source_inputs[source_name])
            else:
                value = deepcopy(port_rule["default"])
                if not encode:
                    value = transform(value, port_rule)
                else:
                    issues.append(
                        issue(
                            "TARGET_DEFAULT",
                            "源材质缺少该语义，使用目标默认值",
                            node_id,
                            target_name,
                            "warning",
                        )
                    )
            node["inputs"][target_name] = value
            if (node_id, source_name) in linked and port_rule.get(
                "transform", "identity"
            ) != "identity":
                issues.append(
                    issue(
                        "LINK_TRANSFORM_UNSUPPORTED",
                        "连接值需要额外变换节点，首版禁止直接复制该连接",
                        node_id,
                        source_name,
                    )
                )
        for unknown in source_inputs.keys() - expected:
            issues.append(issue("UNSUPPORTED_INPUT", "字典缺少该输入的语义映射", node_id, unknown))
            node["inputs"][unknown] = deepcopy(source_inputs[unknown])
        for native, port_rule in rule["outputs"].items():
            source_name = port_rule["semantic"] if encode else native
            target_name = native if encode else port_rule["semantic"]
            output_maps[node_id][source_name] = target_name
            port_types[(node_id, "out", source_name)] = port_rule["type"]
        for output in node["outputs"]:
            if output not in output_maps[node_id]:
                if encode and (node_id, output) not in used_outputs:
                    issues.append(
                        issue(
                            "UNUSED_OUTPUT_OMITTED",
                            "目标缺少未使用的输出，原定义保留在源快照",
                            node_id,
                            output,
                            "info",
                        )
                    )
                    continue
                issues.append(
                    issue("UNSUPPORTED_OUTPUT", "字典缺少该输出的语义映射", node_id, output)
                )
        node["outputs"] = [
            output_maps[node_id].get(name, name)
            for name in node["outputs"]
            if not encode or name in output_maps[node_id] or (node_id, name) in used_outputs
        ]
    for original, link in zip(material["links"], result["links"], strict=True):
        source_type = port_types.get((original["from"]["node"], "out", original["from"]["socket"]))
        target_type = port_types.get((original["to"]["node"], "in", original["to"]["socket"]))
        if source_type and target_type and source_type != target_type:
            issues.append(
                issue(
                    "LINK_TYPE_MISMATCH",
                    "连接两端类型不一致，尚无显式转换规则",
                    original["to"]["node"],
                    original["to"]["socket"],
                )
            )
        link["from"] = remap_endpoint(original["from"], output_maps)
        link["to"] = remap_endpoint(original["to"], input_maps)
    result["output"] = remap_endpoint(material["output"], output_maps)
    return result


def convert(
    source: Material,
    source_dictionary: Dictionary,
    target_dictionary: Dictionary,
    target_renderer: dict,
) -> dict:
    issues = []
    intermediate = transcode(
        source.to_dict(),
        source_dictionary,
        encode=False,
        renderer={"id": "iris.semantic", "version": "1"},
        issues=issues,
    )
    target = None
    if not any(item["severity"] == "error" for item in issues):
        candidate = transcode(
            intermediate, target_dictionary, encode=True, renderer=target_renderer, issues=issues
        )
        if not any(item["severity"] == "error" for item in issues):
            try:
                target = Material.parse(candidate).to_dict()
            except ValidationError as exc:
                issues.append(issue(exc.code, str(exc)))
    return {
        "source": source.to_dict(),
        "intermediate": intermediate,
        "material": target,
        "report": {
            "status": "converted" if target is not None else "blocked",
            "dictionary_versions": {
                "source": source_dictionary.summary(),
                "target": target_dictionary.summary(),
            },
            "dictionary_fingerprints": {
                "source": source_dictionary.fingerprint,
                "target": target_dictionary.fingerprint,
            },
            "issues": issues,
            "calibration": {"status": "not_requested", "model_id": None, "changes": []},
            "appearance_verified": False,
        },
    }
