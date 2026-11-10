"""公开差异报告必须发现参数、资源解释和拓扑变化，允许显式身份对齐。"""

from _tests.test_materialx_core import CUSTOM, STANDARD, call


def test_identical_and_numeric_formatting_are_equivalent():
    response = call("materialx.diff", before=STANDARD, after=STANDARD.replace("0.37", "0.3700"))
    assert response["status"] == "ok", response
    assert response["result"]["identical"]


def test_parameter_resource_and_connection_changes_are_reported():
    modified = STANDARD.replace("0.37", "0.52").replace("srgb_texture", "lin_rec709")
    modified = modified.replace("textures/底色.png", "textures/new.png")
    response = call("materialx.diff", before=STANDARD, after=modified)
    assert response["status"] == "ok", response
    result = response["result"]
    assert not result["identical"]
    assert any("specular_roughness" in c["path"] for c in result["changes"])
    assert any("color_space" in c["path"] for c in result["changes"])
    assert any("file" in c["path"] for c in result["changes"])
    disconnected = STANDARD.replace('nodename="albedo"', 'value="0.1, 0.2, 0.3"')
    result = call("materialx.diff", before=STANDARD, after=disconnected)["result"]
    assert any("source" in c["path"] for c in result["changes"])


def test_explicit_node_mapping_aligns_current_native_identifiers_and_rejects_collisions():
    renamed = STANDARD.replace("albedo", "renamed_image")
    result = call(
        "materialx.diff", before=STANDARD, after=renamed, node_mapping={"albedo": "renamed_image"}
    )
    assert result["result"]["identical"], result
    result = call(
        "materialx.diff", before=STANDARD, after=renamed, node_mapping={"albedo": "surface"}
    )
    assert result["status"] == "error"


def test_invalid_document_is_not_a_successful_comparison():
    assert call("materialx.diff", before=STANDARD, after="<broken>")["status"] == "error"


def test_external_implementation_declaration_changes_are_visible_without_execution():
    before = CUSTOM.replace(
        "</materialx>",
        '<implementation name="IM_external" '
        'nodedef="ND_iris_gain" target="genglsl" sourcecode="value_a"/></materialx>',
    )
    after = before.replace('sourcecode="value_a"', 'sourcecode="value_b"')
    result = call("materialx.diff", before=before, after=after)
    assert result["result"] and not result["result"]["identical"], result
