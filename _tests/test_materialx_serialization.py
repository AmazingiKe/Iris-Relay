"""SDK 写出字符串后必须仍是合法且等价的 XML，资源名称中的特殊字符不能破坏文档。"""

from _tests.test_materialx_bridge import call
from iris_relay import RelayAPI


def test_normalize_escapes_resource_filename_without_changing_its_value():
    document = """<materialx version="1.39">
<image name="image" type="color3" nodedef="ND_image_color3">
<input name="file" type="filename" value="textures/a&amp;b&lt;1&gt;.png"/>
</image></materialx>"""
    api = RelayAPI()
    result = call(api, "materialx.normalize", document=document)
    assert result["status"] == "ok", result
    normalized = result["result"]["document"]
    validation = call(api, "materialx.validate", document=normalized)
    assert validation["status"] == "ok", validation
    resources = call(api, "materialx.resources", document=normalized)
    assert resources["result"]["resources"][0]["uri"] == "textures/a&b<1>.png"
