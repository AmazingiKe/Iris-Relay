"""离线归并材质诊断、保存影响集与代表请求；只报告已观察问题，不预测修复收益。"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from iris_relay.protocol import parse_json  # noqa: E402

FAMILY_PREFIXES = {
    "functions": ("MATERIALX_FUNCTION_", "MATERIALX_DETAIL_", "MATERIALX_RECIPE_"),
    "coordinates": ("UV_", "NORMAL_UV_", "MATERIALX_COORDINATE_"),
    "types_and_connections": ("LINK_", "SOCKET_", "MATERIALX_INPUT_", "MATERIALX_OUTPUT_"),
    "resources": ("TEXTURE_", "IMAGE_", "MATERIALX_RESOURCE_", "MATERIALX_COLORSPACE_"),
    "host_interface": ("NATIVE_", "HOST_"),
    "graph": ("UNSUPPORTED_NODE", "MATERIALX_NODE_", "MATERIALX_CYCLE", "MATERIALX_LIMIT"),
}


def canonical(value):
    """用稳定 JSON 表示条件；不删除实例名或路径来猜测两个错误等价。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()[:20]


def read_report(path):
    """读取已存在的 JSON 证据；坏文件、重复键和超限输入明确失败。"""
    try:
        value = parse_json(path.read_bytes())
        if not isinstance(value, dict):
            raise ValueError("报告必须为 JSON 对象")
        if (
            not {"cases", "status", "report", "issues", "original_issues", "result", "error"}
            & value.keys()
        ):
            raise ValueError("无法识别报告结构")
        return value
    except ValueError as exc:
        raise ValueError(f"无法分析报告 {path}：{exc}") from exc


def _payload(response):
    result = response.get("result", response)
    return result if isinstance(result, dict) else {}


def _issues(report):
    """兼容核心响应、控制层和原生保留报告；信息提示不作为待修复问题。"""
    payload = _payload(report)
    diagnostics = report.get("original_issues", report.get("issues", []))
    core_report = payload.get("report", {})
    if not isinstance(core_report, dict):
        raise ValueError("核心报告必须为对象")
    nested = core_report.get("issues", [])
    if not isinstance(diagnostics, list) or not isinstance(nested, list):
        raise ValueError("诊断必须为列表")
    result = []
    for problem in [*diagnostics, *nested]:
        if not isinstance(problem, dict) or not isinstance(problem.get("code"), str):
            raise ValueError("诊断缺少字符串错误码")
        severity = problem.get("severity", "error")
        if severity not in {"info", "warning", "error"}:
            raise ValueError("诊断严重性无效")
        if severity != "info":
            result.append(dict(problem, severity=severity))
    error = report.get("error")
    if error and not any(p["severity"] == "error" for p in result):
        if not isinstance(error, dict) or not isinstance(error.get("code"), str):
            raise ValueError("顶层错误缺少字符串错误码")
        result.append(dict(error, severity="error"))
    return result


def _operation(payload):
    if isinstance(payload.get("source"), dict) and "document" in payload:
        return "materialx.export"
    if isinstance(payload.get("source"), str) and "material" in payload:
        return "materialx.convert"
    return "unknown"


def _evidence(report):
    """识别报告中实际存在的核心步骤；宿主外围失败仅定位到流程，不猜测 API 行号。"""
    responses = []
    for key, operation in (
        ("export_response", "materialx.export"),
        ("conversion_response", "materialx.convert"),
        ("validation_response", "materialx.validate"),
        ("validation", "materialx.validate"),
        ("response", None),
    ):
        response = report.get(key)
        if isinstance(response, dict):
            responses.append((operation or _operation(_payload(response)), response))
    if not responses:
        operation = _operation(_payload(report))
        if operation == "unknown" and report.get("source_snapshot") is not None:
            operation = "host.read"
        responses.append((operation, report))
    primary = next(
        (
            (stage, response)
            for stage, response in responses
            if stage in {"materialx.export", "materialx.convert"}
        ),
        responses[0],
    )
    return responses, primary


def _entries(path):
    """批次只读取清单声明的同目录详细报告，不递归扫描混入其他作业。"""
    document = read_report(path)
    cases = document.get("cases", [document])
    if not isinstance(cases, list) or any(not isinstance(c, dict) for c in cases):
        raise ValueError(f"{path} 的 cases 必须为对象列表")
    seen = set()
    for index, summary in enumerate(cases):
        case_id = summary.get("id", path.stem if "cases" not in document else str(index))
        if not isinstance(case_id, str) or not case_id:
            raise ValueError("材质编号必须为非空字符串")
        identity = canonical([case_id, summary.get("target")])
        if identity in seen:
            raise ValueError(f"清单重复记录相同材质和目标：{case_id}")
        seen.add(identity)
        detail, detail_path = {}, None
        candidate = path.parent / (case_id + ".json")
        if "cases" in document and candidate.resolve().parent == path.parent.resolve():
            if candidate.is_file() and candidate.resolve() != path:
                detail, detail_path = read_report(candidate), candidate
                if (
                    "target" in detail
                    and "target" in summary
                    and detail["target"] != summary["target"]
                ):
                    raise ValueError("摘要与详细报告的目标不一致：" + case_id)
        # 摘要保留身份和目标；详细报告的嵌套响应不被摘要覆盖。
        record = dict(detail, **summary)
        # 某些摘要没有诊断或只保存首错；详细报告已观察的其他问题仍须保留。
        yield case_id, record, detail, detail_path


