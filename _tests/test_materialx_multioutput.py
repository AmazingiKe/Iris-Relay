"""多输出自定义函数须保留实际输出选择，不能把两个端口都接到默认输出。"""

from _tests.test_materialx_extensions import EXTENSION, fixture
from _tests.test_materialx_link_casts import export
from _tests.test_materialx_native_nodes import convert
from iris_relay.materialx_core import MaterialXCore


def test_custom_multioutput_roundtrip_keeps_distinct_socket_routes():
    profile, source = fixture()
    profile["materialx_extensions"] = EXTENSION.replace(
        '<output name="out" type="float" />',
        '<output name="out" type="float" /><output name="other" type="float" />',
    ).replace(
        '<output name="out" type="float" nodename="calculation" />',
        '<add name="sum" type="float" nodedef="ND_add_float">'
        '<input name="in1" type="float" interfacename="in1" />'
        '<input name="in2" type="float" interfacename="in2" /></add>'
        '<output name="out" type="float" nodename="calculation" />'
        '<output name="other" type="float" nodename="sum" />',
    )
    profile["nodes"]["math"]["outputs"]["Other"] = {"semantic": "other", "type": "float"}
    for node in source["nodes"]:
        if node["type"] == "math":
            node["outputs"].append("Other")
    source["links"][1]["from"]["socket"] = "Other"
    result = export(profile, source)
    assert result["result"]["document"], result
    document = MaterialXCore().read(result["result"]["document"])
    metallic = document.getNode("surface_standard").getInput("metalness")
    assert metallic.getNodeName() == "first" and metallic.getOutputString() == "other"
    restored = convert(profile, source, result["result"]["document"])
    assert restored["result"]["material"], restored
    assert sorted(restored["result"]["material"]["links"], key=str) == sorted(
        source["links"], key=str
    )
