"""Deterministic canvas normalization for model-generated story keyframes.

Image APIs occasionally return a valid image whose dimensions do not match the
requested ratio.  Passing that image straight into a video model makes the
reference aspect ratio dominate the generated clip.  This module guarantees a
known delivery canvas without cropping the important subject out of frame.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image


CANVAS_SIZES: dict[str, tuple[int, int]] = {
    "16:9": (1536, 864),
    "9:16": (864, 1536),
    "1:1": (1200, 1200),
}


def canvas_size(aspect_ratio: str) -> tuple[int, int]:
    """Return the standard production canvas for a supported ratio."""
    return CANVAS_SIZES.get(aspect_ratio, CANVAS_SIZES["16:9"])


def normalize_to_canvas(
    source: Path,
    destination: Path,
    *,
    aspect_ratio: str,
) -> tuple[int, int]:
    """Write an exact-ratio full-bleed JPEG using a centered cover crop.

    Generated keyframes must never introduce blurred padding or letterbox bands:
    those artifacts become animated defects in image-to-video generation.
    """
    target_width, target_height = canvas_size(aspect_ratio)
    with Image.open(source) as opened:
        image = opened.convert("RGB")

    cover_scale = max(target_width / image.width, target_height / image.height)
    covered = image.resize(
        (
            max(1, round(image.width * cover_scale)),
            max(1, round(image.height * cover_scale)),
        ),
        Image.Resampling.LANCZOS,
    )
    left = max(0, (covered.width - target_width) // 2)
    top = max(0, (covered.height - target_height) // 2)
    output = covered.crop((left, top, left + target_width, top + target_height))
    destination.parent.mkdir(parents=True, exist_ok=True)
    output.save(destination, format="JPEG", quality=93, optimize=True)
    return target_width, target_height


def image_matches_canvas(path: Path, aspect_ratio: str) -> bool:
    expected = canvas_size(aspect_ratio)
    with Image.open(path) as image:
        return image.size == expected