def _source_node(source, snapshot, problem):
    """只按报告提供的精确身份寻找证据，不按显示名相似度或处理后的名称反查。"""
    identity = problem.get("node")

    def find(data):
        if isinstance(data, dict) and identity is not None:
            return next(
                (n for n in data.get("nodes", []) if n.get("id", n.get("name")) == identity), {}
            )
        return {}

    node, native = find(source), find(snapshot)
    return node, native


def _signature(report, primary, stage, problem):
    """分组条件包含环境、字典与实际模式；缺少信息照实记录，分组仍只是候选。"""
    payload = _payload(primary)
    source, snapshot = payload.get("source"), report.get("source_snapshot", {})
    node, native = _source_node(source, snapshot, problem)
    value = node.get("inputs", {}).get(problem.get("port"))
    fingerprints = {
        k: v for k, v in payload.get("report", {}).items() if k.endswith("dictionary_fingerprint")
    }
    return {
        "stage": stage,
        "code": problem["code"],
        "message": problem.get("message", ""),
        "severity": problem["severity"],
        "port": problem.get("port"),
        "node_type": node.get("type", native.get("type")),
        "properties": {
            k: v
            for k, v in native.get("properties", {}).items()
            if k not in {"name", "label", "location", "width", "height", "select"}
        },
        "input_type": type(value).__name__ if value is not None else None,
        "source_environment": source.get("renderer")
        if isinstance(source, dict)
        else snapshot.get("renderer"),
        "target_environment": report.get("target")
        or (payload.get("material") or {}).get("renderer"),
        "dictionary_fingerprints": fingerprints,
    }


def _replay(report, primary, operation):
    """只重建有完整来源的核心请求；宿主快照不冒充可执行材质，也不猜测目标环境。"""
    payload = _payload(primary)
    source = payload.get("source")
    if operation == "materialx.export" and isinstance(source, dict):
        data = {"material": source}
    elif operation == "materialx.convert" and isinstance(source, str):
        target = report.get("target") or (payload.get("material") or {}).get("renderer")
        if not target:
            return None
        data = {"document": source, "target": target}
        if report.get("material_name"):
            data["material_name"] = report["material_name"]
    else:
        return None
    return {
        "api_version": "1",
        "request_id": "failure-replay",
        "operation": operation,
        "payload": data,
    }


