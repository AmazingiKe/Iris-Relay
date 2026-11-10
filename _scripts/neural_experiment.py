"""首轮实验的固定几何与文件协议；不包含宿主转换规则或训练好的参数。"""

import hashlib
import json
import math
import struct
import zlib
from pathlib import Path

FEATURE_SCHEMA = "albedo4x4-roughness4x4-params4/v1"


def write_uv_png(path):
    """写入无色彩附加块的 RGB 方向图，用独立像素检验两宿主的实际 UV 方向。"""

    def chunk(kind, data):
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    pixels = bytearray()
    for y in range(128):
        pixels.append(0)
        for x in range(128):
            pixels.extend(
                (
                    round(255 * x / 127),
                    round(255 * (1 - y / 127)),
                    64 + 128 * ((x // 32 + y // 32) % 2),
                )
            )
    Path(path).write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 128, 128, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(pixels)))
        + chunk(b"IEND", b"")
    )


def validate_split(assets, cases):
    """拒绝跨集合的同族、共享贴图和变体泄漏；未知资产或重复样本同样失败。"""
    lookup, families, resources, names = {}, {}, {}, set()
    for asset in assets:
        if asset["id"] in lookup or asset["split"] not in {"train", "validation", "test"}:
            raise ValueError("资产身份重复或集合名称无效")
        lookup[asset["id"]] = asset
        for mapping, keys in ((families, [asset["family"]]), (resources, asset["resource_sha256"])):
            for key in keys:
                if key in mapping and mapping[key] != asset["split"]:
                    raise ValueError("同族或共享实际贴图跨越数据集合")
                mapping[key] = asset["split"]
    for case in cases:
        asset = lookup.get(case["asset"])
        if (
            case["id"] in names
            or asset is None
            or any(case[k] != asset[k] for k in ("family", "split"))
        ):
            raise ValueError("样本重复、身份未知或变体跨越资产集合")
        names.add(case["id"])


def save_json(path, data):
    """只写本次实验拥有的报告，拒绝非有限实验记录。"""
    Path(path).write_text(
        json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )


def fingerprint(data):
    """将固定协议与几何按排序 JSON 计算摘要，防止条件在校正前后漂移。"""
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def sphere_mesh(latitude=32, longitude=64):
    """构造 Y 向上的同一单位球面、面绕序和逐面 UV；两宿主使用完全相同的数值。"""
    vertices = [[0.0, 1.0, 0.0]]
    for i in range(1, latitude):
        phi = math.pi * i / latitude
        for j in range(longitude):
            theta = 2 * math.pi * j / longitude
            vertices.append(
                [math.sin(phi) * math.sin(theta), math.cos(phi), math.sin(phi) * math.cos(theta)]
            )
    south = len(vertices)
    vertices.append([0.0, -1.0, 0.0])

    def ring(i, j):
        return 1 + (i - 1) * longitude + j % longitude

    faces, uvs = [], []
    for j in range(longitude):
        u0, u1 = j / longitude, (j + 1) / longitude
        faces.append([0, ring(1, j), ring(1, j + 1)])
        uvs.append([[(u0 + u1) / 2, 1.0], [u0, 1 - 1 / latitude], [u1, 1 - 1 / latitude]])
        for i in range(1, latitude - 1):
            faces.append([ring(i, j), ring(i + 1, j), ring(i + 1, j + 1), ring(i, j + 1)])
            uvs.append(
                [
                    [u0, 1 - i / latitude],
                    [u0, 1 - (i + 1) / latitude],
                    [u1, 1 - (i + 1) / latitude],
                    [u1, 1 - i / latitude],
                ]
            )
        faces.append([south, ring(latitude - 1, j + 1), ring(latitude - 1, j)])
        uvs.append([[(u0 + u1) / 2, 0.0], [u1, 1 / latitude], [u0, 1 / latitude]])
    return {
        "vertices": vertices,
        "faces": faces,
        "uvs": uvs,
        "additional_uv_sets": {
            "DetailUV": [[[u * 0.5 + 0.1, v * 0.75 + 0.05] for u, v in face] for face in uvs]
        },
    }


def fixed_conditions():
    """在挑选与训练前冻结条件；首轮仅研究均匀环境照明，不推断任意灯光泛化。"""
    return {
        "schema": "iris.n1.uniform-probe/v1",
        "resolution": [64, 64],
        "working_space": "linear_rec709",
        "environment_radiance": [0.5, 0.5, 0.5],
        "camera": {
            "projection": "orthographic",
            "width": 2.7,
            "position": [0, 0, 4],
            "rotation": [0, 0, 0],
            "near": 0.001,
            "far": 1000,
        },
        "background_alpha": 0,
        "pixel_aspect": 1.0,
        "device_aspect": 1.0,
        "seed": 20261111,
        "cycles": {
            "version": "5.2.3",
            "samples": 64,
            "bounces": 8,
            "threads": 8,
            "denoising": False,
            "dither": 0.0,
        },
        "arnold": {
            "version": "7.2.5.3",
            "aa": 4,
            "diffuse_samples": 2,
            "specular_samples": 2,
            "threads": 8,
            "abort_on_license_fail": True,
        },
        "feature_schema": FEATURE_SCHEMA,
        "mask_erosion": 2,
        "loss": "masked_mse_log1p_rgb",
        "search_min_relative_improvement": 0.005,
        "search_delta": {"roughness": [-0.12, 0.0, 0.12], "specular": [-0.25, 0.0, 0.25]},
        "train_seed": 20261111,
        "architecture": [68, 32, 16, 2],
        "activations": ["relu", "relu", "tanh"],
        "learning_rate": 0.001,
        "epochs": 800,
        "patience": 100,
        "batch_size": 32,
        "weight_decay": 0.001,
    }
