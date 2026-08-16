from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
SCREENSHOT = ROOT / "backend" / "data" / "debug" / "truth-and-blind-review-ui.png"


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
    page.get_by_text("素材与Brief", exact=True).click()
    page.get_by_text("生成模型不能猜测的事实", exact=True).wait_for()
    assert page.get_by_text("核对并批准", exact=True).count() == 1

    page.get_by_text("模型路由", exact=True).click()
    page.get_by_text("BLIND HUMAN REVIEW", exact=True).wait_for()
    page.get_by_text("开始盲审", exact=True).click()
    card = page.locator(".blind-review-card")
    card.locator(".blind-review-body video").wait_for()
    assert "minimax" not in card.inner_text().lower()
    assert "seedance" not in card.inner_text().lower()
    SCREENSHOT.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SCREENSHOT), full_page=True)
    assert not console_errors, console_errors
    browser.close()

print(f"Truth ledger and blind review UI smoke test passed: {SCREENSHOT}")
