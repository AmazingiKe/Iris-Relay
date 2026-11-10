"""从已完成语义导出的真实 A12 材质选择有界实验，并在训练前冻结材质族划分。"""

import argparse
import hashlib
import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from _scripts.native_fixture import renderer  # noqa: E402
from _scripts.neural_experiment import (  # noqa: E402
    fingerprint,
    fixed_conditions,
    save_json,
    sphere_mesh,
    validate_split,
)
from iris_relay import RelayAPI  # noqa: E402
from plugins.core import request  # noqa: E402
from plugins.resources import file_sha256  # noqa: E402


def candidate(api, document, source, target, source_dictionary):
    """只保留可编辑的单一表面和底色上游，不删掉原图中的法线、透明或未知分支。"""
    source_root = next(n for n in source["nodes"] if n["id"] == source["output"]["node"])
    if not source_root["type"].startswith("ShaderNodeBsdfPrincipled") or source_root.get(
        "metadata", {}
    ).get("source_group_path"):
        return None
    source_rule = source_dictionary["nodes"].get(source_root["type"], {})
    if source_rule.get("native", {}).get("properties", {}).get("distribution") != "MULTI_GGX":
        return None
    response = request(api, "materialx.convert", document=document, target=target)
    material = (response.get("result") or {}).get("material")
    if not material:
        return None
    root_id = material["output"]["node"]
    root = next(n for n in material["nodes"] if n["id"] == root_id)
    if any(
        e["to"]["node"] == root_id and e["to"]["socket"] != "baseColor" for e in material["links"]
    ):
        return None
    values = root["inputs"]
    if any(values.get(k, 0) != 0 for k in ("coat", "emission", "sheen")) or values.get(
        "opacity"
    ) != [1, 1, 1]:
        return None
    if values.get("specularColor", [1, 1, 1]) != [1, 1, 1] or any(
        values.get(k, 0) != 0
        for k in ("diffuseRoughness", "specularAnisotropy", "specularRotation")
    ):
        return None
    if not 1 <= values.get("specularIOR", 0) <= 3 or not 0 <= values.get("metalness", -1) <= 1:
        return None
    for resource in material["textures"].values():
        if resource["coordinates"].get("transforms") or resource["coordinates"]["set"]:
            return None
        if resource.get("metadata", {}).get("image_alpha", {}).get("channels") == 4:
            return None
    return {"target_root": root_id, "source_root": source_root["id"], "material": material}


