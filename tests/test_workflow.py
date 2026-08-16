import asyncio
import json
from datetime import datetime

import pytest

from fastapi.testclient import TestClient

import backend.app.main as main_module
from backend.app.main import app
from backend.app.orchestration import (
    WORKFLOW_VERSION,
    WorkflowAttempt,
    approve_node,
    build_workflow,
    complete_node,
    reset_node_and_descendants,
    upgrade_workflow,
)
from backend.app.quality_control import (
    decide_keyframe_quality,
    decide_video_quality,
    quality_benchmark,
)
from backend.app.audio import build_audio_timeline
from backend.app.postprocess import enhancement_capabilities
from backend.app.production import build_production_plan
from backend.app.providers import MiniMaxOfficialVideoProvider, VideoJob
from backend.app.pipeline_executor import PipelineExecutor, _deterministic_frame_metrics
from backend.app.vision import OllamaVisionService, PairVisualComparison
from backend.app.routing import route_shot
from backend.app.schemas import Project
from backend.app.director import (
    DirectorPlanDraft,
    DirectorShotDraft,
    _fallback_plan,
    _parse_director_draft,
    ensure_narrated_ad_plan,
)
from backend.app.brand_bible import bible_constraints, draft_brand_bible


def make_project() -> Project:
    timestamp = datetime.now()
    project = Project(
        id="workflow-test",
        created_at=timestamp,
        updated_at=timestamp,
        name="叙事玩具广告",
        product_name="小羊手偶",
        product_category="儿童玩具",
        platform="抖音",
        duration=30,
        aspect_ratio="16:9",
        style="真实生活电影感",
        audience="年轻父母",
        selling_points=["柔软陪伴", "激发表达"],
        brief="产品推动故事变化，表演自然克制。",
    )
    return project.model_copy(update={"creative_plan": _fallback_plan(project)})


def test_workflow_is_a_shot_level_dag() -> None:
    project = make_project()
    plan = build_production_plan(project)
    workflow = build_workflow(project, plan)

    assert workflow.version == WORKFLOW_VERSION
    assert workflow.nodes[0].status == "ready"
    assert len([node for node in workflow.nodes if node.kind == "video_generation"]) == len(plan.shots)
    assert len([node for node in workflow.nodes if node.kind == "keyframe_review"]) == len(plan.keyframes)
    assert workflow.estimated_cost_cny == plan.cost.estimated_total


def test_advisory_quality_is_a_sidecar_not_a_generation_gate() -> None:
    project = make_project().model_copy(update={"quality_mode": "advisory"})
    workflow = build_workflow(project, build_production_plan(project))
    first_video = next(node for node in workflow.nodes if node.kind == "video_generation")
    compose = next(node for node in workflow.nodes if node.id == "final-compose")

    assert all(not dependency.startswith("keyframe-review:") for dependency in first_video.dependencies)
    assert any(dependency.startswith("keyframe:") for dependency in first_video.dependencies)
    assert all(not dependency.startswith("video-review:") for dependency in compose.dependencies)
    assert any(dependency.startswith("video:") for dependency in compose.dependencies)


def test_strict_quality_keeps_review_gates() -> None:
    project = make_project().model_copy(update={"quality_mode": "strict"})
    workflow = build_workflow(project, build_production_plan(project))
    first_video = next(node for node in workflow.nodes if node.kind == "video_generation")
    compose = next(node for node in workflow.nodes if node.id == "final-compose")

    assert any(dependency.startswith("keyframe-review:") for dependency in first_video.dependencies)
    assert any(dependency.startswith("video-review:") for dependency in compose.dependencies)


def test_brand_bible_becomes_generation_constraints() -> None:
    project = make_project()
    bible = draft_brand_bible(project).model_copy(update={"status": "approved"})
    with_bible = project.model_copy(update={"brand_bible": bible})

    constraints = bible_constraints(with_bible)
    plan = build_production_plan(with_bible)

    assert constraints
    assert any(constraint in plan.keyframes[0].required_subjects for constraint in constraints)


