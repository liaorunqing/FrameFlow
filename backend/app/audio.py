from __future__ import annotations

import asyncio
import json
import shutil
from os import getenv
from pathlib import Path

import httpx

from .schemas import Project
from .workflow import AudioTimeline, NarrationCue, SubtitleCue


MAX_NARRATION_CHARS_PER_SECOND = 4.2
MAX_TTS_TEMPO = 1.12


def _srt_time(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    secs, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


def _wrap_subtitle(text: str, max_chars: int = 10) -> str:
    """Wrap CJK captions into at most two balanced, mobile-safe lines."""
    cleaned = " ".join(text.strip().split())
    # Latin captions must wrap on word boundaries. Splitting every ten
    # characters produced fragments such as "st / ruggles" on the master.
    words = cleaned.split()
    if len(words) > 1 and sum(char.isascii() for char in cleaned) / max(1, len(cleaned)) > 0.7:
        max_latin_chars = 26
        lines: list[str] = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if current and len(candidate) > max_latin_chars:
                lines.append(current)
                current = word
            else:
                current = candidate
        if current:
            lines.append(current)
        return "\n".join(lines[:2])
    if len(cleaned) <= max_chars:
        return cleaned
    candidates = [
        index + 1 for index, char in enumerate(cleaned[:-1])
        if char in "，。！？；：,.!?;:"
    ]
    midpoint = len(cleaned) / 2
    split = min(candidates, key=lambda value: abs(value - midpoint)) if candidates else round(midpoint)
    first, second = cleaned[:split].strip(), cleaned[split:].strip()
    if len(first) > max_chars or len(second) > max_chars:
        split = min(max_chars, max(1, round(midpoint)))
        first, second = cleaned[:split].strip(), cleaned[split:].strip()
    return f"{first}\n{second}"


def _split_subtitle_text(text: str, max_total_chars: int = 20) -> list[str]:
    """Split narration into timed mobile captions before line wrapping."""
    cleaned = " ".join(text.strip().split())
    words = cleaned.split()
    if len(words) > 1 and sum(char.isascii() for char in cleaned) / max(1, len(cleaned)) > 0.7:
        max_latin_total = 48
        parts: list[str] = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if current and len(candidate) > max_latin_total:
                parts.append(current)
                current = word
            else:
                current = candidate
        if current:
            parts.append(current)
        return parts
    if len(cleaned) <= max_total_chars:
        return [cleaned]
    # Prefer complete clauses. Character-count chopping produced captions such
    # as “心 / 理” and even a punctuation-only caption in delivered masters.
    import re

    clauses = [item.strip() for item in re.findall(r"[^，。！？；,.!?;]+[，。！？；,.!?;]?", cleaned)]
    parts: list[str] = []
    current = ""
    for clause in clauses:
        if not clause:
            continue
        candidate = f"{current}{clause}"
        if current and len(candidate) > max_total_chars:
            parts.append(current)
            current = clause
        else:
            current = candidate
    if current:
        parts.append(current)
    return [part for part in parts if part and part not in "，。！？；,.!?;"]


def build_audio_timeline(project: Project) -> AudioTimeline:
    if not project.creative_plan:
        raise ValueError("请先生成故事方案。")
    cursor = 0.0
    cues: list[NarrationCue] = []
    subtitles: list[SubtitleCue] = []
    shots = project.creative_plan.shots
    for shot_index, shot in enumerate(shots):
        text = shot.voiceover.strip()
        shot_start = cursor
        shot_end = cursor + float(shot.duration)
        if text:
            cue_start = shot_start + min(0.35, shot.duration * 0.08)
            cue_end = max(cue_start + 0.8, shot_end - min(0.25, shot.duration * 0.05))
            slot = cue_end - cue_start
            max_chars = max(8, int(slot * MAX_NARRATION_CHARS_PER_SECOND))
            if len(text.replace(" ", "")) > max_chars:
                raise ValueError(
                    f"镜头 {shot.index} 旁白过长：{len(text.replace(' ', ''))} 字，"
                    f"{slot:.1f} 秒最多允许约 {max_chars} 字。请先重写脚本，禁止加速硬塞。"
                )
            cues.append(
                NarrationCue(
                    start=round(cue_start, 3),
                    end=round(cue_end, 3),
                    text=text,
                    shot_id=shot.id,
                )
            )
            subtitle_end = cue_end
            if shot_index == len(shots) - 1:
                # Leave a clean brand hold for the post-production CTA. The
                # narration continues, but text exits before the end card.
                subtitle_end = max(cue_start + 1.8, min(cue_end, shot_end - 2.8))
            caption_parts = _split_subtitle_text(text)
            caption_duration = max(0.1, subtitle_end - cue_start)
            total_chars = max(1, sum(len(part) for part in caption_parts))
            part_cursor = cue_start
            for part_index, part in enumerate(caption_parts):
                if part_index == len(caption_parts) - 1:
                    part_end = subtitle_end
                else:
                    share = max(1, len(part)) / total_chars
                    part_end = min(subtitle_end, part_cursor + caption_duration * share)
                subtitles.append(SubtitleCue(
                    index=len(subtitles) + 1,
                    start=round(part_cursor, 3),
                    end=round(part_end, 3),
                    text=part,
                ))
                part_cursor = part_end
        cursor = shot_end
    narration = "".join(cue.text for cue in cues)
    return AudioTimeline(
        narration_text=narration,
        duration=cursor,
        cues=cues,
        subtitles=subtitles,
    )


def write_srt(timeline: AudioTimeline, target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    blocks = [
        f"{cue.index}\n{_srt_time(cue.start)} --> {_srt_time(cue.end)}\n{_wrap_subtitle(cue.text)}"
        for cue in timeline.subtitles
    ]
    target.write_text("\n\n".join(blocks) + "\n", encoding="utf-8-sig")
    return target


async def synthesize_windows_narration(
    timeline: AudioTimeline,
    target: Path,
    *,
    voice: str = "Microsoft Huihui Desktop",
    rate: int = 0,
    cloud_voice_id: str = "male-qn-qingse",
    cloud_speed: float = 0.92,
    allow_paid_fallback: bool = False,
) -> Path:
    powershell = shutil.which("powershell")
    app_home = getenv("FRAMEFLOW_HOME", "").strip()
    project_root = Path(app_home).resolve() if app_home else Path(__file__).resolve().parents[2]
    script = project_root / "scripts" / "synthesize_speech.ps1"
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    edge_tts = shutil.which("edge-tts")
    if not ffmpeg or not ffprobe:
        raise RuntimeError("系统找不到 FFmpeg/FFprobe，无法创建精确旁白时间轴。")
    target.parent.mkdir(parents=True, exist_ok=True)
    cue_dir = target.parent / "narration-cues"
    cue_dir.mkdir(parents=True, exist_ok=True)

    async def run(*arguments: str) -> None:
        process = await asyncio.create_subprocess_exec(
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(stderr.decode("utf-8", errors="replace")[-1000:])

    async def minimax_tts(text: str, destination: Path) -> bool:
        api_key = getenv("MINIMAX_API_KEY", "").strip()
        if not api_key:
            return False
        base_url = getenv("MINIMAX_API_BASE", "https://api-bj.minimaxi.com").rstrip("/")
        payload = {
            "model": getenv("MINIMAX_TTS_MODEL", "speech-2.8-turbo"),
            "text": text,
            "stream": False,
            "voice_setting": {
                "voice_id": cloud_voice_id or getenv("MINIMAX_TTS_VOICE", "male-qn-qingse"),
                "speed": max(0.7, min(1.3, cloud_speed)),
                "vol": 1.0,
                "pitch": 0,
            },
            "audio_setting": {
                "sample_rate": 32000,
                "bitrate": 128000,
                "format": "mp3",
                "channel": 1,
            },
            "language_boost": "Chinese",
        }
        async with httpx.AsyncClient(timeout=120, trust_env=False) as client:
            response = await client.post(
                f"{base_url}/v1/t2a_v2",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
        result = response.json()
        base_resp = result.get("base_resp") or {}
        if int(base_resp.get("status_code") or 0) != 0:
            raise RuntimeError(str(base_resp.get("status_msg") or "MiniMax TTS failed"))
        audio_hex = str((result.get("data") or {}).get("audio") or "")
        if not audio_hex:
            raise RuntimeError("MiniMax TTS 未返回音频内容。")
        destination.write_bytes(bytes.fromhex(audio_hex))
        return True

    segments: list[Path] = []
    cursor = 0.0
    for index, cue in enumerate(timeline.cues, start=1):
        gap = max(0.0, cue.start - cursor)
        if gap > 0.001:
            silence = cue_dir / f"{index:02d}-gap.wav"
            await run(
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
                "-t", f"{gap:.3f}", "-c:a", "pcm_s16le", str(silence),
            )
            segments.append(silence)
        text_file = cue_dir / f"{index:02d}.txt"
        raw_wav = cue_dir / f"{index:02d}-raw.wav"
        raw_mp3 = cue_dir / f"{index:02d}-raw.mp3"
        fitted = cue_dir / f"{index:02d}-fitted.wav"
        text_file.write_text(cue.text, encoding="utf-8")
        raw = raw_wav
        try:
            if not powershell or not script.is_file():
                raise RuntimeError("Windows 本地语音运行体不可用。")
            await run(
                powershell, "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(script), "-TextFile", str(text_file),
                "-OutputFile", str(raw_wav), "-Voice", voice, "-Rate", str(rate),
            )
            if not raw_wav.is_file() or raw_wav.stat().st_size < 1000:
                raise RuntimeError("Windows TTS returned an empty audio file.")
        except RuntimeError:
            if edge_tts:
                await run(
                    edge_tts,
                    "--voice", "zh-CN-XiaoxiaoNeural",
                    "--rate=-8%",
                    "--text", cue.text,
                    "--write-media", str(raw_mp3),
                )
            elif allow_paid_fallback and await minimax_tts(cue.text, raw_mp3):
                pass
            else:
                raise RuntimeError(
                    "Windows本地TTS不可用，且未授权MiniMax付费语音回退。"
                )
            raw = raw_mp3
        probe = await asyncio.create_subprocess_exec(
            ffprobe, "-v", "error", "-show_entries", "format=duration",
            "-of", "json", str(raw),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await probe.communicate()
        if probe.returncode != 0:
            raise RuntimeError(stderr.decode("utf-8", errors="replace")[-800:])
        raw_duration = float(json.loads(stdout)["format"]["duration"])
        slot = max(0.8, cue.end - cue.start)
        required_tempo = raw_duration / slot
        if required_tempo > MAX_TTS_TEMPO:
            raise RuntimeError(
                f"旁白自然语速需要 {raw_duration:.2f} 秒，但镜头只提供 {slot:.2f} 秒；"
                "系统拒绝通过加速硬塞，请缩短文案或调整时间轴。"
            )
        tempo = max(1.0, required_tempo)
        filters: list[str] = []
        while tempo > 2.0:
            filters.append("atempo=2.0")
            tempo /= 2.0
        filters.append(f"atempo={tempo:.6f}")
        filters.append(f"apad=pad_dur={slot:.3f}")
        await run(
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(raw), "-af", ",".join(filters), "-t", f"{slot:.3f}",
            "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(fitted),
        )
        segments.append(fitted)
        cursor = cue.end
    tail = max(0.0, timeline.duration - cursor)
    if tail > 0.001:
        silence = cue_dir / "tail.wav"
        await run(
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
            "-t", f"{tail:.3f}", "-c:a", "pcm_s16le", str(silence),
        )
        segments.append(silence)
    concat = cue_dir / "concat.txt"
    concat.write_text(
        "\n".join(f"file '{path.as_posix()}'" for path in segments),
        encoding="utf-8",
    )
    await run(
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat),
        "-c:a", "pcm_s16le", str(target),
    )
    timeline.narration_path = str(target)
    return target
