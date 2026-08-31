from __future__ import annotations

import json
import logging
import re
from os import getenv
from typing import Literal
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field

from .brand_bible import bible_constraints
from .schemas import CreativePlan, Project, Shot, StorySequence


DIRECTOR_SYSTEM_PROMPT = """
你是一名生活纪录片导演和编剧。系统的主要创作模式是“观察式生活微纪录片”，不是传统广告片。
你的工作不是罗列产品卖点，而是记录真实人物在真实生活里经历的一段具体过程，让产品成为
事件中的工具或陪伴者。观众应该先相信人物和处境，随后从可见事实中自然理解产品价值。

硬性创作原则：
1. 每支片只有一个清晰主角、一个微小但具体的愿望或困难，以及可感知的变化。
2. 镜头之间必须有因果关系：上一镜头的动作、视线、声音或物体推动下一镜头。
3. 产品在前半段作为生活中的物件出现，不允许第一秒就做悬浮转台或朗读卖点。
4. 卖点只能通过动作和结果被观众看见，不允许角色对镜头讲解功能。
5. 表演克制自然，保留停顿、呼吸、犹豫和不完美的小动作，避免广告式夸张大笑。
6. 旁白是一段连续散文，不是每镜头一句口号；能用画面表达时不说话。
7. 最后一镜才允许简短品牌落点。屏幕文字每镜不超过 10 个汉字。
8. 为跨镜头人物、服装、道具位置、光线方向和色彩写出 continuity_bible。
9. 视频模型提示词必须描述可拍摄的具体动作、机位、光线和持续运动，禁止空泛形容词堆砌。
10. 输出严格 JSON，不要 Markdown，不要附加解释。
11. 场景和产品出现方式必须符合现实生活逻辑，不得把商品藏在食物、垃圾、危险容器或不卫生位置。
12. 儿童产品不得靠近刀具、火源、药品、车流、深水或其他危险物；不得设计可能被模仿的危险动作。
13. 不得凭空发明产品结构。无法确认的外观细节保持中性描述，等待素材分析结果补充。
14. 每支片必须包含纪录片证据：至少一个建立环境的观察镜头、一个真实操作细节、一个可见反馈或结果。
15. 摄影采用自然光、人物视线高度、克制手持或固定观察；禁止无动机环绕、升格炫技、快速推拉和棚拍转台。
16. 声音以同期环境声和动作拟音为主体。允许短暂安静与画外声；音乐不得持续填满全片或替代真实反馈。
17. 旁白采用第三人称观察或第一人称口述，只补充画面无法表达的信息；禁止电视购物腔、连续口号和参数朗读。
18. 产品功能必须有“动作—反馈—人物反应”的证据链。无法视觉或听觉验证的功能不得写入本片。
19. 除每镜头的 prompt 必须使用英文外，标题、故事梗概、动作、旁白、屏幕文字、转场与所有用户可见字段必须使用简体中文。
""".strip()