def test_production_prompts_never_inherit_legacy_ai_toy_facts() -> None:
    project = make_project()
    bible = draft_brand_bible(project).model_copy(update={"status": "approved"})
    plan = build_production_plan(project.model_copy(update={"brand_bible": bible}))
    corpus = " ".join(
        [item.prompt for item in plan.keyframes]
        + [item.video_prompt for item in plan.shots]
    ).lower()
    for leaked_fact in ("14.5 cm", "12 cm high", "6.5 cm", "cyan facial", "black circular screen"):
        assert leaked_fact not in corpus
    assert project.product_name.lower() in corpus


def test_qwen_object_shaped_continuity_is_coerced_without_fallback() -> None:
    project = make_project()
    plan = project.creative_plan
    payload = {
        "product_name": project.product_name,
        **plan.model_dump(include={
            "campaign_idea", "logline", "protagonist", "story_question", "hook",
            "emotional_arc", "narration_script", "visual_language",
            "music_direction", "call_to_action",
        }),
        "continuity_bible": [
            {"人物": "同一人物与服装"},
            {"商品": "严格遵循当前商品图"},
            {"场景": "同一空间与光线"},
        ],
        "shots": [
            DirectorShotDraft.model_validate(
                shot.model_dump(include=set(DirectorShotDraft.model_fields))
            ).model_dump()
            for shot in plan.shots
        ],
    }
    parsed = _parse_director_draft(json.dumps(payload, ensure_ascii=False))
    assert isinstance(parsed, DirectorPlanDraft)
    assert parsed.continuity_bible[0] == "人物：同一人物与服装"


def test_partial_retry_preserves_upstream_nodes() -> None:
    project = make_project()
    workflow = build_workflow(project, build_production_plan(project))
    complete_node(workflow, "asset-analysis")
    complete_node(workflow, "story-plan")
    target = next(node for node in workflow.nodes if node.kind == "keyframe_generation")
    complete_node(workflow, target.id)

    affected = reset_node_and_descendants(workflow, target.id)

    assert "asset-analysis" not in affected
    assert "story-plan" not in affected
    assert target.id in affected
    assert next(node for node in workflow.nodes if node.id == "asset-analysis").status == "completed"
    assert next(node for node in workflow.nodes if node.id == target.id).status == "ready"


def test_workflow_upgrade_preserves_matching_completed_nodes() -> None:
    project = make_project()
    legacy = build_workflow(project, build_production_plan(project))
    legacy.version = "frameflow-dag-v2"
    legacy.nodes = [
        node for node in legacy.nodes
        if node.id not in {"optional-upscale", "optional-interpolation"}
    ]
    complete_node(legacy, "asset-analysis")
    fresh = build_workflow(project, build_production_plan(project))

    upgraded = upgrade_workflow(legacy, fresh)

    assert upgraded.version == WORKFLOW_VERSION
    assert next(node for node in upgraded.nodes if node.id == "asset-analysis").status == "completed"
    assert next(node for node in upgraded.nodes if node.id == "optional-upscale").status == "skipped"


def test_router_requests_identity_and_physics_for_product_interaction() -> None:
    project = make_project()
    shot = project.creative_plan.shots[0]
    shot.action = "孩子近景拿起商品，用双手与玩具互动"
    decision = route_shot(project, shot)

    assert "identity_reference" in decision.requested_capabilities
    assert "strong_physics" in decision.requested_capabilities
    # Cost-controlled research routing keeps physical interactions on Fast by
    # default; premium is now an explicit human override only.
    assert decision.model_hint == "story"


def test_billable_node_requires_explicit_confirmation(monkeypatch) -> None:
    project = make_project().model_copy(update={"id": "billing-gate-test"})
    workflow = build_workflow(project, build_production_plan(project))
    complete_node(workflow, "asset-analysis")
    complete_node(workflow, "story-plan")
    monkeypatch.setattr(main_module, "require_project", lambda project_id: project)
    monkeypatch.setattr(main_module.workflow_store, "get", lambda project_id: workflow)
    keyframe = next(node for node in workflow.nodes if node.kind == "keyframe_generation")

    response = TestClient(app).post(
        f"/api/projects/{project.id}/workflow/nodes/{keyframe.id}/execute",
        json={"confirm_billable": False},
    )

    assert response.status_code == 402
    assert "付费API" in response.json()["detail"]
    assert workflow.nodes[2].attempts == []


