"""独立 Arnold 图片采样探测：用已知 RGBA 像素分别渲染颜色和 Alpha，不调整目标外观。"""

import argparse
import json
import struct
import zlib
from pathlib import Path


def write_fixture(path):
    """写入确定的无色彩附加块 RGBA PNG，四通道均非端点值。"""

    def chunk(kind, data):
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress((b"\0" + bytes([128, 64, 32, 64]) * 4) * 4))
        + chunk(b"IEND", b"")
    )


def main():
    """只在新建测试场景采样，输出保存的场景、EXR 和原生属性证据。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--image", type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    image = args.image or args.out / "known-rgba.png"
    if args.image is None:
        write_fixture(image)
    import maya.standalone

    maya.standalone.initialize(name="python")
    try:
        from maya import cmds

        cmds.loadPlugin("mtoa", quiet=True)
        import mtoa.core
        from mtoa.cmds.arnoldRender import arnoldSequenceRender

        cmds.file(new=True, force=True)
        spaces = cmds.colorManagementPrefs(query=True, renderingSpaceNames=True)
        print(json.dumps({"rendering_spaces": spaces}), flush=True)
        target_space = "scene-linear Rec.709-sRGB"
        if target_space not in spaces:
            raise RuntimeError("探测要求已安装的线性 Rec.709 空间，实际空间已记录")
        cmds.colorManagementPrefs(edit=True, renderingSpaceName=target_space)
        mtoa.core.createOptions()
        cmds.setAttr("defaultRenderGlobals.currentRenderer", "arnold", type="string")
        shader = cmds.shadingNode("aiStandardSurface", asShader=True)
        cmds.setAttr(shader + ".base", 0)
        cmds.setAttr(shader + ".specular", 0)
        cmds.setAttr(shader + ".emission", 1)
        group = cmds.sets(renderable=True, noSurfaceShader=True, empty=True)
        cmds.connectAttr(shader + ".outColor", group + ".surfaceShader")
        sphere = cmds.polySphere(subdivisionsX=32, subdivisionsY=16)[0]
        cmds.sets(sphere, edit=True, forceElement=group)
        camera, shape = cmds.camera()
        cmds.setAttr(camera + ".translateZ", 5)
        for other in cmds.ls(type="camera"):
            cmds.setAttr(other + ".renderable", other == shape)
        texture = cmds.shadingNode("aiImage", asTexture=True)
        cmds.setAttr(texture + ".filename", image.as_posix(), type="string")
        cmds.setAttr(texture + ".ignoreColorSpaceFileRules", True)
        cmds.setAttr(texture + ".autoTx", False)
        cmds.setAttr("defaultArnoldRenderOptions.abortOnLicenseFail", True)
        cmds.setAttr("defaultArnoldRenderOptions.AASamples", 2)
        cmds.setAttr("defaultArnoldRenderOptions.threads_autodetect", False)
        cmds.setAttr("defaultArnoldRenderOptions.threads", 4)
        cmds.setAttr("defaultArnoldDriver.aiTranslator", "exr", type="string")
        cmds.setAttr("defaultResolution.width", 32)
        cmds.setAttr("defaultResolution.height", 32)
        cases = []
        for color_space in ("Raw", "sRGB"):
            cmds.setAttr(texture + ".colorSpace", color_space, type="string")
            for output in ("color", "alpha"):
                if output == "color":
                    cmds.connectAttr(texture + ".outColor", shader + ".emissionColor", force=True)
                else:
                    cmds.disconnectAttr(texture + ".outColor", shader + ".emissionColor")
                    for channel in "RGB":
                        cmds.connectAttr(
                            texture + ".outAlpha", shader + ".emissionColor" + channel, force=True
                        )
                name = color_space + "_" + output
                cmds.setAttr(
                    "defaultRenderGlobals.imageFilePrefix", str(args.out / name), type="string"
                )
                cmds.file(rename=str(args.out / (name + ".ma")))
                cmds.file(save=True, type="mayaAscii")
                arnoldSequenceRender(32, 32, camera, False)
                assert (args.out / (name + ".exr")).is_file()
                cases.append({"id": name, "color_space": color_space, "output": output})
                if output == "alpha":
                    for channel in "RGB":
                        cmds.disconnectAttr(
                            texture + ".outAlpha", shader + ".emissionColor" + channel
                        )
        (args.out / "summary.json").write_text(
            json.dumps(
                {
                    "cases": cases,
                    "png_rgba_bytes": [128, 64, 32, 64],
                    "rendering_space": target_space,
                    "pixel_verification_pending": True,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    finally:
        maya.standalone.uninitialize()


if __name__ == "__main__":
    main()
