from __future__ import annotations

from collections import defaultdict
from typing import Any, Literal

from pydantic import BaseModel, Field

from .orchestration import WorkflowNode, WorkflowRun


FailureCategory = Literal[
    "product_identity",
    "character_identity",
    "scene_continuity",
    "hand_physics",
    "story_mismatch",
    "extra_people",
    "camera_gaze",
    "text_watermark",
    "low_visual_quality",
    "exposure_instability",
    "unknown",
]


class RepairStep(BaseModel):
    action: Literal[
        "strengthen_reference",
        "regenerate_keyframe",
        "simplify_action",
        "rewrite_prompt",
        "switch_provider",
        "manual_review",
    ]
    label: str
    target_node_id: str
    prompt_patch: str = ""


class QualityDecision(BaseModel):
    passed: bool
    score: int = Field(ge=0, le=100)
    severity: Literal["none", "minor", "major", "critical"]
    categories: list[FailureCategory] = Field(default_factory=list)
    summary: str
    repair_steps: list[RepairStep] = Field(default_factory=list)
    prompt_patch: str = ""
    suggested_provider: str = ""
    execution_policy: Literal["accept", "manual_review", "automatic_retry"] = "manual_review"
    manual_review_required: bool = True
    retry_recommended: bool = False
    remaining_retries: int = 0
    spent_cny: float = 0
    retry_budget_cny: float = 0
    projected_next_cost_cny: float = 0
    within_budget: bool = True


class ProviderBenchmark(BaseModel):
    provider: str
    node_kind: Literal["keyframe", "video"]
    generated_attempts: int = 0
    accepted_outputs: int = 0
    reviewed_outputs: int = 0
    automatic_pass_rate: float = 0
    actual_cost_cny: float = 0
    average_cost_per_attempt_cny: float = 0


class QualityBenchmarkReport(BaseModel):
    project_id: str
    total_generation_attempts: int
    total_actual_cost_cny: float
    providers: list[ProviderBenchmark]
    failure_categories: dict[str, int]


def _budget(generation: WorkflowNode) -> tuple[int, float, float, bool]:
    remaining = max(0, generation.max_retries - generation.retry_count)
    limit = generation.estimated_cost_cny * (generation.max_retries + 1)
    projected = generation.estimated_cost_cny
    within = (
        remaining > 0
        and generation.actual_cost_cny + projected <= limit + 0.0001
    )
    return remaining, round(limit, 4), projected, within


def _severity(score: int, categories: list[FailureCategory], passed: bool) -> str:
    if passed:
        return "none"
    if score < 45 or "product_identity" in categories:
        return "critical"
    if score < 65 or any(
        item in categories
        for item in ("character_identity", "hand_physics", "extra_people")
    ):
        return "major"
    return "minor"