def test_minimax_boundary_request_sends_supported_first_frame_only(monkeypatch) -> None:
    monkeypatch.setenv("MINIMAX_API_KEY", "test-key")
    provider = MiniMaxOfficialVideoProvider()
    captured: dict = {}

    async def fake_post(path: str, payload: dict) -> dict:
        captured.update(payload)
        return {"task_id": "boundary-task", "base_resp": {"status_code": 0}}

    async def fake_wait(job: VideoJob) -> VideoJob:
        return job

    monkeypatch.setattr(provider, "_post", fake_post)
    monkeypatch.setattr(provider, "wait_for_completion", fake_wait)
    result = asyncio.run(provider.submit_boundary(
        prompt="人物从首帧自然走到尾帧",
        duration=6,
        first_frame="https://example.com/first.jpg",
        last_frame="https://example.com/last.jpg",
        aspect_ratio="16:9",
    ))

    assert result.external_id == "boundary-task"
    assert captured["first_frame_image"] == "https://example.com/first.jpg"
    assert "last_frame_image" not in captured
    assert captured["prompt_optimizer"] is False


def test_audio_timeline_matches_story_duration() -> None:
    project = make_project()
    timeline = build_audio_timeline(project)

    assert timeline.duration == project.duration
    assert all(0 <= cue.start < cue.end <= project.duration for cue in timeline.cues)
    assert len(timeline.subtitles) >= len(timeline.cues)
    assert all(0 <= cue.start < cue.end <= project.duration for cue in timeline.subtitles)


def test_final_compose_waits_for_all_human_review_gates() -> None:
    project = make_project()
    workflow = build_workflow(project, build_production_plan(project))
    complete_node(workflow, "asset-analysis")
    complete_node(workflow, "story-plan")

    for node in [item for item in workflow.nodes if item.kind == "keyframe_generation"]:
        complete_node(workflow, node.id)
    for node in [item for item in workflow.nodes if item.kind == "keyframe_review"]:
        complete_node(workflow, node.id, review_required=True)
        approve_node(workflow, node.id)
    video_nodes = [item for item in workflow.nodes if item.kind == "video_generation"]
    for generation in video_nodes:
        complete_node(workflow, generation.id)
        review_id = f"video-review:{generation.shot_id}"
        complete_node(workflow, review_id, review_required=True)
        approve_node(workflow, review_id)

    compose = next(item for item in workflow.nodes if item.id == "final-compose")
    assert compose.status == "blocked"
    complete_node(workflow, "audio-timeline")
    assert compose.status == "ready"


def test_enhancement_detection_is_safe_when_optional_tools_are_missing() -> None:
    capabilities = {item.id: item for item in enhancement_capabilities()}

    assert capabilities["ffmpeg"].available is True
    assert "realesrgan" in capabilities
    assert "rife" in capabilities


@pytest.mark.skip(reason="superseded by provider-neutral foundational prompt contract")
def test_production_plan_structures_prompts_and_marks_risky_shots() -> None:
    project = make_project()
    plan = build_production_plan(project)

    assert plan.pipeline_version == "story-boundary-v1"
    assert all(item.prompt_sections for item in plan.shots)
    assert all("single_action" in item.prompt_sections for item in plan.shots)
    assert all("documentary_evidence" in item.prompt_sections for item in plan.shots)
    assert all("sound_intent" in item.prompt_sections for item in plan.shots)
    assert all("观察式生活微纪录片" in item.video_prompt for item in plan.shots)
    assert all("商品身份锁定" in item.video_prompt for item in plan.shots)
    assert all("approved_product_identity_reference" in item.reference_strategy for item in plan.shots)
    assert all(item.recommended_variants in {1, 2} for item in plan.shots)


