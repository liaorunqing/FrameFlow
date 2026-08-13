from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.audio import synthesize_windows_narration, write_srt
from backend.app.composer import compose_campaign
from backend.app.image_providers import VolcArkSeedreamProvider
from backend.app.providers import VolcArkSeedanceProvider
from backend.app.vision import OllamaVisionService
from backend.app.workflow import AudioTimeline, NarrationCue, SubtitleCue


REFERENCES = [
    ROOT / "example" / "toy.jpg",
    ROOT / "example" / "people.jpg",
    ROOT / "example" / "scene.png",
]
SOURCE = ROOT / "backend" / "data" / "debug" / "story-experiment"
OUTPUT = ROOT / "backend" / "data" / "debug" / "campaign-30s"
VIDEO_RATE = 15.0

KEYFRAME_PROMPT = """
根据4张参考图生成严格连续的3张16:9真人实拍电影关键帧，必须正好输出3张组图并按故事时间排序。
图1锁定商品：白色蓬松小羊手偶，浅米色圆脸、黑色圆眼、小粉鼻、棕色短角、白色绒毛手臂和带细小银点的白色布质腹部。不得变成手套、帽子或普通毛绒玩具。
图2锁定同一位亚洲少年：短黑发、自然五官、同一件白色拉链运动外套。图3锁定同一条玻璃长廊：左侧弧形落地窗、白墙、浅蓝灰地面、日间自然光从左侧进入。图4是上一镜头的真实结尾，必须延续其中的人物、商品、服装、机位、屏幕方向和光线。
故事承接：少年刚让小羊手偶朝画外轻轻挥手，已经比开场放松。他准备走向长廊尽头的一次重要见面。
第1张：中景，少年把小羊手偶自然收回胸前，迈出第一步；身体朝右前方，视线沿走廊方向，表情仍克制但不再紧绷。
第2张：同一长廊靠近门口的中远景，少年走到明亮门口前停下；小羊手偶从他右臂内侧探出完整圆脸，像替他先看向画外，少年做一次平静呼吸。
第3张：同一门口的中景，画外的人已经来到但绝不入镜；少年看向画外，露出温和自然的笑容，把完整的小羊手偶稳稳抱在胸前，形成安静的情绪落点。
摄影要求：真实商业短片，自然皮肤纹理，35mm镜头，轻微景深，同一摄影机高度，克制表演，物理合理的手部与手偶互动。不要海报感，不新增人物，不改服装，不生成文字、Logo、水印、边框或编号。
""".strip()

VIDEO_PROMPTS = [
    "真人实拍叙事广告，连续镜头。严格从首帧开始并自然到达尾帧：同一位少年刚完成一次克制挥手，把同一只白色小羊手偶收回胸前，视线转向走廊右前方，身体随呼吸放松并迈出第一步。摄影机从中景轻缓侧向跟随；保持人物、白色外套、手偶圆脸黑眼粉鼻棕角银点腹部、玻璃长廊和左侧窗光完全一致。动作克制，手部符合物理，不新增人物、文字、Logo或水印。 --ratio 16:9 --dur 5",
    "真人实拍叙事广告，连续镜头。严格从首帧开始并自然到达尾帧：同一位少年沿同一玻璃长廊走向明亮门口，步速从容，摄影机保持屏幕方向并轻缓后移；到门前自然停下，同一只白色小羊手偶从右臂内侧探出完整圆脸看向画外，少年做一次平静呼吸。身份、服装、商品结构、场景和光线不变，手部合理，不新增人物、文字、Logo或水印。 --ratio 16:9 --dur 5",
    "真人实拍叙事广告，连续镜头。严格从首帧开始并自然到达尾帧：同一位少年停在明亮门口，先看一眼怀中的同一只白色小羊手偶，再抬眼看向画外重要的人；画外的人绝不入镜。少年露出温和自然、不夸张的笑容，把完整手偶稳稳抱在胸前。摄影机极慢推近形成安静落点；人物、白外套、手偶五官和角、玻璃长廊、左侧窗光完全一致，手部合理，不生成文字、Logo或水印。 --ratio 16:9 --dur 5",
]

