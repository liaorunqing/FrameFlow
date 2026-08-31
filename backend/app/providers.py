from __future__ import annotations

import asyncio
import base64
import ipaddress
import mimetypes
import socket
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from os import getenv
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError


_DNS_OVERRIDE_LOCK = threading.Lock()


@dataclass
class VideoJob:
    external_id: str
    status: str
    output_url: str | None = None
    provider: str = "demo"
    model_id: str = "demo"
    estimated_cost: float = 0
    last_frame_url: str | None = None


class VideoProvider(ABC):
    """Provider boundary used by the orchestrator for every generated shot."""

    name: str

    @abstractmethod
    async def submit(
        self,
        *,
        prompt: str,
        duration: int,
        reference_images: list[str],
        model_hint: str = "story",
        aspect_ratio: str = "9:16",
    ) -> VideoJob:
        raise NotImplementedError

    @abstractmethod
    async def poll(self, external_id: str) -> VideoJob:
        raise NotImplementedError

    async def create_boundary_task(
        self,
        *,
        prompt: str,
        duration: int,
        first_frame: str,
        last_frame: str | None,
        model_hint: str = "story",
        aspect_ratio: str = "9:16",
    ) -> VideoJob:
        references = [first_frame]
        if last_frame:
            references.append(last_frame)
        return await self.submit(
            prompt=prompt,
            duration=duration,
            reference_images=references,
            model_hint=model_hint,
            aspect_ratio=aspect_ratio,
        )

    async def submit_boundary(
        self,
        *,
        prompt: str,
        duration: int,
        first_frame: str,
        last_frame: str | None,
        model_hint: str = "story",
        aspect_ratio: str = "9:16",
    ) -> VideoJob:
        """Generate one shot constrained by reviewed story boundaries.

        Providers without native first/last-frame support fall back to their
        normal reference-image contract. Concrete providers should override
        this method whenever their official API supports both boundaries.
        """
        references = [first_frame]
        if last_frame:
            references.append(last_frame)
        return await self.submit(
            prompt=prompt,
            duration=duration,
            reference_images=references,
            model_hint=model_hint,
            aspect_ratio=aspect_ratio,
        )

    async def wait_for_completion(self, submitted: VideoJob) -> VideoJob:
        """Wait for an already-created task without submitting another one."""
        if submitted.status == "completed":
            return submitted
        raise NotImplementedError(
            f"供应商 {self.name} 不支持从已保存的任务 ID 恢复轮询。"
        )


class DemoVideoProvider(VideoProvider):
    name = "demo"

    async def submit(
        self,
        *,
        prompt: str,
        duration: int,
        reference_images: list[str],
        model_hint: str = "story",
        aspect_ratio: str = "9:16",
    ) -> VideoJob:
        await asyncio.sleep(0.35)
        return VideoJob(
            external_id=f"demo-{abs(hash(prompt))}",
            status="completed",
            provider=self.name,
            model_id=f"demo-{model_hint}",
        )

    async def poll(self, external_id: str) -> VideoJob:
        return VideoJob(external_id=external_id, status="completed", provider=self.name)


