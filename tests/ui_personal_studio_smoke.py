from pathlib import Path

from playwright.sync_api import sync_playwright


OUTPUT = Path(__file__).parent / "artifacts" / "personal-studio.png"


def main() -> None:
    errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        page.goto("http://127.0.0.1:5173", wait_until="networkidle")
        page.get_by_text("FrameFlow", exact=True).first.wait_for()
        for label in ("素材", "故事", "镜头", "成片"):
            assert page.get_by_role("button", name=label, exact=False).count() >= 1
        assert page.get_by_text("生成服务已连接", exact=True).count() == 1
        page.get_by_role("button", name="新建项目", exact=True).click()
        page.get_by_text("选择创作基线", exact=True).wait_for()
        for template in ("玩具纪录片", "数码纪录片", "日用纪录片"):
            assert page.get_by_role("button", name=template, exact=False).count() == 1
        assert page.get_by_text("不会提交付费生成任务", exact=False).count() == 1
        page.get_by_role("button", name="关闭", exact=True).click()
        step_buttons = page.locator(".stepper button")
        step_buttons.nth(0).click()
        page.get_by_text("把真实素材交给导演", exact=True).wait_for()
        assert page.get_by_text("商品", exact=True).count() >= 1
        step_buttons.nth(1).click()
        step_buttons.nth(2).click()
        page.get_by_text("一次只解决一个镜头", exact=True).wait_for()
        step_buttons.nth(3).click()
        page.get_by_text("把故事收束成一条完整广告", exact=True).wait_for()
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(OUTPUT), full_page=True)
        assert not errors, errors
        browser.close()


if __name__ == "__main__":
    main()
