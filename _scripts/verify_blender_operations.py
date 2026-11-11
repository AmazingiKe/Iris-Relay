"""将实际 Blender 渐变求值与导出的标准子图独立解释结果比较。"""

import argparse
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from _tests.test_materialx_hsv import evaluate  # noqa: E402
from iris_relay.materialx.core import MaterialXCore  # noqa: E402
from iris_relay.materialx.recipes import ramp_library  # noqa: E402


def main():
    """原生采样来自另一个只读宿主进程；失败与未支持分别报告，不把跳过计为成功。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    core, report = MaterialXCore(), {"cases": [], "passed": 0, "blocked": 0, "failed": 0}
    for record in json.loads(args.probe.read_text("utf-8"))["ramps"]:
        case = {"configuration": record["configuration"], "status": "blocked"}
        try:
            document, _, _, _ = ramp_library(core, record["configuration"])
            graph = next(
                g
                for g in document.getNodeGraphs()
                if g.getAttribute("iris_recipe") == "blender.color_ramp"
            )
            maximum = 0.0
            for sample in record["samples"]:
                factor = struct.unpack("f", struct.pack("f", sample["factor"]))[0]
                observed = evaluate(graph, {"factor": factor}, output_name="color")
                observed.append(evaluate(graph, {"factor": factor}, output_name="alpha"))
                maximum = max(
                    maximum, max(abs(a - b) for a, b in zip(observed, sample["rgba"], strict=True))
                )
            case.update(status="passed" if maximum < 2e-5 else "failed", max_error=maximum)
        except ValueError as exc:
            case["message"] = str(exc)
        report[case["status"]] += 1
        report["cases"].append(case)
    with args.out.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in report.items() if k != "cases"}))
    if report["failed"] or report["blocked"]:
        raise RuntimeError("仍有原生配置未通过，详见逐项报告")


if __name__ == "__main__":
    main()
