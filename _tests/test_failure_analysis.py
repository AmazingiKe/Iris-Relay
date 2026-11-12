"""失败归类保留证据和影响集，代表请求能够复现，不能把首错数当作修复收益。"""

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from _scripts.analyze_material_failures import analyze_reports, recheck_analysis, write_analysis

ROOT = Path(__file__).resolve().parents[1]


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def diagnostic(message="动态系数尚不支持", **changes):
    return dict(
        {
            "code": "MATERIALX_INPUT_UNSUPPORTED",
            "message": message,
            "severity": "error",
            "node": "control",
            "port": "weights",
        },
        **changes,
    )


def export_report(problem=None):
    """构造宿主控制层公开报告；源数据只是分析证据，不用于执行宿主操作。"""
    problem = problem or diagnostic()
    source = {
        "renderer": {"id": "cycles", "version": "5.3.0", "host": "blender"},
        "nodes": [
            {
                "id": "control",
                "type": "VectorMath.DOT_PRODUCT",
                "inputs": {"weights": [0.2, 0.3, 0.5]},
                "outputs": ["Value"],
            }
        ],
    }
    return {
        "status": "error",
        "issues": [problem],
        "export_response": {
            "api_version": "1",
            "status": "error",
            "error": None,
            "result": {
                "source": source,
                "document": None,
                "report": {"issues": [problem], "source_dictionary_fingerprint": "rules-a"},
            },
        },
    }


def test_summary_enrichment_deduplicates_envelopes_and_keeps_affected_cases(tmp_path):
    cases = []
    for case_id in ("material_0001", "material_0002"):
        report = export_report()
        save(tmp_path / (case_id + ".json"), report)
        cases.append({"id": case_id, "status": "error", "issues": report["issues"]})
    path = save(tmp_path / "summary.json", {"cases": cases})
    result = analyze_reports([path])
    assert result["totals"]["cases"] == 2
    assert result["totals"]["observations"] == 2
    assert len(result["groups"]) == 1
    group = result["groups"][0]
    assert len(group["affected_cases"]) == 2
    assert group["signature"]["node_type"] == "VectorMath.DOT_PRODUCT"
    assert group["signature"]["stage"] == "materialx.export"
    assert group["kind"] == "candidate"
    assert group["representative"]["request"]["operation"] == "materialx.export"
    assert "potential_successes" not in result


@pytest.mark.parametrize("difference", ["message", "type", "mode", "target", "fingerprint"])
def test_same_error_code_does_not_merge_distinct_conditions(tmp_path, difference):
    first, second = export_report(), export_report()
    payload = second["export_response"]["result"]
    if difference == "message":
        second = export_report(diagnostic("未知坐标来源"))
    elif difference == "type":
        payload["source"]["nodes"][0]["type"] = "DifferentNode"
    elif difference == "mode":
        first["source_snapshot"] = {
            "nodes": [
                {
                    "name": "control",
                    "type": "VectorMath",
                    "properties": {"operation": "DOT_PRODUCT"},
                }
            ]
        }
        second["source_snapshot"] = {
            "nodes": [
                {"name": "control", "type": "VectorMath", "properties": {"operation": "MULTIPLY"}}
            ]
        }
    elif difference == "target":
        second["target"] = {"id": "arnold", "host": "maya", "version": "7.2"}
    else:
        payload["report"]["source_dictionary_fingerprint"] = "rules-b"
    paths = [save(tmp_path / f"case-{i}.json", r) for i, r in enumerate([first, second])]
    assert len(analyze_reports(paths)["groups"]) == 2


def test_multiple_errors_and_warnings_are_counted_without_claiming_exhaustiveness(tmp_path):
    report = export_report()
    report["issues"] += [
        diagnostic("资源未验证", severity="warning", code="IMAGE_UNVERIFIED"),
        diagnostic("输出不支持", port="other"),
        diagnostic(),
    ]
    result = analyze_reports([save(tmp_path / "case.json", report)])
    assert result["totals"]["observations"] == 3
    assert result["totals"]["failed_cases"] == 1
    assert result["totals"]["warning_cases"] == 0
    assert result["coverage"] == "observed_only"
    assert {g["signature"]["severity"] for g in result["groups"]} == {"error", "warning"}


