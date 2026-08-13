from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    console_errors: list[str] = []
    failed_requests: list[str] = []
    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
    page.on("pageerror", lambda error: console_errors.append(str(error)))
    page.on("requestfailed", lambda request: failed_requests.append(request.url))
    response = page.goto("http://127.0.0.1:8000/", wait_until="networkidle", timeout=30_000)
    screenshot = ROOT / "backend" / "data" / "frontend-studio-validation.png"
    page.screenshot(path=str(screenshot), full_page=True)
    generate = page.get_by_role("button", name="重新生成完整视频")
    if not generate.count():
        generate = page.get_by_role("button", name="生成完整视频")
    creation_button = page.locator(".studio-nav button").filter(has_text="创作")
    creation_button.click()
    creation_form_visible = page.get_by_text("创作设定", exact=True).count() > 0
    page.get_by_title("模型配置").click()
    setup_modal_visible = page.get_by_text("模型与 API 配置", exact=True).count() > 0
    page.get_by_role("button").filter(has=page.locator("svg")).last.click()
    result = {
        "http_status": response.status if response else None,
        "title": page.title(),
        "new_project_visible": page.get_by_role("button", name="新建视频").count() == 1,
        "assets_visible": page.get_by_role("button", name="素材").count() == 1,
        "creation_visible": creation_button.count() == 1,
        "creation_form_visible": creation_form_visible,
        "setup_modal_visible": setup_modal_visible,
        "provider_alert_visible": page.get_by_role("alert").count() > 0,
        "balance_alert_visible": page.get_by_text("MiniMax API 余额不足", exact=True).count() > 0,
        "recharge_link_visible": page.get_by_role("link", name="前往充值").count() > 0,
        "retry_after_recharge_visible": page.get_by_role("button", name="充值后重试").count() > 0,
        "generate_visible": generate.count() == 1,
        "generate_enabled": generate.is_enabled() if generate.count() else False,
        "video_count": page.locator("video").count(),
        "export_visible": page.get_by_role("link", name="导出 MP4").count() == 1,
        "console_errors": console_errors,
        "failed_requests": failed_requests,
        "screenshot": str(screenshot),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    mobile = browser.new_page(viewport={"width": 390, "height": 844})
    mobile.goto("http://127.0.0.1:8000/", wait_until="networkidle", timeout=30_000)
    mobile.screenshot(path=str(ROOT / "backend" / "data" / "frontend-studio-mobile-validation.png"), full_page=True)
    print(json.dumps({
        "mobile_menu_visible": mobile.locator(".mobile-menu").count() == 1,
        "mobile_generate_visible": mobile.locator(".generate-button").count() == 1,
    }, ensure_ascii=False, indent=2))
    mobile.close()
    browser.close()
