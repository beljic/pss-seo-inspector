"""Read image dimensions from the first bytes of PNG, GIF, JPEG and WebP files."""

from __future__ import annotations

import struct


def image_size(data: bytes) -> tuple[int, int] | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
        return int(width), int(height)
    if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        width, height = struct.unpack("<HH", data[6:10])
        return int(width), int(height)
    if data.startswith(b"\xff\xd8"):
        return _jpeg(data)
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return _webp(data)
    return None


def _jpeg(data: bytes) -> tuple[int, int] | None:
    i = 2
    sof = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker == 0xFF:
            i += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        (length,) = struct.unpack(">H", data[i + 2 : i + 4])
        if marker in sof and i + 9 <= len(data):
            height, width = struct.unpack(">HH", data[i + 5 : i + 9])
            return int(width), int(height)
        i += 2 + length
    return None


def _webp(data: bytes) -> tuple[int, int] | None:
    chunk = data[12:16]
    if chunk == b"VP8 " and len(data) >= 30:
        width, height = struct.unpack("<HH", data[26:30])
        return int(width & 0x3FFF), int(height & 0x3FFF)
    if chunk == b"VP8L" and len(data) >= 25:
        b = data[21:25]
        width = 1 + (((b[1] & 0x3F) << 8) | b[0])
        height = 1 + (((b[3] & 0x0F) << 10) | (b[2] << 2) | ((b[1] & 0xC0) >> 6))
        return width, height
    if chunk == b"VP8X" and len(data) >= 30:
        width = 1 + int.from_bytes(data[24:27], "little")
        height = 1 + int.from_bytes(data[27:30], "little")
        return width, height
    return None
