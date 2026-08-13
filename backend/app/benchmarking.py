from __future__ import annotations

import json
import math
import threading
from datetime import datetime
from os import getenv
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, computed_field

from .providers import MiniMaxOfficialVideoProvider, VolcArkSeedanceProvider
from .schemas import AssetKind, Project


def now() -> datetime:
    return datetime.now().astimezone()


class PricingEvidence(BaseModel):
    provider: str
    basis: Literal["official_paygo", "local_empirical"]
    description: str
    source_url: str = ""
    confidence: Literal["high", "medium"]


class BenchmarkCase(BaseModel):
    id: Literal["product_showcase", "character_emotion", "hand_interaction"]
    label: str
    purpose: str
    duration: int = 6
    prompt: str
    reference_asset_ids: list[str] = Field(default_factory=list)
    first_frame_asset_id: str = ""
    requires_composite_keyframe: bool = False
    eligible: bool = True
    blocking_reasons: list[str] = Field(default_factory=list)
    review_dimensions: list[str] = Field(default_factory=list)


class BenchmarkJob(BaseModel):
    id: str
    case_id: str
    provider: Literal["seedance-official", "minimax-official"]
    model_id: str
    duration: int
    resolution: str
    estimated_cost_cny: float
    status: Literal[
        "planned",
        "unavailable",
        "submitted",
        "completed",
        "failed",
        "reviewed",
    ] = "planned"
    task_id: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None
    latency_seconds: float | None = None
    actual_cost_cny: float | None = None
    output_path: str = ""
    output_url: str = ""
    automatic_score: int | None = None
    automatic_passed: bool | None = None
    operator_review_score: int | None = None
    operator_review_passed: bool | None = None
    operator_review_notes: str = ""
    blind_review_score: int | None = None
    blind_review_passed: bool | None = None
    blind_review_token: str = ""
    failure_categories: list[str] = Field(default_factory=list)
    error: str = ""


class BenchmarkExperiment(BaseModel):
    id: str
    project_id: str
    version: str = "provider-benchmark-v1"
    status: Literal[
        "prepared",
        "blocked",
        "approved",
        "running",
        "review_required",
        "completed",
        "cancelled",
    ] = "prepared"
    cases: list[BenchmarkCase]
    jobs: list[BenchmarkJob]
    pricing: list[PricingEvidence]
    keyframe_cost_cny: float = 0
    keyframe_status: Literal["not_required", "planned", "completed", "failed"] = "planned"
    keyframe_path: str = ""
    keyframe_output_url: str = ""
    keyframe_actual_cost_cny: float | None = None
    keyframe_error: str = ""
    assembly_status: Literal["not_started", "running", "completed", "failed"] = "not_started"
    assembly_path: str = ""
    assembly_output_url: str = ""
    assembly_error: str = ""
    assembly_created_at: datetime | None = None
    contingency_rate: float = 0.10
    hard_budget_cny: float
    approval_phrase: str
    report_path: str = ""
    approved_budget_cny: float | None = None
    approved_at: datetime | None = None
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)

    @computed_field
    @property
    def estimated_cost_cny(self) -> float:
        return round(
            self.keyframe_cost_cny
            + sum(job.estimated_cost_cny for job in self.jobs if job.status != "unavailable"),
            4,
        )

    @computed_field
    @property
    def committed_cost_cny(self) -> float:
        keyframe = self.keyframe_actual_cost_cny or 0
        jobs = sum(
            job.actual_cost_cny or 0
            for job in self.jobs
        )
        return round(keyframe + jobs, 4)

    @computed_field
    @property
    def can_execute(self) -> bool:
        return (
            self.status == "approved"
            and self.approved_budget_cny is not None
            and self.approved_budget_cny >= self.hard_budget_cny
            and all(case.eligible for case in self.cases)
            and any(job.status == "planned" for job in self.jobs)
        )


class BlindReviewItem(BaseModel):
    token: str
    sample_label: str
    case_label: str
    review_dimensions: list[str]
    output_url: str


class BenchmarkStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def path(self, experiment_id: str) -> Path:
        return self.root / f"{experiment_id}.json"

    def put(self, experiment: BenchmarkExperiment) -> BenchmarkExperiment:
        experiment.updated_at = now()
        path = self.path(experiment.id)
        temporary = path.with_suffix(".tmp")
        with self._lock:
            temporary.write_text(experiment.model_dump_json(indent=2), encoding="utf-8")
            temporary.replace(path)
        return experiment

    def get(self, experiment_id: str) -> BenchmarkExperiment | None:
        path = self.path(experiment_id)
        if not path.is_file():
            return None
        return BenchmarkExperiment.model_validate_json(path.read_text(encoding="utf-8"))

    def latest(self, project_id: str) -> BenchmarkExperiment | None:
        candidates: list[BenchmarkExperiment] = []
        for path in self.root.glob("*.json"):
            try:
                item = BenchmarkExperiment.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
            except (ValueError, OSError):
                continue
            if item.project_id == project_id:
                candidates.append(item)
        # A later dry-run must not hide an earlier experiment that already has
        # actual clips waiting for human review.  The UI's review surface uses
        # this method, so prefer evidence-bearing experiments first.
        reviewed_candidates = [
            item for item in candidates
            if any(job.status in {"completed", "reviewed", "failed"} for job in item.jobs)
        ]
        return max(reviewed_candidates or candidates, key=lambda item: item.created_at, default=None)


def _assets(project: Project, kind: AssetKind) -> list[str]:
    candidates = [asset for asset in project.assets if asset.kind == kind]
    candidates.sort(key=lambda asset: asset.size, reverse=True)
    return [asset.id for asset in candidates]


def _cases(project: Project) -> list[BenchmarkCase]:
    product = _assets(project, AssetKind.product)
    character = _assets(project, AssetKind.character)
    scene = _assets(project, AssetKind.scene)

    product_case = BenchmarkCase(
        id="product_showcase",
        label="商品静态展示",
        purpose="测量商品轮廓、颜色、结构保持与轻微镜头运动。",
        prompt=(
            "真实商业摄影，商品保持与首帧完全一致。镜头缓慢向前推进，"
            "商品本身不旋转、不变形，不生成文字、Logo或额外道具。"
        ),
        reference_asset_ids=product[:1],
        first_frame_asset_id=product[0] if product else "",
        eligible=bool(product),
        blocking_reasons=[] if product else ["缺少商品参考图"],
        review_dimensions=["product_identity", "low_visual_quality", "text_watermark"],
    )
    emotion_case = BenchmarkCase(
        id="character_emotion",
        label="人物情绪表演",
        purpose="测量人物身份、表情自然度、视线和轻动作稳定性。",
        prompt=(
            "真实生活电影镜头，人物保持与首帧同一身份、年龄、发型和服装。"
            "人物先安静观察，再露出克制自然的微笑，目光不直视镜头。"
        ),
        reference_asset_ids=character[:1],
        first_frame_asset_id=character[0] if character else "",
        eligible=bool(character),
        blocking_reasons=[] if character else ["缺少人物参考图"],
        review_dimensions=["character_identity", "camera_gaze", "story_mismatch"],
    )
    interaction_references = [*product[:1], *character[:1], *scene[:1]]
    missing = []
    if not product:
        missing.append("缺少商品参考图")
    if not character:
        missing.append("缺少人物参考图")
    if not scene:
        missing.append("缺少场景参考图")
    interaction_case = BenchmarkCase(
        id="hand_interaction",
        label="手部商品交互",
        purpose="测量手部物理、商品结构与动作因果连续性。",
        prompt=(
            "真实商业短片，人物用一只手稳定托住商品，另一只手只完成一次轻触动作。"
            "手指数量和关节自然，手与商品不融合，商品结构不变化，镜头保持稳定。"
        ),
        reference_asset_ids=interaction_references,
        first_frame_asset_id="",
        requires_composite_keyframe=True,
        eligible=not missing,
        blocking_reasons=missing,
        review_dimensions=["hand_physics", "product_identity", "story_mismatch"],
    )
    return [product_case, emotion_case, interaction_case]


