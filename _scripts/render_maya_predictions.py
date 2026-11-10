"""用公开 API 已生成的真实模型提案渲染隔离集合，并重复基线测量随机噪声。"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from _scripts.neural_experiment import save_json  # noqa: E402
from _scripts.render_maya_experiment import actual_conditions, render, scene_for  # noqa: E402


def main():
    """只应用经核心与原生复核的两个参数变化，灯光和相机在校正前后保持不变。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    job = json.loads(args.job.read_text("utf8"))
    import maya.standalone

    maya.standalone.initialize(name="python")
    try:
        from maya import cmds

        cmds.loadPlugin("mtoa", quiet=True)
        from _scripts.verify_maya_import import verify
        from iris_relay.model import Material
        from plugins.core import create_service, export_material, request
        from plugins.maya.adapter import MayaAdapter, ensure_environment

        sphere, camera, settings = scene_for(job)
        frozen = actual_conditions(camera, settings)
        completed = []
        with create_service(ROOT) as service:
            adapter = MayaAdapter(service, ensure_environment())
            for case in job["cases"]:
                if case["split"] == "train":
                    continue
                response = json.loads((args.predictions / (case["id"] + ".json")).read_text("utf8"))
                material = Material.parse(response["result"]["material"]).to_dict()
                for resource in material["textures"].values():
                    resource["uri"] = Path(resource["uri"]).resolve().as_posix()
                calibration = response["result"]["report"]["calibration"]
                if response["status"] == "error" or calibration["status"] not in {
                    "applied",
                    "unchanged",
                }:
                    raise RuntimeError("模型提案未通过公开核心的原子核验")
                validated = request(service, "validate", material=material)
                if validated["status"] == "error":
                    raise RuntimeError(validated)
                baseline = json.loads(
                    (args.baseline / case["id"] / "material.json").read_text("utf8")
                )
                assert (
                    material["links"] == baseline["links"]
                    and material["textures"] == baseline["textures"]
                )
                directory = args.out / case["id"]
                directory.mkdir()
                created = adapter.create_material(material)
                verified = verify(adapter, material, created)
                cmds.sets(sphere, edit=True, forceElement=created["shading_group"])
                render(camera, directory / "corrected.exr", job["conditions"]["resolution"])
                if not completed:
                    cmds.file(rename=str(args.out / "representative-corrected.ma"))
                    cmds.file(save=True, type="mayaAscii", force=True)
                    exported = export_material(
                        adapter,
                        service,
                        created["material"],
                        args.out / "representative-corrected.mtlx",
                        accept_warnings=True,
                    )
                    assert exported["saved_path"], exported
                root = created["nodes"][material["output"]["node"]]
                original = next(
                    n for n in baseline["nodes"] if n["id"] == baseline["output"]["node"]
                )
                for attribute in ("specularRoughness", "specular"):
                    cmds.setAttr(root + "." + attribute, original["inputs"][attribute])
                cmds.setAttr("defaultArnoldRenderOptions.AA_seed", job["conditions"]["seed"] + 1)
                render(camera, directory / "baseline-repeat.exr", job["conditions"]["resolution"])
                cmds.setAttr("defaultArnoldRenderOptions.AA_seed", job["conditions"]["seed"])
                assert actual_conditions(camera, settings) == frozen
                completed.append(
                    {
                        "id": case["id"],
                        "split": case["split"],
                        "native_verified": verified,
                        "calibration": calibration,
                    }
                )
                save_json(
                    args.out / "summary.json",
                    {
                        "completed": completed,
                        "conditions_sha256": job["conditions_sha256"],
                        "actual_scene_conditions": frozen,
                        "scene_conditions_unchanged": True,
                        "noise_repeat_seed": job["conditions"]["seed"] + 1,
                    },
                )
                cmds.delete([node for node in created["created_nodes"] if cmds.objExists(node)])
                print(json.dumps({"completed": len(completed), "id": case["id"]}), flush=True)
    finally:
        maya.standalone.uninitialize()


if __name__ == "__main__":
    main()