def test_missing_details_and_success_without_diagnostics_are_not_fabricated(tmp_path):
    path = save(
        tmp_path / "summary.json",
        {"cases": [{"id": "failed", "status": "error"}, {"id": "success", "status": "ok"}]},
    )
    result = analyze_reports([path])
    assert result["totals"]["cases_without_diagnostics"] == 1
    assert result["totals"]["failed_cases"] == 1
    assert result["groups"] == []
    assert result["cases"][0]["evidence_gaps"]


def test_top_level_protocol_error_and_host_read_failure_are_kept(tmp_path):
    path = save(
        tmp_path / "protocol.json",
        {
            "api_version": "1",
            "status": "error",
            "result": None,
            "error": {"code": "INVALID_REQUEST", "message": "请求无效"},
        },
    )
    host = export_report()
    host.pop("export_response")
    host["source_snapshot"] = {"nodes": []}
    result = analyze_reports([path, save(tmp_path / "host.json", host)])
    assert len(result["groups"]) == 2
    assert {g["signature"]["stage"] for g in result["groups"]} == {"unknown", "host.read"}
    assert all(g["representative"]["request"] is None for g in result["groups"])


def test_preservation_reports_count_original_blockers_instead_of_preservation_success(tmp_path):
    path = save(
        tmp_path / "summary.json",
        {
            "cases": [
                {
                    "id": "native-only",
                    "document_saved": True,
                    "preservation_only": True,
                    "conversion_supported": False,
                    "original_issues": [diagnostic()],
                    "preservation_issues": [
                        {"code": "PRESERVED", "severity": "info", "message": "已保存"}
                    ],
                }
            ]
        },
    )
    result = analyze_reports([path])
    assert result["totals"]["failed_cases"] == 1
    assert result["groups"][0]["signature"]["code"] == "MATERIALX_INPUT_UNSUPPORTED"
    assert result["groups"][0]["representative"]["request"] is None


def test_conversion_replay_uses_actual_document_and_target(tmp_path):
    document = '<materialx version="1.39" />'
    target = {"id": "arnold", "host": "maya", "version": "7.2"}
    report = {
        "id": "convert",
        "status": "error",
        "target": target,
        "response": {
            "api_version": "1",
            "status": "error",
            "result": {
                "source": document,
                "material": None,
                "intermediate": None,
                "report": {"issues": [diagnostic()], "target_dictionary_fingerprint": "target-a"},
            },
        },
    }
    result = analyze_reports([save(tmp_path / "case.json", report)])
    request = result["groups"][0]["representative"]["request"]
    assert request["operation"] == "materialx.convert"
    assert request["payload"] == {"document": document, "target": target}
    report.pop("target")
    unavailable = analyze_reports([save(tmp_path / "no-target.json", report)])
    assert unavailable["groups"][0]["representative"]["request"] is None


def test_output_does_not_overwrite_and_keeps_stable_group_ids(tmp_path):
    path = save(tmp_path / "report.json", export_report())
    before = path.read_bytes()
    first = analyze_reports([path])
    out = tmp_path / "analysis"
    write_analysis(first, out)
    saved = json.loads((out / "analysis.json").read_text("utf-8"))
    assert saved["groups"][0]["id"] == analyze_reports([path])["groups"][0]["id"]
    assert list(out.glob("replay-*.json"))
    assert path.read_bytes() == before
    with pytest.raises(FileExistsError):
        write_analysis(first, out)


@pytest.mark.parametrize(
    "content",
    [
        '{"cases": null}',
        '{"cases": [42]}',
        '{"cases": []',
        '{"cases": [], "cases": []}',
        "{}",
        '{"unrelated": []}',
        '{"report": []}',
    ],
)
def test_invalid_input_is_reported_instead_of_being_silently_skipped(tmp_path, content):
    path = tmp_path / "bad.json"
    path.write_text(content, "utf-8")
    with pytest.raises(ValueError):
        analyze_reports([path])


def test_detail_failure_duplicate_cases_and_repeated_inputs_are_rejected(tmp_path):
    path = save(tmp_path / "summary.json", {"cases": [{"id": "x", "status": "error"}]})
    (tmp_path / "x.json").write_text("{", "utf-8")
    with pytest.raises(ValueError):
        analyze_reports([path])
    (tmp_path / "x.json").unlink()
    with pytest.raises(ValueError):
        analyze_reports([path, path])
    save(path, {"cases": [{"id": "x", "status": "error"}] * 2})
    with pytest.raises(ValueError):
        analyze_reports([path])


