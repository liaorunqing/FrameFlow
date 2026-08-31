from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

from backend.app.candidate_selection import CandidateScore, select_candidate
from backend.app.creative_post import build_creative_post_plan, write_feature_state_srt
from backend.app.director import _fallback_plan
from backend.app.identity_lock import build_identity_pack, load_identity_pack
from backend.app.schemas import Project


def project() -> Project:
    now = datetime.now()
    item = Project(
        id="post-test", created_at=now, updated_at=now, name="测试", product_name="AI口语学习机",
        product_category="玩具", platform="抖音", duration=30, aspect_ratio="9:16",
        style="真实生活", audience="家长", selling_points=["AI对话", "口语练习", "词典"], brief="真实家庭故事",
    )
    plan = _fallback_plan(item)
    shots = list(plan.shots)
    shots[0] = shots[0].model_copy(update={"action": "孩子向产品提问", "voiceover": "有问题时，先问一问。"})
    shots[1] = shots[1].model_copy(update={"action": "孩子进行口语跟读", "voiceover": "听一遍，再跟着说。"})
    shots[2] = shots[2].model_copy(update={"action": "孩子查词", "voiceover": "遇到新词，随手查一查。"})
    return item.model_copy(update={"creative_plan": plan.model_copy(update={"shots": shots})})


def test_identity_pack_preserves_original_and_only_composites_real_alpha(tmp_path: Path) -> None:
    opaque = tmp_path / "opaque.jpg"
    alpha = tmp_path / "alpha.png"
    Image.new("RGB", (32, 24), "white").save(opaque)
    image = Image.new("RGBA", (32, 24), (255, 0, 0, 0))
    image.putpixel((16, 12), (255, 0, 0, 255))
    image.save(alpha)
    manifest = load_identity_pack(build_identity_pack(
        project_id="x", product_images=[opaque, alpha], output_root=tmp_path / "out"
    ))
    assert manifest["policy"]["automatic_background_removal"] is False
    assert manifest["assets"][0]["safe_for_direct_composite"] is False
    assert manifest["assets"][1]["safe_for_direct_composite"] is True


@pytest.mark.skip(reason="legacy language-learning labels are no longer part of the generic base pipeline")
def test_creative_post_plan_contains_real_bgm_sfx_and_feature_states(tmp_path: Path) -> None:
    plan = build_creative_post_plan(project(), tmp_path)
    state_srt = write_feature_state_srt(plan, tmp_path / "states.srt")
    assert Path(plan.bgm_path).is_file()
    assert len(plan.sound_cues) >= 4
    assert {cue.label for cue in plan.feature_states} >= {"AI 对话", "口语练习", "词典"}
    assert state_srt and "正在聆听" in state_srt.read_text(encoding="utf-8-sig")


def test_candidate_selection_disqualifies_story_failure(tmp_path: Path) -> None:
    good = tmp_path / "good.mp4"
    bad = tmp_path / "bad.mp4"
    good.write_bytes(b"video")
    bad.write_bytes(b"video")
    result = select_candidate([
        CandidateScore(path=str(bad), automatic_score=95, product_consistency=95, hand_physics=95, story_match=False),
        CandidateScore(path=str(good), automatic_score=82, product_consistency=91, hand_physics=84, story_match=True),
    ])
    assert result.selected_path == str(good)
    assert result.requires_human_review is True


def test_generic_post_plan_always_builds_audio_assets(tmp_path: Path) -> None:
    plan = build_creative_post_plan(project(), tmp_path)

    assert Path(plan.bgm_path).is_file()
    assert len(plan.sound_cues) >= len(project().creative_plan.shots)
    assert all(Path(cue.path).is_file() for cue in plan.sound_cues)


def test_feature_state_overlays_are_disabled_by_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ENABLE_FEATURE_STATE_OVERLAYS", raising=False)
    plan = build_creative_post_plan(project(), tmp_path)

    assert plan.feature_states == []
    assert write_feature_state_srt(plan, tmp_path / "states.srt") is None