class DirectorShotDraft(BaseModel):
    """Creative-only shot fields. Runtime state never comes from an LLM."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=2, max_length=80)
    duration: int = Field(ge=2, le=10)
    purpose: str = Field(min_length=4, max_length=180)
    narrative_beat: str = Field(min_length=8, max_length=300)
    visual: str = Field(min_length=12, max_length=500)
    camera: str = Field(min_length=6, max_length=240)
    action: str = Field(min_length=8, max_length=360)
    voiceover: str = Field(max_length=180)
    on_screen_text: str = Field(max_length=20)
    continuity_anchor: str = Field(min_length=8, max_length=300)
    transition: str = Field(min_length=8, max_length=240)
    prompt: str = Field(min_length=80, max_length=2000)
    model_hint: Literal["economy", "story", "premium"]


class DirectorPlanDraft(BaseModel):
    """Strict schema requested from the local or compatible director model."""

    model_config = ConfigDict(extra="forbid")

    product_name: str = Field(min_length=2, max_length=120)
    campaign_idea: str = Field(min_length=4, max_length=120)
    logline: str = Field(min_length=12, max_length=260)
    protagonist: str = Field(min_length=4, max_length=160)
    story_question: str = Field(min_length=8, max_length=220)
    hook: str = Field(min_length=8, max_length=220)
    emotional_arc: str = Field(min_length=8, max_length=220)
    continuity_bible: list[str] = Field(min_length=3, max_length=10)
    narration_script: str = Field(max_length=600)
    visual_language: str = Field(min_length=8, max_length=300)
    music_direction: str = Field(min_length=8, max_length=300)
    call_to_action: str = Field(min_length=2, max_length=40)
    shots: list[DirectorShotDraft] = Field(min_length=2, max_length=12)


def _durations(total: int) -> list[int]:
    if total <= 15:
        return [5, 5, 5]
    if total <= 30:
        return [5, 6, 7, 6, 6]
    if total <= 45:
        return [5, 6, 6, 5, 6, 6, 5, 6]
    if total <= 60:
        return [6] * 10
    count = min(12, max(10, round(total / 6)))
    base = total // count
    values = [base] * count
    values[-1] += total - sum(values)
    return values


def _build_sequences(shots: list[Shot]) -> list[StorySequence]:
    count = len(shots)
    if count <= 3:
        groups = [[shot] for shot in shots]
    elif count <= 6:
        split_a = 2
        split_b = 4 if count >= 5 else 3
        groups = [shots[:split_a], shots[split_a:split_b], shots[split_b:]]
    else:
        first = max(2, count // 3)
        second = max(first + 2, count * 2 // 3)
        groups = [shots[:first], shots[first:second], shots[second:]]
    groups = [group for group in groups if group]
    labels = [
        ("人物与未完成的事", "让观众先关心人物和具体问题", "人物决定开始尝试"),
        ("产品进入行动", "通过真实使用让卖点成为情节结果", "困难被一次具体行动改变"),
        ("变化被看见", "完成人物情绪与关系落点，并自然进入品牌", "人物主动分享结果"),
    ]
    sequences: list[StorySequence] = []
    for index, group in enumerate(groups, start=1):
        title, goal, turning = labels[min(index - 1, len(labels) - 1)]
        sequences.append(StorySequence(
            id=str(uuid4()),
            index=index,
            title=title,
            narrative_goal=goal,
            turning_point=turning,
            continuity_scope="段落内共享人物、商品、场景、光线和动作方向；段落间必须使用有动机的视觉或声音桥。",
            shot_ids=[shot.id for shot in group],
            duration=sum(shot.duration for shot in group),
        ))
    return sequences


def _fallback_plan(project: Project, reason: str = "未配置可用的导演模型") -> CreativePlan:
    """A narrative fallback used when no low-cost director LLM is configured."""
    durations = _durations(project.duration)
    point = project.selling_points[0] if project.selling_points else "用起来更安心"
    second = project.selling_points[1] if len(project.selling_points) > 1 else "让体验更有趣"
    beats = [
        {
            "title": "还差最后一点",
            "purpose": "先让观众理解人物，而不是先介绍商品",
            "narrative_beat": "主角遇到一个很小但真实的困难，安静地再试一次。",
            "visual": f"清晨的客厅里，孩子坐在地毯上试着完成一件小作品。{project.product_name}自然放在手边，没有刻意面向镜头。",
            "camera": "35mm 肩部高度固定中景，先观察两秒，再极慢地向前移动",
            "action": "孩子停下，看看手里的半成品，轻轻呼气，把一块放错的部件拿下来。",
            "voiceover": "有些小小的坚持，不需要谁来催促。",
            "on_screen_text": "再试一次",
            "continuity_anchor": "米色上衣、左侧窗光、商品位于人物右手边、半成品保持同一结构",
            "transition": "孩子低头寻找下一块，视线向右；下一镜从同一视线方向接入手部特写",
            "model_hint": "story",
        },
        {
            "title": "答案在手里发生",
            "purpose": f"通过具体使用过程呈现 {point}，不口头解释",
            "narrative_beat": "产品帮助主角把困难变成一次可以自己完成的尝试。",
            "visual": f"延续同一空间与晨光，手部近景展示孩子使用{project.product_name}继续完成作品，商品形状、颜色和材质必须与参考图一致。",
            "camera": "50mm 手部近景，沿动作方向轻微横移，焦点从部件自然转到孩子的眼神",
            "action": "孩子试放一次没有成功，停顿半秒，换一个方向后准确连接；嘴角出现克制的笑意。",
            "voiceover": f"当每一步都愿意等一等，{point}就藏在自己完成的那一刻。",
            "on_screen_text": point[:10],
            "continuity_anchor": "延续米色上衣与左侧晨光；双手、商品比例、半成品结构和上一镜完全一致",
            "transition": "连接成功的轻响作为声音桥；孩子抬头看向画外，下一镜沿目光切到家长",
            "model_hint": "story",
        },
        {
            "title": "让完成被看见",
            "purpose": f"用人物关系给故事一个落点，并自然留下 {second} 的印象",
            "narrative_beat": "主角不是因为被夸奖才快乐，而是主动把完成的成果分享给重要的人。",
            "visual": f"同一客厅里，孩子把作品轻轻推到家长面前。家长蹲下看细节，没有夸张鼓掌。最后一秒自然带到{project.product_name}与完成的作品。",
            "camera": "35mm 双人中景缓慢后退，最后用一次自然转焦把注意力落到商品和作品",
            "action": "家长先认真看作品，再与孩子交换一个安静的笑容；孩子把作品往前推一点。",
            "voiceover": f"因为真正值得记住的，从来不只是结果。{project.product_name}，陪他慢慢做到。",
            "on_screen_text": "陪他慢慢做到",
            "continuity_anchor": "服装、窗光、地毯、作品结构保持一致；商品仍在孩子右手边",
            "transition": "动作自然停住，环境声保留半秒后淡出，不使用炫技转场",
            "model_hint": "economy",
        },
    ]
    while len(beats) < len(durations):
        beats.insert(-1, {
            "title": "一个被注意到的细节",
            "purpose": "补充人物观察与产品使用的真实细节",
            "narrative_beat": "主角在行动中发现新的可能，让故事继续向前。",
            "visual": f"同一场景中以生活化近景观察{project.product_name}的一个关键细节，人物仍在画面中自然使用。",
            "camera": "65mm 近景，轻微手持呼吸感，不做机械环绕",
            "action": "人物停顿、观察，再继续完成手里的动作。",
            "voiceover": "",
            "on_screen_text": "",
            "continuity_anchor": "严格延续上一镜人物外观、道具位置和光线方向",
            "transition": "以前一动作的完成作为剪辑点，动作方向延续到下一镜",
            "model_hint": "story",
        })

    shots: list[Shot] = []
    for index, (duration, beat) in enumerate(zip(durations, beats), start=1):
        prompt = (
            f"A photorealistic observational micro-documentary featuring {project.product_name}. "
            f"Story beat: {beat['narrative_beat']} Scene: {beat['visual']} "
            f"Camera: {beat['camera']}. Performance and action: {beat['action']} "
            f"Continuity: {beat['continuity_anchor']}. Transition intention: {beat['transition']}. "
            f"Style: {project.style}, observational documentary cinematography, available natural light, restrained natural acting, "
            "real skin texture, physically plausible hands and object interaction, subtle breathing and pauses, "
            "motivated camera movement only, audible real-world action implied, consistent product geometry and character identity. "
            "No glossy commercial staging, no CGI look, no exaggerated smile, no text, no watermark."
        )
        shots.append(Shot(id=str(uuid4()), index=index, duration=duration, prompt=prompt, **beat))

    narration = " ".join(shot.voiceover for shot in shots if shot.voiceover)
    return CreativePlan(
        director_source="fallback",
        director_model="",
        director_note=reason[:240],
        campaign_idea="一次没有被打扰的小小完成",
        logline=f"一个孩子独自完成眼前的小困难，并借由{project.product_name}把成果分享给家人。",
        protagonist="一个认真、略带倔强、不刻意表演的孩子",
        story_question="这一次，他能不能不依赖提醒，自己把它完成？",
        hook="从一个没有成功的小动作开始，让观众先产生关心，再看见产品。",
        emotional_arc="遇到困难 → 安静尝试 → 自己完成 → 分享喜悦",
        continuity_bible=[
            "主角始终穿米色上衣，发型、年龄与面部特征不变",
            "晨光始终从画面左侧进入，色温和阴影方向不变",
            f"{project.product_name}的颜色、比例、结构与参考图严格一致",
            "半成品只能随故事逐步增加，不能在镜头间倒退或突然变化",
            "表演保持克制，避免持续大笑、对镜头展示或电视购物式动作",
        ],
        narration_script=narration,
        visual_language=f"{project.style}的观察式生活纪录片；自然光、人物视线高度、克制手持或固定观察、动作匹配剪辑和声音桥。",
        music_direction="同期环境声和动作拟音优先；音乐只在情绪转折后极轻进入，保留停顿与真实房间底噪，结尾只抬高一个和弦。",
        call_to_action="陪他慢慢做到",
        sequences=_build_sequences(shots),
        shots=shots,
    )


def _fallback_plan(project: Project, reason: str = "未配置可用的导演模型") -> CreativePlan:
    """Provider-neutral fallback used to prove the basic generation chain.

    It intentionally avoids imposing a documentary or glossy-ad style. The
    project's selected style remains the source of truth, while every shot is
    limited to one visible action and one outcome.
    """
    durations = _durations(project.duration)
    points = project.selling_points or ["核心功能"]
    templates = [
        ("建立情境", "人物进入使用场景并注意到产品", "人物停下并看向产品"),
        ("产生需求", "人物面对一个与产品用途相关的小问题", "人物尝试一次但尚未解决"),
        ("使用产品", f"人物使用{project.product_name}的一项核心功能", "人物完成一次清晰操作"),
        ("获得反馈", "产品给出可见或可听的反馈", "人物自然确认反馈结果"),
        ("产品收束", "人物带着结果回到自然生活状态", "镜头停在人物与产品的干净关系画面"),
    ]
    while len(templates) < len(durations):
        templates.insert(-1, (
            "功能细节",
            f"展示{project.product_name}的一个真实使用细节",
            "人物完成一个独立、可观察的动作",
        ))
    templates = templates[: len(durations)]
    shots: list[Shot] = []
    elapsed = 0
    for index, (duration, template) in enumerate(zip(durations, templates), start=1):
        title, state, action = template
        point = points[(index - 1) % len(points)]
        learning_story = any(
            token in f"{project.product_name} {project.product_category}".lower()
            for token in ("英语", "口语", "学习机", "language", "english")
        ) and len(durations) == 5
        learning_voiceovers = [
            "",
            "有些话，他想用英语说出来。",
            "它先听，再给一句简单示范。",
            "跟着练一遍，表达就慢慢清楚了。",
            "把今天的故事，说给家人听。",
        ]
        generic_voiceovers = [
            "",
            "一个具体的小问题，让他停了下来。",
            "试着用一次，变化从动作里发生。",
            "结果被看见，也让选择变得简单。",
            f"{project.product_name}，让日常多一种从容。",
        ]
        voiceovers = learning_voiceovers if learning_story else generic_voiceovers
        shot = Shot(
            id=str(uuid4()),
            index=index,
            duration=duration,
            title=title,
            purpose=f"用一个可见动作推进故事；卖点只由画面证据呈现，不在旁白中罗列：{point[:40]}",
            narrative_beat=state,
            visual=f"在用户提供的场景中，参考人物与{project.product_name}自然同框，保持真实素材身份。",
            camera="人物视线高度的稳定中景或近景，只进行一次有动机的轻微运镜",
            action=action,
            voiceover=voiceovers[index - 1] if index <= len(voiceovers) else "",
            on_screen_text="",
            continuity_anchor="人物外观、服装、产品结构、场景布局、光线方向和镜头轴线保持一致",
            transition="在本镜头动作完成点硬切，下一镜头从新的已审核关键帧开始",
            prompt=(
                f"Realistic live-action product story featuring {project.product_name}. "
                f"Style: {project.style}. Story state: {state}. Only action: {action}. "
                "Natural acting, plausible hands and product geometry, no text, logo or watermark."
            ),
            model_hint="story",
        )
        shots.append(shot)
        elapsed += duration
    narration = " ".join(shot.voiceover for shot in shots if shot.voiceover)
    return CreativePlan(
        director_source="fallback",
        director_model="",
        director_note=reason[:240],
        campaign_idea="一个需求、一次使用、一个可见结果",
        logline=f"人物在真实场景中遇到需求，使用{project.product_name}并获得可见反馈。",
        protagonist="用户提供的参考人物",
        story_question=f"{project.product_name}如何在一次真实使用中解决眼前需求？",
        hook="前3秒同时建立人物需求与产品存在",
        emotional_arc="注意 → 尝试 → 使用 → 反馈 → 收束",
        continuity_bible=[
            "人物脸部、发型、年龄感与服装严格遵循参考图",
            f"{project.product_name}的轮廓、颜色、材质、配件与关键可见结构严格遵循真实商品图",
            "场景布局、主要家具、光线方向与镜头轴线保持一致",
            "每个镜头只完成一个主要动作，不生成字幕、Logo或水印",
        ],
        narration_script=narration,
        visual_language=f"{project.style}；真实材质、自然表演、清晰主体和简洁镜头运动。",
        music_direction="轻量背景音乐服务叙事，保留环境声与动作拟音，旁白保持清晰。",
        call_to_action="让每一次表达，都更自然",
        sequences=_build_sequences(shots),
        shots=shots,
    )


PLACEHOLDER_PRODUCT_NAMES = {"待定义商品", "未命名商品", "商品"}


def _effective_product_name(project: Project) -> str:
    product_name = project.product_name.strip()
    if product_name in PLACEHOLDER_PRODUCT_NAMES:
        return "上传参考图中的商品（名称未填写，以视觉事实描述为准）"
    return product_name


def _director_request(project: Project) -> str:
    user_brief = project.brief.strip()
    product_name = _effective_product_name(project)
    return json.dumps({
        "任务": "为以下产品创作一支具有完整因果关系、可逐镜头生成的观察式生活微纪录片。",
        "项目": {
            "产品": product_name,
            "品类": project.product_category,
            "产品尺度类型": project.product_scale,
            "产品真实尺寸": project.product_dimensions or "未填写；仅依据商品参考图判断，不得随意放大",
            "平台": project.platform,
            "总时长": project.duration,
            "画幅": project.aspect_ratio,
            "风格": project.style,
            "受众": project.audience,
            "卖点": project.selling_points,
            "视觉模型从上传素材提取的事实（强约束）": project.asset_facts,
            "用户故事要求（可选）": user_brief or "用户未指定故事；请根据真实素材、商品卖点、受众、平台和所选风格自主构思。",
            "故事创作权限": (
                "用户已填写故事要求：将其作为强约束，在不违反素材事实和安全规则的前提下忠实执行。"
                if user_brief else
                "用户未填写故事要求：由你自主决定人物目标、触发事件、行动、可见结果和情绪落点；避免空泛模板和卖点罗列。"
            ),
            "已批准素材事实（强约束，不得改写或补造）": bible_constraints(project),
        },
        "输出要求": {
            "故事中使用的产品身份": product_name,
            "所有JSON Schema字段都必须填写，不允许使用空字符串逃避故事设计": True,
            "所有镜头duration之和必须严格等于项目总时长": True,
            "15秒使用3个镜头，30秒使用5个镜头，镜头之间必须具有明确因果关系": True,
            "每个镜头都必须给出可执行的英文视频模型prompt": True,
            "语言": "除 prompt 使用英文外，其余所有脚本字段必须使用自然、清楚的简体中文",
            "prompt必须包含具体动作、机位、光线、表演和连续性约束": True,
            "logline、protagonist、story_question必须具体描述本片人物与事件": True,
            "禁止替换成其他商品、品牌或示例故事": True,
            "产品在人物和场景中的比例必须服从真实尺寸，不得变成大型道具": True,
            "故事中的商品外观、人物、场景和动作条件必须能追溯到视觉素材事实": True,
            "用户故事要求为空时必须主动完成原创故事设计，不能追问用户补写脚本": True,
            "儿童与家庭品类必须避开刀具、火源、污染和危险模仿动作": True,
            "商品出现方式必须自然、卫生、符合真实生活逻辑": True,
            "必须包含环境建立、真实操作、可见反馈或结果三类纪录片证据": True,
            "功能必须形成动作、反馈、人物反应证据链，不能只靠旁白宣称": True,
            "同期环境声和动作拟音优先，音乐不得填满全片": True,
            "顶层字段": list(DirectorPlanDraft.model_fields),
            "每个镜头字段": list(DirectorShotDraft.model_fields),
            "model_hint": "普通叙事镜头用story，产品静物和低动作镜头用economy，只有复杂多人互动才用premium",
        },
    }, ensure_ascii=False)


def _clean_json_content(content: str) -> str:
    value = content.strip()
    value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s*```$", "", value)
    return value.strip()


def _flatten_text(value: object) -> str:
    """Convert common Qwen structured prose into schema-safe readable text."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return "；".join(
            f"{key}：{_flatten_text(item)}" for key, item in value.items()
            if _flatten_text(item)
        )
    if isinstance(value, list):
        return "；".join(filter(None, (_flatten_text(item) for item in value)))
    if value is None:
        return ""
    return str(value)


