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
BACKEND = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.image_providers import VolcArkSeedreamProvider
from backend.app.providers import VolcArkSeedanceProvider


DEFAULT_REFERENCES = [
    ROOT / "example" / "toy.jpg",
    ROOT / "example" / "people.jpg",
    ROOT / "example" / "scene.png",
]
DEFAULT_OUTPUT = BACKEND / "data" / "debug" / "story-experiment"

STORYBOARD_PROMPT = """
基于三张参考图生成严格连续的三张16:9真人实拍电影关键帧，必须正好输出3张组图，按故事时间顺序排列。

参考图1只用于锁定商品：一只白色蓬松小羊手偶，浅米色圆脸、黑色圆眼、小粉鼻、棕色短角、白色带细小银点的布质腹部。忽略参考图1中的手、红色衣袖和原背景，三张图中手偶的五官、角、绒毛、比例和腹部布料必须完全一致。
参考图2只用于锁定唯一主角：同一位亚洲少年，短黑发、自然五官，穿同一件白色拉链运动外套。忽略证件照红色背景。三张图中年龄、脸部、发型、服装完全一致。
参考图3用于锁定场景：同一个明亮安静的玻璃长廊，左侧弧形落地窗、白色墙面、浅蓝灰色地面、日间自然光从左侧进入。三张图中建筑布局、光线方向和色温一致。

这是一个克制、真实、没有台词的10秒小故事：少年准备带着小羊手偶去探望重要的人，原本略微紧张，通过试着让手偶点头和挥手，神情慢慢放松。

第1张：中远景，少年站在玻璃长廊靠窗处，身体略微迟疑，低头看向手中的白色小羊手偶；手偶位于胸口以下，少年表情平静略紧张，尚未微笑。
第2张：同一位置的中景，少年已经把小羊手偶稳稳戴在右手上，手偶在胸口高度微微抬头看向少年；少年与手偶对视，嘴角出现很轻的自然笑意，右手和手偶结构合理。
第3张：同一长廊稍靠近走廊门口的中景，少年听见画外动静后转头望向右前方，右手的小羊手偶抬起一只绒毛手臂做克制的挥手动作；少年露出温和但不夸张的笑容，像准备走向画外的人。

摄影要求：真实商业短片、自然皮肤纹理、35mm镜头、轻微景深、同一摄影机高度、克制表演、物理合理的手部与手偶互动。不要海报感，不要悬浮商品，不要新增人物，不要改变服装，不要文字，不要Logo，不要水印，不要分镜边框或编号。
""".strip()

REPAIR_THIRD_PROMPT = """
生成一张16:9真人实拍电影关键帧。参考图1是必须严格保持的商品：白色蓬松小羊手偶，浅米色圆脸、两只黑色圆眼、小粉鼻、两只棕色短角、白色绒毛手臂、白色带细小银点的布质腹部。绝对不能把它变成手套、手掌、动物帽、普通毛绒玩具或其他造型。
参考图2锁定同一位亚洲少年及其白色拉链运动外套；参考图3锁定同一玻璃长廊；参考图4是上一镜头边界帧，必须延续其中的人物身份、摄影机高度、服装、场景、日间左侧窗光和色温。

故事状态：少年听见右前方画外有人走近，温和地转头看向右前方。他的右手仍然戴着与参考图1完全相同的小羊手偶，手偶保持完整圆脸、双眼、粉鼻、双角和腹部布料，只让一只短小绒毛手臂轻轻抬起做克制的挥手动作。少年轻轻微笑但不看镜头。中景，35mm镜头，自然皮肤和手部结构，真实商业短片，不新增人物，不改变商品，不要文字、Logo、水印或海报构图。
""".strip()

