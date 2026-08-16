from __future__ import annotations

import asyncio
import json
from os import getenv
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

from .audio import build_audio_timeline, synthesize_windows_narration, write_srt
from .composer import compose_campaign
from .creative_post import build_creative_post_plan, write_feature_state_srt
from .frame_canvas import normalize_to_canvas
from .identity_lock import build_identity_pack
from .image_providers import VolcArkSeedreamProvider
from .integrations import shotcraft_summary
from .network import download_bytes
from .orchestration import (
    WorkflowNode,
    WorkflowRun,
    WorkflowStore,
    complete_node,
    fail_node,
    node_by_id,
    start_node,
)
from .production import ProductionPlan, build_production_plan
from .providers import get_provider
from .postprocess import interpolate_video_rife, upscale_video_realesrgan
from .quality_control import decide_keyframe_quality, decide_video_quality
from .routing import route_shot
from .scene_analysis import analyze_internal_cuts
from .schemas import AssetKind, Project
from .vision import OllamaVisionService


def _deterministic_frame_metrics(frames: list[Path]) -> dict[str, object]:
    """Measure exposure and large visual jumps without asking a language model."""
    samples: list[dict[str, float]] = []
    previous: Image.Image | None = None
    jumps: list[float] = []
    for frame in frames:
        with Image.open(frame) as source:
            gray = source.convert("L").resize((160, 90))
            histogram = gray.histogram()
            pixels = max(1, sum(histogram))
            mean_luma = float(ImageStat.Stat(gray).mean[0])
            samples.append({
                "mean_luma": round(mean_luma, 2),
                "dark_ratio": round(sum(histogram[:24]) / pixels, 4),
                "bright_ratio": round(sum(histogram[240:]) / pixels, 4),
            })
            if previous is not None:
                diff = ImageChops.difference(previous, gray)
                jumps.append(round(float(ImageStat.Stat(diff).rms[0]) / 255.0, 4))
            previous = gray.copy()
    severe_dark = any(
        item["mean_luma"] < 24 or item["dark_ratio"] > 0.72 for item in samples
    )
    severe_bright = any(
        item["mean_luma"] > 238 or item["bright_ratio"] > 0.72 for item in samples
    )
    abrupt_jump = any(value > 0.48 for value in jumps)
    return {
        "samples": samples,
        "frame_jumps": jumps,
        "severe_dark_frame": severe_dark,
        "severe_bright_frame": severe_bright,
        "abrupt_visual_jump": abrupt_jump,
        "passed": not (severe_dark or severe_bright or abrupt_jump),
    }