class MiniMaxOfficialVideoProvider(VideoProvider):
    """Direct integration with MiniMax's mainland-China official video API.

    The official API is asynchronous: create a task, query it until completion,
    then exchange the returned file id for a temporary download URL. The public
    provider contract remains synchronous from the orchestrator's perspective so
    the rest of FrameFlow does not depend on MiniMax-specific task states.
    """

    name = "minimax-official"
    FAST_MODEL = "MiniMax-Hailuo-2.3-Fast"
    QUALITY_MODEL = "MiniMax-Hailuo-2.3"
    ERROR_MESSAGES = {
        1002: "MiniMax 官方接口当前限流，请稍后重试。",
        1004: "MiniMax API Key 鉴权失败，请检查服务端配置。",
        1008: "MiniMax 账户余额不足，请先在开放平台充值。",
        1026: "视频描述或素材未通过 MiniMax 内容安全审核。",
        2013: "MiniMax 请求参数不符合模型要求。",
        2049: "MiniMax API Key 无效，请重新创建密钥。",
    }

    def __init__(self) -> None:
        self.api_key = getenv("MINIMAX_API_KEY", "").strip()
        if not self.api_key:
            raise RuntimeError("尚未配置 MINIMAX_API_KEY；系统不会在没有密钥时产生 API 费用。")
        self.api_base = getenv("MINIMAX_API_BASE", "https://api.minimaxi.com").rstrip("/")
        self.api_hostname = urlparse(self.api_base).hostname or "api.minimaxi.com"
        raw_ips = getenv("MINIMAX_API_IPS", "")
        try:
            self.api_ips = tuple(str(ipaddress.ip_address(value.strip())) for value in raw_ips.split(",") if value.strip())
        except ValueError as exc:
            raise RuntimeError("MINIMAX_API_IPS 包含无效 IP 地址。") from exc
        self.poll_interval = max(1.0, float(getenv("MINIMAX_POLL_INTERVAL_SECONDS", "10")))
        self.timeout_seconds = max(60.0, float(getenv("MINIMAX_TASK_TIMEOUT_SECONDS", "900")))

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _data_uri(path_value: str) -> str:
        path = Path(path_value)
        if not path.is_file():
            if path_value.startswith(("https://", "http://", "data:")):
                return path_value
            raise RuntimeError("真实视频生成需要至少一张可读取的商品或场景参考图。")
        try:
            with Image.open(path) as source_image:
                image = ImageOps.exif_transpose(source_image)
                width, height = image.size
                image.verify()
        except (UnidentifiedImageError, OSError) as exc:
            raise RuntimeError("参考图无法读取，请使用有效的 PNG、JPG 或 WebP 文件。") from exc
        ratio = width / height
        if min(width, height) <= 300 or not 0.4 < ratio < 2.5:
            raise RuntimeError(
                f"参考图尺寸为 {width}×{height}；MiniMax 要求短边大于 300 像素，"
                "长宽比在 2:5 与 5:2 之间。"
            )
        mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{encoded}"

    @classmethod
    def _route(cls, model_hint: str, duration: int) -> tuple[str, int, str, float]:
        """Return model, provider duration, resolution and public CNY estimate."""
        seconds = 6 if duration <= 6 else 10
        if model_hint == "premium":
            resolution = "1080P" if seconds == 6 else "768P"
            cost = 3.50 if resolution == "1080P" else 4.00
            return cls.QUALITY_MODEL, seconds, resolution, cost
        cost = 1.35 if seconds == 6 else 2.25
        return cls.FAST_MODEL, seconds, "768P", cost

    @staticmethod
    def _prepare_prompt(prompt: str) -> str:
        lowered = prompt.lower()
        if "fixed" in lowered or "static" in lowered:
            instruction = "[固定]"
        elif "follow" in lowered or "tracking" in lowered:
            instruction = "[跟随]"
        elif "pull" in lowered or "move backward" in lowered:
            instruction = "[拉远]"
        else:
            instruction = "[推进]"
        # MiniMax accepts up to 2,000 characters. Keep the director's detailed
        # continuity and performance instructions while reserving room for the
        # provider-native camera command.
        return f"{instruction} {prompt}"[:2000]

    @classmethod
    def _check_api_response(cls, payload: dict[str, Any], action: str) -> None:
        base_resp = payload.get("base_resp") or {}
        code = int(base_resp.get("status_code") or 0)
        if code == 0:
            return
        message = cls.ERROR_MESSAGES.get(code) or base_resp.get("status_msg") or "未知错误"
        raise RuntimeError(f"{action}失败（{code}）：{message}")

    def _request_sync(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        original_getaddrinfo = socket.getaddrinfo

        def resolve_official_host(
            host: str,
            port: int,
            family: int = 0,
            socket_type: int = 0,
            proto: int = 0,
            flags: int = 0,
        ) -> list[tuple[Any, ...]]:
            if host != self.api_hostname or not self.api_ips:
                return original_getaddrinfo(host, port, family, socket_type, proto, flags)
            addresses: list[tuple[Any, ...]] = []
            for ip in self.api_ips:
                addresses.extend(
                    original_getaddrinfo(ip, port, socket.AF_INET, socket_type, proto, flags)
                )
            return addresses

        # The override changes only DNS resolution. The request URL, TLS SNI and
        # certificate hostname remain api.minimaxi.com, so HTTPS verification is
        # fully preserved. The lock prevents concurrent requests from nesting
        # process-wide socket patches.
        with _DNS_OVERRIDE_LOCK:
            if self.api_ips:
                socket.getaddrinfo = resolve_official_host
            try:
                with httpx.Client(timeout=60, follow_redirects=True) as client:
                    response = client.request(
                        method,
                        f"{self.api_base}{path}",
                        headers=self.headers,
                        json=payload,
                        params=params,
                    )
                    response.raise_for_status()
                    return response.json()
            finally:
                socket.getaddrinfo = original_getaddrinfo

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(self._request_sync, "POST", path, payload=payload)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"连接 MiniMax 官方接口失败：{exc}") from exc

    async def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(self._request_sync, "GET", path, params=params)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"连接 MiniMax 官方接口失败：{exc}") from exc

    async def submit(
        self,
        *,
        prompt: str,
        duration: int,
        reference_images: list[str],
        model_hint: str = "story",
        aspect_ratio: str = "9:16",
    ) -> VideoJob:
        submitted = await self.create_task(
            prompt=prompt,
            duration=duration,
            reference_images=reference_images,
            model_hint=model_hint,
            aspect_ratio=aspect_ratio,
        )
        return await self.wait_for_completion(submitted)

    async def submit_boundary(
        self,
        *,
        prompt: str,
        duration: int,
        first_frame: str,
        last_frame: str | None,
        model_hint: str = "story",
        aspect_ratio: str = "9:16",
    ) -> VideoJob:
        # Hailuo 2.3 accepts a first frame; sending last_frame_image returns
        # provider error 2013. The executor carries continuity by extracting
        # the real terminal frame and using it as the following shot's input.
        submitted = await self.create_task(
            prompt=prompt,
            duration=duration,
            reference_images=[first_frame],
            model_hint=model_hint,
            aspect_ratio=aspect_ratio,
        )
        return await self.wait_for_completion(submitted)

    async def create_boundary_task(
        self,
        *,
        prompt: str,
        duration: int,
        first_frame: str,
        last_frame: str | None,
        model_hint: str = "story",
        aspect_ratio: str = "9:16",
    ) -> VideoJob:
        # Current Hailuo models use the reviewed opening frame. Continuity is
        # carried by feeding the preceding clip's extracted terminal frame into
        # the next shot, rather than asking MiniMax to accept an unsupported end
        # frame field.
        return await self.create_task(
            prompt=prompt,
            duration=duration,
            reference_images=[first_frame],
            model_hint=model_hint,
            aspect_ratio=aspect_ratio,
        )

    async def create_task(
        self,
        *,
        prompt: str,
        duration: int,
        reference_images: list[str],
        model_hint: str = "story",
        aspect_ratio: str = "9:16",
    ) -> VideoJob:
        """Create one billable provider task and return its recoverable id."""
        if not reference_images:
            raise RuntimeError("请先上传商品参考图，再启动真实视频生成。")

        model_id, provider_duration, resolution, cost = self._route(model_hint, duration)
        payload = {
            "model": model_id,
            "prompt": self._prepare_prompt(prompt),
            "first_frame_image": self._data_uri(reference_images[0]),
            "duration": provider_duration,
            "resolution": resolution,
            "prompt_optimizer": getenv("MINIMAX_PROMPT_OPTIMIZER", "false").strip().lower() == "true",
            "fast_pretreatment": True,
        }
        created = await self._post("/v1/video_generation", payload)
        self._check_api_response(created, "创建视频任务")
        task_id = str(created.get("task_id") or "")
        if not task_id:
            raise RuntimeError("MiniMax 官方接口未返回 task_id。")
        return VideoJob(
            external_id=task_id,
            status="processing",
            provider=self.name,
            model_id=model_id,
            estimated_cost=cost,
        )

    async def wait_for_completion(self, submitted: VideoJob) -> VideoJob:
        """Wait for a previously created task without creating another charge."""
        task_id = submitted.external_id
        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            await asyncio.sleep(self.poll_interval)
            try:
                job = await self.poll(task_id)
            except RuntimeError as exc:
                raise RuntimeError(f"MiniMax 任务 {task_id} 查询失败：{exc}") from exc
            if job.status == "completed":
                job.model_id = submitted.model_id
                job.estimated_cost = submitted.estimated_cost
                return job
            if job.status == "failed":
                raise RuntimeError("MiniMax 视频生成失败，请检查素材、提示词或内容安全要求。")
        raise RuntimeError(f"MiniMax 视频任务等待超过 {int(self.timeout_seconds)} 秒，已停止本次轮询。")

    async def poll(self, external_id: str) -> VideoJob:
        result = await self._get("/v1/query/video_generation", {"task_id": external_id})
        self._check_api_response(result, "查询视频任务")
        status = str(result.get("status") or "").lower()
        if status == "success":
            file_id = str(result.get("file_id") or "")
            if not file_id:
                raise RuntimeError("MiniMax 任务成功但未返回 file_id。")
            file_result = await self._get("/v1/files/retrieve", {"file_id": file_id})
            self._check_api_response(file_result, "获取视频文件")
            output_url = (file_result.get("file") or {}).get("download_url")
            if not output_url:
                raise RuntimeError("MiniMax 文件接口未返回视频下载地址。")
            return VideoJob(
                external_id=external_id,
                status="completed",
                output_url=str(output_url),
                provider=self.name,
            )
        if status in {"fail", "failed"}:
            return VideoJob(external_id=external_id, status="failed", provider=self.name)
        return VideoJob(external_id=external_id, status="processing", provider=self.name)


