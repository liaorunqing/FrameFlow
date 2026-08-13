from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.providers import MiniMaxOfficialVideoProvider
from backend.app.network import download_bytes


DEFAULT_IMAGE = ROOT / "example" / "toy.jpg"
DEFAULT_OUTPUT = ROOT / "backend" / "data" / "debug" / "minimax-single-shot.mp4"
DEFAULT_STATE = ROOT / "backend" / "data" / "debug" / "minimax-task.json"
DEFAULT_PROMPT = (
    "A photorealistic six-second product commercial based strictly on the reference image. "
    "The same white fluffy lamb hand puppet keeps its exact cream face, black bead eyes, tiny pink nose, "
    "brown ears, white fur and silver-white dress. A real hand gently lifts the puppet; it tilts its head "
    "with curiosity and slowly waves one paw. Natural fabric compression and believable hand movement. "
    "Warm indoor light, shallow depth of field, restrained live-action performance, slow camera push-in. "
    "Do not change the product geometry, colors or clothing. No extra limbs, no text, no watermark."
)


async def run(
    image: Path,
    output: Path,
    state_path: Path,
    prompt: str,
    resume_task: str | None,
) -> None:
    load_dotenv(ROOT / "backend" / ".env", override=True)
    provider = MiniMaxOfficialVideoProvider()
    print(
        f"reference={image} model={provider.FAST_MODEL} duration=6 "
        "resolution=768P estimated_cost=CNY_1.35",
        flush=True,
    )
    if resume_task:
        print(f"resuming task_id={resume_task}; no new generation task will be created", flush=True)
        job = await provider.poll(resume_task)
        if job.status != "completed":
            raise RuntimeError(f"任务 {resume_task} 当前状态为 {job.status}。")
    else:
        submitted = await provider.create_task(
            prompt=prompt,
            duration=6,
            reference_images=[str(image)],
            model_hint="story",
            aspect_ratio="1:1",
        )
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(
                {
                    "task_id": submitted.external_id,
                    "model_id": submitted.model_id,
                    "estimated_cost_cny": submitted.estimated_cost,
                    "created_at": datetime.now().isoformat(),
                    "reference_image": str(image),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"submitted task_id={submitted.external_id} state={state_path}", flush=True)
        job = await provider.wait_for_completion(submitted)
    if not job.output_url:
        raise RuntimeError("官方任务完成但没有返回视频地址。")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(await download_bytes(job.output_url))
    print(
        f"completed task_id={job.external_id} model={job.model_id} "
        f"output={output} bytes={output.stat().st_size}",
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one paid MiniMax Hailuo backend smoke test.")
    parser.add_argument("--image", type=Path, default=DEFAULT_IMAGE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--resume-task", help="Resume and download an existing MiniMax task without a new charge")
    args = parser.parse_args()
    image = args.image.resolve()
    if not image.is_file():
        raise SystemExit(f"Reference image not found: {image}")
    asyncio.run(run(image, args.output.resolve(), args.state.resolve(), args.prompt, args.resume_task))


if __name__ == "__main__":
    main()
