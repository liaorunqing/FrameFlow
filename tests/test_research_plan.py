from datetime import datetime

from backend.app.fact_guard import plan_conflicts
from backend.app.production import build_production_plan
from backend.app.research_plan import build_plush_bear_30s_plan
from backend.app.schemas import Project


def test_plush_bear_research_plan_is_factual_and_has_shared_boundaries() -> None:
    project = Project(
        id="plush-research", created_at=datetime.now(), updated_at=datetime.now(),
        name="Plush bear", product_name="White plush bear", product_category="children plush toy",
        platform="TikTok", duration=15, aspect_ratio="9:16", style="warm realistic", audience="parents",
        selling_points=[], brief="research only",
    )
    plan = build_plush_bear_30s_plan(project)
    updated = project.model_copy(update={"duration": 30, "creative_plan": plan})
    production = build_production_plan(updated)

    assert len(plan.shots) == 5
    assert sum(shot.duration for shot in plan.shots) == 30
    assert len(production.keyframes) == 6
    assert all(shot.duration == 6 for shot in plan.shots)
    assert plan_conflicts(updated) == []
    assert all("sleep" not in shot.prompt.lower() for shot in plan.shots)


def test_fact_guard_blocks_unsupported_sleep_claim() -> None:
    project = Project(
        id="guard", created_at=datetime.now(), updated_at=datetime.now(), name="guard",
        product_name="bear", product_category="toy", platform="TikTok", duration=30,
        aspect_ratio="9:16", style="real", audience="parents", selling_points=[], brief="",
    )
    plan = build_plush_bear_30s_plan(project)
    bad = plan.model_copy(update={"narration_script": "A sleep aid for bedtime"})
    assert any("sleep" in issue.lower() for issue in plan_conflicts(project, bad))
