from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

from pydantic import BaseModel


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCAL_REALESRGAN = (
    PROJECT_ROOT
    / "tools"
    / "realesrgan-ncnn-vulkan-20220424-windows"
    / "realesrgan-ncnn-vulkan.exe"
)


class EnhancementCapability(BaseModel):
    id: str
    label: str
    available: bool
    executable: str = ""
    purpose: str
    warning: str


def _configured_file(env_name: str, fallback_command: str = "") -> str:
    configured = os.getenv(env_name, "").strip()
    if configured and Path(configured).is_file():
        return str(Path(configured).resolve())
    if fallback_command:
        located = shutil.which(fallback_command)
        if located:
            return located
    if env_name == "REALESRGAN_BIN" and LOCAL_REALESRGAN.is_file():
        return str(LOCAL_REALESRGAN)
    return ""


def enhancement_capabilities() -> list[EnhancementCapability]:
    realesrgan = _configured_file("REALESRGAN_BIN", "realesrgan-ncnn-vulkan")
    rife_script = _configured_file("RIFE_SCRIPT")
    return [
        EnhancementCapability(
            id="realesrgan",
            label="Real-ESRGAN清晰度修复",
            available=bool(realesrgan),
            executable=realesrgan,
            purpose="在内容和结构审核通过后，对最终视频逐帧进行2倍清晰度修复。",
            warning="不能修复人物换脸、手部错误或商品结构变化；增强可能放大生成伪影。",
        ),
        EnhancementCapability(
            id="rife",
            label="RIFE运动补帧",
            available=bool(rife_script),
            executable=rife_script,
            purpose="在动作连续性审核通过后，将低帧率成片补至更平滑的帧率。",
            warning="快速运动、遮挡和手部交互可能产生重影；默认不自动启用。",
        ),
        EnhancementCapability(
            id="ffmpeg",
            label="FFmpeg编码与交付",
            available=bool(shutil.which("ffmpeg") and shutil.which("ffprobe")),
            executable=shutil.which("ffmpeg") or "",
            purpose="统一画幅、字幕、音轨、Logo、CTA和交付编码。",
            warning="确定性处理不会修复生成内容本身的语义错误。",
        ),
    ]


async def _run(*arguments: str) -> None:
    process = await asyncio.create_subprocess_exec(
        *arguments,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode:
        raise RuntimeError(stderr.decode("utf-8", errors="replace")[-1200:])


async def _fps(source: Path) -> str:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("系统未找到FFprobe。")
    process = await asyncio.create_subprocess_exec(
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=r_frame_rate",
        "-of",
        "json",
        str(source),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode:
        raise RuntimeError(stderr.decode("utf-8", errors="replace")[-800:])
    streams = json.loads(stdout).get("streams") or []
    return str((streams[0] if streams else {}).get("r_frame_rate") or "24/1")


async def upscale_video_realesrgan(
    *,
    source: Path,
    target: Path,
    scale: int = 2,
) -> Path:
    executable = _configured_file("REALESRGAN_BIN", "realesrgan-ncnn-vulkan")
    ffmpeg = shutil.which("ffmpeg")
    if not executable:
        raise RuntimeError(
            "尚未安装Real-ESRGAN命令行程序。请配置REALESRGAN_BIN后再执行增强节点。"
        )
    if not ffmpeg:
        raise RuntimeError("系统未找到FFmpeg。")
    if not source.is_file():
        raise RuntimeError(f"待增强视频不存在：{source}")
    scale = 2 if scale not in {2, 3, 4} else scale
    # x4plus is a native 4x model. Asking the NCNN runtime for 2x directly
    # produced severe tile geometry corruption on the RTX 4060 test machine.
    # Always infer at the native scale, then downsample deterministically.
    native_scale = 4
    work = target.parent / f"{target.stem}-realesrgan-work"
    frames = work / "frames"
    enhanced = work / "enhanced"
    frames.mkdir(parents=True, exist_ok=True)
    enhanced.mkdir(parents=True, exist_ok=True)
    await _run(
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        str(frames / "%08d.png"),
    )
    await _run(
        executable,
        "-i",
        str(frames),
        "-o",
        str(enhanced),
        "-n",
        os.getenv("REALESRGAN_MODEL", "realesrgan-x4plus"),
        "-m",
        # The portable Windows build resolves this value relative to the
        # executable directory and incorrectly prepends that directory to an
        # absolute path. Keep the bundled folder name relative.
        "models",
        "-s",
        str(native_scale),
        "-t",
        os.getenv("REALESRGAN_TILE", "256"),
        "-f",
        "png",
    )
    fps = await _fps(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    await _run(
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-framerate",
        fps,
        "-i",
        str(enhanced / "%08d.png"),
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "1:a?",
        "-vf",
        (
            "scale=trunc(iw*"
            f"{scale}/{native_scale}/2)*2:trunc(ih*{scale}/{native_scale}/2)*2:flags=lanczos"
        ),
        "-c:v",
        "libx264",
        "-preset",
        "slow",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "copy",
        "-shortest",
        "-movflags",
        "+faststart",
        str(target),
    )
    return target


async def interpolate_video_rife(
    *,
    source: Path,
    target: Path,
    exponent: int = 1,
) -> Path:
    script = _configured_file("RIFE_SCRIPT")
    if not script:
        raise RuntimeError(
            "尚未配置RIFE官方inference_video.py。请下载官方项目并设置RIFE_SCRIPT后再执行补帧节点。"
        )
    if not source.is_file():
        raise RuntimeError(f"待补帧视频不存在：{source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    await _run(
        sys.executable,
        script,
        "--video",
        str(source),
        "--output",
        str(target),
        "--exp",
        str(max(1, min(exponent, 2))),
    )
    if not target.is_file():
        raise RuntimeError("RIFE执行结束但没有生成目标视频。")
    return target
