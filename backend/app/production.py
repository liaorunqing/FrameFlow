from __future__ import annotations

from os import getenv
import re
from typing import Literal

from pydantic import BaseModel, Field

from .brand_bible import bible_constraints
from .schemas import AssetKind, Project, Shot


class CostEstimate(BaseModel):
    currency: Literal["CNY"] = "CNY"
    keyframe_count: int
    keyframe_unit_price: float
    keyframe_cost: float
    estimated_video_tokens: int
    video_price_per_million_tokens: float
    video_cost: float
    estimated_total: float
    note: str


class KeyframeSpec(BaseModel):
    id: str
    boundary_index: int
    role: Literal["opening", "bridge", "closing"]
    prompt: str
    reference_asset_ids: list[str]
    required_subjects: list[str]
    generation_strategy: Literal["seedream_multi_reference"]
    status: Literal["planned", "generating", "ready", "failed"] = "planned"
    output_url: str | None = None


class ShotProductionSpec(BaseModel):
    shot_id: str
    shot_index: int
    title: str
    duration: int
    first_frame_id: str
    last_frame_id: str
    first_frame_runtime_policy: Literal["planned", "prefer_previous_actual_last_frame"]
    video_prompt: str
    continuity_checks: list[str]
    edit_transition: str
    risk_level: Literal["low", "medium", "high"] = "medium"
    recommended_variants: int = Field(default=1, ge=1, le=3)
    prompt_sections: dict[str, str] = Field(default_factory=dict)
    reference_strategy: list[str] = Field(default_factory=list)
    return_last_frame: bool = True


class ProductionPlan(BaseModel):
    project_id: str
    script_version: str = ""
    source_asset_ids: list[str] = Field(default_factory=list)
    pipeline_version: str = "story-boundary-v1"
    strategy: Literal["shared_boundary_keyframes"] = "shared_boundary_keyframes"
    keyframe_model: str
    video_model: str
    review_required_before_billing: bool = True
    keyframes: list[KeyframeSpec]
    shots: list[ShotProductionSpec]
    postproduction_rules: list[str]
    cost: CostEstimate
    budget_ceiling_cny: float = 30.0


def _asset_labels(project: Project) -> tuple[list[str], list[str]]:
    ids = [asset.id for asset in project.assets]
    present = {asset.kind for asset in project.assets}
    subjects: list[str] = []
    if AssetKind.product in present:
        subjects.append(f"商品：{project.product_name}，外形、颜色、比例和材质严格保持参考图一致")
    if AssetKind.character in present:
        subjects.append("人物：身份、脸部、发型、年龄、服装与参考图严格一致")
    if AssetKind.scene in present:
        subjects.append("场景：空间布局、家具、光线方向与参考图一致")
    if AssetKind.brand in present:
        subjects.append("品牌素材只作为识别参考，Logo 与文字将在后期叠加，不在关键帧中重绘")
    subjects.extend(bible_constraints(project))
    return ids, subjects


def _approved_subject_facts(project: Project, subject: str) -> tuple[list[str], list[str]]:
    """Return only project-scoped facts; never borrow facts from a prior product."""
    bible = project.brand_bible
    if not bible or bible.status != "approved":
        return [], []
    group = getattr(bible, subject)
    facts = [value.strip() for value in group.immutable_features if value.strip()]
    prohibitions = [value.strip() for value in group.prohibited_changes if value.strip()]
    return facts, prohibitions


def _product_lock(project: Project) -> str:
    facts, prohibitions = _approved_subject_facts(project, "product")
    fact_text = "; ".join(facts) if facts else (
        "use the uploaded product reference as the sole visual truth for silhouette, "
        "color, material, components, accessories and proportions"
    )
    forbidden = "; ".join(prohibitions) if prohibitions else (
        "do not change product category or core structure and do not invent text"
    )
    return (
        f"Keep {project.product_name} identical to this project's approved reference. "
        f"Visible facts: {fact_text}. Prohibited changes: {forbidden}. "
        "Do not invent dimensions, screens, lights, controls, accessories or functions "
        "that are not visible in, or approved for, this project."
    )


def _assert_project_scoped_prompts(project: Project, prompts: list[str]) -> None:
    """Fail closed when legacy product facts leak into a different project."""
    approved, _ = _approved_subject_facts(project, "product")
    approved_text = " ".join([*approved, project.product_dimensions]).lower()
    combined = " ".join(prompts).lower()
    measurements = set(re.findall(r"\b\d+(?:\.\d+)?\s*(?:cm|mm)\b", combined))
    unsupported = sorted(value for value in measurements if value not in approved_text)
    if unsupported:
        raise ValueError(
            "生产提示词包含 Brand Bible 未批准的商品尺寸：" + ", ".join(unsupported)
        )