@pytest.mark.skip(reason="documentary is no longer the forced default mode")
def test_fallback_director_uses_documentary_as_primary_mode() -> None:
    project = make_project()
    plan = project.creative_plan

    assert "观察式生活纪录片" in plan.visual_language
    assert "同期环境声" in plan.music_direction
    assert all("observational micro-documentary" in shot.prompt for shot in plan.shots)
    assert all("No glossy commercial staging" in shot.prompt for shot in plan.shots)


def test_hand_interaction_is_high_risk_and_recommends_two_candidates() -> None:
    project = make_project()
    risky_shot = project.creative_plan.shots[0].model_copy(update={
        "action": "孩子用双手拿起商品并触摸屏幕",
    })
    project = project.model_copy(update={
        "creative_plan": project.creative_plan.model_copy(update={
            "shots": [risky_shot, *project.creative_plan.shots[1:]],
        }),
    })

    first = build_production_plan(project).shots[0]
    assert first.risk_level == "high"
    assert first.recommended_variants == 2


def test_keyframe_failure_becomes_a_budgeted_local_repair_decision() -> None:
    project = make_project()
    workflow = build_workflow(project, build_production_plan(project))
    generation = next(node for node in workflow.nodes if node.kind == "keyframe_generation")
    generation.actual_cost_cny = generation.estimated_cost_cny
    generation.attempts = [WorkflowAttempt(
        number=1,
        status="completed",
        provider="seedream-official",
        actual_cost_cny=generation.estimated_cost_cny,
    )]

    decision = decide_keyframe_quality(
        report={
            "passed": False,
            "overall_score": 42,
            "product_consistency": 30,
            "character_consistency": 82,
            "scene_consistency": 80,
            "hand_and_physics": 55,
            "story_compliance": 78,
            "repair_prompt": "保留玩具的角、眼睛和身体轮廓",
        },
        generation=generation,
        review_node_id=generation.id.replace("keyframe:", "keyframe-review:"),
        allow_automatic_paid_retry=True,
    )

    assert "product_identity" in decision.categories
    assert decision.severity == "critical"
    assert decision.retry_recommended is True
    assert decision.remaining_retries == 2
    assert "参考商品" in decision.prompt_patch


def test_video_failure_recommends_simpler_action_and_fallback() -> None:
    project = make_project()
    workflow = build_workflow(project, build_production_plan(project))
    generation = next(node for node in workflow.nodes if node.kind == "video_generation")

    decision = decide_video_quality(
        report={
            "passed": False,
            "score": 58,
            "frames": [{
                "story_match": False,
                "extra_people": True,
                "direct_camera_gaze": False,
                "text_or_watermark": False,
                "physical_issues": ["手指与商品融合"],
                "story_mismatches": ["没有完成拿起动作"],
            }],
        },
        generation=generation,
        review_node_id=generation.id.replace("video:", "video-review:"),
        fallback_provider="minimax-official",
    )

    assert decision.repair_steps[0].action == "simplify_action"
    assert decision.suggested_provider == "minimax-official"
    assert {"hand_physics", "extra_people", "story_mismatch"} <= set(decision.categories)
    assert decision.execution_policy == "manual_review"
    assert decision.retry_recommended is False


def test_video_product_screen_drift_is_a_critical_failure() -> None:
    project = make_project()
    workflow = build_workflow(project, build_production_plan(project))
    generation = next(node for node in workflow.nodes if node.kind == "video_generation")

    decision = decide_video_quality(
        report={
            "passed": False,
            "score": 86,
            "product_match": False,
            "product_consistency": 38,
            "frames": [{"story_match": True}],
        },
        generation=generation,
        review_node_id=generation.id.replace("video:", "video-review:"),
    )

    assert "product_identity" in decision.categories
    assert decision.severity == "critical"
    assert "never invent, remove, replace, illuminate or transform" in decision.prompt_patch
    assert "face/screen" not in decision.prompt_patch


def test_product_comparison_cannot_pass_with_critical_mismatches(monkeypatch) -> None:
    service = OllamaVisionService()

    async def contradictory_result(**kwargs):
        return PairVisualComparison(
            subject="product",
            match=True,
            score=85,
            critical_mismatches=["black screen changed into a blank white circle"],
        )

    monkeypatch.setattr(service, "_structured", contradictory_result)
    result = asyncio.run(service.compare_pair(
        reference="reference.jpg",
        candidate="candidate.jpg",
        subject="product",
    ))

    assert result.match is False
    assert result.score == 40


