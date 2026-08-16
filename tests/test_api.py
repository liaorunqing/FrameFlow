from fastapi.testclient import TestClient

from backend.app.director import director_mode
from backend.app.image_providers import VolcArkSeedreamProvider
from backend.app.main import app
from backend.app.providers import (
    MiniMaxOfficialVideoProvider,
    VolcArkSeedanceProvider,
    configured_provider_name,
)


client = TestClient(app)


def test_project_plan_and_render_contract(monkeypatch) -> None:
    # Contract tests stay deterministic and never wait for a local model.
    monkeypatch.delenv("DIRECTOR_API_BASE", raising=False)
    monkeypatch.delenv("DIRECTOR_MODEL", raising=False)
    created = client.post("/api/projects", json={
        "name": "测试广告",
        "product_name": "测试积木",
        "selling_points": ["安全", "创造力"],
    })
    assert created.status_code == 201
    project_id = created.json()["id"]

    planned = client.post(f"/api/projects/{project_id}/plan")
    assert planned.status_code == 200
    shots = planned.json()["creative_plan"]["shots"]
    assert sum(shot["duration"] for shot in shots) == 15
    assert all(shot["prompt"] for shot in shots)
    assert all(shot["narrative_beat"] for shot in shots)
    assert all(shot["continuity_anchor"] for shot in shots)
    assert planned.json()["creative_plan"]["story_question"]
    assert planned.json()["creative_plan"]["director_source"] == "fallback"

    production = client.get(f"/api/projects/{project_id}/production-plan")
    assert production.status_code == 200
    production_body = production.json()
    assert production_body["strategy"] == "shared_boundary_keyframes"
    assert len(production_body["keyframes"]) == len(shots) + 1
    assert len(production_body["shots"]) == len(shots)
    assert production_body["shots"][0]["first_frame_runtime_policy"] == "planned"
    assert production_body["shots"][1]["first_frame_runtime_policy"] == "planned"
    assert production_body["shots"][0]["last_frame_id"] == production_body["shots"][1]["first_frame_id"]
    assert production_body["cost"]["estimated_total"] > 0
    assert production_body["review_required_before_billing"] is True

    render = client.post(f"/api/projects/{project_id}/render", json={"provider": "demo"})
    assert render.status_code == 202
    assert render.json()["status"] == "queued"
    assert render.json()["provider"] == "demo"


def test_minimax_router_uses_fast_model_by_default() -> None:
    economy = MiniMaxOfficialVideoProvider._route("economy", 5)
    story = MiniMaxOfficialVideoProvider._route("story", 5)
    premium = MiniMaxOfficialVideoProvider._route("premium", 5)
    assert economy[0] == MiniMaxOfficialVideoProvider.FAST_MODEL
    assert story[0] == MiniMaxOfficialVideoProvider.FAST_MODEL
    assert premium[0] == MiniMaxOfficialVideoProvider.QUALITY_MODEL
    assert economy[3] == 1.35
    assert story[3] < premium[3]


def test_seedance_router_uses_account_visible_models(monkeypatch) -> None:
    monkeypatch.delenv("ARK_VIDEO_MODEL", raising=False)
    assert VolcArkSeedanceProvider._route("economy") == VolcArkSeedanceProvider.MINI_MODEL
    assert VolcArkSeedanceProvider._route("story") == VolcArkSeedanceProvider.FAST_MODEL
    assert VolcArkSeedanceProvider._route("premium") == VolcArkSeedanceProvider.QUALITY_MODEL
    prompt = VolcArkSeedanceProvider._prepare_prompt(
        "孩子自然地拿起商品，镜头缓慢推进。",
        duration=6,
        aspect_ratio="16:9",
        reference_count=3,
    )
    assert "参考图1是商品" in prompt
    assert "参考图2是人物" in prompt
    assert "参考图3是场景" in prompt
    assert "--ratio 16:9" in prompt
    assert "--dur 6" in prompt


def test_seedance_model_can_be_pinned_by_configuration(monkeypatch) -> None:
    monkeypatch.setenv("ARK_VIDEO_MODEL", "doubao-seedance-1-5-pro-251215")
    assert VolcArkSeedanceProvider._route("economy") == "doubao-seedance-1-5-pro-251215"
    assert VolcArkSeedanceProvider._route("premium") == "doubao-seedance-1-5-pro-251215"


def test_story_boundary_payload_contract(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ARK_API_KEY", "test-key")
    monkeypatch.setenv("ARK_IMAGE_MODEL", "doubao-seedream-4-5-251128")
    image = tmp_path / "reference.png"
    image.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR\x00\x00\x01\x2d\x00\x00\x01\x2d\x08\x02\x00\x00\x00"
        b"\x00\x00\x00\x00"
    )
    # Payload structure is tested with remote URLs so no paid request is made.
    provider = VolcArkSeedreamProvider()
    payload = provider.build_payload(
        prompt="同一人物在同一客厅拿起商品",
        reference_images=["https://example.com/product.jpg", "https://example.com/person.jpg"],
        aspect_ratio="16:9",
        max_images=4,
    )
    assert payload["model"] == "doubao-seedream-4-5-251128"
    assert len(payload["image"]) == 2
    assert payload["sequential_image_generation"] == "auto"
    assert payload["sequential_image_generation_options"]["max_images"] == 4
    assert VolcArkSeedanceProvider._image_content(
        "https://example.com/start.jpg",
        role="first_frame",
    )["role"] == "first_frame"
    assert VolcArkSeedanceProvider._image_content(
        "https://example.com/end.jpg",
        role="last_frame",
    )["role"] == "last_frame"


def test_explicit_seedance_configuration(monkeypatch) -> None:
    monkeypatch.setenv("VIDEO_PROVIDER", "seedance")
    monkeypatch.setenv("ARK_API_KEY", "test-key")
    assert configured_provider_name() == "seedance-official"


def test_demo_config_never_implies_api_billing(monkeypatch) -> None:
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.setenv("VIDEO_PROVIDER", "demo")
    config = client.get("/api/config")
    assert config.status_code == 200
    assert config.json()["mode"] == "demo"
    assert config.json()["provider_configured"] is False


def test_local_ollama_director_does_not_require_a_real_api_key(monkeypatch) -> None:
    monkeypatch.setenv("DIRECTOR_API_BASE", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("DIRECTOR_MODEL", "qwen3:latest")
    monkeypatch.delenv("DIRECTOR_API_KEY", raising=False)
    assert director_mode() == "llm"
