from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


def analyze_audio(video_path: Path) -> dict[str, object]:
    """Measure delivery loudness without modifying the source."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return {"available": False, "warning": "ffmpeg missing"}
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-nostats", "-i", str(video_path), "-af",
         "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    start, end = result.stderr.rfind("{"), result.stderr.rfind("}")
    if start < 0 or end < start:
        return {"available": False, "warning": result.stderr[-500:]}
    try:
        metrics = json.loads(result.stderr[start:end + 1])
    except json.JSONDecodeError:
        return {"available": False, "warning": "无法解析 loudnorm 输出"}
    return {"available": True, "target_lufs": -16.0, "target_true_peak_db": -1.5, **metrics}


def analyze_beats(audio_path: Path) -> dict[str, object]:
    """Build an editorial beat grid; callers may align cuts but never retime story logic blindly."""
    try:
        import librosa
    except ImportError:
        return {"available": False, "warning": "librosa missing", "beat_times": []}
    waveform, sample_rate = librosa.load(str(audio_path), sr=22050, mono=True)
    tempo, frames = librosa.beat.beat_track(y=waveform, sr=sample_rate, units="frames")
    beat_times = librosa.frames_to_time(frames, sr=sample_rate).round(3).tolist()
    scalar_tempo = float(tempo[0] if hasattr(tempo, "__len__") else tempo)
    return {"available": True, "tempo_bpm": round(scalar_tempo, 2), "beat_times": beat_times}