def _opening_prompt(project: Project, shot: Shot, subjects: list[str]) -> str:
    return (
        f"生成一张观察式生活纪录片的开场关键帧，画幅 {project.aspect_ratio}。"
        f"故事此刻尚未开始行动：{shot.narrative_beat}。画面：{shot.visual}。"
        f"机位与构图：{shot.camera}。必须保留可继续表演的自然姿态，人物不要看镜头，"
        f"不要悬浮陈列商品，不要广告文字，不要水印。连续性要求：{'；'.join(subjects)}。"
    )


def _bridge_prompt(
    project: Project,
    previous: Shot,
    following: Shot | None,
    subjects: list[str],
) -> str:
    next_clause = (
        f"同时为下一镜头留下可自然继续的动作与视线：{following.narrative_beat}。"
        if following
        else "这是故事完成后的安静落点，人物动作自然停住，保留真实环境状态。"
    )
    return (
        f"生成一张观察式生活纪录片的镜头边界关键帧，画幅 {project.aspect_ratio}。"
        f"上一动作刚刚完成：{previous.action}。{next_clause}"
        f"画面必须像连续拍摄中的真实一帧，不是海报或商品棚拍。"
        f"沿用连续性：{previous.continuity_anchor}；{'；'.join(subjects)}。"
        "人物手部和商品接触符合物理规律，不新增商品结构，不生成文字、Logo 或水印。"
    )


def _shot_risk(shot: Shot) -> tuple[Literal["low", "medium", "high"], int]:
    text = " ".join([shot.action, shot.visual, shot.narrative_beat]).lower()
    high_risk = ("触", "拿", "取下", "翻", "手", "递", "组装", "屏幕", "跟读")
    medium_risk = ("人物", "女孩", "孩子", "说", "表情", "走", "转焦", "视线")
    if any(word in text for word in high_risk):
        return "high", 2
    if any(word in text for word in medium_risk):
        return "medium", 2
    return "low", 1


def _prompt_sections(project: Project, shot: Shot) -> dict[str, str]:
    return {
        "story_state": shot.narrative_beat,
        "single_action": shot.action,
        "camera": shot.camera,
        "documentary_evidence": "镜头必须记录一个可观察事实：环境状态、真实操作、产品反馈或人物对反馈的自然反应。",
        "performance": "人物像被摄影机偶然观察到一样行动，保留停顿、呼吸、纠正动作和画外注意力，不看镜头、不展示商品。",
        "sound_intent": "动作必须有可供后期同步的明确声源，如脚步、衣料、按键、放置、翻页或房间环境声。",
        "continuity": shot.continuity_anchor,
        "product_lock": (
            f"保持 {project.product_name} 与批准的真实商品参考完全一致：轮廓、颜色分区、"
            "材质、屏幕脸、附件位置和相对比例均不得改变。"
        ),
        "physics": "人物只完成一个主要动作；手指数量正常，接触点清楚，商品不融化、不增生、不悬浮。",
        "handoff": shot.transition,
        "negative": "不要字幕、可读文字、Logo、水印、第二件商品、额外人物、直视镜头或夸张表演。",
    }


def _video_prompt(project: Project, shot: Shot) -> tuple[str, dict[str, str]]:
    sections = _prompt_sections(project, shot)
    prompt = (
        f"观察式生活微纪录片，{project.aspect_ratio}，自然光和人物视线高度。"
        f"故事状态：{sections['story_state']}。"
        f"本镜头唯一主要动作：{sections['single_action']}。"
        f"摄影机：{sections['camera']}。"
        f"纪录片证据：{sections['documentary_evidence']}。"
        f"人物状态：{sections['performance']}。"
        f"同期声意图：{sections['sound_intent']}。"
        f"连续性：{sections['continuity']}。"
        f"商品身份锁定：{sections['product_lock']}。"
        f"物理约束：{sections['physics']}。"
        "首帧是精确动作起点，尾帧是精确动作落点；中间只做因果连续的自然表演。"
        f"剪辑交接：{sections['handoff']}。"
        f"禁止项：{sections['negative']}"
    )
    return prompt, sections


