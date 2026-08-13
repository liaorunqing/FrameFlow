from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.benchmark_executor import BenchmarkExecutor
from backend.app.main import DATA_DIR, UPLOAD_DIR, benchmark_store, store
from backend.app.schemas import Project


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("experiment_id")
    args = parser.parse_args()
    experiment = benchmark_store.get(args.experiment_id)
    if not experiment:
        raise SystemExit("Benchmark experiment not found.")
    raw_project = store.get_project(experiment.project_id)
    if not raw_project:
        raise SystemExit("Benchmark project not found.")
    executor = BenchmarkExecutor(
        store=benchmark_store,
        upload_root=UPLOAD_DIR,
        artifact_root=DATA_DIR / "benchmark-artifacts",
    )
    result = asyncio.run(
        executor.execute(experiment, Project.model_validate(raw_project))
    )
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
