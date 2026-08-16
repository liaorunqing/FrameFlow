from pathlib import Path

from playwright.sync_api import sync_playwright


PROJECT_ID = "402cbe1e-eddf-4eb6-9756-befad6fa443c"
SCREENSHOT = Path(
    "backend/data/workflow-artifacts"
    f"/{PROJECT_ID}/delivery-ui.png"
).resolve()


def main() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto("http://127.0.0.1:5173/", wait_until="networkidle")
        page.get_by_text("Plush Bear Test Ad", exact=False).first.wait_for(timeout=15_000)
        workflow = page.evaluate(
            """async (projectId) => {
                const response = await fetch(`/api/projects/${projectId}/workflow`);
                if (!response.ok) throw new Error(`workflow ${response.status}`);
                return await response.json();
            }""",
            PROJECT_ID,
        )
        assert workflow["status"] == "completed", workflow["status"]
        assert workflow["progress"] == 100, workflow["progress"]
        assert abs(workflow["actual_cost_cny"] - 8.75) < 0.001
        assert "无法访问此站点" not in page.locator("body").inner_text()
        SCREENSHOT.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SCREENSHOT), full_page=True)
        print(
            {
                "title": page.title(),
                "workflow_status": workflow["status"],
                "progress": workflow["progress"],
                "actual_cost_cny": workflow["actual_cost_cny"],
                "screenshot": str(SCREENSHOT),
            }
        )
        browser.close()


if __name__ == "__main__":
    main()
