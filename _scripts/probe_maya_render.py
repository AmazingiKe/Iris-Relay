"""在独立 Maya 中检查 Arnold 真实渲染与许可；失败时不会产生训练成功记录。"""

import argparse
import json
from pathlib import Path


def main():
    """只创建独立球体、固定灯光和相机，要求无许可错误的实际图片。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    import maya.standalone

    maya.standalone.initialize(name="python")
    try:
        from maya import cmds

        cmds.loadPlugin("mtoa", quiet=True)
        import mtoa.core
        from mtoa.cmds.arnoldRender import arnoldSequenceRender

        mtoa.core.createOptions()
        cmds.setAttr("defaultRenderGlobals.currentRenderer", "arnold", type="string")
        cmds.file(new=True, force=True)
        mtoa.core.createOptions()
        cmds.setAttr("defaultRenderGlobals.currentRenderer", "arnold", type="string")
        shader = cmds.shadingNode("aiStandardSurface", asShader=True)
        cmds.setAttr(shader + ".baseColor", 0.5, 0.15, 0.05, type="double3")
        group = cmds.sets(renderable=True, noSurfaceShader=True, empty=True)
        cmds.connectAttr(shader + ".outColor", group + ".surfaceShader")
        sphere = cmds.polySphere(subdivisionsX=32, subdivisionsY=16)[0]
        cmds.sets(sphere, edit=True, forceElement=group)
        camera, shape = cmds.camera()
        cmds.setAttr(camera + ".translateZ", 5)
        cmds.setAttr(shape + ".renderable", True)
        for other in cmds.ls(type="camera"):
            if other != shape:
                cmds.setAttr(other + ".renderable", False)
        transform = cmds.shadingNode("aiAreaLight", asLight=True, name="probeLight")
        light = cmds.listRelatives(transform, shapes=True)[0]
        cmds.setAttr(transform + ".translate", -2, 3, 4, type="double3")
        cmds.setAttr(light + ".intensity", 20)
        sky_transform = cmds.shadingNode("aiSkyDomeLight", asLight=True, name="probeSky")
        sky = cmds.listRelatives(sky_transform, shapes=True)[0]
        cmds.setAttr(sky + ".intensity", 1)
        cmds.setAttr("defaultArnoldRenderOptions.abortOnLicenseFail", True)
        cmds.setAttr("defaultArnoldRenderOptions.log_verbosity", 2)
        cmds.setAttr("defaultArnoldRenderOptions.AASamples", 2)
        cmds.setAttr("defaultArnoldRenderOptions.threads_autodetect", False)
        cmds.setAttr("defaultArnoldRenderOptions.threads", 4)
        cmds.setAttr("defaultArnoldDriver.aiTranslator", "exr", type="string")
        cmds.setAttr("defaultRenderGlobals.imageFilePrefix", str(args.out / "probe"), type="string")
        cmds.setAttr("defaultRenderGlobals.startFrame", 1)
        cmds.setAttr("defaultRenderGlobals.endFrame", 1)
        cmds.setAttr("defaultResolution.width", 64)
        cmds.setAttr("defaultResolution.height", 64)
        cmds.file(rename=str(args.out / "probe.ma"))
        cmds.file(save=True, type="mayaAscii")
        cmds.arnoldExportAss(filename=str(args.out / "probe.ass"), mask=255, camera=camera)
        arnoldSequenceRender(64, 64, camera, False)
        files = list(args.out.glob("*.exr"))
        if not files:
            raise RuntimeError("未得到 EXR，须检查渲染与许可日志")
        (args.out / "summary.json").write_text(
            json.dumps(
                {
                    "status": "rendered",
                    "files": [str(p) for p in files],
                    "abort_on_license_fail": True,
                    "appearance_verified": False,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    finally:
        maya.standalone.uninitialize()


if __name__ == "__main__":
    main()