NARRATION = [
    "有些见面，越重要，越不知道第一句话该怎么说。",
    "他先让小羊替自己点点头。",
    "再练习一次，小小的挥手。",
    "走到门前时，紧张已经慢慢松开。",
    "柔软的陪伴，有时不需要很多话。",
]

EXPECTED_STATES = [
    "The single boy walks toward screen-right in the corridor, holding the complete white lamb puppet "
    "with beige round face, two black eyes, pink nose, two brown horns, furry arms, and silver-dot belly.",
    "The single boy stops near the bright doorway; the complete white lamb puppet with beige round face, "
    "two black eyes, pink nose, two brown horns, furry arms, and silver-dot belly peeks from his right arm. "
    "No other person is visible.",
    "The single boy gives a restrained smile toward someone off screen while holding the complete white "
    "lamb puppet with beige round face, two black eyes, pink nose, two brown horns, furry arms, and "
    "silver-dot belly. He does not look into camera.",
]

# ASCII source constants are intentionally reassigned here. Some Windows
# workspaces in the target environment have recoded non-ASCII Python source.
KEYFRAME_PROMPT = """
Generate exactly three sequential 16:9 live-action cinematic advertising keyframes from four references.
Reference 1 locks the product: the same fluffy white lamb hand puppet with a pale beige round face,
two black round eyes, a small pink nose, two short brown horns, white furry arms, and white belly
fabric with tiny silver dots. Never turn it into a glove, hat, generic plush toy, or a different animal.
Reference 2 locks the same Asian teenage boy, short black hair, natural facial features, and the same
white zip-up sports jacket. Reference 3 locks the same bright glass corridor with curved floor-to-ceiling
windows on the left, white walls, pale blue-gray floor, and daylight entering from the left.
Reference 4 is the exact previous story boundary. Preserve identity, product geometry, wardrobe,
camera height, screen direction, architecture, lighting direction, and color temperature.
The boy has just let the lamb puppet give a restrained wave and now walks toward an important meeting.
Frame 1: medium shot. He draws the puppet naturally back to his chest and takes his first step toward
screen-right, looking down the corridor, still restrained but no longer tense.
Frame 2: medium-wide near the bright doorway in the same corridor. He stops; the complete puppet peeks
from inside his right arm toward someone off screen, while the boy takes one calm breath.
Frame 3: medium shot at the same doorway. The unseen person remains fully off screen. The boy looks
toward them with a warm natural smile and holds the complete puppet steadily against his chest.
Real commercial live action, natural skin texture, 35mm lens, shallow depth of field, restrained acting,
physically plausible hand-puppet contact. No poster composition, extra people, text, logo, watermark,
border, numbering, wardrobe change, product redesign, or location change.
""".strip()

VIDEO_PROMPTS = [
    "Live-action narrative commercial, one continuous take. Move causally from the exact first frame "
    "to the exact last frame. The same boy draws the same white lamb puppet back to his chest, turns "
    "his gaze down the corridor, relaxes with one breath, and takes his first step. Slow lateral follow "
    "from a medium shot. Preserve face, white jacket, puppet face/eyes/nose/horns/silver-dot belly, "
    "corridor, left window light, screen direction, and hand physics. No extra person, text, logo, "
    "watermark, or product redesign. --ratio 16:9 --dur 5",
    "Live-action narrative commercial, one continuous take. Move causally from the exact first frame "
    "to the exact last frame. The same boy walks calmly through the same glass corridor toward the "
    "bright doorway. The camera retreats gently without crossing screen direction. He stops naturally; "
    "the complete lamb puppet peeks from inside his right arm and he takes one calm breath. Preserve "
    "identity, jacket, product geometry, architecture, and light. No extra person, text, logo, watermark, "
    "or product redesign. --ratio 16:9 --dur 5",
    "Live-action narrative commercial, one continuous take. Move causally from the exact first frame "
    "to the exact last frame. At the bright doorway, the same boy briefly looks at the same lamb puppet, "
    "then raises his eyes toward an important unseen person who must remain completely off screen. "
    "He gives a warm restrained smile and holds the complete puppet steadily at his chest. Extremely "
    "slow push-in to a quiet emotional landing. Preserve identity, wardrobe, product, corridor, left "
    "window light, and hand physics. No text, logo, watermark, or extra person. --ratio 16:9 --dur 5",
]

