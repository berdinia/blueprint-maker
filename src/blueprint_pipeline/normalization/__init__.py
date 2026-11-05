"""Normalization: derive a model-ready, metadata-free copy of each source.

The original evidence file is never modified. For each usable source a PNG is
written to ``<work>/normalized/<source_id>.png`` with:

* EXIF orientation applied, then **all** metadata (EXIF, XMP, ICC, text chunks)
  dropped — nothing but pixels is sent to the local model;
* conversion to RGB or greyscale;
* downscaling so the longest side is ``inference.max_image_px`` and the encoded
  size is at most ``inference.max_image_bytes``.

A record ``<work>/normalized/<source_id>.json`` ties the copy to the input
checksum, the parameters used and a perceptual hash (dHash) used by
source-family analysis.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from ..io.atomic import sha256_bytes

NORMALIZATION_VERSION = "1"


def dhash(img: Image.Image, size: int = 8) -> str:
    """64-bit difference hash (hex). Deterministic for a given Pillow version."""
    g = img.convert("L").resize((size + 1, size), Image.Resampling.LANCZOS)
    px = g.tobytes()  # mode "L": one byte per pixel; stable across Pillow versions
    bits = 0
    for row in range(size):
        for col in range(size):
            left = px[row * (size + 1) + col]
            right = px[row * (size + 1) + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return f"{bits:0{size * size // 4}x}"


def hamming_hex(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def normalize_image(path: Path, *, max_px: int, max_bytes: int) -> tuple[bytes, dict[str, Any]]:
    """Return ``(png_bytes, record)``. Raises on undecodable input."""
    with Image.open(path) as im:
        im.load()
        original_size = list(im.size)
        ops: list[str] = []
        transposed = ImageOps.exif_transpose(im)
        if transposed is not None and transposed.size != im.size:
            ops.append("exif_orientation_applied")
        work = transposed if transposed is not None else im
        mode = "L" if work.mode in ("1", "L", "LA", "I;16", "I") else "RGB"
        if work.mode in ("RGBA", "LA", "P"):
            # Flatten transparency onto white so the model sees what a viewer sees.
            rgba = work.convert("RGBA")
            bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            bg.alpha_composite(rgba)
            work = bg.convert(mode)
            ops.append("alpha_flattened_on_white")
        else:
            work = work.convert(mode)
        # A fresh image carries no metadata at all.
        clean = Image.new(work.mode, work.size)
        clean.paste(work)
        ops.append("metadata_stripped")
        scale = 1.0
        longest = max(clean.size)
        if longest > max_px:
            scale = max_px / longest
        while True:
            if scale < 1.0:
                new = (max(1, round(clean.size[0] * scale)), max(1, round(clean.size[1] * scale)))
                out_img = clean.resize(new, Image.Resampling.LANCZOS)
            else:
                out_img = clean
            buf = io.BytesIO()
            out_img.save(buf, format="PNG", optimize=False, compress_level=6)
            data = buf.getvalue()
            if len(data) <= max_bytes or scale < 0.05:
                break
            scale *= 0.8
        if scale < 1.0:
            ops.append(f"downscaled_x{scale:.4f}")
        record = {
            "normalization_version": NORMALIZATION_VERSION,
            "original_size_px": original_size,
            "normalized_size_px": list(out_img.size),
            "mode": out_img.mode,
            "scale": round(scale, 6),
            "operations": ops,
            "dhash": dhash(out_img),
            "normalized_sha256": sha256_bytes(data),
            "normalized_bytes": len(data),
        }
        if len(data) > max_bytes:
            record["oversize"] = True
        return data, record


__all__ = ["NORMALIZATION_VERSION", "dhash", "hamming_hex", "normalize_image"]
