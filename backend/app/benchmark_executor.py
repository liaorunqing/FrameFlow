from __future__ import annotations

import asyncio
from io import BytesIO
import time
from pathlib import Path

import httpx
from PIL import Image

from .benchmarking import BenchmarkExperiment, BenchmarkJob, BenchmarkStore, now
from .frame_canvas import normalize_to_canvas
from .image_providers import VolcArkSeedreamProvider
from .network import download_bytes
from .providers import (
    MiniMaxOfficialVideoProvider,
    VideoJob,
    VolcArkSeedanceProvider,
)
from .schemas import AssetKind, Project
from .vision import OllamaVisionService


class BenchmarkExecutor:
    """Run an approved provider benchmark with a hard pre-submit budget check."""

    def __init__(
        self,
        *,
        store: BenchmarkStore,
        upload_root: Path,
        artifact_root: Path,
    ) -> None:
        self.store = store
        self.upload_root = upload_root
        self.artifact_root = artifact_root
        self.artifact_root.mkdir(parents=True, exist_ok=True)

    def root(self, experiment: BenchmarkExperiment) -> Path:
        target = self.artifact_root / experiment.id
        target.mkdir(parents=True, exist_ok=True)
        return target

    def asset_path(self, project: Project, asset_id: str) -> Path:
        asset = next((item for item in project.assets if item.id == asset_id), None)
        if not asset:
            raise RuntimeError(f"实验参考素材不存在：{asset_id}")
        path = self.upload_root / project.id / Path(asset.url).name
        if not path.is_file():
            raise RuntimeError(f"实验参考素材文件不存在：{path}")
        return path

    @staticmethod
    def committed_cost(experiment: BenchmarkExperiment) -> float:
        keyframe = (
            experiment.keyframe_actual_cost_cny
            if experiment.keyframe_actual_cost_cny is not None
            else experiment.keyframe_cost_cny
            if experiment.keyframe_status in {"completed", "failed"}
            else 0
        )
        jobs = 0.0
        for job in experiment.jobs:
            if job.status in {"submitted", "completed", "failed", "reviewed"}:
                jobs += (
                    job.actual_cost_cny
                    if job.actual_cost_cny is not None
                    else job.estimated_cost_cny
                )
        return round(keyframe + jobs, 4)

    def assert_budget(
        self,
        experiment: BenchmarkExperiment,
        next_cost_cny: float,
    ) -> None:
        if not experiment.can_execute and experiment.status != "running":
            raise RuntimeError("实验尚未完成审批，禁止提交付费任务。")
        approved = experiment.approved_budget_cny or 0
        projected = self.committed_cost(experiment) + next_cost_cny
        ceiling = min(approved, experiment.hard_budget_cny)
        if projected > ceiling + 0.0001:
            raise RuntimeError(
                f"提交下一任务将使保守累计费用达到 ¥{projected:.2f}，"
                f"超过批准上限 ¥{ceiling:.2f}。"
            )

    async def ensure_composite_keyframe(
        self,
        experiment: BenchmarkExperiment,
        project: Project,
    ) -> None:
        if experiment.keyframe_status in {"not_required", "completed"}:
            return
        case = next(
            item for item in experiment.cases if item.id == "hand_interaction"
        )
        self.assert_budget(experiment, experiment.keyframe_cost_cny)
        references = [
            str(self.asset_path(project, asset_id))
            for asset_id in case.reference_asset_ids
        ]
        provider = VolcArkSeedreamProvider()
        try:
            result = await provider.generate(
                prompt=(
                    "生成一张16:9真实商业短片关键帧。参考图分别是商品、人物和家庭场景。"
                    "同一人物坐在参考场景中，一只手稳定托住参考商品，另一只手即将轻触商品。"
                    "商品轮廓、颜色和核心结构必须与参考图一致；人物脸型、年龄、发型和服装一致；"
                    "手指完整自然且不与商品融合。真实摄影，不要文字、Logo、水印或棚拍转台。"
                ),
                reference_images=references,
                aspect_ratio=project.aspect_ratio,
                max_images=1,
            )
            experiment.keyframe_output_url = result.output_urls[0]
            experiment.keyframe_actual_cost_cny = round(
                (experiment.keyframe_actual_cost_cny or 0) + result.estimated_cost,
                4,
            )
            self.store.put(experiment)
            async with httpx.AsyncClient(
                timeout=180,
                follow_redirects=True,
            ) as client:
                response = await client.get(result.output_urls[0])
                response.raise_for_status()
                content = response.content
            with Image.open(BytesIO(content)) as image:
                image.verify()
            raw_target = self.root(experiment) / "hand-interaction-keyframe-raw.jpg"
            raw_target.write_bytes(content)
            target = self.root(experiment) / "hand-interaction-keyframe.jpg"
            normalize_to_canvas(
                raw_target,
                target,
                aspect_ratio=project.aspect_ratio,
            )
            experiment.keyframe_path = str(target)
            experiment.keyframe_status = "completed"
            self.store.put(experiment)
        except Exception as exc:
            experiment.keyframe_status = "failed"
            experiment.keyframe_error = str(exc)
            self.store.put(experiment)
            raise

    def first_frame(
        self,
        experiment: BenchmarkExperiment,
        project: Project,
        job: BenchmarkJob,
    ) -> Path:
        case = next(item for item in experiment.cases if item.id == job.case_id)
        if case.requires_composite_keyframe:
            path = Path(experiment.keyframe_path)
            if not path.is_file():
                raise RuntimeError("手部交互复合关键帧尚未生成。")
            return path
        return self.asset_path(project, case.first_frame_asset_id)

    @staticmethod
    async def _extract_midpoint(video: Path, target: Path, seconds: float = 3) -> Path:
        process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            str(seconds),
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-vf",
            "scale=960:-2",
            str(target),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode or not target.is_file():
            raise RuntimeError(
                f"实验视频抽帧失败：{stderr.decode(errors='replace')[-400:]}"
            )
        return target

    async def _automatic_review(
        self,
        experiment: BenchmarkExperiment,
        project: Project,
        job: BenchmarkJob,
        video: Path,
    ) -> None:
        frame = await self._extract_midpoint(
            video,
            self.root(experiment) / f"{job.id.replace(':', '-')}-mid.jpg",
        )
        service = OllamaVisionService()
        product = [item for item in project.assets if item.kind == AssetKind.product]
        character = [item for item in project.assets if item.kind == AssetKind.character]
        scene = [item for item in project.assets if item.kind == AssetKind.scene]
        if job.case_id == "product_showcase":
            review = await service.compare_pair(
                reference=self.asset_path(project, product[0].id),
                candidate=frame,
                subject="product",
            )
            job.automatic_score = review.score
            job.automatic_passed = review.match and review.score >= 80
            job.failure_categories = [] if job.automatic_passed else ["product_identity"]
        elif job.case_id == "character_emotion":
            review = await service.compare_pair(
                reference=self.asset_path(project, character[0].id),
                candidate=frame,
                subject="character",
            )
            job.automatic_score = review.score
            job.automatic_passed = review.match and review.score >= 75
            job.failure_categories = [] if job.automatic_passed else ["character_identity"]
        else:
            review = await service.review_frame(
                product_reference=self.asset_path(project, product[0].id),
                character_reference=self.asset_path(project, character[0].id),
                scene_reference=self.asset_path(project, scene[0].id),
                candidate_frame=frame,
                expected_story_state="人物用一只手托住商品，另一只手自然轻触商品",
            )
            job.automatic_score = review.overall_score
            job.automatic_passed = review.passed
            job.failure_categories = [] if review.passed else ["hand_physics"]

    async def _submit_or_resume(
        self,
        experiment: BenchmarkExperiment,
        job: BenchmarkJob,
        first_frame: Path,
        prompt: str,
        aspect_ratio: str,
    ) -> VideoJob:
        provider = (
            MiniMaxOfficialVideoProvider()
            if job.provider == "minimax-official"
            else VolcArkSeedanceProvider()
        )
        if job.status == "submitted" and job.task_id:
            submitted = VideoJob(
                external_id=job.task_id,
                status="processing",
                provider=job.provider,
                model_id=job.model_id,
                estimated_cost=job.estimated_cost_cny,
            )
        else:
            self.assert_budget(experiment, job.estimated_cost_cny)
            submitted = await provider.create_boundary_task(
                prompt=prompt,
                duration=job.duration,
                first_frame=str(first_frame),
                last_frame=None,
                model_hint="story",
                aspect_ratio=aspect_ratio,
            )
            job.task_id = submitted.external_id
            job.status = "submitted"
            job.started_at = now()
            self.store.put(experiment)
        return await provider.wait_for_completion(submitted)

    async def execute(
        self,
        experiment: BenchmarkExperiment,
        project: Project,
    ) -> BenchmarkExperiment:
        if not experiment.can_execute and experiment.status != "running":
            raise RuntimeError("实验未获批准或素材不完整。")
        experiment.status = "running"
        self.store.put(experiment)
        await self.ensure_composite_keyframe(experiment, project)
        for job in experiment.jobs:
            if job.status in {"unavailable", "completed", "reviewed"}:
                continue
            case = next(item for item in experiment.cases if item.id == job.case_id)
            started = time.monotonic()
            try:
                completed = await self._submit_or_resume(
                    experiment,
                    job,
                    self.first_frame(experiment, project, job),
                    case.prompt,
                    project.aspect_ratio,
                )
                if not completed.output_url:
                    raise RuntimeError("供应商任务完成但没有返回下载地址。")
                target = self.root(experiment) / f"{job.case_id}-{job.provider}.mp4"
                target.write_bytes(
                    await download_bytes(completed.output_url, timeout=300)
                )
                job.output_path = str(target)
                job.output_url = (
                    f"/benchmark-artifacts/{experiment.id}/{target.name}"
                )
                job.finished_at = now()
                job.latency_seconds = round(time.monotonic() - started, 2)
                job.actual_cost_cny = completed.estimated_cost or job.estimated_cost_cny
                job.status = "completed"
                try:
                    await self._automatic_review(experiment, project, job, target)
                except Exception as review_error:
                    job.error = f"视频已生成，但本地自动质检失败：{review_error}"
            except Exception as exc:
                job.finished_at = now()
                job.latency_seconds = round(time.monotonic() - started, 2)
                job.actual_cost_cny = job.estimated_cost_cny
                job.status = "failed"
                job.error = str(exc)
            self.store.put(experiment)
        experiment.status = (
            "review_required"
            if any(job.status == "completed" for job in experiment.jobs)
            else "completed"
        )
        return self.store.put(experiment)
