"""Headless smoke check for the creator-facing workflow studio."""

from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    console_errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        page.on(
            "console",
            lambda message: console_errors.append(message.text)
            if message.type == "error"
            else None,
        )
        # The studio intentionally keeps an SSE connection open; networkidle
        # would therefore never be reached on a healthy streaming page.
        response = page.goto("http://127.0.0.1:8001/", wait_until="domcontentloaded")
        assert response and response.ok, "FrameFlow page did not load"
        page.get_by_text("质检策略").wait_for(state="visible")
        selector = page.locator(".quality-mode-control select")
        assert selector.input_value() in {"advisory", "strict", "technical"}
        page.locator(".project-switch").click()
        with page.expect_response(lambda item: "preview.m3u8" in item.url) as preview_response:
            page.locator(".project-popover > button").filter(has_text="Plush Bear Test Ad").click()
        assert preview_response.value.ok
        page.locator(".rough-cut-badge").wait_for(state="visible")
        page.locator(".viewer video").wait_for(state="visible")
        page.screenshot(path=str(ROOT / "ui-stream-workbench.png"), full_page=True)
        assert not console_errors, "Browser console errors: " + " | ".join(console_errors)
        browser.close()


if __name__ == "__main__":
    main()