def main():
    """输出实际样本身份、资源哈希、固定条件和三份隔离清单，不生成伪标签。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True, help="只读源材质缓存")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--families", type=int, default=32)
    parser.add_argument("--exclude-resource-extension", action="append", default=[])
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / "documents").mkdir()
    api = RelayAPI.from_directory(ROOT / "dictionaries")
    profiles = request(api, "capabilities")["result"]["renderers"]
    source_profile = next(p for p in profiles if p.get("host", {}).get("id") == "blender")
    source_dictionary = request(api, "describe", renderer=renderer(source_profile))["result"][
        "dictionary"
    ]
    profile = next(p for p in profiles if p.get("host", {}).get("id") == "maya")
    target = renderer(profile)
    chosen, rejected, used_families, used_resources = [], [], set(), set()
    constant_signatures = set()
    digest_cache = {}
    excluded = {
        value.lower() if value.startswith(".") else "." + value.lower()
        for value in args.exclude_resource_extension
    }
    for path in sorted(args.documents.glob("*.mtlx")):
        report_path = path.with_suffix(".json")
        if not report_path.is_file():
            continue
        report = json.loads(report_path.read_text("utf8"))
        source = (report.get("export_response") or {}).get("result", {}).get("source")
        if not source:
            continue
        document = path.read_text("utf8")
        found = candidate(api, document, source, target, source_dictionary)
        if not found:
            rejected.append({"id": path.stem, "reason": "不满足首轮单表面及底色范围"})
            continue
        if any(
            Path(value["uri"]).suffix.lower() in excluded
            for value in found["material"]["textures"].values()
        ):
            rejected.append({"id": path.stem, "reason": "实际目标解码器不支持的文件格式"})
            continue
        resources = []
        try:
            for value in found["material"]["textures"].values():
                uri = value["uri"]
                if uri not in digest_cache:
                    digest_cache[uri] = file_sha256(uri)
                resources.append(digest_cache[uri])
        except OSError as exc:
            rejected.append({"id": path.stem, "reason": str(exc)})
            continue
        family = re.sub(r"[\d. _-]+$", "", source["name"]) or source["name"]
        if family in used_families or set(resources) & used_resources:
            rejected.append({"id": path.stem, "reason": "与已选材质同族或共享实际贴图"})
            continue
        root = next(n for n in found["material"]["nodes"] if n["id"] == found["target_root"])
        if len(found["material"]["nodes"]) == 1:
            signature = fingerprint(
                {k: root["inputs"][k] for k in ("baseColor", "specularIOR", "metalness")}
            )
            if signature in constant_signatures:
                rejected.append({"id": path.stem, "reason": "数值等价的无贴图材质已选择"})
                continue
            constant_signatures.add(signature)
        used_families.add(family)
        used_resources.update(resources)
        (args.out / "documents" / path.name).write_text(document, encoding="utf8")
        chosen.append(
            {
                "id": path.stem,
                "name": source["name"],
                "family": family,
                "source_root": found["source_root"],
                "resource_sha256": resources,
                "document_sha256": hashlib.sha256(document.encode("utf8")).hexdigest(),
            }
        )
        if len(chosen) >= args.families:
            break
    if len(chosen) != args.families:
        save_json(args.out / "selection-failed.json", {"chosen": chosen, "rejected": rejected})
        raise RuntimeError("符合首轮域的独立材质族不足：" + str(len(chosen)))
    random.Random(20261111).shuffle(chosen)
    validation_count = max(3, args.families // 6)
    for index, item in enumerate(chosen):
        item["split"] = (
            "test"
            if index < validation_count
            else "validation"
            if index < 2 * validation_count
            else "train"
        )
    conditions, mesh = fixed_conditions(), sphere_mesh()
    conditions["mesh_sha256"] = fingerprint(mesh)
    job = {
        "schema_version": "1",
        "source_cache": str(args.source.resolve()),
        "source_cache_sha256": file_sha256(args.source),
        "source_shading_model": "cycles.principled.MULTI_GGX",
        "source_materials": chosen,
        "conditions": conditions,
        "conditions_sha256": fingerprint(conditions),
        "mesh": mesh,
        "mesh_sha256": fingerprint(mesh),
        "target": target,
        "dictionary_version": profile["dictionary_version"],
        "cases": [],
        "rejected": rejected,
        "resource_hashes": digest_cache,
        "family_policy": "源材质名称去掉数值后缀；同族或共享贴图只选一份，所有变体归属同一集合",
        "excluded_resource_extensions": sorted(excluded),
    }
    variants = [(0.18, 0.20), (0.36, 0.30), (0.58, 0.40), (0.78, 0.45)]
    for item in chosen:
        for index, (roughness, specular) in enumerate(variants):
            job["cases"].append(
                {
                    "id": item["id"] + "_v" + str(index),
                    "asset": item["id"],
                    "family": item["family"],
                    "split": item["split"],
                    "source_name": item["name"],
                    "source_root": item["source_root"],
                    "source_parameters": {"Roughness": roughness, "Specular IOR Level": specular},
                }
            )
    validate_split(job["source_materials"], job["cases"])
    save_json(args.out / "job.json", job)
    print(
        json.dumps(
            {
                "families": len(chosen),
                "cases": len(job["cases"]),
                "conditions_sha256": job["conditions_sha256"],
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
