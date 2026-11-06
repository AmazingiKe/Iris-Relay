"""真实 MaterialX SDK 的公开接口测试；不以模拟节点库代替标准库。"""

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import MaterialX as mx
import pytest

from iris_relay import RelayAPI


def call(operation, **payload):
    return RelayAPI().handle(
        {"api_version": "1", "request_id": "材质文档", "operation": operation, "payload": payload}
    )


STANDARD = """<materialx version="1.39">
  <image name="albedo" type="color3">
    <input name="file" type="filename" value="textures/底色.png" colorspace="srgb_texture" />
  </image>
  <standard_surface name="surface" type="surfaceshader">
    <input name="base_color" type="color3" nodename="albedo" />
    <input name="specular_roughness" type="float" value="0.37" />
  </standard_surface>
  <surfacematerial name="material" type="material">
    <input name="surfaceshader" type="surfaceshader" nodename="surface" />
  </surfacematerial>
</materialx>"""

CUSTOM = """<materialx version="1.39">
  <nodedef name="ND_iris_gain" node="iris_gain" version="1.0">
    <input name="in" type="color3" value="0.2, 0.3, 0.4" />
    <input name="gain" type="float" value="2" />
    <output name="out" type="color3" />
  </nodedef>
  <nodegraph name="NG_iris_gain" nodedef="ND_iris_gain">
    <multiply name="multiply" type="color3">
      <input name="in1" type="color3" interfacename="in" />
      <input name="in2" type="float" interfacename="gain" />
    </multiply>
    <output name="out" type="color3" nodename="multiply" />
  </nodegraph>
  <iris_gain name="gain" type="color3" nodedef="ND_iris_gain">
    <input name="gain" type="float" value="3" />
  </iris_gain>
  <standard_surface name="surface" type="surfaceshader">
    <input name="base_color" type="color3" nodename="gain" />
  </standard_surface>
  <surfacematerial name="material" type="material">
    <input name="surfaceshader" type="surfaceshader" nodename="surface" />
  </surfacematerial>
</materialx>"""


def test_capabilities_and_official_definitions():
    result = call("capabilities")["result"]
    assert "materialx.normalize" in result["operations"]
    assert result["materialx"]["version"] == mx.getVersionString()
    response = call("materialx.definitions", category="image")
    assert response["status"] == "ok"
    definitions = response["result"]["definitions"]
    definition = next(n for n in definitions if n["name"] == "ND_image_color3")
    assert definition["inputs"]["file"]["type"] == "filename"
    assert definition["outputs"]["out"]["type"] == "color3"
    assert len(definitions) > 1  # 不止一个自制图片语义。


def test_standard_roundtrip_preserves_resource_connection_and_source():
    request = {"document": STANDARD}
    original = deepcopy(request)
    result = call("materialx.normalize", **request)
    assert result["status"] == "ok", result
    assert request == original
    assert result["result"]["source"] == STANDARD
    assert result["result"]["appearance_verified"] is False
    doc = mx.createDocument()
    mx.readFromXmlString(doc, result["result"]["document"])
    assert doc.getNode("albedo").getInput("file").getValueString() == "textures/底色.png"
    assert doc.getNode("albedo").getInput("file").getColorSpace() == "srgb_texture"
    assert doc.getNode("surface").getInput("base_color").getNodeName() == "albedo"
    assert not doc.getNodeDefs()  # 不把整个标准库嵌入每个输出文档。


def test_custom_definition_and_graph_survive_roundtrip_and_can_flatten():
    result = call("materialx.normalize", document=CUSTOM)
    assert result["status"] == "ok", result
    assert 'nodedef="ND_iris_gain"' in result["result"]["document"]
    result = call("materialx.normalize", document=CUSTOM, flatten=True)
    assert result["status"] == "ok", result
    doc = mx.createDocument()
    mx.readFromXmlString(doc, result["result"]["document"])
    assert not any(n.getCategory() == "iris_gain" for n in doc.getNodes())
    node = next(n for n in doc.getNodes() if n.getCategory() == "multiply")
    assert node.getInput("in2").getValue() == 3
    assert doc.getNode("surface").getInput("base_color").getNodeName() == node.getName()