VIDEO_PROMPTS = [
    """
真人实拍叙事广告，一个连续镜头。严格从首帧开始：同一位穿白色拉链运动外套的亚洲少年站在同一玻璃长廊，低头看着右手上的同一只白色小羊手偶，神情略微紧张。少年缓慢抬起右手，把小羊手偶举到胸口高度；手偶轻轻点头一次，少年与手偶对视，呼吸放松，嘴角出现克制自然的轻微笑意。摄影机从中远景极缓慢推近到中景，保持左侧窗光、建筑布局、人物脸部、服装和小羊手偶的圆脸、黑眼、粉鼻、棕色短角、白色绒毛与银点腹部完全一致。手部和手偶互动符合物理规律，不新增人物，不生成文字、Logo或水印。严格自然到达尾帧构图。 --ratio 16:9 --dur 5
    """.strip(),
    """
真人实拍叙事广告，一个连续镜头。严格从首帧开始：同一位少年在同一玻璃长廊与右手上的同一只白色小羊手偶对视并轻微微笑。右前方画外传来脚步，少年先用眼神注意到，再自然转头看向右前方，身体向走廊方向轻轻转动；摄影机跟随人物向前并缓慢推近。小羊手偶始终保持圆脸、双黑眼、粉鼻、双棕角、白色绒毛手臂和银点腹部，只抬起一只短小绒毛手臂轻轻挥动一次。少年最后露出温和但不夸张的笑容，像准备走向重要的人。人物身份、发型、白色外套、场景结构、左侧窗光、商品比例与材质全程一致，手部合理，不新增人物，不生成文字、Logo或水印。严格自然到达尾帧构图。 --ratio 16:9 --dur 5
    """.strip(),
]


