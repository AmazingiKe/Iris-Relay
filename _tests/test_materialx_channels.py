"""RGB 三个输出独立保存；Blue／Green 不得退化为 R 或丢失连接。"""

from _scripts.native_fixture import renderer
from _tests.test_materialx_hsv import profile
from _tests.test_materialx_link_casts import export
from _tests.test_materialx_lowerings import rs_profile
from _tests.test_materialx_native_nodes import math_fixture
from _tests.test_maya_materialx import maya_profile
from iris_relay import RelayAPI


def test_rgb_separation_keeps_green_and_blue_through_each_target():
    data = profile()
    assert data["nodes"]["ShaderNodeSeparateColor"]["semantic"] == "materialx.ND_separate3_color3"
    _, source = math_fixture()
    source["nodes"] = [
        source["nodes"][0],
        {
            "id": "channels",
            "type": "ShaderNodeSeparateColor",
            "inputs": {"Color": [0.1, 0.4, 0.8]},
            "outputs": ["Red", "Green", "Blue"],
        },
    ]
    source["links"] = [
        {
            "from": {"node": "channels", "socket": "Green"},
            "to": {"node": "surface_standard", "socket": "Metallic"},
        },
        {
            "from": {"node": "channels", "socket": "Blue"},
            "to": {"node": "surface_standard", "socket": "Roughness"},
        },
    ]
    result = export(data, source)
    assert result["result"]["document"], result
    for target in [data, maya_profile(), rs_profile()]:
        response = RelayAPI([target]).handle(
            {
                "api_version": "1",
                "request_id": "rgb",
                "operation": "materialx.convert",
                "payload": {"document": result["result"]["document"], "target": renderer(target)},
            }
        )
        assert response["result"]["material"], response
        assert len(response["result"]["material"]["links"]) >= 2
