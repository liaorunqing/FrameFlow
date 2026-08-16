from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
SCREENSHOT = ROOT / "backend" / "data" / "debug" / "provider-benchmark-ui.png"


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 1100})
    console_errors: list[str] = []
    page.on(
        "console",
        lambda message: console_errors.append(message.text)
        if message.type == "error"
        else None,
    )
    page.goto("http://127.0.0.1:5173", wait_until="networkidle")
    page.get_by_text("模型路由", exact=True).click()
    page.get_by_text("PROVIDER BENCHMARK / DRY RUN", exact=True).wait_for()
    card = page.locator(".benchmark-card")
    assert card.count() == 1

    button = card.get_by_role(
        "button",
        name="重新计算实验预算"
        if card.get_by_text("重新计算实验预算", exact=True).count()
        else "生成实验预算",
    )
    with page.expect_response(
        lambda response: "/benchmarks" in response.url
        and response.request.method == "POST"
    ) as response_info:
        button.click()
    assert response_info.value.ok
    page.get_by_text("正式执行前必须逐字确认", exact=True).wait_for()
    assert card.locator(".benchmark-cases article").count() == 3
    assert "¥" in card.inner_text()
    SCREENSHOT.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SCREENSHOT), full_page=True)
    assert not console_errors, console_errors
    browser.close()

print(f"Benchmark UI smoke test passed: {SCREENSHOT}")
