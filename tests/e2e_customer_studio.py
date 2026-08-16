from pathlib import Path

from playwright.sync_api import sync_playwright


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    errors: list[str] = []
    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
    page.goto("http://127.0.0.1:5173", wait_until="networkidle")
    page.get_by_text("把商品交给 AI").wait_for()
    assert page.get_by_role("button", name="生成完整视频").is_visible()
    assert page.get_by_text("上传参考素材").is_visible()
    assert page.get_by_text("告诉导演要拍什么").is_visible()
    page.get_by_role("button", name="模型配置").click()
    assert page.get_by_text("连接云端 AI").is_visible()
    assert page.get_by_text("百炼 API Key").is_visible()
    page.screenshot(path="tests/artifacts/customer-studio.png", full_page=True)
    assert not errors, errors
    browser.close()
