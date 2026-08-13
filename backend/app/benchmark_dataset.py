"""Zero-cost benchmark dataset export and quality-gate calibration."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from .benchmarking import BenchmarkExperiment


class CalibrationConfusion(BaseModel):
    true_positive: int = 0
    false_positive: int = 0
    true_negative: int = 0
    false_negative: int = 0
    unlabelled: int = 0


class CaseCalibration(BaseModel):
    case_id: str
    generated: int = 0
    automatic_passed: int = 0
    operator_passed: int = 0
    blind_labelled: int = 0


class CalibrationReport(BaseModel):
    experiment_id: str
    generated: int
    failed: int
    operator_labelled: int
    blind_labelled: int
    cost_cny: float
    confusion: CalibrationConfusion
    by_case: list[CaseCalibration] = Field(default_factory=list)
    automatic_retry_safe: bool
    ready_for_paid_production: bool
    blockers: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    manifest_path: str = ""
    generated_at: datetime = Field(default_factory=lambda: datetime.now().astimezone())


def _reference_label(job) -> bool | None:
    """Prefer blind labels; operator labels remain provisional evidence."""
    if job.blind_review_passed is not None:
        return job.blind_review_passed
    return job.operator_review_passed


def build_calibration_report(experiment: BenchmarkExperiment) -> CalibrationReport:
    completed = [job for job in experiment.jobs if job.status in {"completed", "reviewed"}]
    failed = [job for job in experiment.jobs if job.status == "failed"]
    confusion = CalibrationConfusion()
    cases: dict[str, CaseCalibration] = defaultdict(lambda: CaseCalibration(case_id=""))
    for job in completed:
        case = cases[job.case_id]
        case.case_id = job.case_id
        case.generated += 1
        case.automatic_passed += int(job.automatic_passed is True)
        case.operator_passed += int(job.operator_review_passed is True)
        case.blind_labelled += int(job.blind_review_passed is not None)
        label = _reference_label(job)
        if label is None or job.automatic_passed is None:
            confusion.unlabelled += 1
        elif job.automatic_passed and label:
            confusion.true_positive += 1
        elif job.automatic_passed and not label:
            confusion.false_positive += 1
        elif not job.automatic_passed and not label:
            confusion.true_negative += 1
        else:
            confusion.false_negative += 1

    blind_labelled = sum(job.blind_review_passed is not None for job in completed)
    operator_labelled = sum(job.operator_review_passed is not None for job in completed)
    blind_negative = sum(job.blind_review_passed is False for job in completed)
    blockers: list[str] = []
    if blind_labelled < 3:
        blockers.append("至少完成 3 条匿名人工评分后，才能用真实盲审校准自动质检。")
    # These are calibration limitations, not blockers for a separately
    # approved, human-supervised production run.  Automatic paid retry stays
    # disabled regardless.
    recommendations = [
        "保持“自动质检仅分流；人工审核决定是否付费重做”的执行策略。",
        "优先收集 3 条匿名评分，并至少包含 1 条明确不通过样本，再讨论阈值校准。",
    ]
    if confusion.false_negative:
        recommendations.append("不要直接下调自动评分阈值：当前样本分数跨度大，需先增加盲审数据。")
    if blind_labelled and blind_negative < 1:
        recommendations.append("盲审样本中尚无不通过样本；后续应保留失败样本以测试自动质检的误放行风险。")
    return CalibrationReport(
        experiment_id=experiment.id, generated=len(completed), failed=len(failed),
        operator_labelled=operator_labelled, blind_labelled=blind_labelled,
        cost_cny=experiment.committed_cost_cny, confusion=confusion,
        by_case=sorted(cases.values(), key=lambda item: item.case_id),
        automatic_retry_safe=False, ready_for_paid_production=not blockers,
        blockers=blockers, recommendations=recommendations,
    )


def export_benchmark_dataset(experiment: BenchmarkExperiment, root: Path) -> CalibrationReport:
    """Write a local JSONL manifest. It never calls any provider or model."""
    destination = root / experiment.id
    destination.mkdir(parents=True, exist_ok=True)
    manifest = destination / "manifest.jsonl"
    rows = []
    for job in experiment.jobs:
        rows.append({
            "experiment_id": experiment.id, "job_id": job.id, "case_id": job.case_id,
            "provider": job.provider, "model_id": job.model_id, "status": job.status,
            "duration_seconds": job.duration, "resolution": job.resolution,
            "output_path": job.output_path, "output_url": job.output_url,
            "latency_seconds": job.latency_seconds, "actual_cost_cny": job.actual_cost_cny,
            "automatic_score": job.automatic_score, "automatic_passed": job.automatic_passed,
            "operator_review_score": job.operator_review_score,
            "operator_review_passed": job.operator_review_passed,
            "blind_review_score": job.blind_review_score,
            "blind_review_passed": job.blind_review_passed,
            "failure_categories": job.failure_categories, "error": job.error,
        })
    manifest.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    report = build_calibration_report(experiment)
    report.manifest_path = str(manifest)
    (destination / "calibration-report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return report