def _estimate_cost(project: Project, keyframe_count: int) -> CostEstimate:
    # Calibrated with the successful local 5 s / 1080p Seedance 1.0 Pro task:
    # 246,840 tokens, or 49,368 tokens per generated second.
    video_tokens = project.duration
    keyframe_unit = float(getenv("ARK_SEEDREAM_PRICE_PER_IMAGE", "0.22"))
    minimax_6s = float(getenv("MINIMAX_FAST_6S_ESTIMATE_CNY", "1.35"))
    video_rate = round(minimax_6s / 6 * 1_000_000, 4)
    keyframe_cost = keyframe_count * keyframe_unit
    video_cost = len(project.creative_plan.shots if project.creative_plan else []) * minimax_6s
    return CostEstimate(
        keyframe_count=keyframe_count,
        keyframe_unit_price=keyframe_unit,
        keyframe_cost=round(keyframe_cost, 4),
        estimated_video_tokens=video_tokens,
        video_price_per_million_tokens=video_rate,
        video_cost=round(video_cost, 4),
        estimated_total=round(keyframe_cost + video_cost, 4),
        note="视频 token 按本账号一次 5 秒 1080p 实测线性估算；实际费用受分辨率、套餐折扣和免费额度影响。",
    )


def _opening_prompt(project: Project, shot: Shot, subjects: list[str]) -> str:
    """Provider-neutral opening frame prompt for the foundational pipeline."""
    constraints = "; ".join(subjects)
    return (
        f"Realistic live-action product story opening keyframe, {project.aspect_ratio}. "
        f"Story state: {shot.narrative_beat}. Visual: {shot.visual}. "
        f"Camera and composition: {shot.camera}. Freeze the instant immediately "
        "before the single action begins. Natural light, plausible hands, no pose. "
        f"Identity constraints: {constraints}. Product lock: {_product_lock(project)} "
        "Compose natively for the full delivery canvas: no letterbox, blurred padding, split panel, "
        "portrait card or pasted-photo collage. No readable text, logo or watermark."
    )


def _bridge_prompt(
    project: Project,
    previous: Shot,
    following: Shot | None,
    subjects: list[str],
) -> str:
    """Create an approved visual boundary without inheriting generated drift."""
    next_state = following.narrative_beat if following else "a clean product-story ending"
    continuity = previous.continuity_anchor
    if "阳光" in continuity and "顶部" in continuity:
        continuity = (
            "保持已批准场景的自然光方向；商品所有表面曝光正常、亮度均匀，"
            "没有高光溢出、自发光或发光顶部"
        )
    constraints = "; ".join(subjects)
    return (
        f"Realistic live-action product story boundary keyframe, {project.aspect_ratio}. "
        f"The prior action has just completed: {previous.action}. "
        f"Prepare the next state: {next_state}. Continuity: {continuity}. "
        "Use the uploaded product, character and scene references as identity truth. "
        f"Identity constraints: {constraints}. Match the opening anchor's exact same person, "
        "hair, face, age, clothing, room, furniture, light direction and product-to-hand scale. "
        f"Product lock: {_product_lock(project)} Every product component is passive and non-emissive: "
        "no glowing block, internal light, halo, magical highlight or illuminated material unless "
        "the approved product facts explicitly require it. Plausible "
        "contact and anatomy. Compose natively for the full delivery canvas with no letterbox, "
        "blurred padding or pasted-photo collage. No readable text, duplicate product, extra device, "
        "logo or watermark. Final exposure rule: every wooden surface must remain normally exposed "
        "with visible grain; absolutely no glowing, luminous, white-hot or overexposed block."
    )


def _prompt_sections(project: Project, shot: Shot) -> dict[str, str]:
    """Small executable prompt contract: one shot, one action, one outcome."""
    return {
        "story_state": shot.narrative_beat,
        "single_action": shot.action,
        "camera": shot.camera,
        "performance": "Natural restrained acting; plausible pauses, eye lines and hand contact.",
        "continuity": shot.continuity_anchor,
        "product_lock": _product_lock(project),
        "physics": "Perform only one primary action; normal hands and contact; no morphing or duplication.",
        "handoff": shot.transition,
        "negative": "No unapproved component, size drift, extra device, subtitles, readable text, logo, watermark, extra people, duplicate product or camera gaze.",
    }


