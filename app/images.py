"""Shrink walkthrough photos before the AI sees them.

Phone photos are 12 MP (4032x3024, 2-5 MB). The model does not need that much detail to tell
a restroom from an office, so every photo is resized to at most 1568 px on the long side and
re-saved as JPEG. That cuts upload size about 10x and image tokens a lot (see tokenomics in the report).
"""
from __future__ import annotations

import io

from PIL import Image, ImageOps

from .config import LIMITS


def shrink_photo(data: bytes) -> tuple[bytes, dict]:
    """Return (jpeg_bytes, info). Falls back to the original bytes if the image can't be opened."""
    try:
        im = Image.open(io.BytesIO(data))
        im = ImageOps.exif_transpose(im)          # respect phone rotation
        orig = im.size
        im = im.convert("RGB")
        im.thumbnail((LIMITS["photo_max_side_px"], LIMITS["photo_max_side_px"]))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=LIMITS["photo_jpeg_quality"], optimize=True)
        out = buf.getvalue()
        return out, {"original_px": list(orig), "sent_px": list(im.size),
                     "original_kb": round(len(data) / 1024), "sent_kb": round(len(out) / 1024)}
    except Exception as e:  # noqa: BLE001
        return data, {"error": f"could not resize: {e}", "original_kb": round(len(data) / 1024)}