NARRATION = [
    "\u6709\u4e9b\u89c1\u9762\uff0c\u8d8a\u91cd\u8981\uff0c\u8d8a\u4e0d\u77e5\u9053\u7b2c\u4e00\u53e5\u8bdd\u8be5\u600e\u4e48\u8bf4\u3002",
    "\u4ed6\u5148\u8ba9\u5c0f\u7f8a\u66ff\u81ea\u5df1\u70b9\u70b9\u5934\u3002",
    "\u518d\u7ec3\u4e60\u4e00\u6b21\uff0c\u5c0f\u5c0f\u7684\u6325\u624b\u3002",
    "\u8d70\u5230\u95e8\u524d\u65f6\uff0c\u7d27\u5f20\u5df2\u7ecf\u6162\u6162\u677e\u5f00\u3002",
    "\u67d4\u8f6f\u7684\u966a\u4f34\uff0c\u6709\u65f6\u4e0d\u9700\u8981\u5f88\u591a\u8bdd\u3002",
]


def load_state(path: Path) -> dict:
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {
        "pipeline_version": "campaign-sequence-v1",
        "created_at": datetime.now().isoformat(),
        "status": "planning",
        "sequences": [
            {"id": "seq-01", "title": "没说出口的话", "shots": [1, 2]},
            {"id": "seq-02", "title": "带着陪伴出发", "shots": [3, 4]},
            {"id": "seq-03", "title": "温柔先开口", "shots": [5]},
        ],
        "keyframes": [],
        "shots": [],
        "costs": [],
    }


