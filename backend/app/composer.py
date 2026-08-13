from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .network import download_bytes


SIZE_BY_RATIO = {
    "9:16": (720, 1280),
    "16:9": (1280, 720),
    "1:1": (720, 720),
}


async def _run_ffmpeg(*arguments: str) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("系统未找到 FFmpeg，无法合成多镜头成片。")
    process = await asyncio.create_subprocess_exec(
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        *arguments,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(f"FFmpeg 合成失败：{stderr.decode('utf-8', errors='replace')[-800:]}")


async def compose_clips(
    *,
    project_id: str,
    clip_urls: list[str],
    clip_durations: list[int],
    aspect_ratio: str,
    output_root: Path,
) -> str:
    """Download, normalize and concatenate provider clips into an owned MP4."""
    if not clip_urls:
        raise RuntimeError("没有可合成的视频片段。")
    if len(clip_urls) != len(clip_durations):
        raise RuntimeError("视频片段数量与导演镜头时长不一致。")
    output_root = output_root.resolve()
    project_dir = output_root / project_id
    project_dir.mkdir(parents=True, exist_ok=True)

    for index, url in enumerate(clip_urls, start=1):
        content = await download_bytes(url)
        (project_dir / f"source-{index:02d}.mp4").write_bytes(content)

    width, height = SIZE_BY_RATIO.get(aspect_ratio, SIZE_BY_RATIO["9:16"])
    normalized: list[Path] = []
    filter_value = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,fps=24,format=yuv420p"
    )
    for index in range(1, len(clip_urls) + 1):
        source = project_dir / f"source-{index:02d}.mp4"
        target = project_dir / f"clip-{index:02d}.mp4"
        await _run_ffmpeg(
            "-i", str(source), "-t", str(clip_durations[index - 1]), "-an", "-vf", filter_value,
            "-c:v", "libx264", "-preset", "fast", "-crf", "19",
            "-movflags", "+faststart", str(target),
        )
        normalized.append(target)

    concat_file = project_dir / "concat.txt"
    concat_file.write_text(
        "\n".join(f"file '{path.as_posix()}'" for path in normalized),
        encoding="utf-8",
    )
    output = project_dir / "final.mp4"
    await _run_ffmpeg(
        "-f", "concat", "-safe", "0", "-i", str(concat_file),
        "-c", "copy", "-movflags", "+faststart", str(output),
    )
    return f"/outputs/{project_id}/final.mp4"


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    names = ["msyhbd.ttc", "msyh.ttc"] if bold else ["msyh.ttc", "simhei.ttf"]
    for name in names:
        candidate = Path("C:/Windows/Fonts") / name
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def create_cta_card(
    *,
    background: Path,
    target: Path,
    size: tuple[int, int],
    headline: str,
    product_name: str,
    logo: Path | None = None,
) -> Path:
    width, height = size
    with Image.open(background) as source:
        canvas = ImageOps.fit(
            source.convert("RGB"),
            (width, height),
            method=Image.Resampling.LANCZOS,
        )
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rectangle((0, height * 0.55, width, height), fill=(8, 12, 14, 155))
    headline_font = _font(max(34, width // 20), bold=True)
    product_font = _font(max(22, width // 34))
    headline_box = draw.textbbox((0, 0), headline, font=headline_font)
    product_box = draw.textbbox((0, 0), product_name, font=product_font)
    draw.text(
        ((width - (headline_box[2] - headline_box[0])) / 2, height * 0.68),
        headline,
        font=headline_font,
        fill=(255, 255, 255, 255),
    )
    draw.text(
        ((width - (product_box[2] - product_box[0])) / 2, height * 0.78),
        product_name,
        font=product_font,
        fill=(222, 233, 228, 255),
    )
    if logo and logo.is_file():
        with Image.open(logo) as source:
            mark = source.convert("RGBA")
            mark.thumbnail((width // 5, height // 10), Image.Resampling.LANCZOS)
            overlay.alpha_composite(mark, ((width - mark.width) // 2, int(height * 0.58)))
    target.parent.mkdir(parents=True, exist_ok=True)
    Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB").save(
        target, quality=94
    )
    return target


def create_cta_overlay(
    *,
    target: Path,
    size: tuple[int, int],
    headline: str,
    product_name: str,
    logo: Path | None = None,
) -> Path:
    """Create a transparent end-of-film CTA, used when no spare end-card exists."""
    width, height = size
    panel_width = int(width * 0.86)
    panel_height = max(118, int(height * 0.19))
    overlay = Image.new("RGBA", (panel_width, panel_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rounded_rectangle(
        (0, 0, panel_width - 1, panel_height - 1),
        radius=max(18, panel_height // 7),
        fill=(10, 15, 16, 208),
        outline=(224, 238, 184, 210),
        width=2,
    )
    # Mobile delivery threshold: primary >=44 px, supporting text >=32 px on
    # the 720x1280 master. Never shrink legal/product information into noise.
    headline_font = _font(max(44, panel_width // 14), bold=True)
    product_font = _font(max(32, panel_width // 19))
    text_x = panel_width // 2
    if logo and logo.is_file():
        with Image.open(logo) as source:
            mark = source.convert("RGBA")
            mark.thumbnail((panel_width // 6, panel_height // 3), Image.Resampling.LANCZOS)
            overlay.alpha_composite(mark, ((panel_width - mark.width) // 2, 12))
            headline_y = max(panel_height // 2, 38 + mark.height)
    else:
        headline_y = panel_height // 3
    # Long Chinese CTA copy must wrap inside the panel instead of being
    # centered with negative x coordinates and clipped by the video edge.
    max_text_width = panel_width - 56
    headline_lines: list[str] = []
    current = ""
    for char in headline.strip():
        candidate = current + char
        box = draw.textbbox((0, 0), candidate, font=headline_font)
        if current and box[2] - box[0] > max_text_width:
            headline_lines.append(current)
            current = char
        else:
            current = candidate
    if current:
        headline_lines.append(current)
    headline_text = "\n".join(headline_lines[:2])
    headline_box = draw.multiline_textbbox(
        (0, 0), headline_text, font=headline_font, spacing=4, align="center"
    )
    product_box = draw.textbbox((0, 0), product_name, font=product_font)
    draw.multiline_text(
        (text_x, headline_y),
        headline_text,
        font=headline_font,
        fill=(255, 255, 255, 255),
        anchor="ma",
        spacing=4,
        align="center",
    )
    draw.text(
        (text_x - (product_box[2] - product_box[0]) / 2, panel_height - product_font.size - 14),
        product_name,
        font=product_font,
        fill=(220, 234, 193, 255),
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    overlay.save(target)
    return target


def _subtitle_filter(path: Path, *, margin_vertical: int = 42) -> str:
    resolved = path.resolve()
    try:
        # Avoid FFmpeg/libass's two-layer Windows drive-letter escaping. A
        # relative path is parsed consistently when the API process and FFmpeg
        # share a working directory.
        escaped = Path(os.path.relpath(resolved, Path.cwd())).as_posix()
    except ValueError:
        # Different Windows drives cannot be relativized. The subtitles filter
        # consumes one escaping layer before passing the filename to libass.
        escaped = resolved.as_posix().replace(":", r"\\:")
    escaped = escaped.replace("'", r"\'")
    style = (
        "FontName=SimHei,FontSize=11,Bold=0,Spacing=-0.4,"
        "PrimaryColour=&H00FFFFFF,OutlineColour=&H90000000,"
        f"BorderStyle=1,Outline=1.0,Shadow=0,MarginL=48,MarginR=48,MarginV={margin_vertical},Alignment=2"
    )
    return f"subtitles=filename='{escaped}':force_style='{style}'"


def _feature_state_filter(path: Path) -> str:
    resolved = path.resolve()
    try:
        escaped = Path(os.path.relpath(resolved, Path.cwd())).as_posix()
    except ValueError:
        escaped = resolved.as_posix().replace(":", r"\:")
    escaped = escaped.replace("'", r"\'")
    style = (
        "FontName=SimHei,FontSize=12,Bold=0,"
        "PrimaryColour=&H00FFFFFF,BackColour=&HC0201512,"
        "OutlineColour=&HC0201512,BorderStyle=3,Outline=4,Shadow=0,"
        "MarginL=36,MarginR=36,MarginV=52,Alignment=8"
    )
    return f"subtitles=filename='{escaped}':force_style='{style}'"


async def compose_campaign(
    *,
    clip_paths: list[Path],
    clip_durations: list[float],
    aspect_ratio: str,
    narration_path: Path,
    subtitle_path: Path,
    output_path: Path,
    cta_background: Path | None = None,
    logo_path: Path | None = None,
    cta_headline: str = "让温柔，先开口",
    product_name: str = "",
    cta_duration: float = 5.0,
    cta_overlay_duration: float = 2.6,
    bgm_path: Path | None = None,
    bgm_gain: float = 0.13,
    sound_cues: list[dict[str, Any]] | None = None,
    feature_state_subtitle_path: Path | None = None,
) -> Path:
    instruction_tokens = (
        "屏幕", "画面", "中央", "浮现", "字幕", "小字", "显示", "text appears"
    )
    if any(token.lower() in cta_headline.lower() for token in instruction_tokens):
        cta_headline = "看看下一次，会搭出什么"
    """Create a campaign master with shared-frame match cuts and a continuous sound bridge."""
    if not clip_paths or len(clip_paths) != len(clip_durations):
        raise RuntimeError("镜头文件与时长必须一一对应。")
    width, height = SIZE_BY_RATIO.get(aspect_ratio, SIZE_BY_RATIO["16:9"])
    work_dir = output_path.parent / "compose-work"
    work_dir.mkdir(parents=True, exist_ok=True)
    normalized: list[Path] = []
    filter_value = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
        "fps=24,format=yuv420p"
    )
    for index, (source, duration) in enumerate(
        zip(clip_paths, clip_durations, strict=True), start=1
    ):
        target = work_dir / f"shot-{index:02d}.mp4"
        await _run_ffmpeg(
            "-i", str(source), "-t", f"{duration:.3f}", "-an",
            "-vf", filter_value, "-c:v", "libx264", "-preset", "fast",
            "-crf", "19", "-movflags", "+faststart", str(target),
        )
        normalized.append(target)
    narrative_duration = sum(clip_durations)
    if cta_background:
        card = create_cta_card(
            background=cta_background,
            target=work_dir / "cta.jpg",
            size=(width, height),
            headline=cta_headline,
            product_name=product_name,
            logo=logo_path,
        )
        cta_clip = work_dir / "cta.mp4"
        await _run_ffmpeg(
            "-loop", "1", "-i", str(card), "-t", f"{cta_duration:.3f}",
            "-vf", f"zoompan=z='min(zoom+0.0004,1.025)':d=1:s={width}x{height}:fps=24,format=yuv420p",
            "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "19",
            "-movflags", "+faststart", str(cta_clip),
        )
        normalized.append(cta_clip)
    concat_file = work_dir / "concat.txt"
    concat_file.write_text(
        "\n".join(f"file '{path.as_posix()}'" for path in normalized),
        encoding="utf-8",
    )
    silent_master = work_dir / "silent-master.mp4"
    await _run_ffmpeg(
        "-f", "concat", "-safe", "0", "-i", str(concat_file),
        "-c", "copy", "-movflags", "+faststart", str(silent_master),
    )
    total_duration = narrative_duration + (cta_duration if cta_background else 0.0)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    use_overlay = not cta_background and bool(cta_headline.strip() or product_name.strip())
    overlay = None
    if use_overlay:
        overlay = create_cta_overlay(
            target=work_dir / "cta-overlay.png",
            size=(width, height),
            headline=cta_headline,
            product_name=product_name,
            logo=logo_path,
        )
    overlay_duration = min(max(0.8, cta_overlay_duration), total_duration)
    overlay_start = max(0.0, total_duration - overlay_duration)
    inputs = [
        "-i", str(silent_master),
        "-i", str(narration_path),
        "-f", "lavfi", "-t", f"{total_duration:.3f}",
        "-i", "anoisesrc=color=pink:amplitude=0.015:r=48000",
    ]
    # Reserve the lower quarter for the CTA when it is displayed over the
    # ending shot. This prevents the final narration subtitle from colliding
    # with the brand card.
    # ASS margins use the script's logical PlayRes units rather than output
    # pixels. Passing `height // 4` (320 for portrait HD) pushed every subtitle
    # completely off-canvas. Keep captions above the final CTA panel with a
    # bounded logical margin.
    subtitle_margin = 78 if use_overlay else 34
    # Generated clips and transition trims can leave the silent picture master
    # a few frames shorter than the requested campaign duration. Clone the
    # final frame before applying subtitles/CTA so the delivered video stream,
    # not only its audio/container, reaches the exact requested duration.
    subtitle_active = (
        subtitle_path.is_file()
        and subtitle_path.stat().st_size > 5
        and subtitle_path.read_text(encoding="utf-8-sig").strip() != ""
    )
    if subtitle_active:
        video_filter = (
            f"[0:v]tpad=stop_mode=clone:stop_duration=2,"
            f"{_subtitle_filter(subtitle_path, margin_vertical=subtitle_margin)}[sub0]"
        )
    else:
        video_filter = "[0:v]tpad=stop_mode=clone:stop_duration=2[sub0]"
    if feature_state_subtitle_path and feature_state_subtitle_path.is_file():
        video_filter += f";[sub0]{_feature_state_filter(feature_state_subtitle_path)}[sub]"
    else:
        video_filter += ";[sub0]null[sub]"
    next_input = 3
    overlay_input: int | None = None
    if overlay:
        inputs.extend(["-loop", "1", "-t", f"{total_duration:.3f}", "-i", str(overlay)])
        overlay_input = next_input
        next_input += 1
        fade = min(0.22, overlay_duration / 3)
        video_filter += (
            f";[{overlay_input}:v]format=rgba,setpts=PTS-STARTPTS+{overlay_start:.3f}/TB,"
            f"fade=t=in:st={overlay_start:.3f}:d={fade:.3f}:alpha=1,"
            f"fade=t=out:st={max(overlay_start, total_duration - fade):.3f}:d={fade:.3f}:alpha=1[cta];"
            "[sub][cta]overlay=(W-w)/2:H-h-120:eof_action=pass[v]"
        )
    else:
        video_filter += ";[sub]null[v]"
    audio_filters = ["[1:a]volume=1.0[n]", "[2:a]volume=0.08[room]"]
    audio_labels = ["[n]", "[room]"]
    if bgm_path and bgm_path.is_file():
        inputs.extend(["-stream_loop", "-1", "-i", str(bgm_path)])
        audio_filters.append(
            f"[{next_input}:a]atrim=0:{total_duration:.3f},asetpts=PTS-STARTPTS,"
            f"volume={bgm_gain:.4f},afade=t=in:st=0:d=0.8,"
            f"afade=t=out:st={max(0.0, total_duration - 1.2):.3f}:d=1.2[bgm]"
        )
        audio_labels.append("[bgm]")
        next_input += 1
    for cue_index, cue in enumerate(sound_cues or []):
        path = Path(str(cue.get("path", "")))
        if not path.is_file():
            continue
        inputs.extend(["-i", str(path)])
        delay = max(0, round(float(cue.get("start", 0)) * 1000))
        gain = max(0.0, float(cue.get("gain", 0.25)))
        label = f"sfx{cue_index}"
        audio_filters.append(
            f"[{next_input}:a]adelay={delay}|{delay},volume={gain:.4f}[{label}]"
        )
        audio_labels.append(f"[{label}]")
        next_input += 1
    audio_filters.append(
        "".join(audio_labels)
        + f"amix=inputs={len(audio_labels)}:duration=longest:dropout_transition=2,"
          "loudnorm=I=-16:TP=-1.5:LRA=11[a]"
    )
    await _run_ffmpeg(
        *inputs,
        "-filter_complex",
        f"{video_filter};" + ";".join(audio_filters),
        "-map", "[v]", "-map", "[a]", "-t", f"{total_duration:.3f}",
        "-c:v", "libx264", "-preset", "fast", "-crf", "19",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart", str(output_path),
    )
    return output_path
