from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from .production import ProductionPlan
from .schemas import Project
from .vision import AssetVisualProfile, FrameQualityReview


def timestamp() -> datetime:
    return datetime.now()


class CostEntry(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    category: Literal["image", "video", "vision", "tts", "editing"]
    provider: str
    description: str
    estimated_cny: float = 0
    actual_cny: float | None = None
    usage_tokens: int | None = None
    created_at: datetime = Field(default_factory=timestamp)


class KeyframeAttempt(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    attempt_number: int
    status: Literal["generating", "ready", "failed"] = "generating"
    provider: str = "seedream-official"
    model_id: str = ""
    output_url: str | None = None
    local_path: str | None = None
    review: FrameQualityReview | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=timestamp)


class KeyframeState(BaseModel):
    id: str
    boundary_index: int
    prompt: str
    status: Literal["planned", "generating", "needs_review", "approved", "rejected"] = "planned"
    attempts: list[KeyframeAttempt] = Field(default_factory=list)
    selected_attempt_id: str | None = None
    reviewed_at: datetime | None = None


class ShotAttempt(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    attempt_number: int
    status: Literal["submitted", "running", "succeeded", "failed"] = "submitted"
    task_id: str | None = None
    model_id: str = ""
    output_url: str | None = None
    local_path: str | None = None
    actual_last_frame_path: str | None = None
    usage_tokens: int = 0
    quality_score: int | None = None
    issues: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=timestamp)


class ShotState(BaseModel):
    id: str
    shot_index: int
    sequence_id: str
    status: Literal["blocked", "ready", "rendering", "approved", "failed"] = "blocked"
    first_frame_id: str
    last_frame_id: str
    attempts: list[ShotAttempt] = Field(default_factory=list)
    selected_attempt_id: str | None = None


class NarrationCue(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    start: float
    end: float
    text: str
    shot_id: str


class SubtitleCue(BaseModel):
    index: int
    start: float
    end: float
    text: str


class AudioTimeline(BaseModel):
    narration_text: str = ""
    narration_path: str | None = None
    duration: float = 0
    cues: list[NarrationCue] = Field(default_factory=list)
    subtitles: list[SubtitleCue] = Field(default_factory=list)


class ProductionSession(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    project_id: str
    status: Literal[
        "planning", "keyframes", "review", "rendering", "composing", "completed", "failed"
    ] = "planning"
    pipeline_version: str = "campaign-sequence-v1"
    created_at: datetime = Field(default_factory=timestamp)
    updated_at: datetime = Field(default_factory=timestamp)
    asset_profiles: dict[str, AssetVisualProfile] = Field(default_factory=dict)
    keyframes: list[KeyframeState]
    shots: list[ShotState]
    audio: AudioTimeline = Field(default_factory=AudioTimeline)
    costs: list[CostEntry] = Field(default_factory=list)
    output_url: str | None = None
    error: str | None = None

    @property
    def estimated_cost(self) -> float:
        return round(sum(item.estimated_cny for item in self.costs), 4)

    @property
    def actual_cost(self) -> float:
        return round(sum(item.actual_cny or 0 for item in self.costs), 4)


class ProductionSessionStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def path(self, project_id: str) -> Path:
        return self.root / f"{project_id}.json"

    def get(self, project_id: str) -> ProductionSession | None:
        path = self.path(project_id)
        if not path.exists():
            return None
        with self._lock:
            return ProductionSession.model_validate_json(path.read_text(encoding="utf-8"))

    def put(self, session: ProductionSession) -> ProductionSession:
        session.updated_at = timestamp()
        path = self.path(session.project_id)
        temporary = path.with_suffix(".tmp")
        with self._lock:
            temporary.write_text(session.model_dump_json(indent=2), encoding="utf-8")
            temporary.replace(path)
        return session


def build_session(project: Project, plan: ProductionPlan) -> ProductionSession:
    sequence_by_shot = {
        shot_id: sequence.id
        for sequence in (project.creative_plan.sequences if project.creative_plan else [])
        for shot_id in sequence.shot_ids
    }
    keyframes = [
        KeyframeState(
            id=spec.id,
            boundary_index=spec.boundary_index,
            prompt=spec.prompt,
        )
        for spec in plan.keyframes
    ]
    shots = [
        ShotState(
            id=spec.shot_id,
            shot_index=spec.shot_index,
            sequence_id=sequence_by_shot.get(spec.shot_id, ""),
            first_frame_id=spec.first_frame_id,
            last_frame_id=spec.last_frame_id,
        )
        for spec in plan.shots
    ]
    return ProductionSession(project_id=project.id, keyframes=keyframes, shots=shots)


def review_keyframe(
    session: ProductionSession,
    *,
    keyframe_id: str,
    approved: bool,
    override_auto_failure: bool = False,
) -> ProductionSession:
    keyframe = next((item for item in session.keyframes if item.id == keyframe_id), None)
    if not keyframe:
        raise ValueError("关键帧不存在。")
    if not keyframe.attempts:
        raise ValueError("关键帧尚无可审核结果。")
    latest = keyframe.attempts[-1]
    if approved and latest.review and not latest.review.passed and not override_auto_failure:
        raise ValueError("自动质检未通过，不能直接批准；请先局部重做或明确人工覆盖。")
    keyframe.status = "approved" if approved else "rejected"
    keyframe.selected_attempt_id = latest.id if approved else None
    keyframe.reviewed_at = timestamp()
    approved_ids = {item.id for item in session.keyframes if item.status == "approved"}
    for shot in session.shots:
        if shot.first_frame_id in approved_ids and shot.last_frame_id in approved_ids:
            shot.status = "ready"
    session.status = (
        "rendering"
        if all(item.status in {"ready", "rendering", "approved"} for item in session.shots)
        else "review"
    )
    return session
