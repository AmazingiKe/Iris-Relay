"""图片通道读取使用文件语义，避免把宿主扩展的灰度 RGB 缓存当作文件三通道。"""

import struct

import pytest


def test_exr_y_channel_is_gray_even_when_host_expands_rgb(tmp_path):
    from plugins.resources import image_header_channels

    channels = b"Y\0" + struct.pack("<iB3xii", 1, 0, 1, 1) + b"\0"
    header = b"\x76\x2f\x31\x01" + struct.pack("<I", 2)
    header += b"channels\0chlist\0" + struct.pack("<I", len(channels)) + channels + b"\0"
    path = tmp_path / "gray.exr"
    path.write_bytes(header)
    assert image_header_channels(path) == 1


@pytest.mark.parametrize("names, expected", [(("B", "G", "R"), 3), (("A", "B", "G", "R"), 4)])
def test_exr_rgb_and_alpha_are_distinguished(tmp_path, names, expected):
    from plugins.resources import image_header_channels

    channels = (
        b"".join(n.encode() + b"\0" + struct.pack("<iB3xii", 1, 0, 1, 1) for n in names) + b"\0"
    )
    path = tmp_path / "image.exr"
    path.write_bytes(
        b"\x76\x2f\x31\x01"
        + struct.pack("<I", 2)
        + b"channels\0chlist\0"
        + struct.pack("<I", len(channels))
        + channels
        + b"\0"
    )
    assert image_header_channels(path) == expected


@pytest.mark.parametrize("color_type, expected", [(0, 1), (2, 3), (6, 4)])
def test_png_header_channels(tmp_path, color_type, expected):
    from plugins.resources import image_header_channels

    path = tmp_path / "image.png"
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">IIBBBBB", 2, 2, 8, color_type, 0, 0, 0)
        + b"\0" * 4
        + struct.pack(">I", 0)
        + b"IEND"
        + b"\0" * 4
    )
    assert image_header_channels(path) == expected


def test_truncated_exr_is_reported_instead_of_guessed(tmp_path):
    from plugins.resources import image_header_channels

    path = tmp_path / "bad.exr"
    path.write_bytes(
        b"\x76\x2f\x31\x01" + struct.pack("<I", 2) + b"channels\0chlist\0" + struct.pack("<I", 1000)
    )
    with pytest.raises(ValueError):
        image_header_channels(path)


@pytest.mark.parametrize("kind, alpha, expected", [(3, False, 3), (3, True, 4), (2, True, 4)])
def test_png_palette_and_transparent_color_are_not_misclassified(tmp_path, kind, alpha, expected):
    from plugins.resources import image_header_channels

    header = (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">IIBBBBB", 2, 2, 8, kind, 0, 0, 0)
        + b"\0" * 4
    )
    if alpha:
        header += struct.pack(">I", 1) + b"tRNS" + b"\0" + b"\0" * 4
    path = tmp_path / "palette.png"
    path.write_bytes(header + struct.pack(">I", 0) + b"IEND" + b"\0" * 4)
    assert image_header_channels(path) == expected


@pytest.mark.parametrize(
    "data",
    [
        b"\x89PNG\r\n\x1a\n",
        b"\x76\x2f\x31\x01" + struct.pack("<I", 0x802),
        b"\x76\x2f\x31\x01" + struct.pack("<I", 2) + b"\0",
    ],
)
def test_unsupported_or_incomplete_headers_are_explicit(tmp_path, data):
    from plugins.resources import image_header_channels

    path = tmp_path / "bad"
    path.write_bytes(data)
    with pytest.raises(ValueError):
        image_header_channels(path)


def test_unknown_file_format_is_left_to_host_decoder(tmp_path):
    from plugins.resources import image_header_channels

    path = tmp_path / "other"
    path.write_bytes(b"unknown")
    assert image_header_channels(path) is None
