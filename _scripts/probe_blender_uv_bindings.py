"""只读检查实际网格绑定：命名 UV 是否确实为材质所有使用者的渲染默认 UV。"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

import bpy


def main():
    """读取原始完整场景，不保存；未绑定、缺 UV 和多种默认集都保留为明确状态。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    bpy.ops.wm.open_mainfile(filepath=str(args.source), load_ui=False)
    by_material = {m.name_full: {} for m in bpy.data.materials}
    for obj in bpy.data.objects:
        if not obj.material_slots:
            continue
        layers = getattr(obj.data, "uv_layers", [])
        active = next((layer.name for layer in layers if layer.active_render), None)
        observation = {
            "mesh": obj.data.name_full if obj.data else obj.name_full,
            "render_default": active,
            "uv_sets": [layer.name for layer in layers],
        }
        for slot in obj.material_slots:
            if slot.material:
                by_material[slot.material.name_full][observation["mesh"]] = observation
    digest = hashlib.sha256()
    with args.source.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    result = {
        "source_sha256": digest.hexdigest(),
        "version": bpy.app.version_string,
        "materials": {name: list(owners.values()) for name, owners in by_material.items()},
    }
    with args.out.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(
        json.dumps(
            {"materials": len(by_material), "bound": sum(bool(v) for v in by_material.values())}
        )
    )


if __name__ == "__main__":
    main()