def decide_keyframe_quality(
    *,
    report: dict[str, Any],
    generation: WorkflowNode,
    review_node_id: str,
    allow_automatic_paid_retry: bool = False,
) -> QualityDecision:
    passed = bool(report.get("passed", report.get("story_match", False)))
    score = int(report.get("overall_score", report.get("score", 0)))
    categories: list[FailureCategory] = []
    steps: list[RepairStep] = []
    prompt_parts: list[str] = []
    if int(report.get("product_consistency", 100)) < 80:
        categories.append("product_identity")
        prompt_parts.append("严格保持参考商品的轮廓、颜色、面部和核心结构，不得变成其他物体")
        steps.append(RepairStep(
            action="strengthen_reference",
            label="加强商品参考并重做该关键帧",
            target_node_id=generation.id,
        ))
    if int(report.get("character_consistency", 100)) < 70:
        categories.append("character_identity")
        prompt_parts.append("保持同一人物的脸型、年龄、发型和服装完全一致")
    if int(report.get("scene_consistency", 100)) < 65:
        categories.append("scene_continuity")
        prompt_parts.append("保持参考场景的空间结构、窗户、墙地面与光线方向一致")
    if int(report.get("hand_and_physics", 100)) < 60:
        categories.append("hand_physics")
        prompt_parts.append("减少遮挡，双手结构自然，避免手指与商品融合")
    if int(report.get("story_compliance", 100)) < 70 or not bool(
        report.get("story_match", True)
    ):
        categories.append("story_mismatch")
        prompt_parts.append("画面必须明确完成当前故事动作，不增加无关人物或道具")

    repair_from_model = str(
        report.get("repair_prompt") or report.get("repair_instruction") or ""
    ).strip()
    if repair_from_model:
        prompt_parts.append(repair_from_model)
    if not passed and not categories:
        categories.append("unknown")
        steps.append(RepairStep(
            action="manual_review",
            label="视觉证据不足，请人工确认失败原因",
            target_node_id=review_node_id,
        ))
    elif not passed and not steps:
        steps.append(RepairStep(
            action="regenerate_keyframe",
            label="应用质检补丁后重做该关键帧",
            target_node_id=generation.id,
        ))

    patch = "；".join(dict.fromkeys(part for part in prompt_parts if part))
    if patch and steps:
        steps[0].prompt_patch = patch
    remaining, limit, projected, within = _budget(generation)
    severity = _severity(score, categories, passed)
    auto_retry = not passed and within and allow_automatic_paid_retry
    return QualityDecision(
        passed=passed,
        score=score,
        severity=severity,
        categories=categories,
        summary=(
            "自动质检通过，等待人工确认。"
            if passed
            else f"检测到{len(categories)}类问题，建议仅重做当前关键帧。"
        ),
        repair_steps=steps,
        prompt_patch=patch,
        execution_policy=(
            "accept" if passed else "automatic_retry" if auto_retry else "manual_review"
        ),
        manual_review_required=not auto_retry,
        retry_recommended=auto_retry,
        remaining_retries=remaining,
        spent_cny=round(generation.actual_cost_cny, 4),
        retry_budget_cny=limit,
        projected_next_cost_cny=projected,
        within_budget=within,
    )


def decide_video_quality(
    *,
    report: dict[str, Any],
    generation: WorkflowNode,
    review_node_id: str,
    fallback_provider: str = "",
    allow_automatic_paid_retry: bool = False,
) -> QualityDecision:
    passed = bool(report.get("passed", False))
    score = int(report.get("score", 0))
    frames = list(report.get("frames") or [])
    categories: list[FailureCategory] = []
    prompt_parts: list[str] = []
    scene_analysis = dict(report.get("scene_analysis") or {})
    deterministic = dict(report.get("deterministic_analysis") or {})

    if (
        not bool(report.get("product_match", True))
        or int(report.get("product_consistency", 100)) < 75
    ):
        categories.append("product_identity")
        prompt_parts.append(
            "Keep the exact product silhouette, proportions, material, color regions, surface details, "
            "components and accessories from the approved real product reference in every frame; "
            "never invent, remove, replace, illuminate or transform any product component."
        )

    if int(scene_analysis.get("unexpected_cut_count", 0)) > 0:
        categories.append("scene_continuity")
        prompt_parts.append("保持单一连续镜头，禁止中途跳切、闪断或突然切换场景")

    if not bool(deterministic.get("passed", True)):
        categories.append("exposure_instability")
        prompt_parts.append(
            "Keep exposure stable from start to finish; no black frames, white flashes, "
            "sudden dimming, overexposure, or unexplained full-frame visual jumps."
        )

    if any(item.get("extra_people") for item in frames):
        categories.append("extra_people")
        prompt_parts.append("画面只保留脚本指定人物，禁止新增路人、重复人物或背景人脸")
    if any(item.get("direct_camera_gaze") for item in frames):
        categories.append("camera_gaze")
        prompt_parts.append("人物自然看向商品或动作目标，不直视镜头")
    if any(item.get("text_or_watermark") for item in frames):
        categories.append("text_watermark")
        prompt_parts.append("画面中禁止生成文字、水印、伪Logo和乱码")
    physical = [
        str(issue)
        for item in frames
        for issue in item.get("physical_issues", [])
        if issue
    ]
    if physical:
        categories.append("hand_physics")
        prompt_parts.append("将动作简化为单一连续动作，减少手部遮挡和商品快速旋转")
    mismatches = [
        str(issue)
        for item in frames
        for issue in item.get("story_mismatches", [])
        if issue
    ]
    if mismatches or any(not item.get("story_match", True) for item in frames):
        categories.append("story_mismatch")
        prompt_parts.append("严格按照当前镜头的起点、动作和终点推进，不插入无关事件")
    if score < 65 and not categories:
        categories.append("low_visual_quality")
        prompt_parts.append("保持主体清晰稳定，避免模糊、闪烁、跳变和镜头突然加速")
    if not passed and not categories:
        categories.append("unknown")

    patch = "；".join(dict.fromkeys(prompt_parts))
    steps: list[RepairStep] = []
    if not passed:
        if "hand_physics" in categories:
            steps.append(RepairStep(
                action="simplify_action",
                label="简化动作后局部重做该镜头",
                target_node_id=generation.id,
                prompt_patch=patch,
            ))
        else:
            steps.append(RepairStep(
                action="rewrite_prompt",
                label="应用质检补丁后局部重做该镜头",
                target_node_id=generation.id,
                prompt_patch=patch,
            ))
        if fallback_provider and fallback_provider != "demo":
            steps.append(RepairStep(
                action="switch_provider",
                label=f"若再次失败，建议切换至 {fallback_provider}",
                target_node_id=generation.id,
            ))
    remaining, limit, projected, within = _budget(generation)
    severity = _severity(score, categories, passed)
    auto_retry = not passed and within and allow_automatic_paid_retry
    return QualityDecision(
        passed=passed,
        score=score,
        severity=severity,
        categories=categories,
        summary=(
            "抽帧质检通过，等待人工确认。"
            if passed
            else f"检测到{len(categories)}类镜头问题，建议局部重做而非重跑整片。"
        ),
        repair_steps=steps,
        prompt_patch=patch,
        suggested_provider=fallback_provider if not passed else "",
        execution_policy=(
            "accept" if passed else "automatic_retry" if auto_retry else "manual_review"
        ),
        manual_review_required=not auto_retry,
        retry_recommended=auto_retry,
        remaining_retries=remaining,
        spent_cny=round(generation.actual_cost_cny, 4),
        retry_budget_cny=limit,
        projected_next_cost_cny=projected,
        within_budget=within,
    )


