from pathlib import Path

from playwright.sync_api import sync_playwright


BASE_URL = "http://127.0.0.1:8010"


def main() -> None:
    errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        page.goto(BASE_URL, wait_until="networkidle")
        page.get_by_role("button", name="创作", exact=True).click()
        page.get_by_text("广告脚本模板").wait_for()
        template = page.locator("label", has_text="广告脚本模板").locator("select")
        template.select_option("documentary")
        assert template.input_value() == "documentary"
        assert page.get_by_text("产品规格").count() == 1
        assert page.get_by_text("真实尺寸").count() == 1
        if page.get_by_role("button", name="自定义修改脚本").count():
            page.get_by_role("button", name="自定义修改脚本").click()
            assert page.get_by_role("button", name="保存脚本修改").count() == 1
            assert page.locator(".script-preview textarea").count() >= 3
        page.get_by_role("button", name="素材", exact=True).click()
        assert page.locator(".asset-board").count() == 1
        Path("tests/artifacts").mkdir(parents=True, exist_ok=True)
        page.screenshot(path="tests/artifacts/feedback-smoke.png", full_page=True)
        browser.close()
    if errors:
        raise AssertionError(f"browser console errors: {errors}")


if __name__ == "__main__":
    main()
