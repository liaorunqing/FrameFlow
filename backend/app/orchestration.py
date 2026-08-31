from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field

from .production import ProductionPlan
from .schemas import Project


WORKFLOW_VERSION = "frameflow-dag-v8"


NodeKind = Literal[
    "asset_analysis",
    "story_plan",
    "keyframe_generation",
    "keyframe_review",
    "video_generation",
    "video_review",
    "audio_timeline",
    "composition",
    "upscale",
    "frame_interpolation",
]
NodeStatus = Literal[
    "blocked",
    "ready",
    "running",
    "review_required",
    "completed",
    "failed",
    "skipped",
]


def utcnow() -> datetime:
    return datetime.now().astimezone()


class WorkflowAttempt(BaseModel):
    number: int
    status: Literal["running", "completed", "failed"]
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    provider: str = ""
    model_id: str = ""
    estimated_cost_cny: float = 0
    actual_cost_cny: float | None = None
    error: str | None = None
    outputs: dict[str, str] = Field(default_factory=dict)


class WorkflowNode(BaseModel):
    id: str
    kind: NodeKind
    label: str
    shot_id: str | None = None
    dependencies: list[str] = Field(default_factory=list)
    status: NodeStatus = "blocked"
    progress: int = Field(default=0, ge=0, le=100)
    cache_key: str = ""
    cache_hit: bool = False
    billable: bool = False
    max_retries: int = 2
    attempts: list[WorkflowAttempt] = Field(default_factory=list)
    estimated_cost_cny: float = 0
    actual_cost_cny: float = 0
    quality_decision: dict[str, Any] = Field(default_factory=dict)
    issues: list[str] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=utcnow)

    @computed_field
    @property
    def retry_count(self) -> int:
        return max(0, len(self.attempts) - 1)


class WorkflowRun(BaseModel):
    id: str
    project_id: str
    version: str = WORKFLOW_VERSION
    status: Literal["draft", "running", "review_required", "completed", "failed"] = "draft"
    nodes: list[WorkflowNode]
    live_preview_url: str = ""
    live_preview_revision: int = 0
    live_preview_ready_shots: int = 0
    live_preview_complete: bool = False
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    @computed_field
    @property
    def estimated_cost_cny(self) -> float:
        return round(sum(node.estimated_cost_cny for node in self.nodes), 4)

    @computed_field
    @property
    def actual_cost_cny(self) -> float:
        return round(sum(node.actual_cost_cny for node in self.nodes), 4)

    @computed_field
    @property
    def progress(self) -> int:
        if not self.nodes:
            return 0
        weights = {
            "blocked": 0,
            "ready": 0,
            "running": 0.5,
            "review_required": 0.75,
            "completed": 1,
            "failed": 0,
            "skipped": 1,
        }
        return round(sum(weights[node.status] for node in self.nodes) / len(self.nodes) * 100)


class WorkflowStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def path(self, project_id: str) -> Path:
        return self.root / f"{project_id}.json"

    def get(self, project_id: str) -> WorkflowRun | None:
        path = self.path(project_id)
        if not path.is_file():
            return None
        with self._lock:
            return WorkflowRun.model_validate_json(path.read_text(encoding="utf-8"))

    def put(self, run: WorkflowRun) -> WorkflowRun:
        run.updated_at = utcnow()
        path = self.path(run.project_id)
        temporary = path.with_suffix(".tmp")
        with self._lock:
            temporary.write_text(run.model_dump_json(indent=2), encoding="utf-8")
            temporary.replace(path)
        return run


