"""Encode one canonical grid as a signed raster, without plotting or file IO."""

import base64
import math
import struct
import zlib


RASTER_VERSION = "v10-rgba-1.0.0"
NEGATIVE_RGB = (33, 102, 172)
NEUTRAL_RGB = (247, 247, 247)
POSITIVE_RGB = (178, 24, 43)
LEGEND_GRADIENT = f"linear-gradient(to right, rgb{NEGATIVE_RGB}, rgb{NEUTRAL_RGB}, rgb{POSITIVE_RGB})"


def render_raster(values: tuple[float | None, ...], rows: int, columns: int, colour_limit: float) -> str:
    """Return an inline PNG; row zero is image top and missing cells are clear.

    Values are in canonical row-major order. A finite positive symmetric limit
    is required. Invalid dimensions, values or scale raise ValueError. No source
    values are returned to the browser or reconstructed from raster colours.
    """
    if rows <= 0 or columns <= 0 or len(values) != rows * columns:
        raise ValueError("Raster dimensions do not match the canonical grid.")
    if not math.isfinite(colour_limit) or colour_limit <= 0:
        raise ValueError("Raster colour limit must be finite and positive.")
    pixels = bytearray()
    for row in range(rows):
        pixels.append(0)  # PNG filter: raw scanline; no vertical reversal.
        for value in values[row * columns:(row + 1) * columns]:
            if value is None:
                pixels.extend((0, 0, 0, 0))
                continue
            if not math.isfinite(value) or abs(value) > colour_limit:
                raise ValueError("Raster values must be finite and inside the declared scale.")
            endpoint = NEGATIVE_RGB if value < 0 else POSITIVE_RGB
            weight = abs(value) / colour_limit
            pixels.extend(round(a + (b - a) * weight) for a, b in zip(NEUTRAL_RGB, endpoint))
            pixels.append(255)

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", columns, rows, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(pixels))) + chunk(b"IEND", b"")
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")