def _seedance_estimate(duration: int) -> float:
    tokens_per_second = int(getenv("ARK_SEEDANCE_TOKENS_PER_SECOND", "49368"))
    price_per_million = float(getenv("ARK_SEEDANCE_PRICE_PER_MILLION", "15"))
    return round(tokens_per_second * duration / 1_000_000 * price_per_million, 4)


def prepare_benchmark(project: Project) -> BenchmarkExperiment:
    cases = _cases(project)
    providers = {
        "seedance-official": bool(getenv("ARK_API_KEY", "").strip()),
        "minimax-official": bool(getenv("MINIMAX_API_KEY", "").strip()),
    }
    seedance_model = VolcArkSeedanceProvider._route("story")
    minimax_model, minimax_duration, minimax_resolution, minimax_cost = (
        MiniMaxOfficialVideoProvider._route("story", 6)
    )
    jobs: list[BenchmarkJob] = []
    for case in cases:
        jobs.extend([
            BenchmarkJob(
                id=f"{case.id}:seedance-official",
                case_id=case.id,
                provider="seedance-official",
                model_id=seedance_model,
                duration=case.duration,
                resolution="account-configured",
                estimated_cost_cny=_seedance_estimate(case.duration),
                status="planned" if providers["seedance-official"] and case.eligible else "unavailable",
            ),
            BenchmarkJob(
                id=f"{case.id}:minimax-official",
                case_id=case.id,
                provider="minimax-official",
                model_id=minimax_model,
                duration=minimax_duration,
                resolution=minimax_resolution,
                estimated_cost_cny=minimax_cost,
                status="planned" if providers["minimax-official"] and case.eligible else "unavailable",
            ),
        ])
    keyframe_unit = float(getenv("ARK_SEEDREAM_PRICE_PER_IMAGE", "0.25"))
    keyframe_count = sum(
        case.requires_composite_keyframe and case.eligible for case in cases
    )
    keyframe_cost = round(keyframe_count * keyframe_unit, 4)
    estimated = keyframe_cost + sum(
        job.estimated_cost_cny for job in jobs if job.status == "planned"
    )
    hard_budget = math.ceil(estimated * 1.10 * 100) / 100
    experiment_id = str(uuid4())
    blocked = not all(case.eligible for case in cases) or not any(
        job.status == "planned" for job in jobs
    )
    return BenchmarkExperiment(
        id=experiment_id,
        project_id=project.id,
        status="blocked" if blocked else "prepared",
        cases=cases,
        jobs=jobs,
        pricing=[
            PricingEvidence(
                provider="minimax-official",
                basis="official_paygo",
                description="MiniMax Hailuo Fast 768P 6秒按量公开价格。",
                source_url="https://platform.minimaxi.com/docs/guides/pricing-paygo",
                confidence="high",
            ),
            PricingEvidence(
                provider="seedance-official",
                basis="local_empirical",
                description=(
                    "按本账户历史5秒任务的每秒token消耗与环境配置的"
                    "每百万token价格线性估算，最终以火山账单为准。"
                ),
                confidence="medium",
            ),
        ],
        keyframe_cost_cny=keyframe_cost,
        keyframe_status="planned" if keyframe_count else "not_required",
        hard_budget_cny=hard_budget,
        approval_phrase=f"确认实验 {experiment_id} 预算 ¥{hard_budget:.2f}",
    )


def approve_benchmark(
    experiment: BenchmarkExperiment,
    *,
    phrase: str,
    approved_budget_cny: float,
) -> BenchmarkExperiment:
    if experiment.status == "blocked":
        raise ValueError("实验仍有缺失素材或没有已配置的供应商，不能审批。")
    if phrase.strip() != experiment.approval_phrase:
        raise ValueError("审批短语不匹配，未授权任何付费任务。")
    if round(approved_budget_cny, 2) < round(experiment.hard_budget_cny, 2):
        raise ValueError("批准预算低于实验硬上限，未授权任何付费任务。")
    experiment.status = "approved"
    experiment.approved_budget_cny = round(approved_budget_cny, 2)
    experiment.approved_at = now()
    return experiment


