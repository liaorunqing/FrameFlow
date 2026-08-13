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

from backend.app.network import download_bytes
from backend.app.providers import VideoJob, VolcArkSeedanceProvider


DEFAULT_REFERENCES = [
    ROOT / "example" / "toy.jpg",
    ROOT / "example" / "people.jpg",
    ROOT / "example" / "scene.png",
]
DEFAULT_OUTPUT = ROOT / "backend" / "data" / "debug" / "seedance-single-shot.mp4"
DEFAULT_STATE = ROOT / "backend" / "data" / "debug" / "seedance-task.json"
DEFAULT_PROMPT = (
    "真实电影广告镜头。严格参考图1中的白色绵羊手偶商品。"
    "一只真实的手自然托起同一个绵羊手偶，手偶轻轻转头并挥动前爪，"
    "毛绒材质产生自然形变。镜头从中景缓慢推进到商品近景，真实摄影、自然光、可信物理运动。"
    "商品的颜色、耳朵形状、面部、衣服和比例不得改变；不要生成文字、Logo、水印或额外肢体。"
)


async def run(
    references: list[Path],
    output: Path,
    state_path: Path,
    prompt: str,
    resume_task: str | None,
) -> None:
    load_dotenv(ROOT / "backend" / ".env", override=True)
    provider = VolcArkSeedanceProvider()
    if resume_task:
        print(
            f"resuming task_id={resume_task}; no new generation task will be created",
            flush=True,
        )
        job = await provider.poll(resume_task)
        if job.status == "processing":
            job = await provider.wait_for_completion(
                VideoJob(
                    external_id=resume_task,
                    status="processing",
                    provider=provider.name,
                    model_id=provider.MINI_MODEL,
                )
            )
        if job.status != "completed":
            raise RuntimeError(f"任务 {resume_task} 当前状态为 {job.status}。")
    else:
        model_id = provider._route("economy")
        selected_reference_count = 3 if "seedance-2-0" in model_id else 1
        print(
            f"references={selected_reference_count} model={model_id} duration=5 "
            "billing=official_ark_account",
            flush=True,
        )
        submitted = await provider.create_task(
            prompt=prompt,
            duration=5,
            reference_images=[str(path) for path in references],
            model_hint="economy",
            aspect_ratio="16:9",
        )
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(
                {
                    "task_id": submitted.external_id,
                    "model_id": submitted.model_id,
                    "created_at": datetime.now().isoformat(),
                    "references": [str(path) for path in references],
                    "note": "Resume with --resume-task to avoid creating a second paid task.",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"submitted task_id={submitted.external_id} state={state_path}", flush=True)
        job = await provider.wait_for_completion(submitted)

    if not job.output_url:
        raise RuntimeError("Seedance 任务完成但没有返回视频地址。")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(await download_bytes(job.output_url))
    print(
        f"completed task_id={job.external_id} model={job.model_id} "
        f"output={output} bytes={output.stat().st_size}",
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run one recoverable paid Seedance 2.0 Mini backend smoke test."
    )
    parser.add_argument("--references", type=Path, nargs="+", default=DEFAULT_REFERENCES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument(
        "--resume-task",
        help="Resume an existing Seedance task without creating another paid task.",
    )
    args = parser.parse_args()
    references = [path.resolve() for path in args.references]
    missing = [str(path) for path in references if not path.is_file()]
    if missing:
        raise SystemExit(f"Reference image not found: {', '.join(missing)}")
    asyncio.run(
        run(
            references,
            args.output.resolve(),
            args.state.resolve(),
            args.prompt,
            args.resume_task,
        )
    )


if __name__ == "__main__":
    main()
