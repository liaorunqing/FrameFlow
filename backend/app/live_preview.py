from __future__ import annotations

import asyncio
import json
import math
import shutil
from pathlib import Path

from .orchestration import WorkflowRun
from .schemas import Project


def _selected_video_path(node) -> Path | None:
    for attempt in reversed(node.attempts):
        if attempt.status != "completed":
            continue
        candidate = Path(attempt.outputs.get("video_path", ""))
        if candidate.is_file():
            return candidate
    return None


def _canvas(aspect_ratio: str) -> tuple[int, int]:
    if aspect_ratio == "16:9":
        return 1280, 720
    if aspect_ratio == "1:1":
        return 720, 720
    return 720, 1280


async def _normalize_segment(source: Path, target: Path, aspect_ratio: str) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("缺少 FFmpeg，无法创建流式预览。")
    width, height = _canvas(aspect_ratio)
    filter_graph = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
        "fps=24,format=yuv420p"
    )
    process = await asyncio.create_subprocess_exec(
        ffmpeg,
        "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(source),
        "-map", "0:v:0", "-an",
        "-vf", filter_graph,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-g", "48", "-keyint_min", "48", "-sc_threshold", "0",
        "-f", "mpegts", str(target),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode or not target.is_file():
        raise RuntimeError(
            "流式预览分段转换失败："
            + stderr.decode("utf-8", errors="replace")[-600:]
        )


async def refresh_live_preview(
    *,
    project: Project,
    run: WorkflowRun,
    artifact_root: Path,
) -> WorkflowRun:
    """Build an appendable HLS rough cut from currently selected shot takes.

    Segments are normalized independently, so replacing one selected attempt
    only rebuilds that shot. The playlist is replaced atomically and remains
    playable while later shots are still being generated.
    """
    root = artifact_root / project.id / "live-preview"
    root.mkdir(parents=True, exist_ok=True)
    state_path = root / "state.json"
    try:
        previous = json.loads(state_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        previous = {"segments": {}, "revision": 0}

    shots = project.creative_plan.shots if project.creative_plan else []
    video_nodes = {
        node.shot_id: node
        for node in run.nodes
        if node.kind == "video_generation" and node.shot_id
    }
    segments: list[tuple[str, float]] = []
    state_segments: dict[str, dict[str, str | int]] = {}
    for shot in shots:
        node = video_nodes.get(shot.id)
        if not node:
            continue
        source = _selected_video_path(node)
        if source is None:
            continue
        selected_attempt = node.attempts[-1].number
        signature = f"{source.resolve()}:{source.stat().st_size}:{source.stat().st_mtime_ns}"
        filename = f"shot-{shot.index:02d}-take-{selected_attempt:02d}.ts"
        target = root / filename
        old = dict(previous.get("segments", {}).get(shot.id, {}))
        if old.get("signature") != signature or old.get("filename") != filename or not target.is_file():
            await _normalize_segment(source, target, project.aspect_ratio)
        state_segments[shot.id] = {
            "signature": signature,
            "filename": filename,
            "attempt": selected_attempt,
        }
        segments.append((filename, float(shot.duration)))

    if not segments:
        return run

    revision = int(previous.get("revision", 0)) + 1
    complete = len(segments) == len(shots) and bool(shots)
    target_duration = max(1, math.ceil(max(duration for _, duration in segments)))
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        f"#EXT-X-TARGETDURATION:{target_duration}",
        "#EXT-X-MEDIA-SEQUENCE:0",
        "#EXT-X-PLAYLIST-TYPE:EVENT",
        "#EXT-X-INDEPENDENT-SEGMENTS",
    ]
    for filename, duration in segments:
        lines.extend([f"#EXTINF:{duration:.3f},", filename])
    if complete:
        lines.append("#EXT-X-ENDLIST")
    playlist = root / "preview.m3u8"
    temporary = playlist.with_suffix(".tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary.replace(playlist)
    state_path.write_text(
        json.dumps(
            {"revision": revision, "complete": complete, "segments": state_segments},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    run.live_preview_revision = revision
    run.live_preview_ready_shots = len(segments)
    run.live_preview_complete = complete
    run.live_preview_url = (
        f"/artifacts/{project.id}/live-preview/preview.m3u8?v={revision}"
    )
    return run