def _fit_text(value: object, max_length: int) -> str:
    """Keep a usable model phrase inside a presentation-field limit."""
    text = _flatten_text(value)
    if len(text) <= max_length:
        return text
    shortened = text[:max_length].rstrip(" ,，。;；:：-—")
    # Avoid cutting an English word in half when a useful phrase remains.
    if " " in shortened and max_length < len(text) and text[max_length].isalnum():
        word_boundary = shortened.rfind(" ")
        if word_boundary >= max_length // 2:
            shortened = shortened[:word_boundary].rstrip()
    return shortened


def _parse_director_draft(content: str) -> DirectorPlanDraft:
    """Tolerate prose-shaped fields while preserving strict field coverage."""
    payload = json.loads(_clean_json_content(content))
    if not isinstance(payload, dict):
        raise ValueError("导演模型顶层结果必须是 JSON object")
    allowed_plan_fields = set(DirectorPlanDraft.model_fields)
    payload = {key: value for key, value in payload.items() if key in allowed_plan_fields}
    continuity = payload.get("continuity_bible", [])
    if isinstance(continuity, dict):
        continuity = list(continuity.values())
    if not isinstance(continuity, list):
        continuity = [continuity]
    payload["continuity_bible"] = [
        text for text in (_flatten_text(item) for item in continuity) if text
    ][:10]
    text_limits = {
        "product_name": 120, "campaign_idea": 120, "logline": 260,
        "protagonist": 160, "story_question": 220, "hook": 220,
        "emotional_arc": 220, "narration_script": 600,
        "visual_language": 300, "music_direction": 300,
        "call_to_action": 40,
    }
    for field, max_length in text_limits.items():
        if field in payload:
            payload[field] = _fit_text(payload[field], max_length)
    shot_text_limits = {
        "title": 80, "purpose": 180, "narrative_beat": 300,
        "visual": 500, "camera": 240, "action": 360,
        "voiceover": 180, "on_screen_text": 20,
        "continuity_anchor": 300, "transition": 240, "prompt": 2000,
    }
    for shot in payload.get("shots", []):
        if not isinstance(shot, dict):
            continue
        allowed_shot_fields = set(DirectorShotDraft.model_fields)
        for key in list(shot):
            if key not in allowed_shot_fields:
                shot.pop(key)
        for field, max_length in shot_text_limits.items():
            if field in shot:
                shot[field] = _fit_text(shot[field], max_length)
        hint = str(shot.get("model_hint", "story")).strip().lower()
        shot["model_hint"] = hint if hint in {"economy", "story", "premium"} else "story"
        # Some otherwise valid Qwen plans use terse film terms such as
        # "硬切". Preserve the choice, but expand it into an executable
        # continuity instruction instead of discarding the entire plan.
        if len(str(shot.get("transition", ""))) < 8:
            choice = str(shot.get("transition", "")).strip() or "自然切换"
            shot["transition"] = f"{choice}，延续上一镜的动作、视线与声音方向"
    return DirectorPlanDraft.model_validate(payload)


