from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, computed_field


class AssetKind(str, Enum):
    product = "product"
    character = "character"
    scene = "scene"
    brand = "brand"


class ProjectCreate(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    product_name: str = Field(min_length=2, max_length=120)
    product_category: str = "儿童玩具"
    platform: Literal["抖音", "TikTok", "小红书", "淘宝", "亚马逊"] = "抖音"
    duration: Literal[15, 30, 45, 60] = 15
    aspect_ratio: Literal["9:16", "16:9", "1:1"] = "9:16"
    style: str = "观察式生活微纪录片"
    audience: str = "关注孩子成长与陪伴的年轻父母"
    selling_points: list[str] = Field(default_factory=lambda: ["安全材质", "激发创造力"])
    brief: str = ""


class ProjectPatch(BaseModel):
    quality_mode: Literal["advisory", "strict", "technical"] | None = None
    script_template: str | None = None
    voice_id: str | None = None
    voice_speed: float | None = Field(default=None, ge=0.7, le=1.3)
    bgm_track: str | None = None
    bgm_volume: float | None = Field(default=None, ge=0, le=0.5)
    subtitles_enabled: bool | None = None
    narrative_pace: str | None = None
    shot_count: int | None = Field(default=None, ge=3, le=10)
    hook_style: str | None = None
    camera_style: str | None = None
    lighting_style: str | None = None
    emotion_curve: str | None = None
    narration_density: str | None = None
    product_exposure: str | None = None
    transition_style: str | None = None
    realism_level: str | None = None
    negative_constraints: str | None = None
    name: str | None = None
    product_name: str | None = None
    product_category: str | None = None
    platform: str | None = None
    duration: int | None = None
    aspect_ratio: str | None = None
    style: str | None = None
    audience: str | None = None
    selling_points: list[str] | None = None
    brief: str | None = None


class Asset(BaseModel):
    id: str
    project_id: str
    kind: AssetKind
    name: str
    url: str
    content_type: str
    size: int
    created_at: datetime


class Shot(BaseModel):
    id: str
    index: int
    title: str
    duration: int
    purpose: str
    narrative_beat: str = ""
    visual: str
    camera: str
    action: str
    voiceover: str
    on_screen_text: str
    continuity_anchor: str = ""
    transition: str = ""
    prompt: str
    model_hint: Literal["economy", "story", "premium"] = "story"
    status: Literal["planned", "queued", "rendering", "ready", "failed"] = "planned"
    quality_score: int | None = None
    output_url: str | None = None
    provider: str | None = None
    estimated_cost: float | None = None


class StorySequence(BaseModel):
    id: str
    index: int
    title: str
    narrative_goal: str
    turning_point: str
    continuity_scope: str
    shot_ids: list[str]
    duration: int


class CreativePlan(BaseModel):
    version_id: str = ""
    source_asset_ids: list[str] = Field(default_factory=list)
    source_facts: list[str] = Field(default_factory=list)
    director_source: Literal["ollama", "openai_compatible", "fallback"] = "fallback"
    director_model: str = ""
    director_note: str = ""
    campaign_idea: str
    logline: str = ""
    protagonist: str = ""
    story_question: str = ""
    hook: str
    emotional_arc: str
    continuity_bible: list[str] = Field(default_factory=list)
    narration_script: str = ""
    visual_language: str
    music_direction: str
    call_to_action: str
    sequences: list[StorySequence] = Field(default_factory=list)
    shots: list[Shot]


class RenderTask(BaseModel):
    id: str
    project_id: str
    status: Literal[
        "queued",
        "analyzing",
        "planning",
        "rendering",
        "composing",
        "completed",
        "failed",
    ]
    progress: int = 0
    stage_label: str = "等待调度"
    provider: str = "demo"
    created_at: datetime
    updated_at: datetime
    output_url: str | None = None
    estimated_cost: float = 0
    error: str | None = None


class SubjectBible(BaseModel):
    asset_ids: list[str] = Field(default_factory=list)
    immutable_features: list[str] = Field(default_factory=list)
    required_evidence: list[str] = Field(default_factory=list)
    prohibited_changes: list[str] = Field(default_factory=list)


class BrandBible(BaseModel):
    """The source of truth inherited by planning, generation and review."""
    version: str = "brand-bible-v1"
    status: Literal["draft", "conflict", "approved"] = "draft"
    product: SubjectBible = Field(default_factory=SubjectBible)
    character: SubjectBible = Field(default_factory=SubjectBible)
    scene: SubjectBible = Field(default_factory=SubjectBible)
    brand: SubjectBible = Field(default_factory=SubjectBible)
    mandatory_claims: list[str] = Field(default_factory=list)
    visual_tone: list[str] = Field(default_factory=list)
    global_prohibitions: list[str] = Field(default_factory=list)
    review_checklist: list[str] = Field(default_factory=list)
    fact_conflicts: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class ProductionBudget(BaseModel):
    """A project-wide ceiling. It is deliberately separate from per-node retries."""
    ceiling_cny: float = Field(gt=0)
    approved_cny: float = 0
    approval_phrase: str = ""
    approved_at: datetime | None = None

    @computed_field
    @property
    def approved(self) -> bool:
        return self.approved_cny > 0 and bool(self.approval_phrase)


class ProductionPreflight(BaseModel):
    project_id: str
    ready_for_paid_generation: bool
    estimated_total_cny: float
    budget_ceiling_cny: float
    remaining_budget_cny: float
    approval_phrase: str
    checks: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)