class PipelineExecutor:
    """Execute one production node while preserving workflow state.

    Billable nodes are never entered unless the request carries an explicit
    confirmation. Every provider output is downloaded immediately so temporary
    cloud URLs do not become the project's source of truth.
    """

    def __init__(
        self,
        *,
        workflow_store: WorkflowStore,
        upload_root: Path,
        artifact_root: Path,
    ) -> None:
        self.workflow_store = workflow_store
        self.upload_root = upload_root
        self.artifact_root = artifact_root
        self.artifact_root.mkdir(parents=True, exist_ok=True)

    def project_root(self, project_id: str) -> Path:
        root = self.artifact_root / project_id
        root.mkdir(parents=True, exist_ok=True)
        return root

    def asset_paths(self, project: Project) -> dict[AssetKind, list[Path]]:
        result: dict[AssetKind, list[Path]] = {kind: [] for kind in AssetKind}
        for asset in project.assets:
            candidate = self.upload_root / project.id / Path(asset.url).name
            if candidate.is_file():
                result[asset.kind].append(candidate)
        return result

    @staticmethod
    def _attempt_output(run: WorkflowRun, node_id: str, key: str) -> str:
        node = node_by_id(run, node_id)
        if not node.attempts:
            raise RuntimeError(f"上游节点没有产物：{node_id}")
        value = node.attempts[-1].outputs.get(key)
        if not value:
            raise RuntimeError(f"上游节点缺少产物 {key}：{node_id}")
        return value

    @staticmethod
    def _optional_attempt_output(run: WorkflowRun, node_id: str, key: str) -> str:
        try:
            node = node_by_id(run, node_id)
        except ValueError:
            return ""
        if not node.attempts:
            return ""
        return node.attempts[-1].outputs.get(key, "")

    @staticmethod
    def select_video_provider(
        *,
        node: WorkflowNode,
        primary_provider: str,
        fallback_provider: str,
        provider_override: str = "",
    ) -> tuple[str, bool]:
        """Choose a retry provider without making a new billable request.

        A manual retry still needs the normal billable confirmation.  On its
        second attempt, a failed primary provider is deterministically routed
        to the configured fallback unless an operator explicitly overrides it.
        """
        if provider_override.strip():
            return provider_override.strip(), False
        previous_provider = (
            node.attempts[-2].provider if len(node.attempts) > 1 else ""
        )
        can_fallback = fallback_provider not in {"", "demo", primary_provider}
        if previous_provider == primary_provider and can_fallback:
            return fallback_provider, True
        return primary_provider, False

    async def execute(
        self,
        *,
        project: Project,
        run: WorkflowRun,
        node_id: str,
        confirm_billable: bool,
        provider_override: str = "",
    ) -> WorkflowRun:
        node = node_by_id(run, node_id)
        if node.billable and not confirm_billable:
            raise PermissionError(
                f"节点“{node.label}”会调用付费API，必须显式确认本次计费。"
            )
        if node.status not in {"ready", "failed"}:
            raise ValueError(f"节点当前状态不可执行：{node.status}")

        start_node(run, node_id)
        self.workflow_store.put(run)
        try:
            plan = build_production_plan(project)
            if node.kind == "asset_analysis":
                await self._analyze_assets(project, run, node)
            elif node.kind == "story_plan":
                await self._story_plan(project, run, node)
            elif node.kind == "keyframe_generation":
                await self._generate_keyframe(project, plan, run, node)
            elif node.kind == "keyframe_review":
                await self._review_keyframe(project, run, node)
            elif node.kind == "video_generation":
                await self._generate_video(
                    project,
                    plan,
                    run,
                    node,
                    provider_override=provider_override,
                )
            elif node.kind == "video_review":
                await self._review_video(project, run, node)
            elif node.kind == "audio_timeline":
                await self._build_audio(project, run, node)
            elif node.kind == "composition":
                await self._compose(project, run, node)
            elif node.kind == "upscale":
                await self._upscale(project, run, node)
            elif node.kind == "frame_interpolation":
                await self._interpolate(project, run, node)
            else:
                raise NotImplementedError(
                    f"节点“{node.label}”的正式执行器将在声音与精剪阶段接入。"
                )
        except Exception as exc:
            fail_node(run, node_id, str(exc))
            self.workflow_store.put(run)
            raise
        return self.workflow_store.put(run)

    async def _analyze_assets(
        self,
        project: Project,
        run: WorkflowRun,
        node: WorkflowNode,
    ) -> None:
        paths = self.asset_paths(project)
        service = OllamaVisionService()
        profiles: dict[str, dict] = {}
        analysis_errors: list[str] = []
        for kind, candidates in paths.items():
            for path in candidates:
                try:
                    profile = await service.analyze_asset(
                        image_path=path,
                        asset_type=kind.value,
                    )
                    profiles[str(path)] = profile.model_dump(mode="json")
                except Exception as exc:
                    # A local vision service is an enhancement, not a reason to
                    # block a paid run once the user has approved the Brand
                    # Bible.  Preserve the error as auditable evidence and let
                    # the approved asset constraints drive generation.
                    analysis_errors.append(f"{path.name}: {exc}")
        target = self.project_root(project.id) / "asset-profiles.json"
        target.write_text(
            json.dumps(profiles, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        manifest = {
            "version": "identity-reference-v1",
            "project_id": project.id,
            "policy": {
                "product": "真实商品图是身份最高优先级，不允许用生成图反向覆盖商品事实。",
                "character": "人物参考用于脸部、发型、年龄感和服装连续性。",
                "scene": "场景参考用于空间、家具、屏幕方向和光线连续性。",
                "brand": "Logo与品牌文字仅用于后期合成，不交给生成模型重绘。",
            },
            "assets": [
                {
                    "asset_id": asset.id,
                    "kind": asset.kind.value,
                    "path": str(self.upload_root / project.id / Path(asset.url).name),
                    "profile": profiles.get(
                        str(self.upload_root / project.id / Path(asset.url).name), {}
                    ),
                }
                for asset in project.assets
            ],
        }
        manifest_target = self.project_root(project.id) / "identity-reference-manifest.json"
        manifest_target.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        identity_pack = build_identity_pack(
            project_id=project.id,
            product_images=paths[AssetKind.product],
            output_root=self.project_root(project.id),
        )
        complete_node(
            run,
            node.id,
            outputs={
                "profiles_json": str(target),
                "identity_manifest_json": str(manifest_target),
                "identity_pack_json": str(identity_pack),
                "asset_count": str(len(profiles)),
                "analysis_errors": " | ".join(analysis_errors),
                "fallback": "approved_brand_bible" if analysis_errors else "",
            },
            actual_cost_cny=0,
        )

    async def _story_plan(
        self,
        project: Project,
        run: WorkflowRun,
        node: WorkflowNode,
    ) -> None:
        if not project.creative_plan:
            raise RuntimeError("项目尚未生成故事与分镜。")
        target = self.project_root(project.id) / "creative-plan.json"
        target.write_text(
            project.creative_plan.model_dump_json(indent=2),
            encoding="utf-8",
        )
        complete_node(
            run,
            node.id,
            outputs={
                "creative_plan_json": str(target),
                "script_version": project.creative_plan.version_id,
                "source_asset_ids": ",".join(project.creative_plan.source_asset_ids),
                "shotcraft_summary": json.dumps(
                    shotcraft_summary(), ensure_ascii=False, separators=(",", ":")
                ),
            },
            actual_cost_cny=0,
            cache_hit=True,
        )

    async def _generate_keyframe(
        self,
        project: Project,
        plan: ProductionPlan,
        run: WorkflowRun,
        node: WorkflowNode,
    ) -> None:
        frame_id = node.id.split(":", 1)[1]
        spec = next((item for item in plan.keyframes if item.id == frame_id), None)
        if not spec:
            raise RuntimeError(f"关键帧规格不存在：{frame_id}")
        references: list[str] = []
        reference_root = self.project_root(project.id) / "normalized-references"
        for candidates in self.asset_paths(project).values():
            for path in candidates:
                # Some source uploads are thumbnail-sized.  Providers reject
                # them before billing, so create a deterministic full-canvas
                # reference locally.  The foreground is never cropped.
                normalized = reference_root / f"{path.stem}-{project.aspect_ratio.replace(':', 'x')}.jpg"
                normalize_to_canvas(path, normalized, aspect_ratio=project.aspect_ratio)
                references.append(str(normalized))
        # With no uploaded character/scene reference, the opening boundary is
        # the identity anchor. Reusing it prevents each later frame from
        # inventing a new child, outfit, room and product scale.
        if frame_id != "kf-000":
            opening = node_by_id(run, "keyframe:kf-000")
            if opening.attempts:
                anchor = Path(opening.attempts[-1].outputs.get("image_path", ""))
                if anchor.is_file():
                    references.append(str(anchor))
            boundary_index = int(frame_id.split("-")[-1])
            if boundary_index > 1:
                previous_id = f"keyframe:kf-{boundary_index - 1:03d}"
                previous = node_by_id(run, previous_id)
                if previous.attempts:
                    previous_path = Path(previous.attempts[-1].outputs.get("image_path", ""))
                    if previous_path.is_file():
                        references.append(str(previous_path))
        if not references:
            raise RuntimeError("关键帧生成至少需要一张本地参考图。")
        provider = VolcArkSeedreamProvider()
        repair_patch = self._optional_attempt_output(
            run,
            f"keyframe-review:{frame_id}",
            "repair_prompt_patch",
        )
        prompt = spec.prompt
        if repair_patch and len(node.attempts) > 1:
            prompt = f"{prompt}\n\n本次局部修复必须满足：{repair_patch}"
        result = await provider.generate(
            prompt=prompt,
            reference_images=references,
            aspect_ratio=project.aspect_ratio,
            max_images=1,
        )
        content = await download_bytes(
            result.output_urls[0], timeout=180, media_kind="image"
        )
        raw_target = self.project_root(project.id) / (
            f"{frame_id}-attempt-{len(node.attempts):02d}-raw.jpg"
        )
        raw_target.write_bytes(content)
        target = self.project_root(project.id) / f"{frame_id}-attempt-{len(node.attempts):02d}.jpg"
        canvas_width, canvas_height = normalize_to_canvas(
            raw_target,
            target,
            aspect_ratio=project.aspect_ratio,
        )
        complete_node(
            run,
            node.id,
            outputs={
                "image_path": str(target),
                "image_url": result.output_urls[0],
                "provider": provider.name,
                "model_id": result.model_id,
                "raw_image_path": str(raw_target),
                "canvas": f"{canvas_width}x{canvas_height}",
                "canvas_strategy": "full-bleed-cover-crop",
                "repair_prompt_applied": repair_patch if len(node.attempts) > 1 else "",
            },
            actual_cost_cny=result.estimated_cost,
        )

    async def _review_keyframe(
        self,
        project: Project,
        run: WorkflowRun,
        node: WorkflowNode,
    ) -> None:
        frame_id = node.id.split(":", 1)[1]
        candidate = Path(self._attempt_output(
            run,
            f"keyframe:{frame_id}",
            "image_path",
        ))
        paths = self.asset_paths(project)
        service = OllamaVisionService(purpose="review")
        boundary_index = int(frame_id.split("-")[-1])
        target_shot = next(
            (
                shot for shot in project.creative_plan.shots
                if shot.index == min(
                    len(project.creative_plan.shots),
                    boundary_index + 1,
                )
            ),
            None,
        ) if project.creative_plan else None
        expected = (
            f"故事状态：{target_shot.narrative_beat}；"
            f"动作过程：{target_shot.action}；"
            f"预期画面：{target_shot.visual}。"
            "关键帧是一个静止瞬间，只需清楚呈现该动作的核心落点或可见结果，"
            "不要求把点头、摩挲、呼吸、移动机位等瞬时细节同时冻结在一帧；"
            "只有核心故事结果缺失或相反时才判定 story mismatch。"
            if target_shot else "与相邻故事镜头形成自然连续的真实广告画面"
        )

        product = paths[AssetKind.product]
        character = paths[AssetKind.character]
        scene = paths[AssetKind.scene]
        opening_anchor: Path | None = None
        if frame_id != "kf-000":
            opening_node = node_by_id(run, "keyframe:kf-000")
            if opening_node.attempts:
                candidate_anchor = Path(opening_node.attempts[-1].outputs.get("image_path", ""))
                if candidate_anchor.is_file():
                    opening_anchor = candidate_anchor
        if product and opening_anchor:
            # One structured multimodal request is both faster and more stable
            # than four sequential cloud calls. The opening image supplies the
            # missing character and scene identity references.
            scene_anchor = opening_anchor
            if boundary_index > 1:
                scene_node = node_by_id(run, "keyframe:kf-001")
                if scene_node.attempts:
                    first_scene = Path(scene_node.attempts[-1].outputs.get("image_path", ""))
                    if first_scene.is_file():
                        scene_anchor = first_scene
            review = await service.review_frame(
                product_reference=product[0],
                character_reference=opening_anchor,
                scene_reference=scene_anchor,
                candidate_frame=candidate,
                expected_story_state=expected,
            )
            report = review.model_dump(mode="json")
            passed = review.passed
            score = review.overall_score
        elif product and character and scene:
            review = await service.review_frame(
                product_reference=product[0],
                character_reference=character[0],
                scene_reference=scene[0],
                candidate_frame=candidate,
                expected_story_state=expected,
            )
            report = review.model_dump(mode="json")
            passed = review.passed
            score = review.overall_score
        else:
            review = await service.review_composition(
                candidate=candidate,
                expected_story_state=expected,
                expected_people_count=1 if character else 0,
                allow_camera_gaze=False,
            )
            report = review.model_dump(mode="json")
            passed = review.story_match
            score = review.score

        # Identity alone is not enough: a visually accurate but static/repeated
        # frame must not pass when it fails the intended narrative beat.
        if "story_mismatches" not in report:
            composition = await service.review_composition(
                candidate=candidate,
                expected_story_state=expected,
                expected_people_count=1 if character else 0,
                allow_camera_gaze=False,
            )
            report["composition"] = composition.model_dump(mode="json")
            report["story_match"] = composition.story_match
            report["story_compliance"] = min(
                int(report.get("story_compliance", 100)), composition.score
            )
            report["issues"] = list(dict.fromkeys([
                *report.get("issues", []),
                *composition.story_mismatches,
                *composition.physical_issues,
            ]))
            if composition.repair_instruction:
                report["repair_prompt"] = "; ".join(filter(None, [
                    str(report.get("repair_prompt", "")),
                    composition.repair_instruction,
                ]))
            passed = passed and composition.story_match and composition.score >= 70
            score = min(score, composition.score)
            report["passed"] = passed
            report["overall_score"] = score

        generation = node_by_id(run, f"keyframe:{frame_id}")
        decision = decide_keyframe_quality(
            report=report,
            generation=generation,
            review_node_id=node.id,
        )
        report["decision"] = decision.model_dump(mode="json")
        target = self.project_root(project.id) / f"{frame_id}-review.json"
        target.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        complete_node(
            run,
            node.id,
            outputs={
                "review_json": str(target),
                "passed": str(passed).lower(),
                "score": str(score),
                "failure_categories": ",".join(decision.categories),
                "repair_action": (
                    decision.repair_steps[0].label if decision.repair_steps else ""
                ),
                "repair_prompt_patch": decision.prompt_patch,
                "remaining_retries": str(decision.remaining_retries),
                "retry_budget_cny": str(decision.retry_budget_cny),
                "within_budget": str(decision.within_budget).lower(),
            },
            actual_cost_cny=0,
            # A successful cloud-vision review is final.  Only a failed
            # review should pause the one-click workflow for operator action.
            review_required=not passed,
        )
        node.quality_decision = decision.model_dump(mode="json")
        node.issues = [] if passed else [decision.summary]

    async def _build_audio(
        self,
        project: Project,
        run: WorkflowRun,
        node: WorkflowNode,
    ) -> None:
        timeline = build_audio_timeline(project)
        root = self.project_root(project.id)
        timeline_json = root / "audio-timeline.json"
        subtitle_path = write_srt(timeline, root / "subtitles.srt")
        if not project.subtitles_enabled:
            subtitle_path.write_text("", encoding="utf-8")
        narration_path = await synthesize_windows_narration(
            timeline,
            root / "narration.wav",
            voice=project.voice_id,
            rate=round((project.voice_speed - 1.0) * 10),
            cloud_voice_id=project.voice_id,
            cloud_speed=project.voice_speed,
            allow_paid_fallback=not project.voice_id.startswith("Microsoft "),
        )
        timeline_json.write_text(
            timeline.model_dump_json(indent=2),
            encoding="utf-8",
        )
        complete_node(
            run,
            node.id,
            outputs={
                "timeline_json": str(timeline_json),
                "subtitle_path": str(subtitle_path),
                "narration_path": str(narration_path),
                "duration": str(timeline.duration),
            },
            actual_cost_cny=0,
        )

    async def _compose(
        self,
        project: Project,
        run: WorkflowRun,
        node: WorkflowNode,
    ) -> None:
        if not project.creative_plan:
            raise RuntimeError("项目缺少可合成的故事分镜。")
        clip_paths: list[Path] = []
        clip_durations: list[float] = []
        clip_provenance: list[dict[str, object]] = []
        workflow_video_nodes = [
            item for item in run.nodes if item.kind == "video_generation"
        ]
        for position, shot in enumerate(project.creative_plan.shots):
            try:
                video_node = node_by_id(run, f"video:{shot.id}")
            except ValueError:
                if position >= len(workflow_video_nodes):
                    raise RuntimeError(
                        f"Shot {shot.index} has no matching workflow video node."
                    )
                video_node = workflow_video_nodes[position]
            if not video_node.attempts:
                raise RuntimeError(f"Shot {shot.index} has no generated video attempt.")
            outputs = video_node.attempts[-1].outputs
            provider = outputs.get("provider", "").strip()
            if not provider or provider == "demo":
                raise RuntimeError(
                    f"Shot {shot.index} is not a real provider-generated clip; composition is blocked."
                )
            path = Path(self._attempt_output(run, video_node.id, "video_path"))
            if not path.is_file():
                raise RuntimeError(f"镜头文件不存在：{shot.title}")
            clip_paths.append(path)
            clip_durations.append(float(shot.duration))
            clip_provenance.append({
                "shot_id": shot.id,
                "shot_index": shot.index,
                "title": shot.title,
                "provider": provider,
                "model_id": outputs.get("model_id", ""),
                "video_path": str(path),
                "first_frame_source": outputs.get("first_frame_source", ""),
                "actual_last_frame_path": outputs.get("actual_last_frame_path", ""),
                "actual_cost_cny": video_node.actual_cost_cny,
                "is_real_generated_video": True,
            })

        provenance_path = self.project_root(project.id) / "clip-provenance.json"
        provenance_path.write_text(
            json.dumps({
                "project_id": project.id,
                "all_clips_real_generated": True,
                "clip_count": len(clip_provenance),
                "clips": clip_provenance,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        narration = Path(self._attempt_output(
            run,
            "audio-timeline",
            "narration_path",
        ))
        subtitles = Path(self._attempt_output(
            run,
            "audio-timeline",
            "subtitle_path",
        ))
        keyframe_nodes = [
            item for item in run.nodes if item.kind == "keyframe_generation"
        ]
        cta_background: Path | None = None
        if keyframe_nodes:
            candidate = Path(self._attempt_output(
                run,
                keyframe_nodes[-1].id,
                "image_path",
            ))
            if candidate.is_file():
                cta_background = candidate
        logo_candidates = self.asset_paths(project)[AssetKind.brand]
        narrative_duration = sum(clip_durations)
        spare_duration = max(0.0, float(project.duration) - narrative_duration)
        output = self.project_root(project.id) / "campaign-master.mp4"
        creative_post = build_creative_post_plan(project, self.project_root(project.id))
        feature_srt = write_feature_state_srt(
            creative_post,
            self.project_root(project.id) / "feature-states.srt",
        )
        await compose_campaign(
            clip_paths=clip_paths,
            clip_durations=clip_durations,
            aspect_ratio=project.aspect_ratio,
            narration_path=narration,
            subtitle_path=subtitles,
            output_path=output,
            cta_background=cta_background if spare_duration >= 2.5 else None,
            logo_path=logo_candidates[0] if logo_candidates else None,
            cta_headline=project.creative_plan.call_to_action,
            product_name=project.product_name,
            cta_duration=spare_duration if spare_duration >= 2.5 else 0,
            bgm_path=Path(creative_post.bgm_path) if creative_post.bgm_path else None,
            bgm_gain=creative_post.bgm_gain,
            sound_cues=[cue.model_dump(mode="json") for cue in creative_post.sound_cues],
            feature_state_subtitle_path=feature_srt,
        )
        complete_node(
            run,
            node.id,
            outputs={
                "video_path": str(output),
                "video_url": f"/artifacts/{project.id}/{output.name}?v={output.stat().st_mtime_ns}",
                "duration": str(project.duration),
                "creative_post_plan": str(self.project_root(project.id) / "creative-post-plan.json"),
                "feature_state_subtitles": str(feature_srt) if feature_srt else "",
                "clip_provenance_json": str(provenance_path),
                "all_clips_real_generated": "true",
            },
            actual_cost_cny=0,
        )

    async def _upscale(
        self,
        project: Project,
        run: WorkflowRun,
        node: WorkflowNode,
    ) -> None:
        source = Path(self._attempt_output(
            run,
            "final-compose",
            "video_path",
        ))
        target = self.project_root(project.id) / "campaign-master-upscaled.mp4"
        await upscale_video_realesrgan(source=source, target=target, scale=2)
        complete_node(
            run,
            node.id,
            outputs={
                "video_path": str(target),
                "video_url": f"/artifacts/{project.id}/{target.name}",
            },
            actual_cost_cny=0,
        )

    async def _interpolate(
        self,
        project: Project,
        run: WorkflowRun,
        node: WorkflowNode,
    ) -> None:
        source = Path(self._attempt_output(
            run,
            "final-compose",
            "video_path",
        ))
        target = self.project_root(project.id) / "campaign-master-rife.mp4"
        await interpolate_video_rife(source=source, target=target, exponent=1)
        complete_node(
            run,
            node.id,
            outputs={
                "video_path": str(target),
                "video_url": f"/artifacts/{project.id}/{target.name}",
            },
            actual_cost_cny=0,
        )

    async def _generate_video(
        self,
        project: Project,
        plan: ProductionPlan,
        run: WorkflowRun,
        node: WorkflowNode,
        *,
        provider_override: str,
    ) -> None:
        video_nodes = [item for item in run.nodes if item.kind == "video_generation"]
        position = video_nodes.index(node)
        spec = next((item for item in plan.shots if item.shot_id == node.shot_id), None)
        if spec is None and position < len(plan.shots):
            spec = plan.shots[position]
        shot = next(
            (item for item in project.creative_plan.shots if item.id == node.shot_id),
            None,
        ) if project.creative_plan else None
        if shot is None and project.creative_plan and position < len(project.creative_plan.shots):
            shot = project.creative_plan.shots[position]
        if not spec or not shot:
            raise RuntimeError(f"镜头规格不存在：{node.shot_id}")

        first_frame = self._attempt_output(
            run,
            f"keyframe:{spec.first_frame_id}",
            "image_path",
        )
        first_frame_source = "planned_boundary"
        if spec.first_frame_runtime_policy == "prefer_previous_actual_last_frame":
            previous_spec = next(
                (item for item in plan.shots if item.shot_index == spec.shot_index - 1),
                None,
            )
            if previous_spec:
                previous_last = self._optional_attempt_output(
                    run,
                    f"video:{previous_spec.shot_id}",
                    "actual_last_frame_path",
                )
                if previous_last and Path(previous_last).is_file():
                    first_frame = previous_last
                    first_frame_source = "previous_actual_last_frame"
        last_frame = self._attempt_output(
            run,
            f"keyframe:{spec.last_frame_id}",
            "image_path",
        )
        decision = route_shot(project, shot, provider_override=provider_override)
        provider_name, switched_to_fallback = self.select_video_provider(
            node=node,
            primary_provider=decision.provider,
            fallback_provider=decision.fallback_provider,
            provider_override=provider_override,
        )
        repair_patch = self._optional_attempt_output(
            run,
            f"video-review:{node.shot_id}",
            "repair_prompt_patch",
        )
        prompt = spec.video_prompt
        if repair_patch and len(node.attempts) > 1:
            prompt = f"{prompt}\n\n本次局部修复必须满足：{repair_patch}"
        prompt_path = self.project_root(project.id) / f"shot-{spec.shot_index:02d}-video-prompt.json"
        prompt_path.write_text(
            json.dumps({
                "shot_id": shot.id,
                "shot_index": spec.shot_index,
                "title": shot.title,
                "duration": spec.duration,
                "prompt": prompt,
                "prompt_sections": spec.prompt_sections,
                "first_frame_path": first_frame,
                "first_frame_source": first_frame_source,
                "planned_last_frame_path": last_frame,
                "reference_strategy": spec.reference_strategy,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        provider = get_provider(provider_name)
        node.attempts[-1].provider = provider.name
        job = await provider.submit_boundary(
            prompt=prompt,
            duration=spec.duration,
            first_frame=first_frame,
            last_frame=last_frame,
            model_hint=decision.model_hint,
            aspect_ratio=project.aspect_ratio,
        )
        target = self.project_root(project.id) / (
            f"shot-{spec.shot_index:02d}-attempt-{len(node.attempts):02d}.mp4"
        )
        if job.output_url:
            target.write_bytes(await download_bytes(job.output_url, timeout=300))
        elif provider.name != "demo":
            raise RuntimeError("视频供应商未返回可下载的视频地址。")
        actual_last_frame = self.project_root(project.id) / (
            f"shot-{spec.shot_index:02d}-attempt-{len(node.attempts):02d}-last.jpg"
        )
        if job.last_frame_url:
            actual_last_frame.write_bytes(await download_bytes(
                job.last_frame_url,
                timeout=180,
                media_kind="image",
            ))
        elif target.is_file():
            process = await asyncio.create_subprocess_exec(
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-sseof", "-0.08", "-i", str(target), "-frames:v", "1",
                "-q:v", "2", str(actual_last_frame),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await process.communicate()
            if process.returncode or not actual_last_frame.is_file():
                raise RuntimeError(
                    "无法提取镜头真实末帧："
                    + stderr.decode("utf-8", errors="replace")[-500:]
                )
        complete_node(
            run,
            node.id,
            outputs={
                "video_path": str(target) if target.is_file() else "",
                "video_url": job.output_url or "",
                "provider": job.provider,
                "model_id": job.model_id,
                "routing_provider": decision.provider,
                "fallback_provider": decision.fallback_provider,
                "fallback_applied": str(switched_to_fallback).lower(),
                "last_frame_url": job.last_frame_url or "",
                "actual_last_frame_path": str(actual_last_frame) if actual_last_frame.is_file() else "",
                "first_frame_source": first_frame_source,
                "repair_prompt_applied": repair_patch if len(node.attempts) > 1 else "",
                "prompt_json": str(prompt_path),
            },
            actual_cost_cny=job.estimated_cost,
        )

    async def _review_video(
        self,
        project: Project,
        run: WorkflowRun,
        node: WorkflowNode,
    ) -> None:
        video_node_id = f"video:{node.shot_id}"
        video_path = Path(self._attempt_output(run, video_node_id, "video_path"))
        if not video_path.is_file():
            raise RuntimeError("演示供应商没有真实视频文件，无法执行抽帧质检。")
        frame_root = self.project_root(project.id) / f"{node.shot_id}-review-frames"
        frame_root.mkdir(parents=True, exist_ok=True)
        pattern = frame_root / "frame-%02d.jpg"
        process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-vf",
            "fps=1/2,scale=960:-2",
            "-frames:v",
            "4",
            str(pattern),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode:
            raise RuntimeError(
                f"FFmpeg抽帧失败：{stderr.decode(errors='ignore')[-500:]}"
            )
        frames = sorted(frame_root.glob("frame-*.jpg"))
        if not frames:
            raise RuntimeError("视频抽帧没有产生可审核画面。")

        shot = next(
            (item for item in project.creative_plan.shots if item.id == node.shot_id),
            None,
        ) if project.creative_plan else None
        if shot is None and project.creative_plan:
            review_nodes = [item for item in run.nodes if item.kind == "video_review"]
            position = review_nodes.index(node)
            if position < len(project.creative_plan.shots):
                shot = project.creative_plan.shots[position]
        expected = shot.narrative_beat if shot else "镜头动作符合故事规划"
        service = OllamaVisionService(purpose="review")
        reviews = []
        product_reviews = []
        product_references = self.asset_paths(project)[AssetKind.product]
        character_references = self.asset_paths(project)[AssetKind.character]
        for frame in frames:
            review = await service.review_composition(
                candidate=frame,
                expected_story_state=expected,
                expected_people_count=1 if character_references else 0,
                allow_camera_gaze=False,
            )
            reviews.append(review.model_dump(mode="json"))
            if product_references:
                product_review = await service.compare_pair(
                    reference=product_references[0],
                    candidate=frame,
                    subject="product",
                )
                product_reviews.append(product_review.model_dump(mode="json"))
        score = round(sum(item["score"] for item in reviews) / len(reviews))
        product_score = (
            round(sum(item["score"] for item in product_reviews) / len(product_reviews))
            if product_reviews else 100
        )
        product_passed = all(
            item["match"] and item["score"] >= 75 for item in product_reviews
        )
        deterministic = _deterministic_frame_metrics(frames)
        scene_analysis = await asyncio.to_thread(analyze_internal_cuts, video_path)
        has_unexpected_cuts = scene_analysis["unexpected_cut_count"] > 0
        passed = (
            all(item["story_match"] for item in reviews)
            and score >= 72
            and product_passed
            and bool(deterministic["passed"])
            and not has_unexpected_cuts
        )
        generation = node_by_id(run, video_node_id)
        routing = route_shot(project, shot) if shot else None
        decision = decide_video_quality(
            report={
                "passed": passed,
                "score": score,
                "frames": reviews,
                "product_frames": product_reviews,
                "product_consistency": product_score,
                "product_match": product_passed,
                "scene_analysis": scene_analysis,
                "deterministic_analysis": deterministic,
            },
            generation=generation,
            review_node_id=node.id,
            fallback_provider=routing.fallback_provider if routing else "",
        )
        report = {
            "passed": passed,
            "score": score,
            "frames": reviews,
            "product_frames": product_reviews,
            "product_consistency": product_score,
            "product_match": product_passed,
            "scene_analysis": scene_analysis,
            "deterministic_analysis": deterministic,
            "decision": decision.model_dump(mode="json"),
        }
        target = self.project_root(project.id) / f"{node.shot_id}-video-review.json"
        target.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        complete_node(
            run,
            node.id,
            outputs={
                "review_json": str(target),
                "passed": str(passed).lower(),
                "score": str(score),
                "product_consistency": str(product_score),
                "product_match": str(product_passed).lower(),
                "unexpected_cut_count": str(scene_analysis["unexpected_cut_count"]),
                "deterministic_passed": str(deterministic["passed"]).lower(),
                "review_model": service.model,
                "cut_timecodes": ",".join(scene_analysis["cut_timecodes"]),
                "failure_categories": ",".join(decision.categories),
                "repair_action": (
                    decision.repair_steps[0].label if decision.repair_steps else ""
                ),
                "repair_prompt_patch": decision.prompt_patch,
                "suggested_provider": decision.suggested_provider,
                "remaining_retries": str(decision.remaining_retries),
                "retry_budget_cny": str(decision.retry_budget_cny),
                "within_budget": str(decision.within_budget).lower(),
            },
            actual_cost_cny=0,
            # Passing reviews continue automatically; failures retain the
            # report and stop on this exact shot for a local repair decision.
            review_required=not passed,
        )
        node.quality_decision = decision.model_dump(mode="json")
        node.issues = [] if passed else [decision.summary]