def test_quality_can_opt_in_to_automatic_retry_after_calibration() -> None:
    project = make_project()
    workflow = build_workflow(project, build_production_plan(project))
    generation = next(node for node in workflow.nodes if node.kind == "keyframe_generation")

    decision = decide_keyframe_quality(
        report={"passed": False, "overall_score": 52, "story_match": False},
        generation=generation,
        review_node_id="keyframe-review:kf-000",
        allow_automatic_paid_retry=True,
    )

    assert decision.execution_policy == "automatic_retry"
    assert decision.retry_recommended is True


def test_failed_seedance_retry_selects_minimax_fallback() -> None:
    project = make_project()
    workflow = build_workflow(project, build_production_plan(project))
    generation = next(node for node in workflow.nodes if node.kind == "video_generation")
    generation.attempts = [
        WorkflowAttempt(number=1, status="failed", provider="seedance-official"),
        WorkflowAttempt(number=2, status="running", provider=""),
    ]

    provider, switched = PipelineExecutor.select_video_provider(
        node=generation,
        primary_provider="seedance-official",
        fallback_provider="minimax-official",
    )

    assert provider == "minimax-official"
    assert switched is True


def test_foundational_plan_uses_approved_keyframes_for_every_clip() -> None:
    project = make_project()
    plan = build_production_plan(project)

    assert len(plan.keyframes) == len(plan.shots) + 1
    assert all(shot.first_frame_runtime_policy == "planned" for shot in plan.shots)
    assert all("Realistic live-action product story" in shot.video_prompt for shot in plan.shots)
    assert all("single_action" in shot.prompt_sections for shot in plan.shots)
    assert all("product_lock" in shot.prompt_sections for shot in plan.shots)
    assert all("previous_actual_last_frame" not in shot.reference_strategy for shot in plan.shots)


def test_generic_fallback_does_not_force_documentary_mode() -> None:
    project = make_project()
    plan = project.creative_plan

    assert project.style in plan.visual_language
    assert all("Realistic live-action product story" in shot.prompt for shot in plan.shots)
    assert all("Only action:" in shot.prompt for shot in plan.shots)
    assert "observational micro-documentary" not in " ".join(shot.prompt for shot in plan.shots)


def test_quality_benchmark_uses_attempt_cost_and_review_result() -> None:
    project = make_project()
    workflow = build_workflow(project, build_production_plan(project))
    generation = next(node for node in workflow.nodes if node.kind == "video_generation")
    review = next(
        node for node in workflow.nodes
        if node.id == generation.id.replace("video:", "video-review:")
    )
    generation.attempts = [WorkflowAttempt(
        number=1,
        status="completed",
        provider="seedance-official",
        actual_cost_cny=3.7,
        outputs={"provider": "seedance-official"},
    )]
    review.attempts = [WorkflowAttempt(
        number=1,
        status="completed",
        outputs={"passed": "true"},
    )]

    report = quality_benchmark(workflow)
    row = next(
        item for item in report.providers
        if item.provider == "seedance-official" and item.node_kind == "video"
    )

    assert row.generated_attempts == 1
    assert row.automatic_pass_rate == 100
    assert row.actual_cost_cny == 3.7


def test_default_ad_plan_cannot_silently_drop_narration() -> None:
    project = make_project()
    plan = _fallback_plan(project)
    silent = plan.model_copy(update={
        "shots": [shot.model_copy(update={"voiceover": ""}) for shot in plan.shots],
        "narration_script": "",
    })
    repaired = ensure_narrated_ad_plan(project, silent)
    assert sum(bool(shot.voiceover.strip()) for shot in repaired.shots) >= 2
    assert repaired.narration_script


def test_explicit_no_voiceover_request_is_respected() -> None:
    project = make_project().model_copy(update={"brief": "纯视觉，无旁白"})
    plan = _fallback_plan(project)
    silent = plan.model_copy(update={
        "shots": [shot.model_copy(update={"voiceover": ""}) for shot in plan.shots],
        "narration_script": "",
    })
    repaired = ensure_narrated_ad_plan(project, silent)
    assert all(not shot.voiceover for shot in repaired.shots)