def _fingerprint(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def build_workflow(project: Project, plan: ProductionPlan) -> WorkflowRun:
    asset_signature = [
        {
            "id": asset.id,
            "kind": asset.kind.value,
            "size": asset.size,
            "created_at": asset.created_at.isoformat(),
        }
        for asset in project.assets
    ]
    nodes: list[WorkflowNode] = [
        WorkflowNode(
            id="asset-analysis",
            kind="asset_analysis",
            label="视觉分析与一致性档案",
            status="ready",
            cache_key=_fingerprint({"assets": asset_signature, "vision": "qwen3-vl"}),
        ),
        WorkflowNode(
            id="story-plan",
            kind="story_plan",
            label="营销策略、故事段落与分镜",
            dependencies=["asset-analysis"],
            cache_key=_fingerprint({
                "brief": project.brief,
                "duration": project.duration,
                "selling_points": project.selling_points,
                "plan": project.creative_plan.model_dump(mode="json") if project.creative_plan else None,
            }),
        ),
    ]

    for keyframe in plan.keyframes:
        generation_id = f"keyframe:{keyframe.id}"
        review_id = f"keyframe-review:{keyframe.id}"
        nodes.extend([
            WorkflowNode(
                id=generation_id,
                kind="keyframe_generation",
                label=f"关键帧 {keyframe.boundary_index:02d} 生成",
                dependencies=["asset-analysis", "story-plan"],
                cache_key=_fingerprint({
                    "prompt": keyframe.prompt,
                    "references": keyframe.reference_asset_ids,
                    "model": plan.keyframe_model,
                }),
                billable=True,
                estimated_cost_cny=plan.cost.keyframe_unit_price,
            ),
            WorkflowNode(
                id=review_id,
                kind="keyframe_review",
                label=f"关键帧 {keyframe.boundary_index:02d} 自动质检",
                dependencies=[generation_id],
                cache_key=_fingerprint({"source": generation_id, "qa": "qwen3-vl-v2"}),
            ),
        ])

    video_review_ids: list[str] = []
    per_shot_cost = round(plan.cost.video_cost / max(1, len(plan.shots)), 4)
    allocated_cost = 0.0
    for shot_position, shot in enumerate(plan.shots, start=1):
        shot_cost = (
            round(plan.cost.video_cost - allocated_cost, 4)
            if shot_position == len(plan.shots)
            else per_shot_cost
        )
        allocated_cost = round(allocated_cost + shot_cost, 4)
        video_id = f"video:{shot.shot_id}"
        review_id = f"video-review:{shot.shot_id}"
        video_review_ids.append(review_id)
        # In the default creator-facing mode, visual review is an advisory
        # sidecar. Generation depends on durable media artifacts rather than
        # an AI judge, so a false positive cannot stall the whole production.
        keyframe_dependency_kind = (
            "keyframe-review" if project.quality_mode == "strict" else "keyframe"
        )
        video_dependencies = [
            f"{keyframe_dependency_kind}:{shot.first_frame_id}",
            f"{keyframe_dependency_kind}:{shot.last_frame_id}",
        ]
        if shot_position > 1:
            previous_shot = plan.shots[shot_position - 2]
            previous_kind = "video-review" if project.quality_mode == "strict" else "video"
            video_dependencies.append(f"{previous_kind}:{previous_shot.shot_id}")
        nodes.extend([
            WorkflowNode(
                id=video_id,
                kind="video_generation",
                label=f"镜头 {shot.shot_index:02d} · {shot.title}",
                shot_id=shot.shot_id,
                dependencies=video_dependencies,
                cache_key=_fingerprint({
                    "prompt": shot.video_prompt,
                    "first": shot.first_frame_id,
                    "last": shot.last_frame_id,
                    "model": plan.video_model,
                }),
                billable=True,
                estimated_cost_cny=shot_cost,
            ),
            WorkflowNode(
                id=review_id,
                kind="video_review",
                label=f"镜头 {shot.shot_index:02d} 连贯性与物理质检",
                shot_id=shot.shot_id,
                dependencies=[video_id],
                cache_key=_fingerprint({"source": video_id, "checks": shot.continuity_checks}),
            ),
        ])

    nodes.extend([
        WorkflowNode(
            id="audio-timeline",
            kind="audio_timeline",
            label="旁白、字幕与声音桥时间轴",
            dependencies=["story-plan"],
            cache_key=_fingerprint({
                "narration": project.creative_plan.narration_script if project.creative_plan else "",
                "shots": [
                    shot.voiceover for shot in project.creative_plan.shots
                ] if project.creative_plan else [],
            }),
        ),
        WorkflowNode(
            id="final-compose",
            kind="composition",
            label="FFmpeg精剪、Logo与CTA",
            dependencies=[
                *(
                    video_review_ids
                    if project.quality_mode == "strict"
                    else [f"video:{shot.shot_id}" for shot in plan.shots]
                ),
                "audio-timeline",
            ],
            cache_key=_fingerprint({
                "aspect_ratio": project.aspect_ratio,
                "duration": project.duration,
                "cta": project.creative_plan.call_to_action if project.creative_plan else "",
            }),
        ),
        WorkflowNode(
            id="optional-upscale",
            kind="upscale",
            label="可选 · Real-ESRGAN清晰度修复",
            dependencies=["final-compose"],
            status="skipped",
            cache_key=_fingerprint({
                "source": "final-compose",
                "processor": "realesrgan",
                "scale": 2,
            }),
        ),
        WorkflowNode(
            id="optional-interpolation",
            kind="frame_interpolation",
            label="可选 · RIFE运动补帧",
            dependencies=["final-compose"],
            status="skipped",
            cache_key=_fingerprint({
                "source": "final-compose",
                "processor": "rife",
                "exp": 1,
            }),
        ),
    ])
    run = WorkflowRun(
        id=f"{project.id}:{WORKFLOW_VERSION}",
        project_id=project.id,
        nodes=nodes,
    )
    refresh_readiness(run)
    return run


def upgrade_workflow(existing: WorkflowRun, fresh: WorkflowRun) -> WorkflowRun:
    """Upgrade the DAG while preserving work whose cache fingerprint is valid."""
    existing_by_id = {node.id: node for node in existing.nodes}
    upgraded_nodes: list[WorkflowNode] = []
    for fresh_node in fresh.nodes:
        previous = existing_by_id.get(fresh_node.id)
        if previous is None or previous.cache_key != fresh_node.cache_key:
            upgraded_nodes.append(fresh_node)
            continue
        upgraded_nodes.append(fresh_node.model_copy(update={
            "status": previous.status,
            "progress": previous.progress,
            "cache_hit": previous.cache_hit,
            "attempts": previous.attempts,
            "actual_cost_cny": previous.actual_cost_cny,
            "quality_decision": previous.quality_decision,
            "issues": previous.issues,
            "updated_at": previous.updated_at,
        }))
    upgraded = fresh.model_copy(update={
        "nodes": upgraded_nodes,
        "created_at": existing.created_at,
    })
    refresh_readiness(upgraded)
    return upgraded


def node_by_id(run: WorkflowRun, node_id: str) -> WorkflowNode:
    node = next((item for item in run.nodes if item.id == node_id), None)
    if not node:
        raise ValueError(f"工作流节点不存在：{node_id}")
    return node


def refresh_readiness(run: WorkflowRun) -> None:
    statuses = {node.id: node.status for node in run.nodes}
    for node in run.nodes:
        if node.status != "blocked":
            continue
        if all(statuses.get(dep) in {"completed", "skipped"} for dep in node.dependencies):
            node.status = "ready"
            node.updated_at = utcnow()
    if any(node.status == "failed" for node in run.nodes):
        run.status = "failed"
    elif any(node.status == "review_required" for node in run.nodes):
        run.status = "review_required"
    elif all(node.status in {"completed", "skipped"} for node in run.nodes):
        run.status = "completed"
    elif any(node.status == "running" for node in run.nodes):
        run.status = "running"
    else:
        run.status = "draft"


def start_node(
    run: WorkflowRun,
    node_id: str,
    *,
    provider: str = "",
    model_id: str = "",
) -> WorkflowNode:
    node = node_by_id(run, node_id)
    if node.status not in {"ready", "failed"}:
        raise ValueError(f"节点当前状态不可执行：{node.status}")
    node.status = "running"
    node.progress = 1
    node.issues = []
    node.cache_hit = False
    node.attempts.append(WorkflowAttempt(
        number=len(node.attempts) + 1,
        status="running",
        provider=provider,
        model_id=model_id,
        estimated_cost_cny=node.estimated_cost_cny,
    ))
    node.updated_at = utcnow()
    run.status = "running"
    return node


def complete_node(
    run: WorkflowRun,
    node_id: str,
    *,
    outputs: dict[str, str] | None = None,
    actual_cost_cny: float | None = None,
    review_required: bool = False,
    cache_hit: bool = False,
) -> WorkflowNode:
    node = node_by_id(run, node_id)
    if not node.attempts:
        start_node(run, node_id)
    attempt = node.attempts[-1]
    attempt.status = "completed"
    attempt.finished_at = utcnow()
    attempt.outputs.update(outputs or {})
    if attempt.actual_cost_cny is None:
        attempt.actual_cost_cny = actual_cost_cny
        node.actual_cost_cny += actual_cost_cny or 0
    node.status = "review_required" if review_required else "completed"
    node.progress = 100
    node.cache_hit = cache_hit
    node.updated_at = utcnow()
    refresh_readiness(run)
    return node


def fail_node(run: WorkflowRun, node_id: str, error: str) -> WorkflowNode:
    node = node_by_id(run, node_id)
    if not node.attempts:
        start_node(run, node_id)
    attempt = node.attempts[-1]
    attempt.status = "failed"
    attempt.finished_at = utcnow()
    attempt.error = error
    node.status = "failed"
    node.progress = 0
    node.issues = [error]
    node.updated_at = utcnow()
    refresh_readiness(run)
    return node


def approve_node(run: WorkflowRun, node_id: str) -> WorkflowNode:
    node = node_by_id(run, node_id)
    if node.status != "review_required":
        raise ValueError(f"节点当前不需要审批：{node.status}")
    node.status = "completed"
    node.progress = 100
    node.updated_at = utcnow()
    refresh_readiness(run)
    return node


def reset_node_and_descendants(run: WorkflowRun, node_id: str) -> list[str]:
    node_by_id(run, node_id)
    affected = {node_id}
    changed = True
    while changed:
        changed = False
        for node in run.nodes:
            if node.id not in affected and any(dep in affected for dep in node.dependencies):
                affected.add(node.id)
                changed = True
    for node in run.nodes:
        if node.id not in affected:
            continue
        if node.id != node_id and node.kind in {"upscale", "frame_interpolation"}:
            node.status = "skipped"
            node.progress = 100
            node.issues = []
            node.updated_at = utcnow()
            continue
        node.status = "blocked"
        node.progress = 0
        node.cache_hit = False
        node.issues = []
        node.updated_at = utcnow()
    target = node_by_id(run, node_id)
    if all(
        node_by_id(run, dep).status in {"completed", "skipped"}
        for dep in target.dependencies
    ):
        target.status = "ready"
    refresh_readiness(run)
    return sorted(affected)
