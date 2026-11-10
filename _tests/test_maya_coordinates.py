"""Arnold 静态坐标使用已探测的原生 UV 上下文节点，不把矩阵或命名集丢弃。"""

import json
from pathlib import Path

from _scripts.build_maya_dictionary import build
from _scripts.native_fixture import renderer
from _tests.test_materialx_bridge import call
from _tests.test_materialx_coordinates import ROTATE_TRANSLATE, SCALE, setup
from iris_relay import RelayAPI


def test_maya_dictionary_declares_probed_editable_coordinate_wrapper():
    path = Path(__file__).resolve().parents[1] / "dictionaries/catalogs/maya-arnold-7.2.5.3.json"
    profile = build(json.loads(path.read_text("utf-8")))
    assert profile["texture_coordinates"]["transforms"] == "trs"
    native = profile["nodes"]["image"]["inputs"]["image"]["native"]
    wrapper = native["properties"]["coordinate_wrapper"]
    assert wrapper["asset"] == "aiUvTransform"
    assert wrapper["matrix"] == "uvwMatrix"
    assert wrapper["outputs"] == {"color": "outColor", "alpha": "outAlpha"}
    api, source = setup({"kind": "uv", "set": "纹理UV", "transforms": [SCALE, ROTATE_TRANSLATE]})
    document = call(api, "materialx.export", material=source)["result"]["document"]
    response = call(
        RelayAPI([profile]), "materialx.convert", document=document, target=renderer(profile)
    )
    assert response["result"]["material"], response
    assert (
        next(iter(response["result"]["material"]["textures"].values()))["coordinates"]
        == source["textures"]["fixture"]["coordinates"]
    )