class VolcArkSeedanceProvider(VideoProvider):
    """Official Volcengine Ark Seedance 2.0 asynchronous video provider."""

    name = "seedance-official"
    MINI_MODEL = "doubao-seedance-2-0-mini-260615"
    FAST_MODEL = "doubao-seedance-2-0-fast-260128"
    QUALITY_MODEL = "doubao-seedance-2-0-260128"

    def __init__(self) -> None:
        self.api_key = getenv("ARK_API_KEY", "").strip()
        if not self.api_key:
            raise RuntimeError("尚未配置 ARK_API_KEY；系统不会在没有密钥时产生火山方舟费用。")
        self.api_base = getenv(
            "ARK_API_BASE",
            "https://ark.cn-beijing.volces.com/api/v3",
        ).rstrip("/")
        self.poll_interval = max(1.0, float(getenv("ARK_POLL_INTERVAL_SECONDS", "10")))
        self.timeout_seconds = max(60.0, float(getenv("ARK_TASK_TIMEOUT_SECONDS", "1200")))

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    @classmethod
    def _route(cls, model_hint: str) -> str:
        configured_model = getenv("ARK_VIDEO_MODEL", "").strip()
        if configured_model:
            return configured_model
        if model_hint == "economy":
            return cls.MINI_MODEL
        if model_hint == "premium":
            return cls.QUALITY_MODEL
        return cls.FAST_MODEL

    @staticmethod
    def _image_content(path_value: str, *, role: str | None = None) -> dict[str, Any]:
        image_url = MiniMaxOfficialVideoProvider._data_uri(path_value)
        content: dict[str, Any] = {
            "type": "image_url",
            "image_url": {"url": image_url},
        }
        if role:
            content["role"] = role
        return content

    @staticmethod
    def _prepare_prompt(prompt: str, duration: int, aspect_ratio: str, reference_count: int) -> str:
        reference_roles = ["商品", "人物", "场景"]
        labels = "、".join(
            f"参考图{index + 1}是{reference_roles[index]}"
            for index in range(min(reference_count, len(reference_roles)))
        )
        identity_rule = (
            f"{labels}。严格保持商品外形、人物身份和场景布局，不生成或改写品牌文字与 Logo。"
            if labels
            else ""
        )
        safe_duration = max(4, min(int(duration), 15))
        safe_ratio = aspect_ratio if aspect_ratio in {"9:16", "16:9", "1:1"} else "9:16"
        return f"{identity_rule}{prompt} --ratio {safe_ratio} --dur {safe_duration}"

    async def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                response = await client.request(
                    method,
                    f"{self.api_base}{path}",
                    headers=self.headers,
                    json=payload,
                )
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as exc:
            detail = ""
            try:
                body = exc.response.json()
                error = body.get("error") or body
                detail = str(error.get("message") if isinstance(error, dict) else error)
            except Exception:
                detail = exc.response.text[:300]
            if "has not activated the model" in detail.lower():
                raise RuntimeError(
                    "火山方舟密钥有效，但当前账号尚未开通所选 Seedance 模型。"
                    "请在火山方舟控制台的“开通管理/模型服务”中开通 Seedance 2.0，"
                    "完成后可使用已保存的测试脚本重新提交。"
                ) from exc
            if "does not exist or you do not have access" in detail.lower():
                raise RuntimeError(
                    "火山方舟密钥有效，但该密钥所属项目没有所选 Seedance 模型权限。"
                    "请在已开通模型的同一火山方舟项目中重新创建 API Key，"
                    "并用新密钥替换本地 ARK_API_KEY。"
                ) from exc
            raise RuntimeError(
                f"火山方舟 Seedance 接口返回 HTTP {exc.response.status_code}"
                + (f"：{detail}" if detail else "")
            ) from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(f"连接火山方舟 Seedance 接口失败：{exc}") from exc

    async def submit(
        self,
        *,
        prompt: str,
        duration: int,
        reference_images: list[str],
        model_hint: str = "story",
        aspect_ratio: str = "9:16",
    ) -> VideoJob:
        submitted = await self.create_task(
            prompt=prompt,
            duration=duration,
            reference_images=reference_images,
            model_hint=model_hint,
            aspect_ratio=aspect_ratio,
        )
        return await self.wait_for_completion(submitted)

    async def create_task(
        self,
        *,
        prompt: str,
        duration: int,
        reference_images: list[str],
        model_hint: str = "story",
        aspect_ratio: str = "9:16",
    ) -> VideoJob:
        model_id = self._route(model_hint)
        is_seedance_2 = "seedance-2-0" in model_id
        # Seedance 2.0 supports multimodal references. Seedance 1.5 receives one
        # image-to-video first frame, so FrameFlow will generate a composite
        # keyframe before calling it in the full production pipeline.
        selected_references = reference_images[:3] if is_seedance_2 else reference_images[:1]
        content: list[dict[str, Any]] = [{
            "type": "text",
            "text": self._prepare_prompt(
                prompt,
                duration,
                aspect_ratio,
                len(selected_references),
            ),
        }]
        content.extend(
            self._image_content(path, role="reference_image" if is_seedance_2 else None)
            for path in selected_references
        )
        created = await self._request(
            "POST",
            "/contents/generations/tasks",
            payload={
                "model": model_id,
                "content": content,
                "return_last_frame": True,
            },
        )
        task_id = str(created.get("id") or "")
        if not task_id:
            raise RuntimeError("火山方舟 Seedance 接口未返回任务 ID。")
        return VideoJob(
            external_id=task_id,
            status="processing",
            provider=self.name,
            model_id=model_id,
        )

    async def create_boundary_task(
        self,
        *,
        prompt: str,
        duration: int,
        first_frame: str,
        last_frame: str | None,
        aspect_ratio: str,
        model_hint: str = "story",
    ) -> VideoJob:
        """Create a Seedance 1.0 first-frame or first/last-frame story shot."""
        model_id = self._route(model_hint)
        content: list[dict[str, Any]] = [{
            "type": "text",
            "text": self._prepare_prompt(prompt, duration, aspect_ratio, 0),
        }]
        content.append(self._image_content(first_frame, role="first_frame"))
        if last_frame:
            content.append(self._image_content(last_frame, role="last_frame"))
        created = await self._request(
            "POST",
            "/contents/generations/tasks",
            payload={
                "model": model_id,
                "content": content,
                "return_last_frame": True,
            },
        )
        task_id = str(created.get("id") or "")
        if not task_id:
            raise RuntimeError("火山方舟 Seedance 接口未返回任务 ID。")
        return VideoJob(
            external_id=task_id,
            status="processing",
            provider=self.name,
            model_id=model_id,
        )

    async def submit_boundary(
        self,
        *,
        prompt: str,
        duration: int,
        first_frame: str,
        last_frame: str | None,
        model_hint: str = "story",
        aspect_ratio: str = "9:16",
    ) -> VideoJob:
        submitted = await self.create_boundary_task(
            prompt=prompt,
            duration=duration,
            first_frame=first_frame,
            last_frame=last_frame,
            aspect_ratio=aspect_ratio,
            model_hint=model_hint,
        )
        return await self.wait_for_completion(submitted)

    async def wait_for_completion(self, submitted: VideoJob) -> VideoJob:
        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            await asyncio.sleep(self.poll_interval)
            job = await self.poll(submitted.external_id)
            if job.status == "completed":
                job.model_id = submitted.model_id
                return job
            if job.status == "failed":
                raise RuntimeError(f"Seedance 视频任务 {submitted.external_id} 生成失败。")
        raise RuntimeError(
            f"Seedance 视频任务等待超过 {int(self.timeout_seconds)} 秒，"
            "任务可能仍在云端运行，可使用任务 ID 继续查询。"
        )

    async def poll(self, external_id: str) -> VideoJob:
        result = await self._request(
            "GET",
            f"/contents/generations/tasks/{external_id}",
        )
        status = str(result.get("status") or "").lower()
        if status == "succeeded":
            output = result.get("content") or {}
            output_url = output.get("video_url")
            if not output_url:
                raise RuntimeError("Seedance 任务成功但没有返回视频下载地址。")
            return VideoJob(
                external_id=external_id,
                status="completed",
                output_url=str(output_url),
                last_frame_url=str(output.get("last_frame_url") or "") or None,
                provider=self.name,
                model_id=str(result.get("model") or ""),
            )
        if status in {"failed", "cancelled"}:
            error = result.get("error") or {}
            code = str(error.get("code") or "") if isinstance(error, dict) else ""
            message = str(error.get("message") or "") if isinstance(error, dict) else str(error)
            if code == "SetLimitExceeded" or "inference limit" in message.lower():
                raise RuntimeError(
                    "火山方舟 Seedance 推理限额已触发（SetLimitExceeded），模型服务已暂停。"
                    "请在模型开通管理中调整或关闭安全体验模式后，从当前镜头继续。"
                )
            detail = "：".join(part for part in (code, message) if part)
            raise RuntimeError(
                f"Seedance 视频任务 {external_id} 生成失败"
                + (f"：{detail}" if detail else "。")
            )
        return VideoJob(external_id=external_id, status="processing", provider=self.name)


def configured_provider_name() -> str:
    requested = getenv("VIDEO_PROVIDER", "auto").strip().lower()
    if requested in {"seedance", "seedance-official", "ark"} and getenv("ARK_API_KEY", "").strip():
        return "seedance-official"
    if requested in {"auto", "minimax", "minimax-official"} and getenv("MINIMAX_API_KEY", "").strip():
        return "minimax-official"
    if requested == "auto" and getenv("ARK_API_KEY", "").strip():
        return "seedance-official"
    return "demo"


def get_provider(name: str) -> VideoProvider:
    resolved = configured_provider_name() if name == "auto" else name
    if resolved == "demo":
        return DemoVideoProvider()
    if resolved in {"minimax", "minimax-official"}:
        return MiniMaxOfficialVideoProvider()
    if resolved in {"seedance", "seedance-official", "ark"}:
        return VolcArkSeedanceProvider()
    raise ValueError(f"Unsupported provider: {resolved}")
