from playwright.sync_api import sync_playwright


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1280, "height": 950})
    errors: list[str] = []
    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
    page.goto("http://127.0.0.1:8010", wait_until="networkidle")
    page.get_by_role("button", name="模型配置").click()
    assert page.get_by_text("MiniMax 海螺 API Key").is_visible()
    assert page.get_by_text("Seedance / 火山方舟 API Key").is_visible()
    choices = page.locator(".provider-choice button")
    assert choices.count() == 2
    assert choices.nth(0).is_visible()
    assert choices.nth(1).is_visible()
    choices.nth(1).click()
    assert "selected" in (choices.nth(1).get_attribute("class") or "")
    page.screenshot(path="tests/artifacts/setup-two-providers.png", full_page=True)
    assert not errors, errors
    browser.close()