def test_issue_without_message_is_not_merged_across_cases(tmp_path):
    problem = diagnostic("")
    paths = [save(tmp_path / f"case-{i}.json", export_report(problem)) for i in range(2)]
    result = analyze_reports(paths)
    assert len(result["groups"]) == 2
    assert all("message" in g["evidence_gaps"] for g in result["groups"])


def test_cli_creates_replay_that_reproduces_real_public_api_failure(tmp_path):
    """使用官方公开 API 的真实坏文档响应，复验生成请求会产生同一阻断。"""
    from iris_relay import RelayAPI

    target = json.loads((ROOT / "_examples/dictionaries/reference.rough.json").read_text("utf-8"))
    api = RelayAPI([target])
    renderer = {"id": "reference.rough", "version": "1.0"}
    original = {
        "api_version": "1",
        "request_id": "source",
        "operation": "materialx.convert",
        "payload": {"document": "<broken>", "target": renderer},
    }
    response = api.handle(original)
    assert response["status"] == "error"
    report = save(
        tmp_path / "report.json",
        {"id": "broken", "status": "error", "target": renderer, "response": response},
    )
    before = report.read_bytes()
    out = tmp_path / "result"
    process = subprocess.run(
        [
            sys.executable,
            str(ROOT / "_scripts/analyze_material_failures.py"),
            "--reports",
            str(report),
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    request = json.loads(next(out.glob("replay-*.json")).read_text("utf-8"))
    reproduced = api.handle(request)
    assert (
        reproduced["result"]["report"]["issues"][0]["code"]
        == response["result"]["report"]["issues"][0]["code"]
    )
    assert report.read_bytes() == before


def test_analysis_does_not_mutate_input_objects(tmp_path):
    data = export_report()
    original = deepcopy(data)
    analyze_reports([save(tmp_path / "report.json", data)])
    assert data == original


def test_affected_recheck_visits_every_case_once_and_keeps_host_failures_unverified(tmp_path):
    source = export_report()
    source["issues"].append(diagnostic("第二问题", port="second"))
    host = {"status": "error", "issues": [diagnostic()], "source_snapshot": {"nodes": []}}
    paths = [
        save(tmp_path / name, report)
        for name, report in [
            ("one.json", source),
            ("two.json", export_report()),
            ("host.json", host),
        ]
    ]
    analysis = analyze_reports(paths)

    class Service:
        calls = []

        def handle(self, request):
            self.calls.append(request)
            return {"status": "ok", "result": {"report": {"issues": []}}}

    service = Service()
    checked = recheck_analysis(analysis, service)
    assert len(service.calls) == 2
    assert checked["totals"] == {"checked": 2, "skipped": 1, "still_observed": 0}
    assert checked["scope"] == "core_only"
    assert any(c["status"] == "not_replayable" for c in checked["cases"])
    group = next(g for g in analysis["groups"] if g["signature"]["message"] == "第二问题")
    assert recheck_analysis(analysis, Service(), [group["id"]])["totals"]["checked"] == 1
    with pytest.raises(ValueError, match="分组"):
        recheck_analysis(analysis, Service(), ["unknown-group"])


def test_recheck_keeps_original_diagnostic_and_detects_changed_evidence(tmp_path):
    path = save(tmp_path / "report.json", export_report())
    analysis = analyze_reports([path])

    class Service:
        def handle(self, request):
            return export_report()["export_response"]

    checked = recheck_analysis(analysis, Service())
    assert checked["totals"]["still_observed"] == 1
    assert checked["cases"][0]["still_observed_groups"]
    save(path, {"status": "ok", "issues": []})
    with pytest.raises(ValueError, match="改变"):
        recheck_analysis(analysis, Service())


def test_summary_and_detail_cannot_disagree_about_case_target(tmp_path):
    """同名报告属于另一目标时不能拼接证据，避免生成发送给错误目标的复现请求。"""
    detail = export_report()
    detail["target"] = {"id": "arnold", "host": "maya"}
    save(tmp_path / "one.json", detail)
    path = save(
        tmp_path / "summary.json",
        {"cases": [{"id": "one", "status": "error", "target": {"id": "redshift"}}]},
    )
    with pytest.raises(ValueError, match="目标"):
        analyze_reports([path])


def test_unfinished_case_is_reported_as_missing_evidence(tmp_path):
    path = save(tmp_path / "summary.json", {"cases": [{"id": "not-yet-checked"}]})
    result = analyze_reports([path])
    assert result["totals"]["unknown_cases"] == 1
    assert result["totals"]["cases_without_diagnostics"] == 1
    assert result["cases"][0]["status"] == "unknown"