def test_opaque_custom_node_is_preserved_with_no_execution_claim():
    document = """<materialx version="1.39">
      <nodedef name="ND_vendor_noise" node="vendor_noise">
        <input name="seed" type="integer" value="42" />
        <output name="out" type="color3" />
      </nodedef>
      <vendor_noise name="noise" type="color3" nodedef="ND_vendor_noise" />
    </materialx>"""
    result = call("materialx.normalize", document=document)
    assert result["status"] == "warning", result
    assert result["result"]["valid"] is True
    assert "ND_vendor_noise" in result["result"]["document"]
    assert any(i["code"] == "MATERIALX_IMPLEMENTATION_MISSING" for i in result["result"]["issues"])


@pytest.mark.parametrize(
    "document",
    [
        STANDARD.replace('nodename="albedo"', 'nodename="missing"'),
        STANDARD.replace('name="base_color" type="color3"', 'name="base_color" type="float"'),
        STANDARD.replace('value="0.37"', 'value="nan"'),
        STANDARD.replace('<image name="albedo"', '<not_registered name="albedo"'),
    ],
)
def test_invalid_graph_is_rejected_without_losing_original(document):
    result = call("materialx.normalize", document=document)
    assert result["status"] == "error", result
    assert result["result"]["source"] == document
    assert result["result"]["document"] is None
    assert result["result"]["valid"] is False


@pytest.mark.parametrize(
    "document",
    [
        (
            '<materialx version="1.39"><xi:include xmlns:xi="http://www.w3.org/2001'
            '/XInclude" href="secret.mtlx" /></materialx>'
        ),
        (
            '<!DOCTYPE materialx [<!ENTITY x SYSTEM "file:///secret">]><materialx v'
            'ersion="1.39">&x;</materialx>'
        ),
        '<materialx version="1.38" />',
        (
            '<materialx version="1.39"><nodedef name="ND_image_color3" node="image"'
            '><output name="out" type="color3" /></nodedef></materialx>'
        ),
        "<broken",
    ],
)
def test_unsafe_or_unsupported_documents_are_not_loaded(document):
    result = call("materialx.validate", document=document)
    assert result["status"] == "error"
    assert result["error"]["code"] != "UNSUPPORTED_OPERATION"


def test_request_isolation_and_strict_fields():
    api = RelayAPI()
    for document in (CUSTOM, STANDARD):
        response = api.handle(
            {
                "api_version": "1",
                "request_id": "隔离",
                "operation": "materialx.normalize",
                "payload": {"document": document},
            }
        )
        assert response["status"] == "ok", response
    assert "ND_iris_gain" not in response["result"]["document"]
    assert call("materialx.normalize", document=STANDARD, flatten="yes")["status"] == "error"
    assert call("materialx.validate", document=STANDARD, extra=True)["status"] == "error"


def test_custom_definitions_can_be_queried_and_standard_library_is_not_mutated():
    response = call("materialx.definitions", document=CUSTOM, category="iris_gain")
    assert response["status"] == "ok", response
    assert response["result"]["definitions"][0]["inputs"]["gain"]["default"] == "2"
    assert call("materialx.definitions", category="iris_gain")["result"]["definitions"] == []


def test_recursive_custom_graph_is_rejected_before_expansion():
    document = (
        CUSTOM.replace(
            '<multiply name="multiply" type="color3">',
            '<iris_gain name="multiply" type="color3" nodedef="ND_iris_gain">',
        )
        .replace("</multiply>", "</iris_gain>")
        .replace('name="in1"', 'name="in"')
        .replace('name="in2"', 'name="gain"')
    )
    result = call("materialx.normalize", document=document, flatten=True)
    assert result["status"] == "error", result
    assert result["result"]["source"] == document


def test_cyclic_links_and_duplicate_names_fail_validation():
    for content in (
        (
            '<add name="a" type="float"><input name="in1" type="float" nodename="b"'
            ' /></add><add name="b" type="float"><input name="in1" type="float" nod'
            'ename="a" /></add>'
        ),
        '<constant name="a" type="float" /><constant name="a" type="float" />',
    ):
        response = call(
            "materialx.validate", document='<materialx version="1.39">' + content + "</materialx>"
        )
        assert response["status"] == "error", response


