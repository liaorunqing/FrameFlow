from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
SCREENSHOT = ROOT / "backend" / "data" / "debug" / "production-console.png"
WORKFLOW_SCREENSHOT = ROOT / "backend" / "data" / "debug" / "production-workflow.png"


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    console_errors: list[str] = []
    page.on(
        "console",
        lambda message: console_errors.append(message.text)
        if message.type == "error"
        else None,
    )
    page.goto("http://127.0.0.1:5173", wait_until="networkidle")
    page.get_by_text("FrameFlow", exact=True).wait_for()
    SCREENSHOT.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SCREENSHOT), full_page=True)
    brief_count = page.get_by_text("素材与Brief", exact=True).count()
    assert brief_count >= 1, page.locator("body").inner_text()
    page.get_by_text("生产工作流", exact=True).click()
    page.get_by_text("PRODUCTION GRAPH", exact=False).wait_for()
    assert page.locator(".node").count() >= 10
    page.screenshot(path=str(WORKFLOW_SCREENSHOT), full_page=True)
    page.get_by_text("模型路由", exact=True).click()
    page.get_by_text("MODEL ROUTING", exact=False).wait_for()
    assert page.locator(".provider").count() >= 4
    page.screenshot(path=str(SCREENSHOT), full_page=True)
    assert not console_errors, console_errors
    browser.close()

print(f"UI smoke test passed: {SCREENSHOT}")
