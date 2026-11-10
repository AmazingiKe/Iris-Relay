"""资源枚举和改写走公开核心接口，宿主不自行解释 MaterialX 的前缀或定义。"""

from _tests.test_materialx_bridge import call
from iris_relay import RelayAPI

DOCUMENT = """<materialx version="1.39" fileprefix="source/">
<image name="texture" type="color3" nodedef="ND_image_color3">
<input name="file" type="filename" value="color.png" colorspace="srgb_texture"/>
</image></materialx>"""


def test_resource_query_resolves_prefix_and_does_not_read_file():
    response = call(RelayAPI(), "materialx.resources", document=DOCUMENT)
    assert response["status"] == "ok", response
    resources = response["result"]["resources"]
    assert resources == [
        {
            "path": "texture/file",
            "uri": "color.png",
            "resolved_uri": "source/color.png",
            "color_space": "srgb_texture",
        }
    ]


def test_full_resource_rewrite_removes_prefix_and_preserves_color_interpretation():
    response = call(
        RelayAPI(),
        "materialx.rewrite_resources",
        document=DOCUMENT,
        resources={"texture/file": {"expected_uri": "color.png", "uri": "textures/copied.png"}},
    )
    assert response["status"] == "ok", response
    updated = response["result"]["document"]
    query = call(RelayAPI(), "materialx.resources", document=updated)
    assert query["result"]["resources"] == [
        {
            "path": "texture/file",
            "uri": "textures/copied.png",
            "resolved_uri": "textures/copied.png",
            "color_space": "srgb_texture",
        }
    ]
    assert 'fileprefix="source/"' in DOCUMENT


def test_stale_or_incomplete_resource_mapping_is_rejected():
    for resources in (
        {},
        {"texture/file": {"expected_uri": "wrong.png", "uri": "textures/a.png"}},
        {"missing": {"expected_uri": "color.png", "uri": "x.png"}},
    ):
        result = call(
            RelayAPI(), "materialx.rewrite_resources", document=DOCUMENT, resources=resources
        )
        assert result["status"] == "error", result