def test_external_implementation_is_preserved_without_execution(tmp_path):
    missing = tmp_path / "never_execute.osl"
    start = CUSTOM.index("  <nodegraph")
    end = CUSTOM.index("</nodegraph>") + len("</nodegraph>")
    document = CUSTOM[:start] + CUSTOM[end:]
    document = document.replace(
        "</materialx>",
        '<implementation name="IM_iris_gain" nodedef="ND_iris_gain" target="genosl" '
        f'file="{missing.as_posix()}" function="gain" /></materialx>',
    )
    result = call("materialx.normalize", document=document)
    assert result["status"] in {"ok", "warning"}, result
    assert missing.as_posix() in result["result"]["document"]
    assert not missing.exists()


def test_materialx_subprocess_and_public_example(tmp_path):
    from iris_relay.plugin import JsonCommandService

    root = Path(__file__).resolve().parents[1]
    service = JsonCommandService(
        [sys.executable, "-m", "iris_relay", "--dictionaries", str(root / "dictionaries")]
    )
    response = service.handle(
        {
            "api_version": "1",
            "request_id": "本地子进程",
            "operation": "materialx.normalize",
            "payload": {"document": CUSTOM},
        }
    )
    assert response["status"] == "ok", response
    assert response["result"]["source"] == CUSTOM
    result = subprocess.run(
        [
            sys.executable,
            str(root / "_examples/materialx_demo.py"),
            "--out",
            str(tmp_path / "demo"),
        ],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["materialx_saved"] is True
    saved = (tmp_path / "demo/material.mtlx").read_text("utf-8")
    assert call("materialx.validate", document=saved)["status"] == "ok"
    assert "ND_iris_surface" in saved


def test_old_source_host_without_sdk_can_use_legacy_operations():
    root = Path(__file__).resolve().parents[1]
    code = """import json, sys
sys.path.insert(0, sys.argv[1])
from iris_relay import RelayAPI
api = RelayAPI()
def request(op, payload):
    return api.handle(dict(api_version="1", request_id="test", operation=op, payload=payload))
print(json.dumps([request("capabilities", {}), request("semantics", {}),
                  request("materialx.validate", {"document": "unused"})]))
"""
    result = subprocess.run(
        [sys.executable, "-S", "-c", code, str(root)], capture_output=True, check=False
    )
    assert result.returncode == 0, result.stderr
    capabilities, legacy, missing = json.loads(result.stdout)
    assert capabilities["result"]["materialx"]["available"] is False
    assert legacy["status"] == "ok"
    assert missing["error"]["code"] == "MATERIALX_UNAVAILABLE"


@pytest.mark.parametrize(
    "document",
    [
        '<materialx version="1.39">' + " " * (8 * 1024 * 1024) + "</materialx>",
        '<materialx version="1.39">' + "<nodegraph>" * 65 + "</nodegraph>" * 65 + "</materialx>",
    ],
)
def test_document_resource_limits(document):
    response = call("materialx.normalize", document=document)
    assert response["status"] == "error", response
    assert response["result"]["issues"][0]["code"] == "MATERIALX_LIMIT"


@pytest.mark.parametrize(
    "implementation",
    [
        '<implementation name="IM_override" nodedef="ND_image_color3" '
        'target="genosl" file="override.osl" function="override" />',
        '<nodegraph name="NG_override" nodedef="ND_image_color3">'
        '<constant name="c" type="color3" />'
        '<output name="out" type="color3" nodename="c" /></nodegraph>',
    ],
)
def test_custom_library_cannot_override_standard_implementations(implementation):
    document = '<materialx version="1.39">' + implementation + "</materialx>"
    response = call("materialx.normalize", document=document)
    assert response["status"] == "error", response
    assert any(i["code"] == "MATERIALX_LIBRARY_CONFLICT" for i in response["result"]["issues"])
