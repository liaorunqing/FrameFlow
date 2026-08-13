from __future__ import annotations

import asyncio
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / "backend" / ".env")


async def check(name: str, url: str, key: str) -> None:
    if not key:
        print(f"{name}: not_configured")
        return
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=8), trust_env=False) as client:
            response = await client.get(url, headers={"Authorization": f"Bearer {key}"})
        print(f"{name}: connected http={response.status_code}")
    except Exception as exc:
        print(f"{name}: connection_failed type={type(exc).__name__}")


async def main() -> None:
    await check(
        "qwen_dashscope",
        os.getenv("VISION_CLOUD_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/") + "/models",
        os.getenv("VISION_CLOUD_API_KEY", "") or os.getenv("DASHSCOPE_API_KEY", ""),
    )
    await check(
        "minimax",
        os.getenv("MINIMAX_API_BASE", "https://api-bj.minimaxi.com").rstrip("/") + "/v1/models",
        os.getenv("MINIMAX_API_KEY", ""),
    )


if __name__ == "__main__":
    asyncio.run(main())