def test_dashscope_vision_uses_cloud_without_ollama(monkeypatch, tmp_path):
    import httpx
    from PIL import Image

    monkeypatch.setenv("VISION_PROVIDER", "dashscope")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-cloud-key")
    monkeypatch.setenv("VISION_CLOUD_API_BASE", "https://dashscope.example/v1")
    monkeypatch.setenv("VISION_CLOUD_MODEL", "qwen-vl-test")
    image_path = tmp_path / "product.jpg"
    Image.new("RGB", (64, 64), "white").save(image_path)
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert str(request.url) == "https://dashscope.example/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-cloud-key"
        payload = json.loads(request.content)
        assert payload["model"] == "qwen-vl-test"
        assert payload["messages"][0]["content"][1]["image_url"]["url"].startswith(
            "data:image/jpeg;base64,"
        )
        result = {
            "subject": "product", "match": True, "score": 92,
            "reference_features": ["round body"],
            "candidate_features": ["round body"],
            "critical_mismatches": [], "repair_instruction": "",
        }
        return httpx.Response(200, json={
            "choices": [{"message": {"content": json.dumps(result)}}]
        })

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    service = OllamaVisionService()
    comparison = asyncio.run(service.compare_pair(
        reference=image_path, candidate=image_path, subject="product"
    ))
    assert service.provider == "dashscope"
    assert comparison.match is True
    assert comparison.score == 92
    assert len(requests) == 1


def test_dashscope_vision_requires_cloud_key(monkeypatch):
    monkeypatch.setenv("VISION_PROVIDER", "dashscope")
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("VISION_CLOUD_API_KEY", raising=False)
    service = OllamaVisionService()
    with pytest.raises(RuntimeError, match="DASHSCOPE_API_KEY"):
        asyncio.run(service._structured_cloud(
            prompt="review", images=[], schema=PairVisualComparison
        ))


def test_review_vision_uses_separate_cloud_model(monkeypatch):
    monkeypatch.setenv("VISION_PROVIDER", "dashscope")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-cloud-key")
    monkeypatch.setenv("VISION_CLOUD_MODEL", "qwen3.7-plus")
    monkeypatch.setenv("VISION_REVIEW_MODEL", "qwen3.6-flash")
    assert OllamaVisionService().model == "qwen3.7-plus"
    assert OllamaVisionService(purpose="review").model == "qwen3.6-flash"


def test_deterministic_frame_metrics_detects_black_frame(tmp_path):
    from PIL import Image

    normal = tmp_path / "normal.jpg"
    black = tmp_path / "black.jpg"
    Image.new("RGB", (320, 180), (130, 120, 110)).save(normal)
    Image.new("RGB", (320, 180), (0, 0, 0)).save(black)
    report = _deterministic_frame_metrics([normal, black])
    assert report["passed"] is False
    assert report["severe_dark_frame"] is True


def test_billable_retry_stops_at_default_budget(monkeypatch) -> None:
    project = make_project().model_copy(update={"id": "retry-budget-test"})
    workflow = build_workflow(project, build_production_plan(project))
    complete_node(workflow, "asset-analysis")
    complete_node(workflow, "story-plan")
    generation = next(node for node in workflow.nodes if node.kind == "keyframe_generation")
    generation.attempts = [
        WorkflowAttempt(number=index, status="completed")
        for index in range(1, generation.max_retries + 2)
    ]
    generation.actual_cost_cny = generation.estimated_cost_cny * len(generation.attempts)
    monkeypatch.setattr(main_module, "require_project", lambda project_id: project)
    monkeypatch.setattr(main_module.workflow_store, "get", lambda project_id: workflow)

    response = TestClient(app).post(
        f"/api/projects/{project.id}/workflow/nodes/{generation.id}/execute",
        json={"confirm_billable": True},
    )

    assert response.status_code == 409
    assert "上限" in response.json()["detail"]