def _is_local_ollama(base_url: str) -> bool:
    parsed = urlparse(base_url)
    return parsed.hostname in {"127.0.0.1", "localhost", "::1"} and parsed.port == 11434


def _director_source(base_url: str) -> Literal["ollama", "openai_compatible"]:
    return "ollama" if _is_local_ollama(base_url) else "openai_compatible"


def _director_headers(api_key: str) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _strict_response_format() -> dict[str, object]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "frameflow_director_plan",
            "strict": True,
            "schema": DirectorPlanDraft.model_json_schema(),
        },
    }


def _draft_to_plan(
    draft: DirectorPlanDraft,
    *,
    project: Project,
    source: Literal["ollama", "openai_compatible"],
    model: str,
    note: str,
) -> CreativePlan:
    expected_product_name = _effective_product_name(project)
    # When the user has not named the product, visual facts—not a UI
    # placeholder—define its identity. Requiring the model to echo
    # “待定义商品” made every otherwise valid draft fail validation.
    if (
        project.product_name.strip() not in PLACEHOLDER_PRODUCT_NAMES
        and draft.product_name.strip() != expected_product_name
    ):
        raise ValueError(
            f"导演写错产品：返回“{draft.product_name}”，应为“{expected_product_name}”"
        )
    duration_sum = sum(shot.duration for shot in draft.shots)
    if duration_sum != project.duration:
        raise ValueError(f"镜头总时长为 {duration_sum} 秒，应为 {project.duration} 秒")

    expected_count = len(_durations(project.duration))
    if len(draft.shots) != expected_count:
        raise ValueError(f"镜头数量为 {len(draft.shots)}，当前时长应生成 {expected_count} 个镜头")

    visible_script = " ".join([
        draft.campaign_idea, draft.logline, draft.protagonist,
        draft.story_question, draft.hook, draft.emotional_arc,
        draft.narration_script, draft.visual_language,
        draft.music_direction, draft.call_to_action,
        *draft.continuity_bible,
        *(value for shot in draft.shots for value in (
            shot.title, shot.purpose, shot.narrative_beat, shot.visual,
            shot.camera, shot.action, shot.voiceover, shot.on_screen_text,
            shot.continuity_anchor, shot.transition,
        )),
    ])
    if len(re.findall(r"[\u4e00-\u9fff]", visible_script)) < 20:
        raise ValueError("脚本面向用户的字段必须使用简体中文，只有视频模型 prompt 使用英文")

    shots = [
        Shot(id=str(uuid4()), index=index, **shot.model_dump())
        for index, shot in enumerate(draft.shots, start=1)
    ]
    cta = draft.call_to_action.strip()
    if any(token in cta for token in ("屏幕", "浮现", "字幕", "小字", "画面中央", "：", "“", "”")):
        cta = f"和{project.product_name}一起慢慢搭"
    return CreativePlan(
        director_source=source,
        director_model=model,
        director_note=note,
        **draft.model_dump(exclude={"product_name", "shots", "call_to_action"}),
        call_to_action=cta[:40],
        sequences=_build_sequences(shots),
        shots=shots,
    )