def analyze_reports(paths):
    """归并已观察诊断并保留所有受影响案例；同错误码不等于同根因。输入文件只读。"""
    paths = sorted(Path(p).resolve() for p in paths)
    if len(set(paths)) != len(paths):
        raise ValueError("同一报告不能重复输入")
    cases, groups, observations, evidence_paths = [], {}, 0, set(paths)
    # 1. 读取摘要和详细证据，精确去除同一案例中被多层报告重复携带的诊断。
    for path in paths:
        for case_id, record, detail, detail_path in _entries(path):
            if detail_path:
                evidence_paths.add(detail_path.resolve())
            responses, (operation, primary) = _evidence(record)
            ref = digest([str(path), case_id, record.get("target")])
            found = {}
            for stage, response in responses:
                for problem in _issues(response):
                    found[canonical(problem)] = (stage, problem)
            for problem in [*_issues(detail), *_issues(record)]:
                key = canonical(problem)
                if key not in found:
                    stage = "host.workflow" if operation != "unknown" else "unknown"
                    if (
                        not any(
                            k in record
                            for k in ("response", "export_response", "conversion_response")
                        )
                        and record.get("source_snapshot") is not None
                    ):
                        stage = "host.read"
                    found[key] = (stage, problem)
            failed = record.get("status") == "error" or record.get("preservation_only", False)
            failed |= any(p["severity"] == "error" for _, p in found.values())
            status = "error" if failed else record.get("status", primary.get("status", "unknown"))
            gaps = []
            if (failed or status == "unknown") and not found:
                gaps.append("diagnostics")
            case = {
                "ref": ref,
                "id": case_id,
                "name": record.get("name"),
                "report": str(detail_path or path),
                "summary": str(path),
                "target": record.get("target"),
                "status": status,
                "groups": [],
                "evidence_gaps": gaps,
            }
            cases.append(case)
            # 2. 精确条件相同才归入候选组；消息缺失时不跨案例合并。
            for stage, problem in found.values():
                signature = _signature(record, primary, stage, problem)
                missing = [key for key in ("message", "node_type") if not signature[key]]
                if stage == "unknown":
                    missing.append("stage")
                key = digest([signature, ref if not signature["message"] else None])
                request = _replay(record, primary, operation)
                representative = {
                    "case_ref": ref,
                    "report": str(detail_path or path),
                    "request": request,
                    "scope": "full_source",
                    "verified": False,
                }
                group = groups.setdefault(
                    key,
                    {
                        "id": key,
                        "kind": "candidate",
                        "signature": signature,
                        "observations": 0,
                        "affected_cases": [],
                        "evidence_gaps": missing,
                        "representative": representative,
                    },
                )
                group["family_hint"] = next(
                    (
                        name
                        for name, prefixes in FAMILY_PREFIXES.items()
                        if problem["code"].startswith(prefixes)
                    ),
                    "unclassified",
                )
                group["observations"] += 1
                observations += 1
                if ref not in group["affected_cases"]:
                    group["affected_cases"].append(ref)
                    case["groups"].append(key)
                if group["representative"]["request"] is None and request is not None:
                    group["representative"] = representative
    # 3. 数量只描述已观察证据；没有穷举或自动最小化成功的暗示。
    return {
        "schema_version": "1",
        "coverage": "observed_only",
        "inputs": [
            {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for path in sorted(evidence_paths)
        ],
        "totals": {
            "cases": len(cases),
            "failed_cases": sum(c["status"] == "error" for c in cases),
            "warning_cases": sum(c["status"] == "warning" for c in cases),
            "unknown_cases": sum(c["status"] == "unknown" for c in cases),
            "cases_without_diagnostics": sum(bool(c["evidence_gaps"]) for c in cases),
            "observations": observations,
            "groups": len(groups),
        },
        "cases": cases,
        "groups": sorted(groups.values(), key=lambda g: (-len(g["affected_cases"]), g["id"])),
    }


def recheck_analysis(analysis, service, group_ids=None):
    """通过公开服务复测选中分组的全部影响案例；只证明核心行为，不能替代宿主重读。"""
    # 1. 固定旧报告证据；修改后的源码和字典可以参与复测，旧报告不能被替换。
    for item in analysis["inputs"]:
        if hashlib.sha256(Path(item["path"]).read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("分析来源已经改变，请重新归类后复测：" + item["path"])
    groups = {g["id"]: g for g in analysis["groups"]}
    selected = set(groups if group_ids is None else group_ids)
    if selected - groups.keys():
        raise ValueError("选择了不存在的分组")
    affected = {ref for key in selected for ref in groups[key]["affected_cases"]}
    cases, checked, skipped, still_observed = [], 0, 0, 0
    summaries = sorted({c["summary"] for c in analysis["cases"] if c["ref"] in affected})
    # 2. 每个案例只请求一次；一个案例同时受多个问题影响时仍保留逐组比对。
    for summary in summaries:
        for case_id, record, _, _ in _entries(Path(summary)):
            ref = digest([summary, case_id, record.get("target")])
            if ref not in affected:
                continue
            _, (operation, primary) = _evidence(record)
            request = _replay(record, primary, operation)
            result = {"case_ref": ref, "id": case_id, "still_observed_groups": []}
            if request is None:
                skipped += 1
                result["status"] = "not_replayable"
                cases.append(result)
                continue
            response = service.handle(request)
            problems = _issues(response)
            checked += 1
            for key in sorted(selected):
                group = groups[key]
                expected = group["signature"]
                if ref in group["affected_cases"] and any(
                    all(
                        p.get(field) == expected[field]
                        for field in ("code", "message", "port", "severity")
                    )
                    for p in problems
                ):
                    result["still_observed_groups"].append(key)
            still_observed += bool(result["still_observed_groups"])
            result.update(status=response["status"], issues=problems)
            cases.append(result)
    return {
        "scope": "core_only",
        "totals": {"checked": checked, "skipped": skipped, "still_observed": still_observed},
        "cases": cases,
    }


def write_analysis(result, output):
    """排他创建分析目录和代表请求；影响清单留在 analysis.json，不覆盖输入或旧分析。"""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    for group in result["groups"]:
        request = group["representative"]["request"]
        if request is not None:
            (output / ("replay-" + group["id"] + ".json")).write_text(
                json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    (output / "analysis.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main():
    """从明确指定的报告生成候选问题组；无诊断与不可复现的案例仍保留在输出中。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports", type=Path, nargs="+", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--recheck", action="store_true", help="通过公开 API 复测全部受影响案例")
    parser.add_argument("--group", action="append", help="只复测指定分组，可重复提供")
    parser.add_argument("--dictionaries", type=Path, default=ROOT / "dictionaries")
    args = parser.parse_args()
    try:
        result = analyze_reports(args.reports)
        if args.group and not args.recheck:
            raise ValueError("--group 需要与 --recheck 一起使用")
        rechecked = None
        if args.recheck:
            from iris_relay import RelayAPI

            rechecked = recheck_analysis(
                result, RelayAPI.from_directory(args.dictionaries), args.group
            )
        write_analysis(result, args.out)
        if rechecked is not None:
            (args.out / "recheck.json").write_text(
                json.dumps(rechecked, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    except (OSError, ValueError) as exc:
        parser.exit(1, str(exc) + "\n")
    print(json.dumps(result["totals"], ensure_ascii=False))


if __name__ == "__main__":
    main()