def record_blind_review(
    experiment: BenchmarkExperiment,
    *,
    job_id: str,
    score: int,
    passed: bool,
    failure_categories: list[str] | None = None,
) -> BenchmarkExperiment:
    job = next((item for item in experiment.jobs if item.id == job_id), None)
    if not job:
        raise ValueError("实验任务不存在。")
    if job.status not in {"completed", "reviewed"}:
        raise ValueError("实验任务尚未生成完成，不能记录盲审结果。")
    if not 0 <= score <= 100:
        raise ValueError("盲审分数必须在0到100之间。")
    job.blind_review_score = score
    job.blind_review_passed = passed
    job.failure_categories = list(dict.fromkeys(failure_categories or []))
    job.status = "reviewed"
    active = [item for item in experiment.jobs if item.status != "unavailable"]
    experiment.status = (
        "completed"
        if active and all(item.status == "reviewed" for item in active)
        else "review_required"
    )
    return experiment


def next_blind_review(experiment: BenchmarkExperiment) -> BlindReviewItem | None:
    case_by_id = {item.id: item for item in experiment.cases}
    eligible = [
        job for job in experiment.jobs
        if job.status in {"completed", "reviewed"}
        and job.output_url
        and job.blind_review_score is None
    ]
    if not eligible:
        return None
    job = sorted(eligible, key=lambda item: (item.case_id, item.id))[0]
    if not job.blind_review_token:
        job.blind_review_token = str(uuid4())
    case = case_by_id[job.case_id]
    return BlindReviewItem(
        token=job.blind_review_token,
        sample_label="匿名候选镜头",
        case_label=case.label,
        review_dimensions=case.review_dimensions,
        output_url=job.output_url,
    )


def record_blind_review_by_token(
    experiment: BenchmarkExperiment,
    *,
    token: str,
    score: int,
    passed: bool,
    failure_categories: list[str],
) -> BenchmarkExperiment:
    job = next((item for item in experiment.jobs if item.blind_review_token == token), None)
    if not job:
        raise ValueError("盲审样本令牌无效或已过期。")
    return record_blind_review(
        experiment,
        job_id=job.id,
        score=score,
        passed=passed,
        failure_categories=failure_categories,
    )


def write_markdown_report(experiment: BenchmarkExperiment, target: Path) -> Path:
    lines = [
        f"# 供应商基准实验 {experiment.id}",
        "",
        f"- 状态：`{experiment.status}`",
        f"- 项目：`{experiment.project_id}`",
        f"- 预计费用：¥{experiment.estimated_cost_cny:.2f}",
        f"- 硬预算上限（含 {experiment.contingency_rate:.0%} 预留）：¥{experiment.hard_budget_cny:.2f}",
        f"- 付费执行条件：`{experiment.approval_phrase}`",
        "",
        "## 测试镜头",
        "",
    ]
    for case in experiment.cases:
        lines.extend([
            f"### {case.label}",
            "",
            case.purpose,
            "",
            f"- 可执行：{'是' if case.eligible else '否'}",
            f"- 参考素材：{', '.join(case.reference_asset_ids) or '无'}",
            f"- 阻塞原因：{', '.join(case.blocking_reasons) or '无'}",
            f"- 质检维度：{', '.join(case.review_dimensions)}",
            "",
        ])
    lines.extend(["## 任务与费用", "", "| 镜头 | 供应商 | 模型 | 状态 | 预计费用 |", "| --- | --- | --- | --- | ---: |"])
    lines.extend(
        f"| {job.case_id} | {job.provider} | {job.model_id} | {job.status} | ¥{job.estimated_cost_cny:.2f} |"
        for job in experiment.jobs
    )
    lines.extend([
        "",
        "## 安全约束",
        "",
        "生成本报告不会调用任何付费 API。只有实验状态为 `approved`、审批短语完全一致、批准预算不低于硬上限时，执行器才允许提交任务。",
    ])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines), encoding="utf-8")
    return target