async def _request_director_draft(
    *,
    base_url: str,
    api_key: str,
    model: str,
    project: Project,
    correction: str = "",
    strict_schema: bool = True,
) -> tuple[DirectorPlanDraft, int]:
    messages = [
        {"role": "system", "content": DIRECTOR_SYSTEM_PROMPT},
        {"role": "user", "content": _director_request(project)},
    ]
    if correction:
        messages.append({
            "role": "user",
            "content": (
                "上一版未通过服务端校验。请重新生成完整JSON，不要解释。"
                f"必须修正的问题：{correction[:500]}"
            ),
        })
    payload: dict[str, object] = {
        "model": model,
        "temperature": 0.2 if correction else 0.4,
        "stream": False,
        "response_format": _strict_response_format() if strict_schema else {"type": "json_object"},
        "messages": messages,
    }
    reasoning_effort = getenv("DIRECTOR_REASONING_EFFORT", "").strip().lower()
    # Ark's OpenAI-compatible Chat endpoint currently rejects the
    # reasoning_effort extension even though DashScope accepts it.
    if "ark.cn-beijing.volces.com" in base_url:
        payload["thinking"] = {"type": "disabled"}
        payload["max_tokens"] = 3200
    elif reasoning_effort in {"none", "low", "medium", "high"}:
        payload["reasoning_effort"] = reasoning_effort
        payload["max_tokens"] = 3200
    timeout = max(30.0, float(getenv("DIRECTOR_TIMEOUT_SECONDS", "90")))
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        response = await client.post(
            f"{base_url}/chat/completions",
            headers=_director_headers(api_key),
            json=payload,
        )
        response.raise_for_status()
    message = response.json()["choices"][0]["message"]
    content = str(message.get("content") or "")
    if not content:
        raise ValueError("导演模型返回了空内容")
    reasoning_chars = len(str(message.get("reasoning") or ""))
    return _parse_director_draft(content), reasoning_chars


