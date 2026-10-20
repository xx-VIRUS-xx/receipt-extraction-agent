"""Load an image, fix orientation, bound its size, and compute a cheap quality score.

The quality score is informational only (weight 0 in confidence). Measured on the 45 sample images it
ranges 0.78-1.00 and mostly reflects how much white page surrounds the receipt, not legibility, so it
cannot detect an unreadable image. Legibility is judged from the model's own output instead.
"""
from __future__ import annotations

import io
import statistics
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps

MAX_EDGE = 1600
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


@dataclass
class Prepared:
    jpeg: bytes
    media_type: str
    width: int
    height: int
    sharpness: float
    contrast: float
    quality: float  # 0..1


def _quality(gray: Image.Image) -> tuple[float, float, float]:
    small = gray.copy()
    small.thumbnail((800, 800))
    edges = small.filter(ImageFilter.FIND_EDGES)
    sharp = statistics.pvariance(edges.tobytes()) if small.size[0] * small.size[1] else 0.0
    contrast = statistics.pstdev(small.tobytes())
    q_sharp = min(1.0, sharp / 400.0)
    q_contrast = min(1.0, contrast / 50.0)
    shortest = min(gray.size)
    q_res = min(1.0, shortest / 500.0)
    return sharp, contrast, round(0.5 * q_sharp + 0.3 * q_contrast + 0.2 * q_res, 3)


def prepare(path: Path) -> Prepared:
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
    sharp, contrast, quality = _quality(im.convert("L"))
    if max(im.size) > MAX_EDGE:
        im.thumbnail((MAX_EDGE, MAX_EDGE), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    return Prepared(buf.getvalue(), "image/jpeg", im.size[0], im.size[1], round(sharp, 1), round(contrast, 1), quality)
