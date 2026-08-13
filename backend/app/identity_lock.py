from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from PIL import Image


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_identity_pack(
    *,
    project_id: str,
    product_images: list[Path],
    output_root: Path,
) -> Path:
    """Create an immutable, auditable product-reference pack.

    This deliberately does not guess a foreground mask. Fur, transparent
    parts and pale backgrounds make unattended segmentation unsafe. A source
    image with a real alpha channel is retained as a compositing asset;
    otherwise the original remains a conditioning/review reference only.
    """
    pack_root = output_root / "identity-pack"
    pack_root.mkdir(parents=True, exist_ok=True)
    assets: list[dict[str, object]] = []
    for index, source in enumerate(product_images, start=1):
        suffix = source.suffix.lower() or ".png"
        target = pack_root / f"product-{index:02d}{suffix}"
        shutil.copy2(source, target)
        with Image.open(target) as image:
            has_alpha = image.mode in {"RGBA", "LA"} and (
                image.getextrema()[-1][0] < 255
            )
            width, height = image.size
        assets.append({
            "path": str(target),
            "sha256": _sha256(target),
            "width": width,
            "height": height,
            "role": "identity_and_review_reference",
            "safe_for_direct_composite": bool(has_alpha),
        })
    manifest = {
        "version": "product-identity-pack-v1",
        "project_id": project_id,
        "policy": {
            "source_priority": "real_product_image",
            "generated_images_may_not_override_product_facts": True,
            "automatic_background_removal": False,
            "direct_composite_requires_real_alpha": True,
            "hero_and_cta_should_prefer_real_product_pixels": True,
        },
        "assets": assets,
    }
    target = pack_root / "manifest.json"
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def load_identity_pack(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