async def generate_creative_plan(project: Project) -> CreativePlan:
    """Generate a validated plan through Ollama or another compatible LLM."""
    api_key = getenv("DIRECTOR_API_KEY", "").strip()
    base_url = getenv("DIRECTOR_API_BASE", "").strip().rstrip("/")
    model = getenv("DIRECTOR_MODEL", "").strip()
    if not base_url or not model:
        return _fallback_plan(project)

    source = _director_source(base_url)
    last_error = ""
    strict_schema = True
    for attempt in range(2):
        try:
            draft, reasoning_chars = await _request_director_draft(
                base_url=base_url,
                api_key=api_key,
                model=model,
                project=project,
                correction=last_error if attempt else "",
                strict_schema=strict_schema,
            )
            note = f"严格Schema校验通过；模型推理字段 {reasoning_chars} 字符"
            plan = _draft_to_plan(
                draft,
                project=project,
                source=source,
                model=model,
                note=note,
            )
            return ensure_narrated_ad_plan(project, plan)
        except httpx.HTTPStatusError as exc:
            last_error = f"接口返回 HTTP {exc.response.status_code}"
            if attempt == 0 and exc.response.status_code in {400, 404, 422}:
                strict_schema = False
                continue
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            last_error = str(exc)
            # A malformed creative response is cheaper and faster to replace
            # with the deterministic director than to make another full LLM
            # request. Only an API-level schema incompatibility above receives
            # the one compatibility retry.
            break

    reason = f"{model} 导演失败，已使用内置导演：{last_error or '未知错误'}"
    logging.getLogger(__name__).error("Director fallback for project %s: %s", project.id, reason)
    return ensure_narrated_ad_plan(project, _fallback_plan(project, reason=reason))


