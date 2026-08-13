from __future__ import annotations

import json
from pathlib import Path

import cv2
from pydantic import BaseModel, Field


class TrackPoint(BaseModel):
    frame: int
    x: int
    y: int
    width: int
    height: int
    confidence: float = Field(ge=0, le=1)


class MotionTrack(BaseModel):
    source: str
    fps: float
    points: list[TrackPoint]
    usable: bool
    reason: str = ""


def track_template(
    *,
    video_path: Path,
    initial_box: tuple[int, int, int, int],
    target: Path | None = None,
    search_margin: float = 1.5,
) -> MotionTrack:
    """Track an operator-approved product-screen ROI with template matching.

    The tracker never invents an ROI. When confidence drops, the post layer
    must fall back to a fixed safe-zone badge instead of drifting over a face.
    """
    capture = cv2.VideoCapture(str(video_path))
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 24)
    ok, first = capture.read()
    if not ok:
        return MotionTrack(source=str(video_path), fps=fps, points=[], usable=False, reason="无法读取首帧")
    x, y, width, height = initial_box
    frame_height, frame_width = first.shape[:2]
    if width < 8 or height < 8 or x < 0 or y < 0 or x + width > frame_width or y + height > frame_height:
        return MotionTrack(source=str(video_path), fps=fps, points=[], usable=False, reason="ROI 越界或过小")
    template = first[y:y + height, x:x + width]
    points = [TrackPoint(frame=0, x=x, y=y, width=width, height=height, confidence=1.0)]
    previous_x, previous_y = x, y
    frame_index = 1
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        margin_x = round(width * search_margin)
        margin_y = round(height * search_margin)
        sx = max(0, previous_x - margin_x)
        sy = max(0, previous_y - margin_y)
        ex = min(frame_width, previous_x + width + margin_x)
        ey = min(frame_height, previous_y + height + margin_y)
        search = frame[sy:ey, sx:ex]
        if search.shape[0] < height or search.shape[1] < width:
            break
        result = cv2.matchTemplate(search, template, cv2.TM_CCOEFF_NORMED)
        _, confidence, _, location = cv2.minMaxLoc(result)
        nx, ny = sx + location[0], sy + location[1]
        points.append(TrackPoint(
            frame=frame_index, x=nx, y=ny, width=width, height=height,
            confidence=max(0.0, min(1.0, float(confidence))),
        ))
        if confidence >= 0.55:
            previous_x, previous_y = nx, ny
        frame_index += 1
    capture.release()
    usable = bool(points) and sum(point.confidence >= 0.55 for point in points) / len(points) >= 0.85
    track = MotionTrack(
        source=str(video_path), fps=fps, points=points, usable=usable,
        reason="" if usable else "跟踪置信度不足，必须使用固定安全区叠加",
    )
    if target:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(track.model_dump_json(indent=2), encoding="utf-8")
    return track
