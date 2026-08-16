from backend.app.orchestration import WorkflowNode
from backend.app.quality_control import decide_video_quality


def test_internal_cut_becomes_scene_continuity_failure() -> None:
    generation = WorkflowNode(
        id="video:shot-01",
        kind="video_generation",
        label="shot",
        status="completed",
        estimated_cost_cny=1.35,
        actual_cost_cny=1.35,
    )
    decision = decide_video_quality(
        report={
            "passed": False,
            "score": 88,
            "frames": [],
            "scene_analysis": {"unexpected_cut_count": 1},
        },
        generation=generation,
        review_node_id="video-review:shot-01",
    )

    assert "scene_continuity" in decision.categories
    assert "跳切" in decision.prompt_patch
    assert decision.retry_recommended is False