def ensure_narrated_ad_plan(project: Project, plan: CreativePlan) -> CreativePlan:
    """Guarantee usable narration/subtitles unless silence was explicitly requested."""
    request_text = f"{project.style} {project.brief}".lower()
    silent_requested = any(token in request_text for token in (
        "纯视觉", "无旁白", "不要旁白", "silent film", "no voiceover",
    ))
    spoken = [shot for shot in plan.shots if shot.voiceover.strip()]
    if silent_requested or len(spoken) >= min(2, len(plan.shots)):
        return plan
    lines = ["" for _ in plan.shots]
    if len(lines) == 1:
        lines[0] = f"{project.product_name}，让一次真实使用，自然说明它的价值。"
    else:
        lines[max(0, len(lines) // 2 - 1)] = "一次次尝试，让想法慢慢成形。"
        lines[-1] = "动手搭建，让想法成形。"
    repaired = [
        shot.model_copy(update={"voiceover": lines[index], "on_screen_text": ""})
        for index, shot in enumerate(plan.shots)
    ]
    return plan.model_copy(update={
        "shots": repaired,
        "narration_script": "".join(line for line in lines if line),
        "director_note": f"{plan.director_note}；已执行广告音频完整性校验并补充自然旁白与对应字幕。",
    })


def director_mode() -> str:
    configured = bool(getenv("DIRECTOR_API_BASE", "").strip() and getenv("DIRECTOR_MODEL", "").strip())
    return "llm" if configured else "story_fallback"


def repair_editorial_plan(project: Project) -> CreativePlan:
    """Rewrite only narration/CTA while preserving every paid shot identity.

    This is intentionally deterministic so an existing project can be fixed
    and recomposed without regenerating keyframes or video clips.
    """
    if not project.creative_plan:
        raise ValueError("项目还没有可修复的创意方案。")
    if len(project.creative_plan.shots) == 3:
        cleared = project.creative_plan.model_copy(update={
            "shots": [
                shot.model_copy(update={"voiceover": "", "on_screen_text": ""})
                for shot in project.creative_plan.shots
            ],
            "narration_script": "",
        })
        return ensure_narrated_ad_plan(project, cleared)
    shots = list(project.creative_plan.shots)
    learning_story = any(
        token in f"{project.product_name} {project.product_category}".lower()
        for token in ("英语", "口语", "学习机", "language", "english")
    )
    if learning_story and len(shots) == 5:
        lines = [
            "",
            "有些话，他想用英语说出来。",
            "它先听，再给一句简单示范。",
            "跟着练一遍，表达就慢慢清楚了。",
            "把今天的故事，说给家人听。",
        ]
        cta = "让每一次表达，都更自然"
    else:
        lines = ["" for _ in shots]
        narrative = [
            "一个具体的小问题，让他停了下来。",
            "试着用一次，变化从动作里发生。",
            "结果被看见，也让选择变得简单。",
            f"{project.product_name}，让日常多一种从容。",
        ]
        for offset, text in enumerate(narrative, start=max(1, len(shots) - len(narrative))):
            if offset < len(lines):
                lines[offset] = text
        cta = "从一个真实日常开始"
    repaired = [
        shot.model_copy(update={"voiceover": lines[index], "on_screen_text": ""})
        for index, shot in enumerate(shots)
    ]
    return project.creative_plan.model_copy(update={
        "shots": repaired,
        "narration_script": "".join(shot.voiceover for shot in repaired if shot.voiceover),
        "call_to_action": cta,
        "director_note": "已执行编辑修复：故事旁白、自然语速、语义字幕与单层文字。",
    })


def repair_visual_consistency_plan(project: Project) -> CreativePlan:
    """Strengthen continuity without injecting facts from another project."""
    if not project.creative_plan or len(project.creative_plan.shots) != 5:
        raise ValueError("当前视觉修复器仅支持五镜头项目。")
    facts = bible_constraints(project)
    identity_lock = "；".join(facts) if facts else (
        "人物、服装、商品外形与场景必须逐镜遵循本项目上传参考图"
    )
    repaired = []
    for shot in project.creative_plan.shots:
        repaired.append(shot.model_copy(update={
            "visual": f"{shot.visual}。事实锁定：{identity_lock}",
            "camera": f"{shot.camera}；保持同一轴线与已批准光线方向，不无动机切换地点。",
            "continuity_anchor": f"{shot.continuity_anchor}；{identity_lock}",
            "transition": f"{shot.transition}；下一镜延续同一人物、场景和商品身份。",
        }))
    return project.creative_plan.model_copy(update={
        "shots": repaired,
        "continuity_bible": facts or project.creative_plan.continuity_bible,
    })
