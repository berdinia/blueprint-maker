from __future__ import annotations

import io

from PIL import Image

from blueprint_pipeline.normalization import dhash, hamming_hex, normalize_image


def test_metadata_stripped_orientation_applied_and_size_limited(tmp_path):
    img = Image.new("RGB", (400, 200), "white")
    exif = Image.Exif()
    exif[0x0112] = 6  # orientation: rotate 90 CW
    exif[0x010F] = "SYNTHETIC-CAMERA"  # make
    p = tmp_path / "syn.jpg"
    img.save(p, format="JPEG", exif=exif.tobytes())
    data, rec = normalize_image(p, max_px=100, max_bytes=10_000_000)
    out = Image.open(io.BytesIO(data))
    assert out.format == "PNG" and max(out.size) == 100
    assert out.size == (50, 100)  # orientation applied before scaling
    assert not out.getexif() and "exif" not in out.info and not getattr(out, "text", {})
    assert b"SYNTHETIC-CAMERA" not in data
    assert (
        "exif_orientation_applied" in rec["operations"] and "metadata_stripped" in rec["operations"]
    )


def test_transparency_flattened_and_dhash_stable(tmp_path):
    p = tmp_path / "a.png"
    Image.new("RGBA", (64, 64), (0, 0, 0, 0)).save(p)
    data, rec = normalize_image(p, max_px=2048, max_bytes=10_000_000)
    assert Image.open(io.BytesIO(data)).getpixel((5, 5)) == (255, 255, 255)
    a = Image.new("L", (90, 80), 255)
    a.paste(0, (10, 10, 40, 70))
    b = a.point(lambda v: min(255, v + 8))
    assert hamming_hex(dhash(a), dhash(b)) <= 6
    assert dhash(a) == dhash(a.copy())
