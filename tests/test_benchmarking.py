from datetime import datetime
from pathlib import Path

import pytest

from backend.app.benchmarking import (
    BenchmarkStore,
    approve_benchmark,
    prepare_benchmark,
    record_blind_review,
    next_blind_review,
    write_markdown_report,
)
from backend.app.benchmark_executor import BenchmarkExecutor
from backend.app.benchmark_dataset import export_benchmark_dataset
from backend.app.director import _fallback_plan
from backend.app.schemas import Asset, AssetKind, Project


def make_project(*, with_assets: bool = True) -> Project:
    timestamp = datetime.now().astimezone()
    project = Project(
        id="benchmark-test",
        created_at=timestamp,
        updated_at=timestamp,
        name="供应商基准",
        product_name="陪伴玩具",
        product_category="儿童玩具",
        platform="抖音",
        duration=30,
        aspect_ratio="16:9",
        style="真实生活电影感",
        audience="年轻父母",
        selling_points=["陪伴", "表达"],
        brief="以克制真实的表演介绍商品。",
    )
    if not with_assets:
        return project.model_copy(update={"creative_plan": _fallback_plan(project)})
    assets = [
        Asset(
            id=f"{kind.value}-asset",
            project_id=project.id,
            kind=kind,
            name=f"{kind.value}.jpg",
            url=f"/uploads/{project.id}/{kind.value}.jpg",
            content_type="image/jpeg",
            size=1024,
            created_at=timestamp,
        )
        for kind in (AssetKind.product, AssetKind.character, AssetKind.scene)
    ]
    return project.model_copy(update={
        "assets": assets,
        "creative_plan": _fallback_plan(project),
    })


