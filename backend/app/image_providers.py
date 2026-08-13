from __future__ import annotations

from dataclasses import dataclass
from os import getenv
import httpx

from .providers import MiniMaxOfficialVideoProvider


@dataclass
class ImageGenerationResult:
    model_id: str
    output_urls: list[str]
    estimated_cost: float


class VolcArkSeedreamProvider:
    """Official Seedream image API used to create reviewed story keyframes."""

    name = "seedream-official"

    def __init__(self) -> None:
        self.api_key = getenv("ARK_API_KEY", "").strip()
        if not self.api_key:
            raise RuntimeError("尚未配置 ARK_API_KEY，不能生成关键帧。")
        self.api_base = getenv(
            "ARK_API_BASE",
            "https://ark.cn-beijing.volces.com/api/v3",
        ).rstrip("/")
        self.model = getenv("ARK_IMAGE_MODEL", "doubao-seedream-4-5-251128").strip()
        self.unit_price = float(getenv("ARK_SEEDREAM_PRICE_PER_IMAGE", "0.25"))

    @staticmethod
    def _size(aspect_ratio: str) -> str:
        return {
            "16:9": "2560x1440",
            "9:16": "1440x2560",
            "1:1": "2048x2048",
        }.get(aspect_ratio, "1440x2560")

    def build_payload(
        self,
        *,
        prompt: str,
        reference_images: list[str],
        aspect_ratio: str,
        max_images: int = 1,
    ) -> dict[str, object]:
        references = [
            MiniMaxOfficialVideoProvider._data_uri(path)
            for path in reference_images[:10]
        ]
        payload: dict[str, object] = {
            "model": self.model,
            "prompt": prompt,
            "size": self._size(aspect_ratio),
            "sequential_image_generation": "auto" if max_images > 1 else "disabled",
            "response_format": "url",
            "watermark": False,
        }
        if references:
            payload["image"] = references
        if max_images > 1:
            payload["sequential_image_generation_options"] = {
                "max_images": min(max_images, 15 - len(references)),
            }
        return payload

    async def generate(
        self,
        *,
        prompt: str,
        reference_images: list[str],
        aspect_ratio: str,
        max_images: int = 1,
    ) -> ImageGenerationResult:
        payload = self.build_payload(
            prompt=prompt,
            reference_images=reference_images,
            aspect_ratio=aspect_ratio,
            max_images=max_images,
        )
        async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
            response = await client.post(
                f"{self.api_base}/images/generations",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
        body = response.json()
        urls = [
            str(item["url"])
            for item in body.get("data", [])
            if item.get("url")
        ]
        if not urls:
            raise RuntimeError("Seedream 任务成功，但没有返回关键帧图片。")
        return ImageGenerationResult(
            model_id=str(body.get("model") or self.model),
            output_urls=urls,
            estimated_cost=round(len(urls) * self.unit_price, 4),
        )
