from __future__ import annotations

import asyncio
import traceback
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import backend.app.main  # noqa: E402,F401 - loads delivery .env
from backend.app.vision import OllamaVisionService  # noqa: E402


PROJECT = "3951c396-6a9d-470c-9b70-81fe88957903"


async def main() -> None:
    try:
        result = await OllamaVisionService().review_frame(
            product_reference=ROOT / "backend/data/uploads" / PROJECT / "5650357b-7de8-4736-8d43-1983330f231d.jpg",
            character_reference=ROOT / "backend/data/workflow-artifacts" / PROJECT / "kf-000-attempt-03.jpg",
            scene_reference=ROOT / "backend/data/workflow-artifacts" / PROJECT / "kf-000-attempt-03.jpg",
            candidate_frame=ROOT / "backend/data/workflow-artifacts" / PROJECT / "kf-001-attempt-04.jpg",
            expected_story_state="child speaks to the toy in the same living room",
        )
        print(result.model_dump_json(indent=2))
    except Exception as exc:  # diagnostic entry point
        print(type(exc).__name__, repr(exc))
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