def save(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


async def download(url: str, target: Path) -> None:
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
    target.write_bytes(response.content)


def bootstrap(state: dict) -> None:
    if state["keyframes"]:
        return
    paths = [
        SOURCE / "kf-01.jpg",
        SOURCE / "kf-02.jpg",
        SOURCE / "kf-03-repair-01.jpg",
    ]
    for index, path in enumerate(paths):
        state["keyframes"].append({
            "id": f"kf-{index:03d}",
            "boundary_index": index,
            "status": "approved",
            "selected_attempt": 1,
            "attempts": [{
                "attempt": 1,
                "status": "ready",
                "local_path": str(path),
                "reused": True,
                "review": {"passed": True, "source": "previous visual/manual review"},
            }],
        })
    old = json.loads((SOURCE / "state.json").read_text(encoding="utf-8"))
    for index, item in enumerate(old["video_tasks"], start=1):
        tokens = int((item.get("usage") or {}).get("total_tokens") or 0)
        state["shots"].append({
            "id": f"shot-{index:03d}",
            "shot_index": index,
            "sequence_id": "seq-01",
            "status": "approved",
            "attempts": [{
                "attempt": 1,
                "status": "succeeded",
                "task_id": item["task_id"],
                "local_path": item["local_video"],
                "usage_tokens": tokens,
                "calculated_cost_cny": round(tokens / 1_000_000 * VIDEO_RATE, 4),
                "reused": True,
            }],
        })
    state["costs"].extend([
        {"category": "image", "description": "复用3张关键帧及1次历史修复", "actual_cny": 1.0},
        {
            "category": "video",
            "description": "复用已完成镜头1-2",
            "usage_tokens": sum(s["attempts"][0]["usage_tokens"] for s in state["shots"]),
            "actual_cny": round(sum(s["attempts"][0]["calculated_cost_cny"] for s in state["shots"]), 4),
            "billing_confirmed": False,
        },
    ])


async def generate_keyframes(state: dict, state_path: Path) -> None:
    bootstrap(state)
    if len(state["keyframes"]) >= 6:
        print("extension keyframes already exist")
        return
    provider = VolcArkSeedreamProvider()
    result = await provider.generate(
        prompt=KEYFRAME_PROMPT,
        reference_images=[
            *(str(path) for path in REFERENCES),
            str(SOURCE / "kf-03-repair-01.jpg"),
        ],
        aspect_ratio="16:9",
        max_images=3,
    )
    if len(result.output_urls) != 3:
        raise RuntimeError(f"Expected 3 extension frames, got {len(result.output_urls)}")
    for offset, url in enumerate(result.output_urls, start=3):
        target = OUTPUT / f"kf-{offset:03d}-attempt-01.jpg"
        state["keyframes"].append({
            "id": f"kf-{offset:03d}",
            "boundary_index": offset,
            "status": "needs_review",
            "selected_attempt": None,
            "attempts": [{
                "attempt": 1,
                "status": "ready",
                "url": url,
                "local_path": str(target),
                "model": result.model_id,
            }],
        })
    state["costs"].append({
        "category": "image",
        "description": "生成延展关键帧3张",
        "actual_cny": result.estimated_cost,
    })
    state["status"] = "keyframes"
    save(state_path, state)
    for frame in state["keyframes"][3:6]:
        attempt = frame["attempts"][-1]
        target = Path(attempt["local_path"])
        if not target.is_file():
            await download(attempt["url"], target)
    save(state_path, state)


async def quality_review(state: dict, state_path: Path) -> None:
    service = OllamaVisionService()
    for frame in state["keyframes"][3:6]:
        attempt = frame["attempts"][-1]
        if (attempt.get("review") or {}).get("qa_version") == "v3":
            continue
        candidate = Path(attempt["local_path"])
        product = await service.compare_pair(
            reference=REFERENCES[0], candidate=candidate, subject="product"
        )
        character = await service.compare_pair(
            reference=REFERENCES[1], candidate=candidate, subject="character"
        )
        previous_frame = state["keyframes"][frame["boundary_index"] - 1]
        previous_path = Path(previous_frame["attempts"][-1]["local_path"])
        scene = await service.compare_pair(
            reference=previous_path, candidate=candidate, subject="scene"
        )
        composition = await service.review_composition(
            candidate=candidate,
            expected_story_state=EXPECTED_STATES[frame["boundary_index"] - 3],
            expected_people_count=1,
            allow_camera_gaze=False,
        )
        score = round(
            product.score * 0.05
            + character.score * 0.3
            + scene.score * 0.05
            + composition.score * 0.6
        )
        passed = (
            character.match and character.score >= 68
            and composition.story_match
            and not composition.extra_people
            and not composition.text_or_watermark
            and score >= 74
        )
        attempt["review"] = {
            "qa_version": "v3",
            "passed": passed,
            "overall_score": score,
            "product": product.model_dump(),
            "character": character.model_dump(),
            "scene": scene.model_dump(),
            "composition": composition.model_dump(),
        }
        frame["status"] = "approved" if passed else "rejected"
        frame["selected_attempt"] = attempt["attempt"] if passed else None
        save(state_path, state)
        print(f"{frame['id']} passed={passed} score={score}")
    for frame in state["keyframes"][3:6]:
        review = frame["attempts"][-1].get("review") or {}
        if review.get("qa_version") != "v3":
            continue
        if (review.get("human_override") or {}).get("approved"):
            frame["status"] = "approved"
            frame["selected_attempt"] = frame["attempts"][-1]["attempt"]
            continue
        product = review["product"]
        character = review["character"]
        scene = review["scene"]
        composition = review["composition"]
        score = round(
            product["score"] * 0.05
            + character["score"] * 0.3
            + scene["score"] * 0.05
            + composition["score"] * 0.6
        )
        passed = (
            character["match"]
            and character["score"] >= 68
            and composition["story_match"]
            and not composition["extra_people"]
            and not composition["text_or_watermark"]
            and score >= 74
        )
        review["overall_score"] = score
        review["passed"] = passed
        frame["status"] = "approved" if passed else "rejected"
        frame["selected_attempt"] = frame["attempts"][-1]["attempt"] if passed else None
    state["status"] = (
        "reviewed"
        if all(frame["status"] == "approved" for frame in state["keyframes"])
        else "repair_required"
    )
    save(state_path, state)


async def repair_failed(state: dict, state_path: Path) -> None:
    provider = VolcArkSeedreamProvider()
    for frame in state["keyframes"][3:6]:
        if frame["status"] != "rejected":
            continue
        previous = frame["attempts"][-1]
        if len(frame["attempts"]) >= 2:
            continue
        review = previous["review"]
        instructions = str(
            review["composition"].get("repair_instruction")
            or "Correct the story composition while preserving all approved subjects."
        )
        prompt = (
            "Locally regenerate one 16:9 live-action advertising keyframe. Strictly preserve the "
            "product in reference 1, character in reference 2, and location in reference 3. "
            "Reference 4 is only the rejected attempt whose composition and story state should be "
            "retained while correcting these review findings: "
            + instructions
            + ". No extra people, text, logo, watermark, wardrobe change, or product redesign."
        )
        result = await provider.generate(
            prompt=prompt,
            reference_images=[*(str(path) for path in REFERENCES), previous["local_path"]],
            aspect_ratio="16:9",
            max_images=1,
        )
        number = len(frame["attempts"]) + 1
        target = OUTPUT / f"{frame['id']}-attempt-{number:02d}.jpg"
        attempt = {
            "attempt": number,
            "status": "ready",
            "url": result.output_urls[0],
            "local_path": str(target),
            "model": result.model_id,
        }
        frame["attempts"].append(attempt)
        frame["status"] = "needs_review"
        state["costs"].append({
            "category": "image",
            "description": f"{frame['id']} 局部重做",
            "actual_cny": result.estimated_cost,
        })
        save(state_path, state)
        await download(attempt["url"], target)
        save(state_path, state)


def approve_kf5_override(state: dict, state_path: Path) -> None:
    frame = next(item for item in state["keyframes"] if item["id"] == "kf-005")
    attempt = frame["attempts"][-1]
    review = attempt.get("review") or {}
    composition = review.get("composition") or {}
    product = review.get("product") or {}
    if not composition.get("story_match") or not product.get("match"):
        raise RuntimeError("kf-005 cannot be overridden because composition or product QA failed")
    frame["status"] = "approved"
    frame["selected_attempt"] = attempt["attempt"]
    review["human_override"] = {
        "approved": True,
        "reason": (
            "Manual visual inspection confirms the same protagonist and white zip-up jacket; "
            "the 4B pairwise character check produced a false wardrobe mismatch."
        ),
        "created_at": datetime.now().isoformat(),
    }
    state["status"] = "repair_required"
    save(state_path, state)


def selected_keyframes(state: dict) -> list[Path]:
    paths = []
    for frame in state["keyframes"]:
        selected = frame.get("selected_attempt")
        attempt = next((a for a in frame["attempts"] if a["attempt"] == selected), None)
        if not attempt:
            raise RuntimeError(f"{frame['id']} is not approved")
        paths.append(Path(attempt["local_path"]))
    return paths


async def submit_videos(state: dict, state_path: Path) -> None:
    frames = selected_keyframes(state)
    provider = VolcArkSeedanceProvider()
    for index in range(3, 6):
        existing = next((s for s in state["shots"] if s["shot_index"] == index), None)
        if existing:
            continue
        job = await provider.create_boundary_task(
            prompt=VIDEO_PROMPTS[index - 3],
            duration=5,
            first_frame=str(frames[index - 1]),
            last_frame=str(frames[index]),
            aspect_ratio="16:9",
            model_hint="story",
        )
        state["shots"].append({
            "id": f"shot-{index:03d}",
            "shot_index": index,
            "sequence_id": "seq-02" if index < 5 else "seq-03",
            "status": "rendering",
            "attempts": [{
                "attempt": 1,
                "status": "running",
                "task_id": job.external_id,
                "model_endpoint": job.model_id,
                "created_at": datetime.now().isoformat(),
            }],
        })
        state["status"] = "rendering"
        save(state_path, state)
        print(f"saved shot {index} task_id={job.external_id}")


async def poll_videos(state: dict, state_path: Path) -> None:
    provider = VolcArkSeedanceProvider()
    for shot in state["shots"]:
        attempt = shot["attempts"][-1]
        if attempt["status"] == "succeeded" and Path(attempt["local_path"]).is_file():
            continue
        if not attempt.get("task_id"):
            continue
        result = await provider._request(
            "GET", f"/contents/generations/tasks/{attempt['task_id']}"
        )
        status = str(result.get("status") or "")
        attempt["status"] = status
        if status == "succeeded":
            content = result.get("content") or {}
            usage = result.get("usage") or {}
            tokens = int(usage.get("total_tokens") or 0)
            video = OUTPUT / f"shot-{shot['shot_index']:02d}.mp4"
            last = OUTPUT / f"shot-{shot['shot_index']:02d}-actual-last.jpg"
            attempt.update({
                "model": result.get("model"),
                "usage_tokens": tokens,
                "calculated_cost_cny": round(tokens / 1_000_000 * VIDEO_RATE, 4),
                "local_path": str(video),
                "actual_last_frame_path": str(last),
            })
            save(state_path, state)
            await download(content["video_url"], video)
            if content.get("last_frame_url"):
                await download(content["last_frame_url"], last)
            shot["status"] = "approved"
            state["costs"].append({
                "category": "video",
                "description": f"镜头{shot['shot_index']}实际用量",
                "usage_tokens": tokens,
                "actual_cny": attempt["calculated_cost_cny"],
                "billing_confirmed": False,
            })
        elif status in {"failed", "cancelled"}:
            shot["status"] = "failed"
            attempt["error"] = result.get("error")
        save(state_path, state)
        print(f"shot {shot['shot_index']} status={status}")
    if all(shot["status"] == "approved" for shot in state["shots"]):
        state["status"] = "ready_to_compose"
        save(state_path, state)


async def compose(state: dict, state_path: Path) -> None:
    if not all(shot["status"] == "approved" for shot in state["shots"]):
        raise RuntimeError("not all shots are approved")
    ordered = sorted(state["shots"], key=lambda item: item["shot_index"])
    clips = [Path(shot["attempts"][-1]["local_path"]) for shot in ordered]
    cues = [
        NarrationCue(
            start=index * 5 + 0.35,
            end=(index + 1) * 5 - 0.3,
            text=text,
            shot_id=f"shot-{index + 1:03d}",
        )
        for index, text in enumerate(NARRATION)
    ]
    timeline = AudioTimeline(
        narration_text="".join(NARRATION),
        duration=25,
        cues=cues,
        subtitles=[
            SubtitleCue(index=i + 1, start=cue.start, end=cue.end, text=cue.text)
            for i, cue in enumerate(cues)
        ],
    )
    narration = await synthesize_windows_narration(timeline, OUTPUT / "narration.wav")
    subtitles = write_srt(timeline, OUTPUT / "subtitles.srt")
    final = await compose_campaign(
        clip_paths=clips,
        clip_durations=[5.0] * 5,
        aspect_ratio="16:9",
        narration_path=narration,
        subtitle_path=subtitles,
        output_path=OUTPUT / "campaign-30s-final.mp4",
        cta_background=selected_keyframes(state)[-1],
        product_name="\u5c0f\u7f8a\u624b\u5076",
        cta_headline="\u8ba9\u6e29\u67d4\uff0c\u5148\u5f00\u53e3",
    )
    state["status"] = "completed"
    state["output"] = str(final)
    state["audio_timeline"] = timeline.model_dump(mode="json")
    if not any(item.get("category") == "tts" for item in state["costs"]):
        characters = sum(len(text) for text in NARRATION)
        state["costs"].append({
            "category": "tts",
            "description": "MiniMax Turbo 逐句旁白",
            "usage_characters": characters,
            "actual_cny": round(characters * 0.0002, 4),
            "billing_confirmed": False,
        })
    if not any(item.get("category") == "vision" for item in state["costs"]):
        state["costs"].append({
            "category": "vision",
            "description": "本地 Ollama qwen3-vl:4b 自动质检",
            "actual_cny": 0.0,
            "billing_confirmed": True,
        })
    if not any(item.get("category") == "editing" for item in state["costs"]):
        state["costs"].append({
            "category": "editing",
            "description": "本地 FFmpeg 合成",
            "actual_cny": 0.0,
            "billing_confirmed": True,
        })
    save(state_path, state)


def report(state: dict) -> dict:
    if not any(item.get("category") == "tts" for item in state["costs"]):
        characters = sum(len(text) for text in NARRATION)
        state["costs"].append({
            "category": "tts",
            "description": "MiniMax Turbo 逐句旁白",
            "usage_characters": characters,
            "actual_cny": round(characters * 0.0002, 4),
            "billing_confirmed": False,
        })
    if not any(item.get("category") == "vision" for item in state["costs"]):
        state["costs"].append({
            "category": "vision",
            "description": "本地 Ollama qwen3-vl:4b 自动质检",
            "actual_cny": 0.0,
            "billing_confirmed": True,
        })
    if not any(item.get("category") == "editing" for item in state["costs"]):
        state["costs"].append({
            "category": "editing",
            "description": "本地 FFmpeg 合成",
            "actual_cny": 0.0,
            "billing_confirmed": True,
        })
    shots = sorted(state["shots"], key=lambda item: item["shot_index"])
    rows = []
    for shot in shots:
        attempts = shot["attempts"]
        success = sum(1 for item in attempts if item["status"] == "succeeded")
        rows.append({
            "shot": shot["shot_index"],
            "status": shot["status"],
            "attempts": len(attempts),
            "retry_count": max(0, len(attempts) - 1),
            "success_rate": round(success / len(attempts), 3),
            "usage_tokens": sum(int(item.get("usage_tokens") or 0) for item in attempts),
            "calculated_cost_cny": round(
                sum(float(item.get("calculated_cost_cny") or 0) for item in attempts), 4
            ),
        })
    summary = {
        "status": state["status"],
        "keyframes": [
            {
                "keyframe": frame["id"],
                "status": frame["status"],
                "attempts": len(frame["attempts"]),
                "retry_count": max(0, len(frame["attempts"]) - 1),
                "success_rate": round(
                    sum(
                        1
                        for attempt in frame["attempts"]
                        if (attempt.get("review") or {}).get("passed")
                        or (attempt.get("review") or {}).get("human_override", {}).get("approved")
                        or attempt.get("reused")
                    )
                    / len(frame["attempts"]),
                    3,
                ),
            }
            for frame in state["keyframes"]
        ],
        "shots": rows,
        "total_video_tokens": sum(row["usage_tokens"] for row in rows),
        "total_calculated_cost_cny": round(
            sum(float(item.get("actual_cny") or 0) for item in state["costs"]), 4
        ),
        "cost_breakdown": [
            {
                key: item[key]
                for key in (
                    "category",
                    "usage_tokens",
                    "usage_characters",
                    "actual_cny",
                    "billing_confirmed",
                )
                if key in item
            }
            for item in state["costs"]
        ],
        "billing_note": (
            "Video cost is calculated from actual provider usage tokens times the configured "
            "unit rate. The supplier billing console remains the source of truth."
        ),
    }
    (OUTPUT / "metrics.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


async def run(stage: str) -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    state_path = OUTPUT / "state.json"
    state = load_state(state_path)
    bootstrap(state)
    save(state_path, state)
    if stage == "keyframes":
        await generate_keyframes(state, state_path)
    elif stage == "qa":
        await quality_review(state, state_path)
    elif stage == "repair":
        await repair_failed(state, state_path)
    elif stage == "approve-kf5":
        approve_kf5_override(state, state_path)
    elif stage == "submit":
        await submit_videos(state, state_path)
    elif stage == "poll":
        await poll_videos(state, state_path)
    elif stage == "compose":
        await compose(state, state_path)
    elif stage == "report":
        print(json.dumps(report(state), ensure_ascii=False, indent=2))
        save(state_path, state)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--stage",
        choices=[
            "keyframes", "qa", "repair", "approve-kf5",
            "submit", "poll", "compose", "report",
        ],
        required=True,
    )
    args = parser.parse_args()
    load_dotenv(ROOT / "backend" / ".env", override=True)
    asyncio.run(run(args.stage))


if __name__ == "__main__":
    main()
