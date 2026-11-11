"""官方文档的线性 Rec.709 上下文有明确语义，未知全局色彩解释继续阻断。"""

import json
from pathlib import Path

import pytest

from _scripts.native_fixture import renderer
from _tests.test_materialx_bridge import call
from iris_relay import RelayAPI

ROOT = Path(__file__).resolve().parents[1]
DOCUMENT = """<materialx version="1.39" colorspace="{space}">
<standard_surface name="surface" type="surfaceshader">
<input name="base_color" type="color3" value="0.1, 0.3, 0.7"/>
</standard_surface>
<surfacematerial name="material" type="material">
<input name="surfaceshader" type="surfaceshader" nodename="surface"/>
</surfacematerial></materialx>"""


@pytest.mark.parametrize("name", ["blender-cycles", "maya-arnold", "cinema4d-redshift"])
def test_linear_rec709_context_accepted_without_transforming_values(name):
    profile = json.loads((ROOT / "dictionaries" / (name + ".json")).read_text("utf8"))
    result = call(
        RelayAPI([profile]),
        "materialx.convert",
        document=DOCUMENT.format(space="lin_rec709"),
        target=renderer(profile),
    )
    assert result["result"]["material"], result


def test_unknown_color_context_is_not_silently_reinterpreted():
    profile = json.loads((ROOT / "dictionaries/blender-cycles.json").read_text("utf8"))
    result = call(
        RelayAPI([profile]),
        "materialx.convert",
        document=DOCUMENT.format(space="acescg"),
        target=renderer(profile),
    )
    assert result["status"] == "error"
