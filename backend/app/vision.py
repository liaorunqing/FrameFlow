from __future__ import annotations

import base64
import json
import re
from io import BytesIO
from os import getenv
from pathlib import Path
from typing import Literal

import httpx
from PIL import Image, ImageOps
from pydantic import BaseModel, Field


class AssetVisualProfile(BaseModel):
    asset_type: Literal["product", "character", "scene", "brand"]
    summary: str
    immutable_features: list[str] = Field(default_factory=list)
    dominant_colors: list[str] = Field(default_factory=list)
    geometry_or_identity: list[str] = Field(default_factory=list)
    quality_score: int = Field(ge=0, le=100)
    generation_risks: list[str] = Field(default_factory=list)


class FrameQualityReview(BaseModel):
    passed: bool
    overall_score: int = Field(ge=0, le=100)
    product_consistency: int = Field(ge=0, le=100)
    character_consistency: int = Field(ge=0, le=100)
    scene_consistency: int = Field(ge=0, le=100)
    hand_and_physics: int = Field(ge=0, le=100)
    story_compliance: int = Field(ge=0, le=100)
    issues: list[str] = Field(default_factory=list)
    repair_prompt: str = ""


class PairVisualComparison(BaseModel):
    subject: Literal["product", "character", "scene"]
    match: bool
    score: int = Field(ge=0, le=100)
    reference_features: list[str] = Field(default_factory=list)
    candidate_features: list[str] = Field(default_factory=list)
    critical_mismatches: list[str] = Field(default_factory=list)
    repair_instruction: str = ""


class CompositionReview(BaseModel):
    story_match: bool
    score: int = Field(ge=0, le=100)
    visible_people_count: int = Field(ge=0)
    extra_people: bool
    direct_camera_gaze: bool
    text_or_watermark: bool
    physical_issues: list[str] = Field(default_factory=list)
    story_mismatches: list[str] = Field(default_factory=list)
    repair_instruction: str = ""


