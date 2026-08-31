from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any


class JsonStore:
    """Small durable store for the MVP; replace with PostgreSQL behind this boundary."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        if not self.path.exists():
            self._write(self._empty())

    @staticmethod
    def _empty() -> dict[str, dict[str, Any]]:
        return {"projects": {}, "tasks": {}}

    def _read(self) -> dict[str, Any]:
        # The data volume may be new, rotated or cleared while a long-running
        # process is alive. Recreate the MVP store instead of turning a missing
        # local file into a permanent HTTP 500 loop.
        if not self.path.exists():
            initial = self._empty()
            self._write(initial)
            return initial
        # utf-8-sig accepts both ordinary UTF-8 and files created by Windows
        # PowerShell 5, whose `-Encoding utf8` prepends a BOM.
        with self.path.open("r", encoding="utf-8-sig") as handle:
            return json.load(handle)

    def _write(self, data: dict[str, Any]) -> None:
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, default=str)
        temporary.replace(self.path)

    def list_projects(self) -> list[dict[str, Any]]:
        with self._lock:
            projects = list(self._read()["projects"].values())
        return sorted(projects, key=lambda item: item["updated_at"], reverse=True)

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._read()["projects"].get(project_id)

    def put_project(self, project: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            data = self._read()
            data["projects"][project["id"]] = project
            self._write(data)
        return project

    def delete_project(self, project_id: str) -> bool:
        """Delete one exact project id; intended for deterministic test cleanup."""
        with self._lock:
            data = self._read()
            removed = data["projects"].pop(project_id, None)
            self._write(data)
        return removed is not None

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._read()["tasks"].get(task_id)

    def put_task(self, task: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            data = self._read()
            data["tasks"][task["id"]] = task
            self._write(data)
        return task

    def update_task(self, task_id: str, **changes: Any) -> dict[str, Any]:
        with self._lock:
            data = self._read()
            task = data["tasks"][task_id]
            task.update(changes)
            task["updated_at"] = datetime.now().isoformat()
            data["tasks"][task_id] = task
            self._write(data)
        return task
