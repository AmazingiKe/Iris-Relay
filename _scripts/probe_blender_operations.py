"""只读盘点材质缓存中的混合与渐变配置，保留 Blender 原生渐变求值作为独立证据。"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import bpy


def main():
    """在独立后台进程读取材质和节点组，不保存源文件或改写其节点。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    with bpy.data.libraries.load(str(args.source), link=False) as (source, target):
        target.materials = list(source.materials)
    trees = [m.node_tree for m in target.materials if m and m.use_nodes]
    trees += list(bpy.data.node_groups)
    ramps, mixes = {}, Counter()
    for tree in trees:
        for node in tree.nodes:
            if node.bl_idname == "ShaderNodeMix":
                mixes[
                    (
                        node.data_type,
                        node.blend_type,
                        node.factor_mode,
                        node.clamp_factor,
                        node.clamp_result,
                    )
                ] += 1
            elif node.bl_idname == "ShaderNodeValToRGB":
                ramp = node.color_ramp
                data = {
                    "color_mode": ramp.color_mode,
                    "hue_interpolation": ramp.hue_interpolation,
                    "interpolation": ramp.interpolation,
                    "elements": [
                        {"position": e.position, "color": list(e.color)} for e in ramp.elements
                    ],
                }
                key = json.dumps(data, sort_keys=True)
                if key not in ramps:
                    ramps[key] = {
                        "configuration": data,
                        "samples": [
                            {"factor": f, "rgba": list(ramp.evaluate(f))}
                            for f in [-0.1, 0.0, 0.123, 0.5, 0.91, 1.0, 1.1]
                        ],
                    }
    result = {
        "version": bpy.app.version_string,
        "ramps": list(ramps.values()),
        "mixes": [{"configuration": list(k), "count": v} for k, v in mixes.most_common()],
    }
    with args.out.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(
        json.dumps(
            {
                "unique_ramps": len(ramps),
                "modes": Counter(
                    (v["configuration"]["color_mode"] + ":" + v["configuration"]["interpolation"])
                    for v in ramps.values()
                ),
            }
        )
    )


if __name__ == "__main__":
    main()
