"""独立核对全量文档：标准文件字节一致，原生保留文件的节点、端口和连线可追溯。"""

import argparse
import base64
import json
import math
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from iris_relay import RelayAPI  # noqa: E402
from plugins.core import request  # noqa: E402


def decode(element, name):
    """按文档声明读取原生元数据，保留 Unicode 和 XML 特殊字符。"""
    value = element.get(name, "")
    if element.get(name + "_encoding", "").startswith("base64"):
        value = base64.b64decode(value).decode("utf-8")
    return value


def verify_native(root, snapshot):
    """直接从实际 XML 接口与连线还原逻辑边，不把快照自身当作拓扑证明。"""
    definitions = {d.get("name"): d for d in root.findall("nodedef")}
    nodes = {n.get("name"): n for n in root if n.get("nodedef")}
    native = {decode(n, "iris_native_id"): n for n in nodes.values() if n.get("iris_native_id")}
    expected = {n["id"]: n for n in snapshot["nodes"]}
    assert set(native) == set(expected)
    edges = []
    for identifier, node in native.items():
        nd = definitions[node.get("nodedef")]
        declared = {decode(p, "iris_native_socket"): p for p in nd.findall("input")}
        assert set(declared) == {p["id"] for p in expected[identifier]["inputs"]}
        ports = {p.get("name"): p for p in nd.findall("input")}
        bindings = {p.get("name"): p for p in node.findall("input")}
        for specification in expected[identifier]["inputs"]:
            value = specification.get("value")
            if value is None:
                continue
            actual = bindings.get(declared[specification["id"]].get("name"))
            if actual is not None and actual.get("nodename"):
                continue
            assert actual is not None
            found = actual.get("value", "")
            if actual.get("iris_native_value_encoding") == "base64-utf8":
                found = base64.b64decode(found).decode("utf-8")
            if isinstance(value, list):
                assert [float(v) for v in found.split(",")] == value
            elif isinstance(value, bool):
                assert found == str(value).lower()
            elif isinstance(value, (int, float)):
                assert float(found) == value
            else:
                assert found == value
        for port in node.findall("input"):
            upstream = port.get("nodename")
            if not upstream:
                continue
            origin = nodes[upstream]
            incoming = port
            if not origin.get("iris_native_id"):
                incoming = origin.find("input")
                origin = nodes[incoming.get("nodename")]
            origin_nd = definitions[origin.get("nodedef")]
            output = (
                origin_nd.find(f"output[@name='{incoming.get('output')}']")
                if incoming.get("output")
                else origin_nd.find("output")
            )
            edges.append(
                {
                    "from": {
                        "node": decode(origin, "iris_native_id"),
                        "socket": decode(output, "iris_native_socket"),
                    },
                    "to": {
                        "node": identifier,
                        "socket": decode(ports[port.get("name")], "iris_native_socket"),
                    },
                }
            )
    assert sorted(edges, key=str) == sorted(snapshot["links"], key=str)
    return {"nodes": len(native), "links": len(edges)}


def same_original(found, original):
    """逐字段比较盘点原值；只有非有限数值允许使用明确的保存标签。"""
    if isinstance(original, float) and not math.isfinite(original):
        return found == {
            "iris_nonfinite_float": "nan"
            if math.isnan(original)
            else "+inf"
            if original > 0
            else "-inf"
        }
    if isinstance(original, dict):
        return (
            isinstance(found, dict)
            and found.keys() == original.keys()
            and all(same_original(found[k], v) for k, v in original.items())
        )
    if isinstance(original, list):
        return (
            isinstance(found, list)
            and len(found) == len(original)
            and all(same_original(a, b) for a, b in zip(found, original, strict=True))
        )
    return found == original


def main():
    """对全部案例检查实际文档及原生接口，任何缺失或不一致都使审计失败。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--exports", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    summary = json.loads((args.documents / "summary.json").read_text("utf-8"))
    inventory = json.loads(args.inventory.read_text("utf-8"))
    originals = {m["name"]: m for m in inventory["materials"]}
    api = RelayAPI([])
    report = {"status": "ok", "cases": [], "standard": 0, "preserved": 0}
    assert len(summary["cases"]) == len(originals)
    for case in summary["cases"]:
        path = args.documents / (case["id"] + ".mtlx")
        document = path.read_text("utf-8")
        validation = request(api, "materialx.validate", document=document)
        assert validation["status"] != "error", validation
        if case["standard_exported"]:
            assert path.read_bytes() == (args.exports / path.name).read_bytes()
            report["standard"] += 1
            checked = {"standard_bytes_verified": True}
        else:
            root = ET.fromstring(document)
            assert root.get("iris_preservation_only") == "true"
            snapshot = json.loads(decode(root, "iris_native_snapshot"))
            original = originals[case["name"]]
            assert snapshot["name"] == original["name"]
            assert same_original(snapshot["metadata"]["material"], original)
            for name, group in snapshot["metadata"]["groups"].items():
                assert same_original(group, inventory["groups"][name])
            assert {n["id"] for n in snapshot["nodes"]} == (
                {n["name"] for n in original.get("nodes", [])} or {"native_material"}
            )
            assert snapshot["metadata"]["material"].get("links", []) == original.get("links", [])
            checked = verify_native(root, snapshot)
            report["preserved"] += 1
        report["cases"].append(dict(id=case["id"], **checked))
    with args.out.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in report.items() if k != "cases"}))


if __name__ == "__main__":
    main()
