from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from playwright.sync_api import sync_playwright


BASE = "http://127.0.0.1:8011"


def api(path: str, method: str = "GET", body: dict | None = None):
    payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = Request(
        BASE + path,
        data=payload,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            if response.status == 204:
                return None
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise AssertionError(f"{method} {path} -> {exc.code}: {exc.read().decode('utf-8', errors='replace')}") from exc


def shot(index: int, voiceover: str) -> dict:
    return {
        "id": f"reg-shot-{index}", "index": index, "title": f"测试镜头 {index}",
        "duration": 5, "purpose": "验证用户体验", "narrative_beat": "动作自然推进",
        "visual": "人物在真实场景中使用参考商品。", "camera": "稳定中景",
        "action": "人物完成一个清楚、单一的动作。", "voiceover": voiceover,
        "on_screen_text": "", "continuity_anchor": "人物、商品和场景保持一致",
        "transition": "动作匹配直切", "prompt": "Realistic product demonstration, stable medium shot.",
        "model_hint": "story", "status": "planned",
    }


def main() -> None:
    project = api("/api/projects", "POST", {
        "name": "UX反馈隔离回归项目", "product_name": "尺寸测试玩具", "product_category": "儿童玩具",
        "platform": "抖音", "duration": 15, "aspect_ratio": "9:16", "style": "真实故事",
        "audience": "家庭用户", "selling_points": ["真实体验"], "brief": "",
    })
    project_id = project["id"]
    try:
        for item_id in ("product-rainbow", "product-stacker", "character-boy", "scene-modern-living"):
            api(f"/api/projects/{project_id}/catalog/{item_id}", "POST")
        api(f"/api/projects/{project_id}", "PATCH", {
            "product_scale": "custom", "product_dimensions": "14.5 × 12 × 6.5 cm", "quality_mode": "strict",
        })
        project = api(f"/api/projects/{project_id}")
        asset_ids = [item["id"] for item in project["assets"]]
        plan = {
            "version_id": "ux-regression-v1", "source_asset_ids": asset_ids,
            "source_facts": ["商品比例必须符合 14.5 × 12 × 6.5 cm"],
            "director_source": "openai_compatible", "director_model": "regression-fixture", "director_note": "隔离测试",
            "campaign_idea": "一次真实的产品体验", "logline": "人物发现需求、尝试商品并获得可见反馈。",
            "protagonist": "参考图人物", "story_question": "商品能否自然解决眼前需求？",
            "hook": "一个具体需求出现", "emotional_arc": "注意—尝试—满足",
            "continuity_bible": ["商品、人物、场景保持一致"], "narration_script": "",
            "visual_language": "自然光真实摄影", "music_direction": "轻柔克制",
            "call_to_action": "了解更多", "sequences": [],
            "shots": [
                shot(1, "这是一段明显超过五秒镜头自然语速承载能力的旁白文字需要系统自动缩短。"),
                shot(2, "一次尝试，让变化被看见。"), shot(3, "结果自然发生。"),
            ],
        }
        saved = api(f"/api/projects/{project_id}/script", "PUT", plan)
        assert len(saved["creative_plan"]["shots"][0]["voiceover"]) <= 20
        assert saved["product_scale"] == "custom"
        assert saved["product_dimensions"] == "14.5 × 12 × 6.5 cm"
        workflow_path = Path("backend/data/workflows") / f"{project_id}.json"
        workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
        first_video = next(node for node in workflow["nodes"] if node["kind"] == "video_generation")
        first_video["status"] = "completed"
        first_video["progress"] = 100
        first_video["attempts"] = [{
            "number": 1, "status": "completed", "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(), "provider": "regression-fixture",
            "model_id": "fixture", "estimated_cost_cny": 0, "actual_cost_cny": 0,
            "outputs": {"video_url": "/artifacts/regression-fixture.mp4"},
        }]
        first_review = next(node for node in workflow["nodes"] if node["id"] == f"video-review:reg-shot-1")
        first_review["status"] = "review_required"
        first_review["progress"] = 100
        first_review["attempts"] = [{
            "number": 1, "status": "completed", "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(), "provider": "regression-fixture",
            "model_id": "fixture-review", "estimated_cost_cny": 0, "actual_cost_cny": 0,
            "outputs": {"passed": "false", "score": "65", "failure_categories": "motion_continuity"},
        }]
        first_review["quality_decision"] = {
            "passed": False, "score": 65, "severity": "minor", "categories": ["motion_continuity"],
            "summary": "测试镜头衔接需要确认", "repair_steps": [], "prompt_patch": "保持动作连续",
            "suggested_provider": "", "retry_recommended": True, "remaining_retries": 1,
            "spent_cny": 0, "retry_budget_cny": 3, "projected_next_cost_cny": 1.35, "within_budget": True,
        }
        workflow["status"] = "review_required"
        workflow_path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8")

        errors: list[str] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1680, "height": 1050})
            page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
            page.route("**/artifacts/regression-fixture.mp4", lambda route: route.fulfill(status=200, content_type="video/mp4", body=b""))
            retry_urls: list[str] = []
            resume_calls: list[str] = []
            page.route("**/workflow/nodes/*/retry", lambda route: (retry_urls.append(route.request.url), route.fulfill(status=200, content_type="application/json", body=json.dumps(workflow))))
            page.route("**/produce/resume", lambda route: (resume_calls.append(route.request.url), route.fulfill(status=202, content_type="application/json", body=json.dumps(workflow))))
            page.goto(BASE, wait_until="domcontentloaded")
            page.locator(".studio-shell").wait_for()
            page.locator(".project-switch").filter(has_text="UX反馈隔离回归项目").wait_for()
            page.get_by_role("button", name="素材", exact=True).click()
            product_tile = page.locator(".asset-tile").filter(has_text="商品")
            assert product_tile.locator(".asset-stack img").count() == 2
            assert product_tile.get_by_text("商品 · 2 张").count() == 1

            page.get_by_role("button", name="创作", exact=True).click()
            assert page.locator("label", has_text="产品规格").locator("select").input_value() == "custom"
            assert page.locator("label", has_text="真实尺寸").locator("input").input_value() == "14.5 × 12 × 6.5 cm"
            assert page.locator(".shot-stream video").count() == 1
            page.get_by_role("button", name="局部重做当前镜头").click()
            page.wait_for_timeout(300)
            assert retry_urls and "video%3Areg-shot-1" in retry_urls[-1]
            assert resume_calls
            page.get_by_role("button", name="接受风险并继续").click()
            page.wait_for_timeout(300)
            page.get_by_role("button", name="自定义修改脚本").click()
            first_voiceover = page.locator('.script-preview textarea[aria-label="镜头1旁白"]')
            first_voiceover.fill("五秒镜头，只说一句清楚的话。")
            page.get_by_role("button", name="保存脚本修改").click()
            page.get_by_text("脚本修改已保存").wait_for()
            persisted = api(f"/api/projects/{project_id}")
            assert persisted["creative_plan"]["shots"][0]["voiceover"] == "五秒镜头，只说一句清楚的话。"

            template = page.locator("label", has_text="广告脚本模板").locator("select")
            template.select_option("documentary")
            assert template.input_value() == "documentary"
            assert page.locator(".script-preview").count() == 0

            Path("tests/artifacts").mkdir(parents=True, exist_ok=True)
            page.screenshot(path="tests/artifacts/feedback-full-regression.png", full_page=True)
            browser.close()
        if errors:
            raise AssertionError(f"browser console errors: {errors}")
    finally:
        api(f"/api/projects/{project_id}", "DELETE")


if __name__ == "__main__":
    main()
