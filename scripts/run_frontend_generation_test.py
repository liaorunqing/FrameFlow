from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright


SOURCE = Path(r"C:\Users\liaoq\Desktop\toy\example")
ROOT = Path(__file__).resolve().parents[1]


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    console_errors: list[str] = []
    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
    page.goto("http://127.0.0.1:8000/", wait_until="networkidle", timeout=30_000)

    with page.expect_response(lambda response: response.url.endswith("/api/projects") and response.request.method == "POST") as created_info:
        page.get_by_role("button", name="新建视频").click()
    created = created_info.value.json()
    project_id = created["id"]

    file_inputs = page.locator(".asset-tile input[type=file]")
    file_inputs.nth(0).set_input_files(str(SOURCE / "toy.jpg"))
    page.wait_for_timeout(700)
    file_inputs = page.locator(".asset-tile input[type=file]")
    file_inputs.nth(1).set_input_files(str(SOURCE / "people.jpg"))
    page.wait_for_timeout(700)
    file_inputs = page.locator(".asset-tile input[type=file]")
    file_inputs.nth(2).set_input_files(str(SOURCE / "scene.png"))
    page.wait_for_timeout(900)

    page.locator(".studio-nav button").filter(has_text="创作").click()
    page.get_by_label("项目名称").fill("小羊手偶 · 15秒故事测试")
    page.get_by_label("商品名称").fill("白色小羊手偶")
    page.get_by_label("商品类别").fill("毛绒手偶玩具")
    page.get_by_label("目标平台").select_option(label="抖音")
    page.get_by_label("画面风格").select_option(label="真实生活叙事广告")
    page.get_by_label("核心卖点 每行一项").fill("可套在手上进行角色互动\n白色绒毛、棕色小角与圆眼造型\n适合即兴讲故事与亲子互动")
    page.get_by_label("故事要求").fill("少年在明亮的室内活动空间发现一只白色小羊手偶，把手伸入手偶并尝试让它挥手。随着动作越来越自然，他用手偶完成一段简短的即兴表演，最后把小羊举到镜头前。画面真实克制，功能必须通过手部动作展示，不加入未经证实的功效。")
    page.get_by_label("视频时长").first.select_option("15")
    page.get_by_label("画面比例").first.select_option("9:16")
    page.locator(".budget-control input[type=range]").evaluate("(el) => { el.value='20'; el.dispatchEvent(new Event('input', {bubbles:true})); el.dispatchEvent(new Event('change', {bubbles:true})); }")
    page.screenshot(path=str(ROOT / "backend" / "data" / "frontend-lamb-project-ready.png"), full_page=True)

    with page.expect_response(lambda response: response.url.endswith(f"/api/projects/{project_id}/produce") and response.request.method == "POST", timeout=60_000) as production_info:
        page.locator(".generate-button").click()
    production = production_info.value.json()
    page.wait_for_timeout(1000)
    result = {
        "project_id": project_id,
        "project_name": created["name"],
        "workflow_status": production["status"],
        "workflow_progress": production["progress"],
        "estimated_cost_cny": production["estimated_cost_cny"],
        "actual_cost_cny": production["actual_cost_cny"],
        "console_errors": console_errors,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    browser.close()