def _video_prompt(project: Project, shot: Shot) -> tuple[str, dict[str, str]]:
    sections = _prompt_sections(project, shot)
    prompt = (
        f"Realistic live-action product story video, {project.aspect_ratio}, natural light. "
        f"Story state: {sections['story_state']}. "
        f"Only action: {sections['single_action']}. "
        f"Camera: {sections['camera']}. Performance: {sections['performance']} "
        f"Continuity: {sections['continuity']}. Product identity: {sections['product_lock']} "
        f"Product scale: {project.product_scale}; real dimensions: {project.product_dimensions or 'follow the reference image, never oversized'}. "
        f"Physics: {sections['physics']} End-state handoff: {sections['handoff']} "
        f"Forbidden: {sections['negative']}"
    )
    return prompt, sections


def build_production_plan(project: Project) -> ProductionPlan:
    if not project.creative_plan or not project.creative_plan.shots:
        raise ValueError("请先生成并审核故事分镜，再创建生产计划。")

    asset_ids, subjects = _asset_labels(project)
    shots = project.creative_plan.shots
    keyframes: list[KeyframeSpec] = [
        KeyframeSpec(
            id="kf-000",
            boundary_index=0,
            role="opening",
            prompt=_opening_prompt(project, shots[0], subjects),
            reference_asset_ids=asset_ids,
            required_subjects=subjects,
            generation_strategy="seedream_multi_reference",
        )
    ]
    for index, shot in enumerate(shots, start=1):
        following = shots[index] if index < len(shots) else None
        keyframes.append(KeyframeSpec(
            id=f"kf-{index:03d}",
            boundary_index=index,
            role="closing" if following is None else "bridge",
            prompt=_bridge_prompt(project, shot, following, subjects),
            reference_asset_ids=asset_ids,
            required_subjects=subjects,
            generation_strategy="seedream_multi_reference",
        ))

    production_shots: list[ShotProductionSpec] = []
    for index, shot in enumerate(shots, start=1):
        risk_level, recommended_variants = _shot_risk(shot)
        video_prompt, prompt_sections = _video_prompt(project, shot)
        production_shots.append(ShotProductionSpec(
            shot_id=shot.id,
            shot_index=shot.index,
            title=shot.title,
            duration=shot.duration,
            first_frame_id=keyframes[index - 1].id,
            last_frame_id=keyframes[index].id,
            first_frame_runtime_policy="planned",
            video_prompt=video_prompt,
            continuity_checks=[
                "人物身份、脸部、发型和服装一致",
                f"{project.product_name} 的颜色、比例、结构和材质一致",
                "动作方向、视线方向、道具位置和光线方向连续",
                "手部与商品交互符合物理规律",
            ],
            edit_transition=shot.transition,
            risk_level=risk_level,
            recommended_variants=recommended_variants,
            prompt_sections=prompt_sections,
            reference_strategy=[
                "approved_product_identity_reference",
                "approved_story_boundary_frame",
                "opening_boundary_frame" if index == 1 else "approved_bridge_boundary_frame",
            ],
        ))

    keyframe_model = getenv("ARK_IMAGE_MODEL", "doubao-seedream-5-0-lite-260128").strip()
    video_model = getenv("MINIMAX_VIDEO_MODEL", "MiniMax-Hailuo-2.3-Fast").strip()
    plan = ProductionPlan(
        project_id=project.id,
        script_version=project.creative_plan.version_id,
        source_asset_ids=list(project.creative_plan.source_asset_ids),
        keyframe_model=keyframe_model,
        video_model=video_model,
        keyframes=keyframes,
        shots=production_shots,
        postproduction_rules=[
            "Logo、品牌文字、价格和 CTA 只在 FFmpeg 后期叠加，避免生成模型改写文字。",
            "优先使用动作匹配硬切；仅在时间跳跃时使用 4–8 帧短叠化，避免重影。",
            "同期环境声与动作拟音优先，旁白只补充画面无法表达的信息；音乐不得持续填满整片。",
            "旁白、环境声和音乐按整片时间轴统一制作，不使用各视频片段自带的独立音轨。",
            "低风险镜头生成1个候选；人物或商品交互镜头建议2个候选，但付费候选必须逐个记账并经过预算门控。",
        ],
        cost=_estimate_cost(project, len(keyframes)),
        budget_ceiling_cny=float(getenv("RESEARCH_AD_BUDGET_CNY", "30")),
    )
    _assert_project_scoped_prompts(
        project,
        [frame.prompt for frame in plan.keyframes]
        + [shot.video_prompt for shot in plan.shots],
    )
    return plan
