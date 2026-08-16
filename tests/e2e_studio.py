from pathlib import Path

from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parents[1]
SCREENSHOT = ROOT / "frameflow-studio.png"


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
    console_errors: list[str] = []
    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)

    page.goto("http://127.0.0.1:5173")
    page.wait_for_load_state("networkidle")
    expect(page.get_by_text("让 AI 导演把你的商品素材")).to_be_visible()

    # Start from the first step even when the durable demo store already has a plan.
    page.get_by_role("button", name="创作设定").click()
    expect(page.get_by_text("告诉导演，你想拍什么")).to_be_visible()
    page.get_by_role("button", name="保存并选择素材").click()
    expect(page.get_by_text("给 AI 一组清晰的视觉证据")).to_be_visible()
    expect(page.get_by_text("配置 MINIMAX_API_KEY 后")).to_be_visible()

    page.get_by_role("button", name="让 AI 导演生成分镜").click()
    expect(page.get_by_text("导演分镜已经就位")).to_be_visible(timeout=10_000)
    expect(page.locator(".shot-card")).to_have_count(3)

    page.get_by_role("button", name="开始生成全部镜头").click()
    expect(page.get_by_text("正在制作你的广告")).to_be_visible(timeout=10_000)
    expect(page.get_by_text("成片准备好了")).to_be_visible(timeout=20_000)
    expect(page.get_by_text("广告成片 · V1")).to_be_visible()

    page.screenshot(path=str(SCREENSHOT), full_page=True)
    relevant_errors = [error for error in console_errors if "favicon" not in error.lower()]
    assert not relevant_errors, f"Browser console errors: {relevant_errors}"
    browser.close()

print(f"E2E passed; screenshot: {SCREENSHOT}")
