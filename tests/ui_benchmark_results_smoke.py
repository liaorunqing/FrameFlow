from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
SCREENSHOT = ROOT / "backend" / "data" / "debug" / "provider-benchmark-results-ui.png"


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 1200})
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

    results = page.locator(".benchmark-results")
    results.wait_for()
    assert results.locator(".result-row").count() == 6
    assert results.get_by_text("打开视频", exact=True).count() == 5
    assert results.get_by_text("失败", exact=True).count() == 1
    assert "17.88" in page.locator(".benchmark-summary").inner_text()

    first_video_url = results.get_by_text("打开视频", exact=True).first.get_attribute("href")
    assert first_video_url
    response = page.request.get(f"http://127.0.0.1:8001{first_video_url}")
    assert response.ok
    assert "video/mp4" in response.headers.get("content-type", "")

    SCREENSHOT.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SCREENSHOT), full_page=True)
    assert not console_errors, console_errors
    browser.close()

print(f"Benchmark results UI smoke test passed: {SCREENSHOT}")