class OllamaVisionService:
    def __init__(self, *, purpose: str = "analysis") -> None:
        self.provider = getenv("VISION_PROVIDER", "ollama").strip().lower()
        self.purpose = purpose
        if self.provider in {"dashscope", "cloud", "qwen", "ark", "volcengine", "doubao"}:
            requested_provider = self.provider
            self.provider = "ark" if requested_provider in {"ark", "volcengine", "doubao"} else "dashscope"
            default_base = (
                "https://ark.cn-beijing.volces.com/api/v3"
                if self.provider == "ark"
                else "https://dashscope.aliyuncs.com/compatible-mode/v1"
            )
            self.base_url = getenv("VISION_CLOUD_API_BASE", default_base).rstrip("/")
            model_env = "VISION_REVIEW_MODEL" if purpose == "review" else "VISION_CLOUD_MODEL"
            # Prefer the flagship multimodal model for both analysis and QC.
            # Flash can still be selected explicitly with VISION_REVIEW_MODEL.
            model_default = "doubao-seed-2-0-lite-260215" if self.provider == "ark" else "qwen3.7-plus"
            self.model = getenv(model_env, model_default).strip()
            fallback_key = "ARK_API_KEY" if self.provider == "ark" else "DASHSCOPE_API_KEY"
            # Prefer the provider-specific key. This prevents a stale generic
            # cloud key from silently crossing providers after a UI switch.
            self.api_key = getenv(fallback_key, "").strip() or getenv("VISION_CLOUD_API_KEY", "").strip()
        else:
            self.provider = "ollama"
            self.base_url = getenv("VISION_API_BASE", "http://127.0.0.1:11434").rstrip("/")
            model_env = "VISION_REVIEW_LOCAL_MODEL" if purpose == "review" else "VISION_MODEL"
            self.model = getenv(model_env, getenv("VISION_MODEL", "qwen3-vl:4b")).strip()
            self.api_key = ""
        self.timeout = max(60.0, float(getenv("VISION_TIMEOUT_SECONDS", "240")))

    @staticmethod
    def _image(path: str | Path) -> str:
        value = str(path)
        file_path = Path(value)
        if not file_path.is_file():
            raise RuntimeError(f"视觉分析图片不存在：{value}")
        with Image.open(file_path) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
            buffer = BytesIO()
            image.save(buffer, format="JPEG", quality=85, optimize=True)
        return base64.b64encode(buffer.getvalue()).decode("ascii")

    async def _structured(
        self,
        *,
        prompt: str,
        images: list[str | Path],
        schema: type[BaseModel],
    ) -> BaseModel:
        if self.provider in {"dashscope", "ark"}:
            try:
                return await self._structured_cloud(prompt=prompt, images=images, schema=schema)
            except httpx.TransportError as cloud_error:
                # Cloud remains authoritative. A locally installed Ollama
                # vision model is only a zero-cost continuity fallback during
                # transient DNS/TLS/network failures; customer machines with
                # no Ollama simply receive the original cloud error.
                try:
                    async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
                        health = await client.get("http://127.0.0.1:11434/api/tags")
                        health.raise_for_status()
                    self.provider = "ollama"
                    self.base_url = getenv("VISION_API_BASE", "http://127.0.0.1:11434").rstrip("/")
                    model_env = "VISION_REVIEW_LOCAL_MODEL" if self.purpose == "review" else "VISION_MODEL"
                    self.model = getenv(model_env, getenv("VISION_MODEL", "qwen3-vl:4b")).strip()
                except Exception:
                    raise cloud_error
        payload = {
            "model": self.model,
            "stream": False,
            "think": False,
            "format": schema.model_json_schema(),
            "messages": [{
                "role": "user",
                "content": f"/no_think\n{prompt}",
                "images": [self._image(path) for path in images],
            }],
            "options": {"temperature": 0, "num_ctx": 8192, "num_predict": 512},
        }
        async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as client:
            response = await client.post(f"{self.base_url}/api/chat", json=payload)
            response.raise_for_status()
        message = response.json().get("message") or {}
        content = str(message.get("content") or "")
        if content:
            try:
                return schema.model_validate_json(content)
            except ValueError:
                pass
        evidence = str(message.get("thinking") or content)
        if not evidence:
            raise RuntimeError("本地视觉模型返回空内容。")
        return await self._normalize_evidence(
            original_prompt=prompt,
            evidence=evidence,
            schema=schema,
        )

    @staticmethod
    def _json_content(value: str) -> str:
        content = value.strip()
        fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", content, re.DOTALL | re.IGNORECASE)
        return fenced.group(1).strip() if fenced else content

    async def _structured_cloud(
        self,
        *,
        prompt: str,
        images: list[str | Path],
        schema: type[BaseModel],
    ) -> BaseModel:
        if not self.api_key:
            expected = "ARK_API_KEY" if self.provider == "ark" else "DASHSCOPE_API_KEY"
            raise RuntimeError(
                f"VISION_PROVIDER={self.provider} requires {expected} or VISION_CLOUD_API_KEY"
            )
        schema_json = schema.model_json_schema()
        content: list[dict] = [{
            "type": "text",
            "text": (
                f"{prompt}\n\nReturn only one JSON object matching this schema exactly:\n"
                f"{json.dumps(schema_json, ensure_ascii=False)}"
            ),
        }]
        content.extend({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{self._image(path)}"},
        } for path in images)
        payload = {
            "model": self.model,
            "temperature": 0,
            # Visual evidence is consumed as a compact machine record.  A
            # generous unconstrained completion lets reasoning-capable models
            # repeat the schema and can truncate the JSON before its closing
            # brace, which blocks the whole production pipeline.
            "max_tokens": 1200,
            "stream": False,
            "messages": [{"role": "user", "content": content}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "strict": True,
                    "schema": schema_json,
                },
            },
        }
        if self.provider == "ark":
            payload["thinking"] = {"type": "disabled"}
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        timeout = httpx.Timeout(self.timeout, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions", json=payload, headers=headers
            )
            # Some DashScope models support JSON object mode but not json_schema.
            if response.status_code in {400, 422}:
                payload["response_format"] = {"type": "json_object"}
                response = await client.post(
                    f"{self.base_url}/chat/completions", json=payload, headers=headers
                )
            response.raise_for_status()
        raw = response.json()
        value = str(raw["choices"][0]["message"].get("content") or "")
        if not value.strip():
            raise RuntimeError("Cloud vision model returned empty content")
        return schema.model_validate_json(self._json_content(value))

    async def _normalize_evidence(
        self,
        *,
        original_prompt: str,
        evidence: str,
        schema: type[BaseModel],
    ) -> BaseModel:
        base_url = getenv("VISION_STRUCTURER_BASE", "http://127.0.0.1:11434/v1").rstrip("/")
        model = getenv("VISION_STRUCTURER_MODEL", "qwen3:latest").strip()
        payload = {
            "model": model,
            "temperature": 0,
            "stream": False,
            "reasoning_effort": "none",
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "strict": True,
                    "schema": schema.model_json_schema(),
                },
            },
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是视觉质检证据整理器。只能依据视觉模型观察证据填写JSON，"
                        "不得把用户期望当作已发生事实。若证据指出商品变成手套、缺少核心结构"
                        "或人物身份明显不一致，必须判定失败。只输出符合Schema的JSON。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"原质检要求：{original_prompt}\n\n"
                        f"视觉模型观察证据：\n{evidence[:16000]}"
                    ),
                },
            ],
        }
        async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as client:
            response = await client.post(
                f"{base_url}/chat/completions",
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
        content = str(response.json()["choices"][0]["message"].get("content") or "")
        return schema.model_validate_json(content)

    async def analyze_asset(
        self,
        *,
        image_path: str | Path,
        asset_type: Literal["product", "character", "scene", "brand"],
    ) -> AssetVisualProfile:
        prompt = (
            f"你是商业视频素材分析员。当前只有1张图片，类型是 {asset_type}。"
            "输出必须简洁：summary不超过80个汉字；每个数组最多6项；每项不超过40个汉字；"
            "不要解释分析过程，不要复述JSON Schema，不要输出Markdown。"
            "只描述图片中真实可见且可核验的信息，不猜测品牌、材质认证、人物姓名或使用效果。"
            "如果类型是product，只分析被展示或握持的商品主体，必须忽略握持者的手、衣服、"
            "背景门窗、地板和环境；这些都不能进入immutable_features。"
            "如果类型是character，只分析人物身份外观和服装，忽略证件照背景。"
            "如果类型是scene，只分析空间结构、固定物体和光线。"
            "immutable_features填写视频生成时绝不能改变的特征；"
            "geometry_or_identity填写商品几何结构、人物身份特征或场景空间布局；"
            "dominant_colors必须填写1至6个主体颜色；generation_risks填写容易被生成模型改错的细节。"
            "quality_score表示该图片作为生成参考图的清晰度与可用性：清晰完整为80至100，"
            "部分遮挡但主体清晰为60至79，严重模糊或无法识别才低于40，不允许无理由填写0。"
            "严格按JSON Schema输出。"
        )
        result = await self._structured(
            prompt=prompt,
            images=[image_path],
            schema=AssetVisualProfile,
        )
        return AssetVisualProfile.model_validate(result)

    async def review_frame(
        self,
        *,
        product_reference: str | Path,
        character_reference: str | Path,
        scene_reference: str | Path,
        candidate_frame: str | Path,
        expected_story_state: str,
    ) -> FrameQualityReview:
        product = await self.compare_pair(
            reference=product_reference,
            candidate=candidate_frame,
            subject="product",
        )
        character = await self.compare_pair(
            reference=character_reference,
            candidate=candidate_frame,
            subject="character",
        )
        scene = await self.compare_pair(
            reference=scene_reference,
            candidate=candidate_frame,
            subject="scene",
        )
        scores = [product.score, character.score, scene.score]
        hand_score = 45 if any(
            keyword in " ".join(product.critical_mismatches)
            for keyword in ("手套", "手指", "手部", "融合", "肢体")
        ) else min(90, product.score)
        overall = round(
            product.score * 0.45
            + character.score * 0.25
            + scene.score * 0.15
            + hand_score * 0.15
        )
        issues = [
            *product.critical_mismatches,
            *character.critical_mismatches,
            *scene.critical_mismatches,
        ]
        passed = (
            product.match
            and product.score >= 80
            and character.match
            and character.score >= 70
            and scene.score >= 65
            and hand_score >= 60
            and overall >= 75
        )
        repair = "；".join(filter(None, [
            product.repair_instruction,
            character.repair_instruction,
            scene.repair_instruction,
        ]))
        return FrameQualityReview(
            passed=passed,
            overall_score=overall,
            product_consistency=product.score,
            character_consistency=character.score,
            scene_consistency=scene.score,
            hand_and_physics=hand_score,
            story_compliance=80,
            issues=issues,
            repair_prompt=repair,
        )

    async def compare_pair(
        self,
        *,
        reference: str | Path,
        candidate: str | Path,
        subject: Literal["product", "character", "scene"],
    ) -> PairVisualComparison:
        rules = {
            "product": (
                "只比较商品主体。图1可能包含握持者和背景，必须忽略。"
                "先分别列出图1商品和图2对应物体的可见特征，再判断是否同一商品。"
                "若图2把毛绒玩偶变成手套、帽子、普通动物，或缺少眼睛、鼻子、角、"
                "主体轮廓等核心结构，match必须为false且score不得高于40。"
                "轻微姿态、尺度和拍摄角度变化不是错误。"
            ),
            "character": (
                "只比较人物身份、脸部比例、发型、年龄和服装。忽略参考图背景与候选场景。"
                "同一人物在不同角度和表情下可以判为匹配；明显换脸、年龄变化或服装变化必须扣分。"
            ),
            "scene": (
                "只比较场景空间类型、主要建筑结构、窗户、墙面、地面和光线方向。"
                "摄影机位置变化允许；换成不同房间、户外或明显不同建筑必须判为不匹配。"
            ),
        }
        prompt = (
            f"你是商业视频{subject}一致性质检员。共有2张图片：图1是参考，图2是候选。"
            f"{rules[subject]}"
            "critical_mismatches只填写图像中明确看见的差异，不确定时降低score但不要编造。"
            "repair_instruction用一句可执行中文说明如何重做。严格按JSON Schema输出。"
        )
        rules = {
            "product": (
                "Compare only the product subject. Ignore the holder and background in reference 1. "
                "List visible product features in both images before judging. If a puppet becomes a "
                "glove, hat, generic plush, different animal, or loses defining eyes, nose, horns, "
                "face outline, limbs, or belly construction, match must be false and score <= 40. "
                "For an electronic product with an active screen, changing facial expressions, eye "
                "animation, transient icons, text, glare, or exposure on the same screen is a normal "
                "device state and is not an identity mismatch. Only changed screen geometry/location "
                "or replacement by a physical blank panel is critical. "
                "Pose and camera-angle changes alone are not defects. When both images include a "
                "person, a product-size change greater than about 20% relative to the same hand or "
                "head is a critical scale mismatch. A white-flooded display is a critical physical "
                "screen-state failure, not harmless glare."
            ),
            "character": (
                "Compare only character identity, facial proportions, hairstyle, apparent age, and "
                "wardrobe. Ignore background changes. Different pose or expression can match; an "
                "obvious face swap, age shift, or wardrobe change must reduce the score."
            ),
            "scene": (
                "Compare only spatial type, architectural layout, windows, walls, floor, and lighting "
                "direction. Camera position can change. A different room, outdoor setting, or "
                "substantially different architecture must not match."
            ),
        }
        prompt = (
            f"You are a commercial-video {subject} consistency reviewer. There are exactly two "
            f"images: image 1 is the reference and image 2 is the candidate. {rules[subject]} "
            "critical_mismatches must contain only clearly visible differences. When uncertain, "
            "lower the score but do not invent a defect. Give one executable repair instruction. "
            "Return strict JSON matching the schema."
        )
        result = await self._structured(
            prompt=prompt,
            images=[reference, candidate],
            schema=PairVisualComparison,
        )
        comparison = PairVisualComparison.model_validate(result)
        if subject == "product" and comparison.critical_mismatches:
            display_state_terms = (
                "screen", "display", "eye graphic", "star pupil", "glare",
                "exposure", "text", "icon", "animation",
            )
            physical_identity_terms = (
                "silhouette", "body", "limb", "ear", "strap", "carabiner",
                "color", "material", "geometry", "shape", "missing screen",
                "physical panel", "blank", "white circle", "different product", "different animal",
            )
            mismatches = [item.lower() for item in comparison.critical_mismatches]
            display_state_only = all(
                any(term in item for term in display_state_terms)
                and not any(term in item for term in physical_identity_terms)
                for item in mismatches
            )
            if display_state_only:
                comparison = comparison.model_copy(update={
                    "match": True,
                    "score": max(comparison.score, 82),
                    "critical_mismatches": [],
                })
        # Structured vision models occasionally describe a defining product
        # mismatch while still returning match=true and a high score. The
        # field is explicitly named critical_mismatches, so deterministic
        # policy takes precedence over the contradictory model verdict.
        if subject == "product" and comparison.critical_mismatches:
            comparison = comparison.model_copy(update={
                "match": False,
                "score": min(comparison.score, 40),
            })
        return comparison

    async def review_composition(
        self,
        *,
        candidate: str | Path,
        expected_story_state: str,
        expected_people_count: int = 1,
        allow_camera_gaze: bool = False,
    ) -> CompositionReview:
        prompt = (
            "You are reviewing one candidate keyframe for a live-action narrative advertisement. "
            f"Expected story state: {expected_story_state}. "
            f"Exactly {expected_people_count} visible person or person-like duplicate is allowed. "
            f"Direct gaze into the camera is {'allowed' if allow_camera_gaze else 'not allowed'}. "
            "Count every visible human figure, including background duplicates, reflections that look "
            "like extra actors, and partial bodies. Check hand-object contact, duplicated limbs, text, "
            "watermarks, poster-like posing, blurred padding bands, letterboxing, repeated portrait-card "
            "composition, and whether the image causally matches the expected beat. "
            "Do not infer invisible facts. If an extra person, duplicate protagonist, direct camera gaze "
            "when forbidden, obvious physical defect, text, watermark, blurred/letterbox padding, or "
            "major story mismatch exists, "
            "story_match must be false and score must be 60 or lower. Return strict JSON."
        )
        result = await self._structured(
            prompt=prompt,
            images=[candidate],
            schema=CompositionReview,
        )
        return CompositionReview.model_validate(result)


def vision_mode() -> str:
    provider = getenv("VISION_PROVIDER", "ollama").strip().lower()
    if provider in {"dashscope", "cloud", "qwen"}:
        return "dashscope"
    return "ollama" if getenv("VISION_MODEL", "qwen3-vl:4b").strip() else "disabled"
