"""在 Maya 2022 中渲染固定 Arnold 参考、基础图及真实有界联合搜索候选。"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from _scripts.neural_experiment import fingerprint, save_json, write_uv_png  # noqa: E402


def scene_for(job):
    """只建立独立比较场景；网格数值、相机和环境辐亮度均来自冻结协议。"""
    import maya.api.OpenMaya as om
    import mtoa.core
    from maya import cmds

    cmds.file(new=True, force=True)
    cmds.colorManagementPrefs(edit=True, renderingSpaceName="scene-linear Rec.709-sRGB")
    cmds.colorManagementPrefs(edit=True, outputTransformEnabled=False)
    mtoa.core.createOptions()
    cmds.setAttr("defaultRenderGlobals.currentRenderer", "arnold", type="string")
    spec = job["conditions"]
    transform = cmds.createNode("transform", name="IdenticalProbe")
    selection = om.MSelectionList()
    selection.add(transform)
    mesh = om.MFnMesh()
    mesh.create(
        [om.MPoint(*p) for p in job["mesh"]["vertices"]],
        [len(f) for f in job["mesh"]["faces"]],
        [i for face in job["mesh"]["faces"] for i in face],
        parent=selection.getDependNode(0),
    )
    mesh.setVertexNormals(
        [om.MVector(*p) for p in job["mesh"]["vertices"]],
        list(range(len(job["mesh"]["vertices"]))),
        om.MSpace.kObject,
    )
    uvs = [uv for face in job["mesh"]["uvs"] for uv in face]
    mesh.setUVs([uv[0] for uv in uvs], [uv[1] for uv in uvs], uvSet="map1")
    mesh.assignUVs([len(f) for f in job["mesh"]["faces"]], list(range(len(uvs))), uvSet="map1")
    for name, coordinates in job["mesh"].get("additional_uv_sets", {}).items():
        assert mesh.createUVSet(name) == name
        extra = [value for face in coordinates for value in face]
        mesh.setUVs([value[0] for value in extra], [value[1] for value in extra], uvSet=name)
        mesh.assignUVs([len(f) for f in job["mesh"]["faces"]], list(range(len(extra))), uvSet=name)
    mesh.setCurrentUVSetName("map1")
    camera, shape = cmds.camera(name="FixedCamera")
    cmds.setAttr(camera + ".translate", *spec["camera"]["position"], type="double3")
    cmds.setAttr(shape + ".orthographic", True)
    cmds.setAttr(shape + ".orthographicWidth", spec["camera"]["width"])
    cmds.setAttr(shape + ".nearClipPlane", spec["camera"]["near"])
    cmds.setAttr(shape + ".farClipPlane", spec["camera"]["far"])
    for other in cmds.ls(type="camera"):
        cmds.setAttr(other + ".renderable", other == shape)
    light_transform = cmds.shadingNode(
        "aiSkyDomeLight", asLight=True, name="FixedUniformEnvironment"
    )
    light = cmds.listRelatives(light_transform, shapes=True)[0]
    cmds.setAttr(light + ".color", *spec["environment_radiance"], type="double3")
    cmds.setAttr(light + ".intensity", 1)
    cmds.setAttr(light + ".aiExposure", 0)
    cmds.setAttr(light + ".aiNormalize", False)
    camera_visibility = (
        "aiCamera" if cmds.attributeQuery("aiCamera", node=light, exists=True) else "camera"
    )
    cmds.setAttr(light + "." + camera_visibility, 0)
    settings = {
        "abortOnLicenseFail": True,
        "AASamples": spec["arnold"]["aa"],
        "GIDiffuseSamples": spec["arnold"]["diffuse_samples"],
        "GISpecularSamples": spec["arnold"]["specular_samples"],
        "threads_autodetect": False,
        "threads": spec["arnold"]["threads"],
        "AA_seed": spec["seed"],
        "log_verbosity": 1,
    }
    for name, value in settings.items():
        cmds.setAttr("defaultArnoldRenderOptions." + name, value)
    cmds.setAttr("defaultArnoldDriver.aiTranslator", "exr", type="string")
    if cmds.attributeQuery("halfPrecision", node="defaultArnoldDriver", exists=True):
        cmds.setAttr("defaultArnoldDriver.halfPrecision", False)
    cmds.setAttr("defaultResolution.width", spec["resolution"][0])
    cmds.setAttr("defaultResolution.height", spec["resolution"][1])
    cmds.setAttr("defaultResolution.pixelAspect", spec["pixel_aspect"])
    cmds.setAttr("defaultResolution.deviceAspectRatio", spec["device_aspect"])
    return transform, camera, settings


def render(camera, path, resolution):
    """要求实际 EXR 写出；许可失败由 Arnold 中止，不能当作训练标签。"""
    from maya import cmds
    from mtoa.cmds.arnoldRender import arnoldSequenceRender

    cmds.setAttr("defaultRenderGlobals.imageFilePrefix", str(path.with_suffix("")), type="string")
    arnoldSequenceRender(resolution[0], resolution[1], camera, False)
    if not path.is_file():
        raise RuntimeError("Arnold 未写出图像：" + str(path))


def actual_conditions(camera, settings):
    """记录实际固定条件；排除允许变化的材质、对象绑定和输出文件名。"""
    from maya import cmds

    lights = cmds.ls(type="aiSkyDomeLight") or []
    assert len(lights) == 1
    light = lights[0]
    shape = cmds.listRelatives(camera, shapes=True)[0]
    return {
        "rendering_space": cmds.colorManagementPrefs(query=True, renderingSpaceName=True),
        "camera_matrix": cmds.getAttr(camera + ".worldMatrix[0]"),
        "ortho_width": cmds.getAttr(shape + ".orthographicWidth"),
        "resolution": {
            k: cmds.getAttr("defaultResolution." + k)
            for k in ("width", "height", "pixelAspect", "deviceAspectRatio")
        },
        "renderer": {k: cmds.getAttr("defaultArnoldRenderOptions." + k) for k in settings},
        "environment": {
            k: cmds.getAttr(light + "." + k)
            for k in ("color", "intensity", "aiExposure", "aiNormalize")
        },
    }


def main():
    """只在本进程测试场景设置材质候选；所有灯光、相机和色彩条件在候选间保持固定。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--documents", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--probe-only", action="store_true")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--coordinate-reference", type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    job = json.loads(args.job.read_text("utf8"))
    assert fingerprint(job["conditions"]) == job["conditions_sha256"]
    import maya.standalone

    maya.standalone.initialize(name="python")
    try:
        from maya import cmds

        cmds.loadPlugin("mtoa", quiet=True)
        sphere, camera, settings = scene_for(job)
        frozen = actual_conditions(camera, settings)
        if args.probe_only:
            for name in ("diffuse", "emission", "uv"):
                shader = cmds.shadingNode("aiStandardSurface", asShader=True)
                cmds.setAttr(shader + ".specular", 0)
                cmds.setAttr(shader + ".base", 0 if name in {"emission", "uv"} else 1)
                cmds.setAttr(shader + ".baseColor", 0.4, 0.4, 0.4, type="double3")
                if name in {"emission", "uv"}:
                    cmds.setAttr(shader + ".emission", 1)
                    cmds.setAttr(shader + ".emissionColor", 0.2, 0.4, 0.7, type="double3")
                if name == "uv":
                    texture_path = args.out / "uv.png"
                    write_uv_png(texture_path)
                    texture = cmds.shadingNode("aiImage", asTexture=True)
                    cmds.setAttr(texture + ".filename", str(texture_path), type="string")
                    cmds.setAttr(texture + ".colorSpace", "Raw", type="string")
                    cmds.setAttr(texture + ".ignoreColorSpaceFileRules", True)
                    cmds.setAttr(texture + ".autoTx", False)
                    cmds.connectAttr(texture + ".outColor", shader + ".emissionColor")
                group = cmds.sets(renderable=True, noSurfaceShader=True, empty=True)
                cmds.connectAttr(shader + ".outColor", group + ".surfaceShader")
                cmds.sets(sphere, edit=True, forceElement=group)
                render(camera, args.out / (name + ".exr"), job["conditions"]["resolution"])
            if args.coordinate_reference:
                from plugins.core import create_service, import_material
                from plugins.maya.adapter import MayaAdapter, ensure_environment

                with create_service(ROOT) as service:
                    adapter = MayaAdapter(service, ensure_environment())
                    restored = import_material(
                        adapter,
                        service,
                        args.coordinate_reference,
                        job["target"],
                        accept_warnings=True,
                    )
                    assert restored["created"], restored
                    cmds.sets(sphere, edit=True, forceElement=restored["created"]["shading_group"])
                    render(camera, args.out / "coordinates.exr", job["conditions"]["resolution"])
                    save_json(args.out / "coordinate-import.json", restored)
            assert actual_conditions(camera, settings) == frozen
            cmds.file(rename=str(args.out / "reference.ma"))
            cmds.file(save=True, type="mayaAscii", force=True)
            save_json(
                args.out / "summary.json",
                {
                    "conditions_sha256": job["conditions_sha256"],
                    "mesh_sha256": job["mesh_sha256"],
                    "settings": settings,
                    "rendered": 3 + int(bool(args.coordinate_reference)),
                    "actual_scene_conditions": frozen,
                    "scene_conditions_unchanged": True,
                },
            )
            return
        if args.documents is None:
            raise RuntimeError("实际样本必须提供源材质本轮导出的文档目录")
        from plugins.core import check_import, create_service, import_material
        from plugins.maya.adapter import MayaAdapter, ensure_environment

        completed = []
        with create_service(ROOT) as service:
            adapter = MayaAdapter(service, ensure_environment())
            # 实际宿主先检查整批资源；核心可转换不能代替本机解码能力。
            checks = []
            for case in job["cases"]:
                result = check_import(
                    adapter, service, args.documents / (case["id"] + ".mtlx"), job["target"]
                )
                checks.append(
                    {"id": case["id"], "status": result["status"], "message": result["message"]}
                )
            save_json(args.out / "preflight.json", {"cases": checks})
            if any(result["status"] == "error" for result in checks):
                raise RuntimeError("实际 Maya 资源预检未全部通过，详见 preflight.json")
            if args.preflight_only:
                return
            for index, case in enumerate(job["cases"]):
                directory = args.out / case["id"]
                directory.mkdir()
                result = import_material(
                    adapter,
                    service,
                    args.documents / (case["id"] + ".mtlx"),
                    job["target"],
                    accept_warnings=True,
                )
                if not result["created"]:
                    raise RuntimeError(result)
                created = result["created"]
                cmds.sets(sphere, edit=True, forceElement=created["shading_group"])
                material = result["material"]
                shader = created["nodes"][material["output"]["node"]]
                initial = [
                    cmds.getAttr(shader + ".specularRoughness"),
                    cmds.getAttr(shader + ".specular"),
                ]
                candidates = [{"id": "baseline", "parameters": initial}]
                for a in job["conditions"]["search_delta"]["roughness"]:
                    for b in job["conditions"]["search_delta"]["specular"]:
                        if a == 0 and b == 0:
                            continue
                        candidates.append(
                            {
                                "id": "candidate_" + str(len(candidates)),
                                "parameters": [
                                    max(0.05, min(0.95, initial[0] + a)),
                                    max(0, min(1, initial[1] + b)),
                                ],
                            }
                        )
                for candidate in candidates:
                    for attribute, value in zip(
                        ("specularRoughness", "specular"), candidate["parameters"]
                    ):
                        binding = adapter.profile["nodes"]["standard_surface"]["inputs"][attribute]
                        low, high = binding["adjustment_range"]
                        assert low <= value <= high
                        cmds.setAttr(shader + "." + attribute, value)
                        assert abs(cmds.getAttr(shader + "." + attribute) - value) < 2e-6
                    render(
                        camera,
                        directory / (candidate["id"] + ".exr"),
                        job["conditions"]["resolution"],
                    )
                save_json(directory / "material.json", material)
                save_json(directory / "candidates.json", candidates)
                assert actual_conditions(camera, settings) == frozen
                completed.append({"id": case["id"], "rendered": len(candidates)})
                save_json(
                    args.out / "summary.json",
                    {
                        "conditions_sha256": job["conditions_sha256"],
                        "mesh_sha256": job["mesh_sha256"],
                        "settings": settings,
                        "completed": completed,
                        "total": len(job["cases"]),
                        "training_started": False,
                        "actual_scene_conditions": frozen,
                        "scene_conditions_unchanged": True,
                    },
                )
                print(json.dumps({"completed": index + 1, "total": len(job["cases"])}), flush=True)
                if index == 0:
                    for attribute, value in zip(("specularRoughness", "specular"), initial):
                        cmds.setAttr(shader + "." + attribute, value)
                    cmds.file(rename=str(args.out / "representative.ma"))
                    cmds.file(save=True, type="mayaAscii", force=True)
                cmds.delete([n for n in created["created_nodes"] if cmds.objExists(n)])
    finally:
        maya.standalone.uninitialize()


if __name__ == "__main__":
    main()
