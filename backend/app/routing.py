from __future__ import annotations

from os import getenv
from typing import Literal

from pydantic import BaseModel, Field

from .schemas import Project, Shot


class ProviderCapability(BaseModel):
    id: str
    label: str
    configured: bool
    region: Literal["中国大陆", "全球", "本地"]
    modes: list[str]
    strengths: list[str]
    limitations: list[str]


class RoutingDecision(BaseModel):
    shot_id: str
    provider: str
    model_hint: Literal["economy", "story", "premium"]
    requested_capabilities: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    fallback_provider: str = "demo"


def provider_capabilities() -> list[ProviderCapability]:
    return [
        ProviderCapability(
            id="seedance-official",
            label="火山方舟 Seedance",
            configured=bool(getenv("ARK_API_KEY", "").strip()),
            region="中国大陆",
            modes=["首帧图生视频", "首尾帧", "返回末帧", "电影感运动"],
            strengths=["运镜表现", "叙事动作", "中文提示词", "现有工程已验证"],
            limitations=["不同模型版本的多参考能力不同", "复杂手部与商品交互仍需质检"],
        ),
        ProviderCapability(
            id="minimax-official",
            label="MiniMax 海螺",
            configured=bool(getenv("MINIMAX_API_KEY", "").strip()),
            region="中国大陆",
            modes=["首帧图生视频", "首尾帧", "人物主体参考", "1080P"],
            strengths=["人物动作", "物理表现", "人脸主体保持", "Fast性价比模式"],
            limitations=["主体参考主要面向人物", "商品几何一致性仍需关键帧锁定"],
        ),
        ProviderCapability(
            id="wan-bailian",
            label="阿里云百炼 万相",
            configured=bool(getenv("DASHSCOPE_API_KEY", "").strip()),
            region="中国大陆",
            modes=["首帧", "首尾帧", "参考生视频", "视频编辑"],
            strengths=["跨镜头衔接", "多参考主体", "局部修复", "中国区部署"],
            limitations=["适配器尚未启用时只作为路由候选", "不同模型计费和时长不同"],
        ),
        ProviderCapability(
            id="ollama-vision",
            label="Ollama Qwen3-VL",
            configured=(
                bool(
                    (getenv("VISION_CLOUD_API_KEY", "").strip() or getenv("DASHSCOPE_API_KEY", "").strip())
                    and getenv("VISION_CLOUD_MODEL", "qwen-vl-max").strip()
                )
                if getenv("VISION_PROVIDER", "ollama").strip().lower() in {"dashscope", "cloud", "qwen"}
                else bool(getenv("VISION_MODEL", "qwen3-vl:4b").strip())
            ),
            region="本地",
            modes=["素材分析", "关键帧质检", "抽帧质检"],
            strengths=["零API费用", "数据留在本地", "结构化审核"],
            limitations=["不能替代人工最终审片", "视频需先抽取代表帧"],
        ),
        ProviderCapability(
            id="ffmpeg",
            label="FFmpeg确定性后期",
            configured=True,
            region="本地",
            modes=["剪辑", "字幕", "声音桥", "Logo", "CTA"],
            strengths=["文字准确", "完全可复现", "零生成费用"],
            limitations=["不生成新的写实人物动作"],
        ),
    ]


def _configured(*provider_ids: str) -> str | None:
    available = {
        capability.id: capability.configured
        for capability in provider_capabilities()
    }
    return next((provider_id for provider_id in provider_ids if available.get(provider_id)), None)


def route_shot(project: Project, shot: Shot, provider_override: str = "") -> RoutingDecision:
    text = " ".join([
        shot.title,
        shot.purpose,
        shot.narrative_beat,
        shot.visual,
        shot.action,
        shot.transition,
    ]).lower()
    requested: list[str] = ["image_to_video", "return_last_frame"]
    reasons: list[str] = []

    identity_words = ("人物", "面部", "表情", "近景", "对话", "孩子", "家庭")
    transition_words = ("过渡", "衔接", "连续", "走入", "走向", "推近", "拉远")
    complex_words = ("拿起", "打开", "组装", "递给", "双手", "互动", "接触")

    if any(word in text for word in identity_words):
        requested.append("identity_reference")
        reasons.append("镜头包含人物表情或身份连续性")
    if any(word in text for word in transition_words):
        requested.append("first_last_frame")
        reasons.append("镜头承担跨场景或动作衔接")
    if any(word in text for word in complex_words):
        requested.append("strong_physics")
        reasons.append("镜头包含手部与商品交互，需要更强物理表现")

    if provider_override:
        if provider_override not in {"minimax-official", "seedance-official", "wan-bailian", "demo"}:
            raise ValueError("Unknown video provider override.")
        if provider_override == "seedance-official":
            reasons.append("Seedance was selected as an explicit human upgrade.")
        provider = provider_override
    else:
        configured_default = getenv("VIDEO_PROVIDER", "minimax-official").strip()
        supported = {
            "minimax-official", "seedance-official", "wan-bailian", "demo"
        }
        provider = (
            _configured(configured_default)
            if configured_default in supported and configured_default != "demo"
            else "demo" if configured_default == "demo"
            else None
        )
        provider = provider or _configured(
            "minimax-official", "seedance-official", "wan-bailian"
        )
        reasons.append(f"使用系统设置的默认视频供应商：{provider or 'demo'}")

    provider = provider or "demo"
    if provider == "wan-bailian" and not getenv("WAN_PROVIDER_ENABLED", "").strip():
        fallback = _configured("minimax-official") or "demo"
        reasons.append("万相适配器尚未启用，执行时使用已接入供应商")
        provider = fallback
    if not reasons:
        reasons.append("常规叙事镜头，优先使用已验证的默认供应商")

    model_hint: Literal["economy", "story", "premium"] = "story"
    if "strong_physics" in requested or shot.index == 1:
        # Keep the budgeted research baseline on MiniMax Fast. Premium is a
        # separate explicit upgrade, never an automatic cost escalation.
        model_hint = "story"
    elif shot.purpose in {"过渡", "环境建立"}:
        model_hint = "economy"

    fallback = "minimax-official" if provider == "seedance-official" and _configured("minimax-official") else "demo"
    return RoutingDecision(
        shot_id=shot.id,
        provider=provider,
        model_hint=model_hint,
        requested_capabilities=sorted(set(requested)),
        reasons=reasons,
        fallback_provider=fallback,
    )
