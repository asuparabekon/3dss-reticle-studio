"""Checked layout of the RGBA8 and BC5 DDS files used by reticle atlases."""

from dataclasses import dataclass
import struct


@dataclass(frozen=True)
class Mip:
    width: int
    height: int
    offset: int
    size: int


@dataclass(frozen=True)
class Layout:
    compressed: bool
    mips: tuple[Mip, ...]


def layout(data):
    if len(data) < 128 or data[:4] != b"DDS ":
        raise ValueError("Not a DDS file")

    def integer(offset):
        return struct.unpack_from("<I", data, offset)[0]

    height, width, count = integer(12), integer(16), integer(28)
    if integer(4) != 124 or integer(76) != 32 or integer(112) != 0:
        raise ValueError("Unsupported DDS header")
    if not 1 <= width <= 16384 or not 1 <= height <= 16384:
        raise ValueError("Invalid DDS dimensions")
    if not 1 <= count <= max(width, height).bit_length():
        raise ValueError("Invalid DDS mip count")
    compressed = data[84:88] == b"DX10"
    if compressed:
        if len(data) < 148 or struct.unpack_from("<4I", data, 128) != (83, 3, 0, 1):
            raise ValueError("Expected a 2D BC5_UNORM DDS")
    elif struct.unpack_from("<7I", data, 80) != (
        0x41,
        0,
        32,
        0xFF,
        0xFF00,
        0xFF0000,
        0xFF000000,
    ):
        raise ValueError("Expected an RGBA8 or BC5 DDS")
    offset = 148 if compressed else 128
    mips = []
    for level in range(count):
        w, h = max(1, width >> level), max(1, height >> level)
        size = -(-w // 4) * -(-h // 4) * 16 if compressed else w * h * 4
        if size > len(data) - offset:
            raise ValueError("Truncated DDS mip")
        mips.append(Mip(w, h, offset, size))
        offset += size
    if offset != len(data):
        raise ValueError("DDS size mismatch")
    return Layout(compressed, tuple(mips))
