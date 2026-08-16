import asyncio
import subprocess
from datetime import datetime
from pathlib import Path

from backend.app.director import _fallback_plan
from backend.app.live_preview import refresh_live_preview
from backend.app.orchestration import WorkflowAttempt, build_workflow
from backend.app.production import build_production_plan
from backend.app.schemas import Project


def _project() -> Project:
    timestamp = datetime.now()
    project = Project(
        id="live-preview-test",
        created_at=timestamp,
        updated_at=timestamp,
        name="流式预览测试",
        product_name="测试玩具",
        product_category="儿童玩具",
        platform="抖音",
        duration=15,
        aspect_ratio="9:16",
        style="真实生活",
        audience="家庭用户",
        selling_points=["互动体验"],
        brief="",
    )
    return project.model_copy(update={"creative_plan": _fallback_plan(project)})


def _clip(target: Path, color: str) -> None:
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"color=c={color}:s=360x640:d=0.35:r=24",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(target),
        ],
        check=True,
    )


def _attach_take(run, shot_id: str, source: Path, number: int = 1) -> None:
    node = next(item for item in run.nodes if item.id == f"video:{shot_id}")
    node.status = "completed"
    node.attempts.append(WorkflowAttempt(
        number=number,
        status="completed",
        provider="test",
        outputs={"video_path": str(source)},
    ))


def test_live_preview_grows_and_replaces_selected_take(tmp_path: Path) -> None:
    project = _project()
    run = build_workflow(project, build_production_plan(project))
    first, second = project.creative_plan.shots[:2]
    red = tmp_path / "red.mp4"
    blue = tmp_path / "blue.mp4"
    green = tmp_path / "green.mp4"
    _clip(red, "red")
    _clip(blue, "blue")
    _clip(green, "green")

    _attach_take(run, first.id, red)
    asyncio.run(refresh_live_preview(project=project, run=run, artifact_root=tmp_path))
    playlist = tmp_path / project.id / "live-preview" / "preview.m3u8"
    first_manifest = playlist.read_text(encoding="utf-8")
    assert run.live_preview_ready_shots == 1
    assert "shot-01-take-01.ts" in first_manifest
    assert "#EXT-X-ENDLIST" not in first_manifest

    _attach_take(run, second.id, blue)
    asyncio.run(refresh_live_preview(project=project, run=run, artifact_root=tmp_path))
    assert run.live_preview_ready_shots == 2
    assert "shot-02-take-01.ts" in playlist.read_text(encoding="utf-8")

    _attach_take(run, first.id, green, number=2)
    asyncio.run(refresh_live_preview(project=project, run=run, artifact_root=tmp_path))
    replaced = playlist.read_text(encoding="utf-8")
    assert "shot-01-take-02.ts" in replaced
    assert "shot-01-take-01.ts" not in replaced
    assert run.live_preview_revision == 3