def write_state(path: Path, state: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


async def download(url: str, target: Path) -> None:
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
    target.write_bytes(response.content)


async def generate_keyframes(output_dir: Path, state_path: Path) -> None:
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
    else:
        state = {}

    existing = state.get("keyframes")
    if isinstance(existing, list) and len(existing) == 3:
        print("keyframe URLs already exist in state; no new image request will be created")
        urls = [str(item["url"]) for item in existing]
    else:
        provider = VolcArkSeedreamProvider()
        print(f"submitting one Seedream group request model={provider.model} images=3")
        result = await provider.generate(
            prompt=STORYBOARD_PROMPT,
            reference_images=[str(path) for path in DEFAULT_REFERENCES],
            aspect_ratio="16:9",
            max_images=3,
        )
        if len(result.output_urls) != 3:
            raise RuntimeError(f"Seedream returned {len(result.output_urls)} images; expected exactly 3")
        urls = result.output_urls
        state.update({
            "created_at": datetime.now().isoformat(),
            "story": "少年用小羊手偶为一次重要探望练习问候",
            "keyframe_model": result.model_id,
            "estimated_keyframe_cost_cny": result.estimated_cost,
            "references": [str(path) for path in DEFAULT_REFERENCES],
            "keyframes": [
                {"index": index, "url": url, "local_path": str(output_dir / f"kf-{index:02d}.jpg")}
                for index, url in enumerate(urls, start=1)
            ],
            "video_tasks": [],
        })
        write_state(state_path, state)
        print("keyframe state saved before downloads")

    output_dir.mkdir(parents=True, exist_ok=True)
    for index, url in enumerate(urls, start=1):
        target = output_dir / f"kf-{index:02d}.jpg"
        if target.exists() and target.stat().st_size > 0:
            continue
        await download(url, target)
        print(f"downloaded {target} bytes={target.stat().st_size}")


async def repair_third_keyframe(output_dir: Path, state_path: Path) -> None:
    if not state_path.exists():
        raise RuntimeError("Run the keyframes stage first.")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    repair = state.get("third_keyframe_repair")
    if isinstance(repair, dict) and repair.get("url"):
        print("third-frame repair already exists; no new image request will be created")
        url = str(repair["url"])
    else:
        previous_frame = output_dir / "kf-02.jpg"
        if not previous_frame.is_file():
            raise RuntimeError("The second keyframe is missing.")
        provider = VolcArkSeedreamProvider()
        print(f"submitting one Seedream repair image model={provider.model}")
        result = await provider.generate(
            prompt=REPAIR_THIRD_PROMPT,
            reference_images=[
                str(DEFAULT_REFERENCES[0]),
                str(DEFAULT_REFERENCES[1]),
                str(DEFAULT_REFERENCES[2]),
                str(previous_frame),
            ],
            aspect_ratio="16:9",
            max_images=1,
        )
        url = result.output_urls[0]
        state["third_keyframe_repair"] = {
            "url": url,
            "local_path": str(output_dir / "kf-03-repair-01.jpg"),
            "model": result.model_id,
            "estimated_cost_cny": result.estimated_cost,
            "created_at": datetime.now().isoformat(),
        }
        write_state(state_path, state)
        print("repair state saved before download")
    target = output_dir / "kf-03-repair-01.jpg"
    if not target.exists() or target.stat().st_size == 0:
        await download(url, target)
        print(f"downloaded {target} bytes={target.stat().st_size}")


async def submit_videos(output_dir: Path, state_path: Path) -> None:
    if not state_path.exists():
        raise RuntimeError("Run the keyframe stages first.")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    selected = [
        output_dir / "kf-01.jpg",
        output_dir / "kf-02.jpg",
        output_dir / "kf-03-repair-01.jpg",
    ]
    if not all(path.is_file() for path in selected):
        raise RuntimeError("Selected keyframes are incomplete.")
    tasks = state.setdefault("video_tasks", [])
    provider = VolcArkSeedanceProvider()
    for index in range(2):
        if len(tasks) > index and tasks[index].get("task_id"):
            print(f"shot {index + 1} already submitted as {tasks[index]['task_id']}")
            continue
        print(f"submitting Seedance shot {index + 1}/2")
        job = await provider.create_boundary_task(
            prompt=VIDEO_PROMPTS[index],
            duration=5,
            first_frame=str(selected[index]),
            last_frame=str(selected[index + 1]),
            aspect_ratio="16:9",
            model_hint="story",
        )
        task = {
            "shot": index + 1,
            "task_id": job.external_id,
            "model_endpoint": job.model_id,
            "status": "processing",
            "created_at": datetime.now().isoformat(),
            "first_frame": str(selected[index]),
            "last_frame": str(selected[index + 1]),
        }
        if len(tasks) > index:
            tasks[index] = task
        else:
            tasks.append(task)
        write_state(state_path, state)
        print(f"saved task_id={job.external_id}")


async def poll_videos(output_dir: Path, state_path: Path) -> None:
    if not state_path.exists():
        raise RuntimeError("Experiment state is missing.")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    tasks = state.get("video_tasks") or []
    if len(tasks) != 2:
        raise RuntimeError("Two video tasks have not been submitted.")
    provider = VolcArkSeedanceProvider()
    for task in tasks:
        if task.get("status") == "succeeded" and Path(str(task.get("local_video", ""))).is_file():
            print(f"shot {task['shot']} already downloaded")
            continue
        result = await provider._request(
            "GET",
            f"/contents/generations/tasks/{task['task_id']}",
        )
        task["status"] = str(result.get("status") or "")
        task["model"] = str(result.get("model") or "")
        task["usage"] = result.get("usage") or {}
        task["resolution"] = result.get("resolution")
        task["duration"] = result.get("duration")
        content = result.get("content") or {}
        if task["status"] == "succeeded":
            video_url = str(content.get("video_url") or "")
            last_frame_url = str(content.get("last_frame_url") or "")
            video_target = output_dir / f"shot-{int(task['shot']):02d}.mp4"
            last_target = output_dir / f"shot-{int(task['shot']):02d}-actual-last.jpg"
            if video_url and not video_target.exists():
                await download(video_url, video_target)
            if last_frame_url and not last_target.exists():
                await download(last_frame_url, last_target)
            task["video_url"] = video_url
            task["last_frame_url"] = last_frame_url
            task["local_video"] = str(video_target)
            task["local_actual_last_frame"] = str(last_target)
            print(f"shot {task['shot']} succeeded tokens={(task['usage'] or {}).get('total_tokens')}")
        else:
            print(f"shot {task['shot']} status={task['status']}")
    write_state(state_path, state)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--stage",
        choices=["keyframes", "repair-third", "submit-videos", "poll-videos"],
        default="keyframes",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    load_dotenv(BACKEND / ".env", override=True)
    state_path = args.output_dir / "state.json"
    if args.stage == "keyframes":
        asyncio.run(generate_keyframes(args.output_dir, state_path))
    elif args.stage == "repair-third":
        asyncio.run(repair_third_keyframe(args.output_dir, state_path))
    elif args.stage == "submit-videos":
        asyncio.run(submit_videos(args.output_dir, state_path))
    else:
        asyncio.run(poll_videos(args.output_dir, state_path))


if __name__ == "__main__":
    main()
