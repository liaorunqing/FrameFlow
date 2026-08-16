from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8000/"

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    console_errors: list[str] = []
    failed: list[tuple[int, str]] = []
    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
    page.on("response", lambda response: failed.append((response.status, response.url)) if response.status >= 400 else None)
    page.goto(BASE, wait_until="networkidle")

    assert page.locator(".business-steps span").count() == 4
    assert page.locator(".generate-button").is_visible()
    assert page.locator(".media-controls").is_visible()
    assert page.locator(".director-console").count() == 0

    page.locator(".studio-nav button").nth(1).click()
    page.wait_for_timeout(200)
    assert page.locator(".director-console").is_visible()
    assert page.locator(".control-matrix select").count() == 10
    page.locator(".director-console summary").click()
    assert not page.locator(".control-matrix").is_visible()
    page.locator(".director-console summary").click()
    assert page.locator(".control-matrix").is_visible()

    page.locator(".studio-nav button").nth(0).click()
    page.locator(".library-button").click()
    assert page.locator(".library-grid").is_visible()
    page.locator(".library-button").click()
    assert not page.locator(".library-grid").is_visible()

    assert not console_errors, console_errors
    assert not failed, failed
    browser.close()
