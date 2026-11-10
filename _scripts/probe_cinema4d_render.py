"""用完整 Redshift VideoPost 设置验证真实渲染，不更改许可配置或用户场景。"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    """独立发光球体、固定相机；分别记录渲染返回值、文件与像素证据。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    import c4d
    import maxon

    from plugins.cinema4d.adapter import SPACE, set_value

    document = c4d.documents.BaseDocument()
    result = {"status": "pending", "appearance_verified": False}
    try:
        sphere = c4d.BaseObject(c4d.Osphere)
        document.InsertObject(sphere)
        material = c4d.BaseMaterial(c4d.Mmaterial)
        document.InsertMaterial(material)
        graph = maxon.GraphDescription.GetGraph(material, maxon.Id(SPACE), createEmpty=False)
        standard = "com.redshift3d.redshift4c4d.nodes.core.standardmaterial"
        shader = next(
            n
            for n in graph.GetViewRoot().GetChildren()
            if str(n.GetValue(maxon.nodes.AssetId)[0]) == standard
        )
        with graph.BeginTransaction() as transaction:
            set_value(shader, standard + ".base_color_weight", 0)
            set_value(shader, standard + ".refl_weight", 0)
            set_value(shader, standard + ".emission_weight", 1)
            set_value(shader, standard + ".emission_color", [0.8, 0.15, 0.04, 1])
            transaction.Commit()
        tag = sphere.MakeTag(c4d.Ttexture)
        tag[c4d.TEXTURETAG_MATERIAL] = material
        camera = c4d.BaseObject(c4d.Ocamera)
        camera.SetAbsPos(c4d.Vector(0, 0, -500))
        document.InsertObject(camera)
        document.GetRenderBaseDraw().SetSceneCamera(camera)
        render = document.GetActiveRenderData()
        render[c4d.RDATA_RENDERENGINE] = c4d.VPrsrenderer
        video = c4d.documents.BaseVideoPost(c4d.VPrsrenderer)
        assert video is not None
        render.InsertVideoPost(video)
        render[c4d.RDATA_XRES] = 64
        render[c4d.RDATA_YRES] = 64
        bitmap = c4d.bitmaps.MultipassBitmap(64, 64, c4d.COLORMODE_RGBf)
        bitmap.AddChannel(True, True)
        result["video_post_present"] = render.GetFirstVideoPost().GetType() == c4d.VPrsrenderer
        assert c4d.documents.SaveDocument(
            document,
            str(args.out / "probe.c4d"),
            c4d.SAVEDOCUMENTFLAGS_DONTADDTORECENTLIST,
            c4d.FORMAT_C4DEXPORT,
        )
        code = c4d.documents.RenderDocument(
            document, render.GetDataInstance(), bitmap, c4d.RENDERFLAGS_EXTERNAL
        )
        result.update(
            code=int(code),
            labels=[
                name
                for name in dir(c4d)
                if name.startswith("RENDERRESULT_") and getattr(c4d, name) == code
            ],
            status="rendered" if code == c4d.RENDERRESULT_OK else "failed",
        )
        if code == c4d.RENDERRESULT_OK:
            assert bitmap.Save(str(args.out / "probe.exr"), c4d.FILTER_EXR) == c4d.IMAGERESULT_OK
            result["exr_written"] = True
    finally:
        (args.out / "summary.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        c4d.documents.KillDocument(document)
    print(json.dumps(result), flush=True)
    if result["status"] != "rendered":
        raise RuntimeError("Redshift 独立渲染尚未通过")


if __name__ == "__main__":
    main()