def quality_benchmark(run: WorkflowRun) -> QualityBenchmarkReport:
    grouped: dict[tuple[str, str], dict[str, float]] = defaultdict(
        lambda: {"attempts": 0, "accepted": 0, "reviewed": 0, "cost": 0}
    )
    failure_categories: dict[str, int] = defaultdict(int)
    for node in run.nodes:
        if node.kind not in {"keyframe_generation", "video_generation"}:
            continue
        kind = "keyframe" if node.kind == "keyframe_generation" else "video"
        providers = [
            attempt.outputs.get("provider") or attempt.provider or "unknown"
            for attempt in node.attempts
        ]
        for attempt, provider in zip(node.attempts, providers, strict=True):
            bucket = grouped[(provider, kind)]
            bucket["attempts"] += 1
            bucket["cost"] += attempt.actual_cost_cny or 0
        review_id = (
            node.id.replace("keyframe:", "keyframe-review:", 1)
            if kind == "keyframe"
            else node.id.replace("video:", "video-review:", 1)
        )
        review = next((item for item in run.nodes if item.id == review_id), None)
        if review and review.attempts:
            provider = providers[-1] if providers else "unknown"
            grouped[(provider, kind)]["reviewed"] += 1
            if review.attempts[-1].outputs.get("passed") == "true":
                grouped[(provider, kind)]["accepted"] += 1
            for category in review.quality_decision.get("categories", []):
                failure_categories[str(category)] += 1

    providers = []
    for (provider, kind), values in sorted(grouped.items()):
        attempts = int(values["attempts"])
        accepted = int(values["accepted"])
        reviewed = int(values["reviewed"])
        cost = round(values["cost"], 4)
        providers.append(ProviderBenchmark(
            provider=provider,
            node_kind=kind,
            generated_attempts=attempts,
            accepted_outputs=accepted,
            reviewed_outputs=reviewed,
            automatic_pass_rate=round(accepted / reviewed * 100, 1) if reviewed else 0,
            actual_cost_cny=cost,
            average_cost_per_attempt_cny=round(cost / attempts, 4) if attempts else 0,
        ))
    return QualityBenchmarkReport(
        project_id=run.project_id,
        total_generation_attempts=sum(item.generated_attempts for item in providers),
        total_actual_cost_cny=round(sum(item.actual_cost_cny for item in providers), 4),
        providers=providers,
        failure_categories=dict(sorted(failure_categories.items())),
    )
