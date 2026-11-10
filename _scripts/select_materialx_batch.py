"""按公开目标能力选择可复现的复杂材质样本，并保留全部候选的排除原因。"""

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from iris_relay import RelayAPI  # noqa: E402
from plugins.core import request  # noqa: E402
from plugins.resources import image_format, image_header_channels  # noqa: E402


def main():
    """优先覆盖不同节点、资源和材质族；不把排除的复杂失败样本藏进成功数量。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--count", type=int, default=50)
    parser.add_argument(
        "--max-target-nodes", type=int, help="实机批次的单材质节点预算；不是转换能力上限"
    )
    parser.add_argument(
        "--exclude-resource-extension",
        action="append",
        default=[],
        help="显式排除已有宿主失败证据的资源后缀；在报告中单列筛选原因",
    )
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    api = RelayAPI.from_directory(ROOT / "dictionaries")
    profiles = request(api, "capabilities")["result"]["renderers"]
    targets = [
        p
        for p in profiles
        if (p.get("host", {}).get("id") == "maya" or p["renderer"]["id"] == "redshift")
    ]
    from _scripts.native_fixture import renderer

    targets = [renderer(p) for p in targets]
    candidates, eligible = [], []
    for path in sorted(args.documents.glob("*.mtlx")):
        text = path.read_text("utf-8")
        root = ET.fromstring(text)
        case = {
            "id": path.stem,
            "name": root.get("iris_material_name", path.stem),
            "issues": [],
            "targets": [],
        }
        graphs, features = {}, set()
        excluded_files = [
            item.get("value")
            for item in root.iter("input")
            if item.get("type") == "filename"
            and item.get("value")
            and Path(item.get("value")).suffix.lower() in args.exclude_resource_extension
        ]
        if excluded_files:
            case["issues"].append(
                {"code": "SELECTION_RESOURCE_FORMAT", "resources": excluded_files}
            )
            candidates.append(case)
            continue
        for target in targets:
            response = request(api, "materialx.convert", document=text, target=target)
            payload = response.get("result") or {}
            case["issues"].extend(
                i for i in payload.get("report", {}).get("issues", []) if i["severity"] == "error"
            )
            graph = payload.get("material")
            case["targets"].append({"target": target, "status": response["status"]})
            if graph is None:
                continue
            if args.max_target_nodes and len(graph["nodes"]) > args.max_target_nodes:
                case["issues"].append(
                    {
                        "code": "SELECTION_NODE_BUDGET",
                        "nodes": len(graph["nodes"]),
                        "limit": args.max_target_nodes,
                    }
                )
            for resource in graph["textures"].values():
                image = Path(resource["uri"])
                if not image.is_file():
                    case["issues"].append({"code": "TEXTURE_MISSING"})
                    continue
                description = resource.get("metadata", {}).get("image_alpha", {})
                channels = image_header_channels(image)
                if channels is not None and description.get("channels", channels) != channels:
                    case["issues"].append({"code": "IMAGE_CHANNELS_MISMATCH"})
                if channels == 4 and image_format(image) not in {"png", "exr"}:
                    case["issues"].append({"code": "HOST_RGBA_FORMAT_UNVERIFIED"})
            graphs[target["host"]] = graph
        if len(graphs) == len(targets) and not case["issues"]:
            features = {n.tag for n in root if n.tag not in {"nodedef", "nodegraph"}}
            features.add("images:" + str(len(graphs["maya"]["textures"])))
            case["features"] = sorted(features)
            case["nodes"] = {host: len(g["nodes"]) for host, g in graphs.items()}
            case["family"] = re.sub(r"[.\d_]+$", "", case["name"])
            case["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            eligible.append(case)
        candidates.append(case)
    selected, covered, families = [], set(), set()
    while eligible and len(selected) < args.count:
        case = max(
            eligible,
            key=lambda c: (
                len(set(c["features"]) - covered),
                c["family"] not in families,
                sum(c["nodes"].values()),
                c["id"],
            ),
        )
        eligible.remove(case)
        selected.append(case)
        covered.update(case["features"])
        families.add(case["family"])
        shutil.copyfile(args.documents / (case["id"] + ".mtlx"), args.out / (case["id"] + ".mtlx"))
    report = {
        "requested": args.count,
        "selected": selected,
        "candidates": candidates,
        "covered_features": sorted(covered),
        "targets": targets,
        "max_target_nodes": args.max_target_nodes,
        "excluded_resource_extensions": args.exclude_resource_extension,
    }
    (args.out / "selection.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if len(selected) != args.count:
        raise RuntimeError("当前共同可导入样本不足，请检查 selection.json 中的全部排除原因")
    print(
        json.dumps(
            {"selected": len(selected), "candidates": len(candidates), "features": sorted(covered)},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
