from pathlib import Path

from playwright.sync_api import sync_playwright


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 1100})
    errors: list[str] = []
    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
    page.goto("http://127.0.0.1:5173", wait_until="networkidle")
    page.get_by_text("模型路由", exact=True).click()
    page.get_by_text("Video Shotcraft 镜头知识库", exact=True).wait_for(timeout=10_000)
    visible = [
        "Video Shotcraft 镜头知识库", "PySceneDetect 镜头连续性", "DOVER-Mobile 观感质量",
        "VBench / VBench-Long", "Remotion 可编程包装", "节拍与响度母版",
    ]
    for label in visible:
        assert page.get_by_text(label, exact=True).is_visible(), label
    target = Path("tests/artifacts/integrations-ui.png").resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(target), full_page=True)
    assert not errors, errors
    print(target)
    browser.close()
