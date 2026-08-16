from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
SCREENSHOT = ROOT / "backend" / "data" / "debug" / "local-assembly-ui.png"


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 1300})
    console_errors: list[str] = []
    page.on(
        "console",
        lambda message: console_errors.append(message.text)
        if message.type == "error"
        else None,
    )
    page.goto("http://127.0.0.1:5173", wait_until="networkidle")
    page.get_by_text("模型路由", exact=True).click()
    page.get_by_text("LOCAL EDITORIAL SAMPLE", exact=True).wait_for()
    link = page.get_by_text("打开本地样片", exact=True)
    assert link.count() == 1
    href = link.get_attribute("href")
    assert href
    response = page.request.get(f"http://127.0.0.1:8001{href}")
    assert response.ok
    assert "video/mp4" in response.headers.get("content-type", "")
    SCREENSHOT.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SCREENSHOT), full_page=True)
    assert not console_errors, console_errors
    browser.close()

print(f"Local assembly UI smoke test passed: {SCREENSHOT}")
