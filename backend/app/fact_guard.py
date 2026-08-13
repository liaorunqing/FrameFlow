"""Deterministic factual gates used before any billable generation step."""

from __future__ import annotations

from .schemas import BrandBible, CreativePlan, Project


FORBIDDEN_CLAIMS = (
    "助眠", "睡眠", "入睡", "卧室", "治疗", "疗愈", "治愈", "安全保障",
    "insomnia", "sleep", "bedroom", "medical", "therapeutic", "therapy",
)


def bible_conflicts(bible: BrandBible) -> list[str]:
    """Return unresolved conflicts embedded in the Brand Bible itself."""
    return list(dict.fromkeys(item.strip() for item in bible.fact_conflicts if item.strip()))


def plan_conflicts(project: Project, plan: CreativePlan | None = None) -> list[str]:
    """Conservative checks for unsupported claims and prohibited setting changes."""
    plan = plan or project.creative_plan
    if not plan:
        return ["尚未冻结脚本与分镜。"]
    corpus = " ".join([
        plan.campaign_idea, plan.logline, plan.protagonist, plan.story_question,
        plan.hook, plan.emotional_arc, plan.narration_script, plan.call_to_action,
        *[" ".join([shot.title, shot.visual, shot.action, shot.voiceover, shot.prompt]) for shot in plan.shots],
    ]).lower()
    conflicts: list[str] = []
    for phrase in FORBIDDEN_CLAIMS:
        if phrase.lower() in corpus:
            conflicts.append(f"脚本包含未获素材或事实支持的内容：{phrase}")
    if project.brand_bible:
        conflicts.extend(bible_conflicts(project.brand_bible))
    return list(dict.fromkeys(conflicts))


def assert_billable_ready(project: Project) -> None:
    bible = project.brand_bible
    if not bible or bible.status != "approved":
        raise ValueError("付费生成前必须由人工批准无冲突的 Brand Bible。")
    conflicts = plan_conflicts(project)
    if conflicts:
        raise ValueError("事实冲突阻断付费生成：" + "；".join(conflicts))
