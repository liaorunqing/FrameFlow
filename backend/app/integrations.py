from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .scene_analysis import analyze_internal_cuts


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SHOTCRAFT_ROOT = Path(os.getenv(
    "SHOTCRAFT_ROOT",
    Path.home() / ".codex" / "skills" / "video-shotcraft",
))


class IntegrationCapability(BaseModel):
    id: str
    label: str
    category: Literal["director", "quality", "editor", "audio"]
    available: bool
    mode: str
    detail: str
    license_note: str = ""
    missing: list[str] = Field(default_factory=list)


class EvaluationReport(BaseModel):
    video_path: str
    checks: dict[str, object]
    external_commands: dict[str, str]
    warnings: list[str]


def _command(name: str) -> str:
    return shutil.which(name) or ""


def integration_capabilities() -> list[IntegrationCapability]:
    library = SHOTCRAFT_ROOT / "gallery" / "api" / "library.json"
    remotion_ready = (PROJECT_ROOT / "remotion" / "node_modules" / ".bin" / "remotion.cmd").is_file()
    dover_command = os.getenv("DOVER_COMMAND", "").strip()
    vbench_command = os.getenv("VBENCH_COMMAND", "").strip() or _command("vbench")
    return [
        IntegrationCapability(
            id="video-shotcraft", label="Video Shotcraft 镜头知识库", category="director",
            available=library.is_file(), mode="native-catalog",
            detail="在脚本冻结前提供镜头配方、留白、转场和声音设计约束。",
            license_note="Apache-2.0；本系统只读取配方元数据。",
            missing=[] if library.is_file() else [str(library)],
        ),
        IntegrationCapability(
            id="pyscenedetect", label="PySceneDetect 镜头连续性", category="quality",
            available=importlib.util.find_spec("scenedetect") is not None, mode="in-process",
            detail="检测单个生成镜头内不应出现的硬切。", license_note="BSD-3-Clause",
        ),
        IntegrationCapability(
            id="dover-mobile", label="DOVER-Mobile 观感质量", category="quality",
            available=bool(dover_command), mode="isolated-command",
            detail="输出技术质量与审美质量分；未配置时不会阻塞成片。",
            license_note="研究模型，商用前需单独复核代码与权重许可。",
            missing=[] if dover_command else ["DOVER_COMMAND"],
        ),
        IntegrationCapability(
            id="vbench", label="VBench / VBench-Long", category="quality",
            available=bool(vbench_command), mode="isolated-command",
            detail="用于候选模型离线基准，不进入每次生产的同步链路。",
            license_note="研究评测套件；模型权重各自适用许可。",
            missing=[] if vbench_command else ["VBENCH_COMMAND 或 vbench CLI"],
        ),
        IntegrationCapability(
            id="remotion", label="Remotion 可编程包装", category="editor",
            available=remotion_ready, mode="isolated-node-project",
            detail="负责可复用字幕、Logo、CTA 与数据驱动版式；FFmpeg 仍是稳定交付主引擎。",
            license_note="个人和不超过 3 人的营利组织可免费商用；更大组织需公司许可。",
            missing=[] if remotion_ready else ["remotion/node_modules"],
        ),
        IntegrationCapability(
            id="audio-mastering", label="节拍与响度母版", category="audio",
            available=bool(_command("ffmpeg") and _command("ffprobe")), mode="ffmpeg+librosa",
            detail="节拍网格、声音桥、EBU R128 响度与无 BGM 交付版本。",
            license_note="FFmpeg 许可取决于本机编译选项。",
            missing=[] if _command("ffmpeg") and _command("ffprobe") else ["ffmpeg/ffprobe"],
        ),
    ]


def shotcraft_summary() -> dict[str, object]:
    library = SHOTCRAFT_ROOT / "gallery" / "api" / "library.json"
    if not library.is_file():
        return {"available": False, "cards": 0, "styles": 0, "revision": ""}
    data = json.loads(library.read_text(encoding="utf-8"))
    stats = data.get("stats", {})
    return {
        "available": True,
        "cards": int(stats.get("cardCount", len(data.get("cards", [])))),
        "styles": int(stats.get("styleCount", 0)),
        "revision": data.get("revision", ""),
        "principles": [
            "one-primary-effect-per-shot", "three-second-hook", "one-second-brand-hold",
            "picture-lock-before-sound", "declarative-sfx-timeline", "beat-aligned-cuts",
        ],
    }


def _probe(video: Path) -> dict[str, object]:
    ffprobe = _command("ffprobe")
    if not ffprobe:
        return {"available": False}
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height,r_frame_rate", "-of", "json", str(video)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    return json.loads(result.stdout) if result.returncode == 0 else {"available": False, "error": result.stderr[-500:]}


def evaluate_video(video_path: Path) -> EvaluationReport:
    video = video_path.resolve()
    if not video.is_file():
        raise FileNotFoundError(video)
    commands: dict[str, str] = {}
    warnings: list[str] = []
    dover = os.getenv("DOVER_COMMAND", "").strip()
    vbench = os.getenv("VBENCH_COMMAND", "").strip() or _command("vbench")
    if dover:
        commands["dover"] = dover.replace("{video}", str(video))
    else:
        warnings.append("DOVER_COMMAND 未配置，跳过 DOVER-Mobile。")
    if vbench:
        commands["vbench"] = f'{vbench} evaluate --dimension subject_consistency background_consistency motion_smoothness aesthetic_quality imaging_quality --videos_path "{video}" --mode=custom_input'
    else:
        warnings.append("VBench CLI 未配置，跳过重型离线基准。")
    return EvaluationReport(
        video_path=str(video),
        checks={"media_probe": _probe(video), "scene_continuity": analyze_internal_cuts(video), "shotcraft": shotcraft_summary()},
        external_commands=commands,
        warnings=warnings,
    )
