from __future__ import annotations

from pathlib import Path
from typing import Any


def analyze_internal_cuts(
    video_path: Path,
    *,
    threshold: float = 38.0,
    min_scene_len: int = 12,
) -> dict[str, Any]:
    """Detect unintended hard cuts inside one generated narrative shot.

    Each provider clip represents a single planned shot, so an internal hard
    cut usually indicates a generation discontinuity. A deliberately high
    threshold avoids treating normal camera/object motion as a cut. The module
    is optional at runtime so a missing local dependency never submits a paid
    retry or breaks delivery.
    """
    try:
        from scenedetect import ContentDetector, detect
    except ImportError:
        return {
            "available": False,
            "detector": "pyscenedetect-content",
            "unexpected_cut_count": 0,
            "cut_timecodes": [],
            "warning": "PySceneDetect is not installed; internal-cut QC skipped.",
        }

    try:
        scenes = detect(
            str(video_path),
            ContentDetector(threshold=threshold, min_scene_len=min_scene_len),
            show_progress=False,
        )
    except Exception as exc:
        # OpenCV/PySceneDetect builds on Windows can fail to open otherwise
        # valid videos when their absolute path contains non-ASCII characters.
        # Internal-cut detection is advisory; visual frame QC still runs and
        # a local decoder limitation must never trigger a paid regeneration.
        return {
            "available": False,
            "detector": "pyscenedetect-content",
            "unexpected_cut_count": 0,
            "cut_timecodes": [],
            "warning": f"Internal-cut QC skipped: {exc}",
        }
    cut_timecodes = [scene[0].get_timecode() for scene in scenes[1:]]
    return {
        "available": True,
        "detector": "pyscenedetect-content",
        "threshold": threshold,
        "scene_count": len(scenes),
        "unexpected_cut_count": max(0, len(scenes) - 1),
        "cut_timecodes": cut_timecodes,
        "warning": "" if len(scenes) <= 1 else "Single-shot clip contains internal hard cuts.",
    }
