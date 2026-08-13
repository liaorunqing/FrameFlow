from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.audio import AudioTimeline
from backend.app.composer import compose_campaign
from backend.app.main import store
from backend.app.network import download_bytes
from backend.app.providers import VideoJob, VolcArkSeedanceProvider


PROJECT_ID = "afa97e5b-1325-4c8d-96ba-28cbb99b04c6"
# Mini is the lowest-cost Seedance 2.0 A/B route and still exposes multimodal
# reference, editing and extension capabilities.
MODEL_ID = "doubao-seedance-2-0-mini-260615"
PROJECT_ARTIFACTS = ROOT / "backend" / "data" / "workflow-artifacts" / PROJECT_ID
OUTPUT = PROJECT_ARTIFACTS / "seedance2-comparison"
STATE_PATH = OUTPUT / "state.json"
PRODUCT_IMAGE = (
    PROJECT_ARTIFACTS
    / "normalized-references"
    / "6fa1e63d-d804-4185-809a-ba76ed16f4fa-9x16.jpg"
)


def save_state(state: dict) -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_state() -> dict:
    if STATE_PATH.is_file():
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        state["model_id"] = MODEL_ID
        return state
    project = store.get_project(PROJECT_ID)
    shots = project["creative_plan"]["shots"]
    return {
        "project_id": PROJECT_ID,
        "model_id": MODEL_ID,
        "created_at": datetime.now().isoformat(),
        "status": "planned",
        "shots": [
            {
                "index": shot["index"],
                "id": shot["id"],
                "title": shot["title"],
                "prompt": shot["prompt"],
                "task_id": None,
                "status": "planned",
                "output": str(OUTPUT / f"shot-{shot['index']:02d}.mp4"),
                "last_frame": str(OUTPUT / f"shot-{shot['index']:02d}-last.jpg"),
                "usage": {},
            }
            for shot in shots
        ],
        "actual_tokens": 0,
        "output": None,
    }


def enhanced_prompt(shot: dict) -> str:
    return (
        "参考图1是真实商品身份照，必须锁定毛色、紫色耳朵和四肢、黑色屏幕脸、"
        "蓝色圆眼、粉色腕带与整体比例。参考图2是本镜头开场构图，参考图3是本镜头"
        "结束构图；在二者之间生成一次连续、因果明确的真人实拍动作。"
        "不得将学习卡变成可读文字，不得生成字幕、Logo、水印或新产品结构。"
        "人物面部、黑色短发、黄色开衫和客厅木桌、米色沙发、左侧窗光保持一致。"
        + shot["prompt"]
    )


async def submit_shot(provider: VolcArkSeedanceProvider, state: dict, shot: dict) -> None:
    if shot["task_id"]:
        return
    index = shot["index"]
    references = [
        str(PRODUCT_IMAGE),
        str(PROJECT_ARTIFACTS / f"kf-{index - 1:03d}-attempt-01.jpg"),
        str(PROJECT_ARTIFACTS / f"kf-{index:03d}-attempt-01.jpg"),
    ]
    submitted = await provider.create_task(
        prompt=enhanced_prompt(shot),
        duration=6,
        reference_images=references,
        model_hint="premium",
        aspect_ratio="9:16",
    )
    shot["task_id"] = submitted.external_id
    shot["status"] = "processing"
    state["status"] = "rendering"
    save_state(state)
    print(
        f"submitted shot={index} task_id={submitted.external_id} model={submitted.model_id}",
        flush=True,
    )


async def finish_shot(provider: VolcArkSeedanceProvider, state: dict, shot: dict) -> None:
    if shot["status"] == "completed" and Path(shot["output"]).is_file():
        return
    submitted = VideoJob(
        external_id=shot["task_id"],
        status="processing",
        provider=provider.name,
        model_id=MODEL_ID,
    )
    job = await provider.wait_for_completion(submitted)
    raw = await provider._request(
        "GET", f"/contents/generations/tasks/{submitted.external_id}"
    )
    if not job.output_url:
        raise RuntimeError(f"镜头 {shot['index']} 完成但没有视频地址。")
    Path(shot["output"]).write_bytes(await download_bytes(job.output_url))
    if job.last_frame_url:
        Path(shot["last_frame"]).write_bytes(await download_bytes(job.last_frame_url))
    shot["usage"] = raw.get("usage") or {}
    shot["status"] = "completed"
    state["actual_tokens"] = sum(
        int(item.get("usage", {}).get("total_tokens") or 0) for item in state["shots"]
    )
    save_state(state)
    print(
        f"completed shot={shot['index']} tokens={shot['usage'].get('total_tokens', 0)} "
        f"output={shot['output']}",
        flush=True,
    )


async def generate(state: dict) -> None:
    load_dotenv(ROOT / "backend" / ".env", override=True)
    os.environ["ARK_VIDEO_MODEL"] = MODEL_ID
    provider = VolcArkSeedanceProvider()

    # Submit and finish the first shot before starting the remaining paid tasks.
    # This validates account entitlement without risking a five-task failure burst.
    first = state["shots"][0]
    await submit_shot(provider, state, first)
    await finish_shot(provider, state, first)

    remaining = state["shots"][1:]
    for shot in remaining:
        await submit_shot(provider, state, shot)
    await asyncio.gather(*(finish_shot(provider, state, shot) for shot in remaining))
    state["status"] = "ready_to_compose"
    save_state(state)


async def compose(state: dict) -> None:
    if not all(item["status"] == "completed" for item in state["shots"]):
        raise RuntimeError("五个 Seedance 2.0 镜头尚未全部完成。")
    timeline_data = json.loads(
        (PROJECT_ARTIFACTS / "audio-timeline.json").read_text(encoding="utf-8")
    )
    AudioTimeline.model_validate(timeline_data)
    final = await compose_campaign(
        clip_paths=[Path(item["output"]) for item in state["shots"]],
        clip_durations=[6.0] * 5,
        aspect_ratio="9:16",
        narration_path=PROJECT_ARTIFACTS / "narration.wav",
        subtitle_path=PROJECT_ARTIFACTS / "subtitles.srt",
        output_path=OUTPUT / "campaign-seedance2-master.mp4",
        cta_background=None,
        cta_headline="把今天想说的话，慢慢说出来。",
        product_name="语小贝 AI 英语口语学习机 AI-012",
        cta_overlay_duration=2.8,
    )
    state["status"] = "completed"
    state["output"] = str(final)
    save_state(state)
    print(f"composed output={final}", flush=True)


async def main_async(action: str) -> None:
    state = load_state()
    save_state(state)
    try:
        if action in {"all", "generate"}:
            await generate(state)
        if action in {"all", "compose"}:
            await compose(state)
    except Exception as exc:
        state["status"] = "blocked"
        state["blocking_error"] = str(exc)
        state["blocked_at"] = datetime.now().isoformat()
        save_state(state)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a recoverable Seedance 2.0 comparison campaign."
    )
    parser.add_argument("action", choices=["all", "generate", "compose"], default="all", nargs="?")
    args = parser.parse_args()
    asyncio.run(main_async(args.action))


if __name__ == "__main__":
    main()