class Project(BaseModel):
    id: str
    status: Literal["draft", "planned", "rendering", "completed"] = "draft"
    created_at: datetime
    updated_at: datetime
    assets: list[Asset] = Field(default_factory=list)
    creative_plan: CreativePlan | None = None
    latest_task: RenderTask | None = None
    output_url: str | None = None
    brand_bible: BrandBible | None = None
    production_budget: ProductionBudget | None = None
    asset_analysis_status: Literal["pending", "running", "ready", "failed"] = "pending"
    asset_analysis_model: str = ""
    analyzed_asset_ids: list[str] = Field(default_factory=list)
    asset_facts: list[str] = Field(default_factory=list)
    name: str
    product_name: str
    product_category: str
    platform: str
    duration: int
    aspect_ratio: str
    style: str
    audience: str
    selling_points: list[str]
    brief: str
    script_template: str = "story"
    voice_id: str = "male-qn-qingse"
    voice_speed: float = 0.92
    bgm_track: str = "house-vibez.mp3"
    bgm_volume: float = 0.13
    subtitles_enabled: bool = True
    narrative_pace: str = "balanced"
    shot_count: int = 5
    hook_style: str = "action"
    camera_style: str = "observational"
    lighting_style: str = "natural-warm"
    emotion_curve: str = "curiosity-growth-resolution"
    narration_density: str = "medium"
    product_exposure: str = "natural"
    transition_style: str = "match-action"
    realism_level: str = "photoreal"
    negative_constraints: str = "禁止乱码、水印、额外人物、产品变形、手部异常和虚假功效"
    quality_mode: Literal["advisory", "strict", "technical"] = "advisory"


class LibraryAssetRequest(BaseModel):
    asset_id: str


class ScriptCandidate(BaseModel):
    id: str
    label: str
    template: str
    plan: CreativePlan


class ScriptSelectionRequest(BaseModel):
    candidate: ScriptCandidate


class GenerationRequest(BaseModel):
    provider: str = "auto"
    quality: Literal["standard", "pro"] = "standard"
    variants: int = Field(default=1, ge=1, le=3)


class SystemConfig(BaseModel):
    mode: Literal["demo", "api"]
    default_provider: str
    provider_configured: bool
    director_mode: Literal["story_fallback", "llm"]
    vision_mode: Literal["ollama", "dashscope", "disabled"] = "disabled"
    vision_model: str = ""
    pricing_note: str
    billing_currency: Literal["CNY", "USD"] = "CNY"
