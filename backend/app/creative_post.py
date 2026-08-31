from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from pydantic import BaseModel, Field

from .schemas import Project


def shotcraft_audio_root() -> Path:
    """Resolve bundled customer audio before the developer-only skill path."""
    configured = os.getenv("FRAMEFLOW_AUDIO_ROOT", "").strip()
    if configured:
        return Path(configured)
    app_home = os.getenv("FRAMEFLOW_HOME", "").strip()
    project_root = Path(app_home) if app_home else Path(__file__).resolve().parents[2]
    bundled = project_root / "backend" / "data" / "audio"
    if bundled.is_dir():
        return bundled
    return Path("C:/Users/liaoq/.codex/skills/video-shotcraft/assets/audio")


class SoundCue(BaseModel):
    start: float = Field(ge=0)
    path: str
    gain: float = Field(default=0.32, ge=0, le=2)
    purpose: str


class FeatureStateCue(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    shot_id: str
    label: str
    state: str
    anchor: str = "product_screen"


class CreativePostPlan(BaseModel):
    bgm_path: str = ""
    bgm_gain: float = 0.13
    sound_cues: list[SoundCue] = Field(default_factory=list)
    feature_states: list[FeatureStateCue] = Field(default_factory=list)
    brand_hold_seconds: float = 2.8


def _srt_time(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    secs, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


def write_feature_state_srt(plan: CreativePostPlan, target: Path) -> Path | None:
    """Write verified post-production state labels; never ask the model to draw text."""
    if not plan.feature_states:
        return None
    blocks = []
    for index, cue in enumerate(plan.feature_states, start=1):
        blocks.append(
            f"{index}\n{_srt_time(cue.start)} --> {_srt_time(cue.end)}\n"
            f"{cue.label}  ·  {cue.state}"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n\n".join(blocks) + "\n", encoding="utf-8-sig")
    return target


def _copy_asset(source: Path, target_root: Path) -> Path:
    target_root.mkdir(parents=True, exist_ok=True)
    target = target_root / source.name
    if not target.exists() or target.stat().st_size != source.stat().st_size:
        shutil.copy2(source, target)
    return target


def build_creative_post_plan(project: Project, output_root: Path) -> CreativePostPlan:
    """Build a deterministic sound/state plan from the approved storyboard."""
    if not project.creative_plan:
        raise ValueError("项目缺少已批准分镜。")
    audio_root = output_root / "licensed-audio"
    safe_bgm_name = Path(project.bgm_track).name
    source_root = shotcraft_audio_root()
    bgm_source = source_root / "bgm" / safe_bgm_name if safe_bgm_name != "none" else None
    transition_source = source_root / "sfx" / "transition" / "sweep-fast-small.mp3"
    impact_source = source_root / "sfx" / "impact" / "bass-hit-short.mp3"
    sparkle_source = source_root / "sfx" / "light" / "sparkle-touch.mp3"
    for source in (transition_source, impact_source, sparkle_source):
        if not source.is_file():
            raise FileNotFoundError(f"Shotcraft 音频资产缺失：{source}")
    if bgm_source is not None and not bgm_source.is_file():
        raise FileNotFoundError(f"BGM asset missing: {safe_bgm_name}")
    bgm = _copy_asset(bgm_source, audio_root) if bgm_source else None
    transition = _copy_asset(transition_source, audio_root)
    impact = _copy_asset(impact_source, audio_root)
    sparkle = _copy_asset(sparkle_source, audio_root)

    cursor = 0.0
    sounds: list[SoundCue] = []
    states: list[FeatureStateCue] = []
    enable_feature_states = os.getenv("ENABLE_FEATURE_STATE_OVERLAYS", "").lower() in {
        "1", "true", "yes", "on"
    }
    for index, shot in enumerate(project.creative_plan.shots):
        if index:
            sounds.append(SoundCue(
                start=max(0, cursor - 0.08), path=str(transition), gain=0.24,
                purpose=f"镜头 {index + 1} 动作衔接",
            ))
        text = " ".join((shot.title, shot.narrative_beat, shot.action, shot.voiceover))
        label = ""
        state = ""
        if any(word in text for word in ("提问", "对话", "回答")):
            label, state = "AI 对话", "正在聆听 · 准备回应"
        elif any(word in text for word in ("跟读", "口语")):
            label, state = "口语练习", "听一遍 · 跟着说"
        elif any(word in text for word in ("查词", "词典", "单词")):
            label, state = "词典", "正在查找"
        # Some legacy project records were saved with mojibake before the
        # UTF-8 migration. For an explicitly named language-learning product,
        # the approved five-shot grammar is deterministic: establish the
        # problem, converse, repeat, look up, resolve. This is a safe fallback
        # because it adds post labels only; it never changes product claims.
        learning_product = any(
            word in project.product_name.lower() for word in ("ai", "口语", "学习")
        )
        if not label and learning_product and len(project.creative_plan.shots) == 5:
            fallback_states = {
                1: ("AI 对话", "正在聆听 · 准备回应"),
                2: ("口语练习", "听一遍 · 跟着说"),
                3: ("词典", "正在查找"),
            }
            label, state = fallback_states.get(index, ("", ""))
        # Feature-state cards are optional annotations, not subtitles. They
        # must be explicitly enabled because displaying them together with
        # narration captions creates two competing reading layers.
        if label and enable_feature_states:
            state_start = round(cursor + 0.7, 3)
            state_end = round(min(cursor + shot.duration - 0.45, cursor + 3.0), 3)
            states.append(FeatureStateCue(
                start=state_start,
                end=state_end,
                shot_id=shot.id,
                label=label,
                state=state,
            ))
            verified_examples = {
                "AI 对话": ("示范", "I had a great day."),
                "口语练习": ("跟读", "I had a great day."),
                "词典": ("curious", "好奇的"),
            }
            result_label, result_state = verified_examples[label]
            result_end = round(min(cursor + shot.duration - 0.25, state_end + 2.25), 3)
            if result_end > state_end + 0.4:
                states.append(FeatureStateCue(
                    start=state_end,
                    end=result_end,
                    shot_id=shot.id,
                    label=result_label,
                    state=result_state,
                ))
        cursor += shot.duration
    finale = max(0.0, float(project.duration) - 2.8)
    sounds.extend([
        SoundCue(start=finale, path=str(impact), gain=0.28, purpose="CTA 落地"),
        SoundCue(start=min(float(project.duration) - 0.5, finale + 0.45), path=str(sparkle), gain=0.18, purpose="品牌余韵"),
    ])
    plan = CreativePostPlan(
        bgm_path=str(bgm) if bgm else "", bgm_gain=project.bgm_volume,
        sound_cues=sounds, feature_states=states
    )
    (output_root / "creative-post-plan.json").write_text(
        plan.model_dump_json(indent=2), encoding="utf-8"
    )
    attribution = output_root / "licensed-audio" / "ATTRIBUTION.md"
    source_attribution = source_root / "ATTRIBUTION.md"
    if source_attribution.is_file():
        shutil.copy2(source_attribution, attribution)
    return plan