def test_benchmark_dry_run_is_blocked_when_reference_assets_are_missing(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ARK_API_KEY", "test")
    monkeypatch.setenv("MINIMAX_API_KEY", "test")
    experiment = prepare_benchmark(make_project(with_assets=False))

    assert experiment.status == "blocked"
    assert experiment.can_execute is False
    assert experiment.estimated_cost_cny == 0
    assert all(not case.eligible for case in experiment.cases)


def test_blind_review_item_hides_provider_identity(monkeypatch) -> None:
    monkeypatch.setenv("MINIMAX_API_KEY", "test")
    monkeypatch.setenv("ARK_API_KEY", "test")
    experiment = prepare_benchmark(make_project())
    job = next(item for item in experiment.jobs if item.provider == "minimax-official")
    job.status = "completed"
    job.output_url = "/benchmark-artifacts/example.mp4"

    item = next_blind_review(experiment)

    assert item is not None
    serialized = item.model_dump_json()
    assert "minimax" not in serialized
    assert "seedance" not in serialized
    assert item.token


def test_benchmark_prepares_same_three_cases_for_both_providers(monkeypatch) -> None:
    monkeypatch.setenv("ARK_API_KEY", "test")
    monkeypatch.setenv("MINIMAX_API_KEY", "test")
    monkeypatch.setenv("ARK_VIDEO_MODEL", "doubao-seedance-1-0-pro-test")
    experiment = prepare_benchmark(make_project())

    assert experiment.status == "prepared"
    assert len(experiment.cases) == 3
    assert len([job for job in experiment.jobs if job.status == "planned"]) == 6
    # Seedream 5.0 Lite is currently accounted at the configured ¥0.22/image.
    assert experiment.keyframe_cost_cny == 0.22
    assert experiment.hard_budget_cny >= experiment.estimated_cost_cny
    assert experiment.can_execute is False


def test_benchmark_requires_exact_phrase_and_sufficient_budget(monkeypatch) -> None:
    monkeypatch.setenv("ARK_API_KEY", "test")
    monkeypatch.setenv("MINIMAX_API_KEY", "test")
    experiment = prepare_benchmark(make_project())

    with pytest.raises(ValueError, match="审批短语"):
        approve_benchmark(
            experiment,
            phrase="我同意",
            approved_budget_cny=experiment.hard_budget_cny,
        )
    with pytest.raises(ValueError, match="低于"):
        approve_benchmark(
            experiment,
            phrase=experiment.approval_phrase,
            approved_budget_cny=experiment.hard_budget_cny - 0.01,
        )

    approve_benchmark(
        experiment,
        phrase=experiment.approval_phrase,
        approved_budget_cny=experiment.hard_budget_cny,
    )
    assert experiment.status == "approved"
    assert experiment.can_execute is True


def test_benchmark_markdown_report_is_a_zero_cost_artifact(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("ARK_API_KEY", "test")
    monkeypatch.setenv("MINIMAX_API_KEY", "test")
    experiment = prepare_benchmark(make_project())

    target = write_markdown_report(experiment, tmp_path / "benchmark.md")
    content = target.read_text(encoding="utf-8")

    assert "商品静态展示" in content
    assert "手部商品交互" in content
    assert "不会调用任何付费 API" in content


def test_blind_review_can_only_be_recorded_after_generation(monkeypatch) -> None:
    monkeypatch.setenv("ARK_API_KEY", "test")
    monkeypatch.setenv("MINIMAX_API_KEY", "test")
    experiment = prepare_benchmark(make_project())
    job = experiment.jobs[0]

    with pytest.raises(ValueError, match="尚未生成完成"):
        record_blind_review(
            experiment,
            job_id=job.id,
            score=80,
            passed=True,
        )

    job.status = "completed"
    record_blind_review(
        experiment,
        job_id=job.id,
        score=82,
        passed=True,
    )
    assert job.status == "reviewed"
    assert job.blind_review_score == 82
    assert experiment.status == "review_required"


def test_executor_refuses_a_task_that_would_cross_hard_budget(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("ARK_API_KEY", "test")
    monkeypatch.setenv("MINIMAX_API_KEY", "test")
    experiment = prepare_benchmark(make_project())
    approve_benchmark(
        experiment,
        phrase=experiment.approval_phrase,
        approved_budget_cny=experiment.hard_budget_cny,
    )
    executor = BenchmarkExecutor(
        store=BenchmarkStore(tmp_path / "store"),
        upload_root=tmp_path / "uploads",
        artifact_root=tmp_path / "artifacts",
    )

    with pytest.raises(RuntimeError, match="超过批准上限"):
        executor.assert_budget(experiment, experiment.hard_budget_cny + 0.01)


def test_dataset_export_keeps_automatic_qc_manual_only(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ARK_API_KEY", "test")
    monkeypatch.setenv("MINIMAX_API_KEY", "test")
    experiment = prepare_benchmark(make_project())
    for job in experiment.jobs[:2]:
        job.status = "completed"
        job.output_url = f"/benchmark-artifacts/{job.id}.mp4"
        job.output_path = str(tmp_path / f"{job.id}.mp4")
        job.operator_review_passed = True
        job.automatic_passed = False
        job.automatic_score = 20

    report = export_benchmark_dataset(experiment, tmp_path / "datasets")

    assert report.generated == 2
    assert report.confusion.false_negative == 2
    assert report.automatic_retry_safe is False
    assert report.ready_for_paid_production is False
    assert Path(report.manifest_path).is_file()


def test_latest_benchmark_prefers_completed_evidence_over_later_dry_run(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ARK_API_KEY", "test")
    monkeypatch.setenv("MINIMAX_API_KEY", "test")
    store = BenchmarkStore(tmp_path / "benchmarks")
    evidence = prepare_benchmark(make_project())
    evidence.created_at = datetime(2026, 1, 1).astimezone()
    evidence.jobs[0].status = "completed"
    evidence.jobs[0].output_url = "/benchmark-artifacts/evidence.mp4"
    later_dry_run = prepare_benchmark(make_project())
    later_dry_run.created_at = datetime(2026, 1, 2).astimezone()
    store.put(evidence)
    store.put(later_dry_run)

    assert store.latest(evidence.project_id).id == evidence.id


def test_three_blind_reviews_enable_manual_production_but_not_auto_retry(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ARK_API_KEY", "test")
    monkeypatch.setenv("MINIMAX_API_KEY", "test")
    experiment = prepare_benchmark(make_project())
    for job in experiment.jobs[:3]:
        job.status = "reviewed"
        job.output_url = "/benchmark-artifacts/sample.mp4"
        job.automatic_passed = False
        job.blind_review_passed = True

    report = export_benchmark_dataset(experiment, tmp_path / "calibration")

    assert report.ready_for_paid_production is True
    assert report.automatic_retry_safe is False
