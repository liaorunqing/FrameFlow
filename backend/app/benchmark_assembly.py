"""Zero-cost editorial assembly from completed provider-benchmark clips."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from .audio import build_audio_timeline, synthesize_windows_narration, write_srt
from .benchmarking import BenchmarkExperiment
from .composer import compose_campaign
from .schemas import AssetKind, Project


class BenchmarkAssemblyResult(BaseModel):
    experiment_id: str
    status: str
    output_url: str = ""
    output_path: str = ""
    selected_job_ids: list[str] = []
    duration_seconds: float = 0
    narration_path: str = ""
    subtitle_path: str = ""
    note: str = ""


CASE_ORDER = ("character_emotion", "hand_interaction", "product_showcase")


def _best_job(experiment: BenchmarkExperiment, case_id: str):
    candidates = [
        job for job in experiment.jobs
        if job.case_id == case_id and job.status in {"completed", "reviewed"}
        and job.output_path and Path(job.output_path).is_file()
    ]
    if not candidates:
        raise RuntimeError(f"缺少可本地剪辑的成功镜头：{case_id}")
    return max(
        candidates,
        key=lambda item: (
            item.operator_review_score or item.blind_review_score or 0,
            -(item.latency_seconds or 99999),
        ),
    )


def _brand_path(project: Project, upload_root: Path) -> Path | None:
    brand = next((item for item in project.assets if item.kind == AssetKind.brand), None)
    if not brand:
        return None
    candidate = upload_root / project.id / Path(brand.url).name
    return candidate if candidate.is_file() else None


async def assemble_benchmark_reel(
    *,
    project: Project,
    experiment: BenchmarkExperiment,
    upload_root: Path,
    artifact_root: Path,
) -> BenchmarkAssemblyResult:
    """Assemble the best completed clips according to the existing story order.

    This is deliberately labelled as a local editorial sample, rather than a
    production delivery: the clips originate from a capability benchmark and
    were not rendered as one connected multi-shot production run.
    """
    if not project.creative_plan:
        raise RuntimeError("项目缺少故事分镜，无法建立旁白与字幕时间轴。")
    if len(project.creative_plan.shots) != len(CASE_ORDER):
        raise RuntimeError("本地实验样片当前只支持三段故事分镜。")

    selected = [_best_job(experiment, case_id) for case_id in CASE_ORDER]
    root = artifact_root / experiment.id / "local-assembly"
    root.mkdir(parents=True, exist_ok=True)
    timeline = build_audio_timeline(project)
    subtitle_path = write_srt(timeline, root / "subtitles.srt")
    narration_path = await synthesize_windows_narration(
        timeline,
        root / "narration.wav",
        allow_paid_fallback=False,
    )
    output_path = root / "benchmark-story-sample.mp4"
    await compose_campaign(
        clip_paths=[Path(job.output_path) for job in selected],
        clip_durations=[float(shot.duration) for shot in project.creative_plan.shots],
        aspect_ratio=project.aspect_ratio,
        narration_path=narration_path,
        subtitle_path=subtitle_path,
        output_path=output_path,
        logo_path=_brand_path(project, upload_root),
        cta_headline=project.creative_plan.call_to_action,
        product_name=project.product_name,
        cta_background=None,
        cta_duration=0,
        cta_overlay_duration=min(2.8, max(1.2, project.duration * 0.2)),
    )
    return BenchmarkAssemblyResult(
        experiment_id=experiment.id,
        status="completed",
        output_url=(
            f"/benchmark-artifacts/{experiment.id}/local-assembly/{output_path.name}"
        ),
        output_path=str(output_path),
        selected_job_ids=[job.id for job in selected],
        duration_seconds=float(project.duration),
        narration_path=str(narration_path),
        subtitle_path=str(subtitle_path),
        note="本地剪辑样片：复用基准实验中评分最高的镜头；未触发任何生成 API。",
    )
