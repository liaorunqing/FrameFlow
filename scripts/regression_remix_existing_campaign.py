from __future__ import annotations

import asyncio
import json
from pathlib import Path

from backend.app.composer import compose_campaign
from backend.app.creative_post import build_creative_post_plan, write_feature_state_srt
from backend.app.audio import build_audio_timeline, write_srt
from backend.app.schemas import Project


PROJECT_ID = "afa97e5b-1325-4c8d-96ba-28cbb99b04c6"
ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "backend" / "data" / "workflow-artifacts" / PROJECT_ID


async def main() -> None:
    store = json.loads((ROOT / "backend" / "data" / "store.json").read_text(encoding="utf-8"))
    project = Project.model_validate(store["projects"][PROJECT_ID])
    clip_paths = [
        ARTIFACT / "shot-01-attempt-01.mp4",
        ARTIFACT / "shot-02-attempt-01.mp4",
        ARTIFACT / "shot-03-attempt-01.mp4",
        ARTIFACT / "shot-04-attempt-02.mp4",
        ARTIFACT / "shot-05-attempt-01.mp4",
    ]
    durations = [float(shot.duration) for shot in project.creative_plan.shots]
    post = build_creative_post_plan(project, ARTIFACT)
    feature_srt = write_feature_state_srt(post, ARTIFACT / "feature-states.srt")
    write_srt(build_audio_timeline(project), ARTIFACT / "subtitles-v2.srt")
    output = ARTIFACT / "campaign-master-v2-post.mp4"
    await compose_campaign(
        clip_paths=clip_paths,
        clip_durations=durations,
        aspect_ratio=project.aspect_ratio,
        narration_path=ARTIFACT / "narration.wav",
        subtitle_path=ARTIFACT / "subtitles-v2.srt",
        output_path=output,
        cta_background=None,
        cta_headline=project.creative_plan.call_to_action,
        product_name=project.product_name,
        cta_overlay_duration=2.8,
        bgm_path=Path(post.bgm_path),
        bgm_gain=post.bgm_gain,
        sound_cues=[cue.model_dump(mode="json") for cue in post.sound_cues],
        feature_state_subtitle_path=feature_srt,
    )
    print(output)


if __name__ == "__main__":
    asyncio.run(main())
