from __future__ import annotations

import asyncio
import ipaddress
import socket
import threading
from os import getenv
from urllib.parse import urlparse

import httpx


_DOWNLOAD_DNS_LOCK = threading.Lock()
_DOWNLOAD_PROXY_LOCK = threading.Lock()
_FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")


def _is_fake_ip(value: str) -> bool:
    try:
        return ipaddress.ip_address(value) in _FAKE_IP_NETWORK
    except ValueError:
        return False


def _resolve_download_host(hostname: str) -> tuple[str, ...]:
    """Resolve through HTTPS when a local proxy returns a 198.18/15 Fake-IP."""
    system_ips = tuple({item[4][0] for item in socket.getaddrinfo(hostname, 443, socket.AF_INET)})
    if system_ips and not all(_is_fake_ip(value) for value in system_ips):
        return system_ips
    response = httpx.get(
        "https://1.1.1.1/dns-query",
        params={"name": hostname, "type": "A"},
        headers={"accept": "application/dns-json"},
        timeout=30,
        trust_env=False,
    )
    response.raise_for_status()
    answers = tuple(
        item["data"]
        for item in response.json().get("Answer", [])
        if item.get("type") == 1 and item.get("data")
    )
    if not answers:
        raise RuntimeError(f"无法解析视频下载域名：{hostname}")
    return answers


def _validate_response(response: httpx.Response, *, media_kind: str) -> bytes:
    response.raise_for_status()
    content = response.content
    content_type = response.headers.get("content-type", "").lower()
    if media_kind == "image":
        if len(content) < 256 or ("image/" not in content_type and content[:2] != b"\xff\xd8" and content[:8] != b"\x89PNG\r\n\x1a\n"):
            raise RuntimeError("图片下载接口未返回有效的图像文件。")
        return content
    if len(content) < 1024 or ("video/" not in content_type and b"ftyp" not in content[:64]):
        raise RuntimeError("视频下载接口未返回有效的 MP4 文件。")
    return content


def _download_direct_sync(url: str, timeout: float, *, media_kind: str) -> bytes:
    hostname = urlparse(url).hostname
    if not hostname:
        raise RuntimeError("供应商返回的视频地址缺少有效域名。")
    resolved_ips = _resolve_download_host(hostname)
    original_getaddrinfo = socket.getaddrinfo

    def resolver(
        host: str,
        port: int,
        family: int = 0,
        socket_type: int = 0,
        proto: int = 0,
        flags: int = 0,
    ) -> list[tuple[object, ...]]:
        if host != hostname:
            return original_getaddrinfo(host, port, family, socket_type, proto, flags)
        addresses: list[tuple[object, ...]] = []
        for ip in resolved_ips:
            addresses.extend(original_getaddrinfo(ip, port, socket.AF_INET, socket_type, proto, flags))
        return addresses

    # Only DNS is overridden. The original HTTPS hostname remains in the URL,
    # therefore SNI and certificate hostname verification are still enforced.
    with _DOWNLOAD_DNS_LOCK:
        socket.getaddrinfo = resolver
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True, trust_env=False) as client:
                response = client.get(url)
                return _validate_response(response, media_kind=media_kind)
        finally:
            socket.getaddrinfo = original_getaddrinfo


def _download_via_configured_proxy_unlocked(url: str, timeout: float, *, media_kind: str) -> bytes:
    proxy_url = getenv("VIDEO_DOWNLOAD_PROXY_URL", "").strip()
    if not proxy_url:
        raise RuntimeError("未配置视频下载代理。")

    controller_url = getenv("CLASH_CONTROLLER_URL", "").strip().rstrip("/")
    node_match = getenv("CLASH_DOWNLOAD_NODE_MATCH", "").strip()
    original_node: str | None = None
    controller: httpx.Client | None = None
    try:
        if controller_url and node_match:
            controller = httpx.Client(timeout=15, trust_env=False)
            group = controller.get(f"{controller_url}/proxies/GLOBAL")
            group.raise_for_status()
            group_data = group.json()
            original_node = str(group_data.get("now") or "")
            target_node = next(
                (str(name) for name in group_data.get("all", []) if node_match in str(name)),
                None,
            )
            if not target_node:
                raise RuntimeError(f"未找到包含“{node_match}”的 Clash 下载节点。")
            if target_node != original_node:
                switched = controller.put(
                    f"{controller_url}/proxies/GLOBAL",
                    json={"name": target_node},
                )
                switched.raise_for_status()

        with httpx.Client(
            proxy=proxy_url,
            timeout=timeout,
            follow_redirects=True,
            trust_env=False,
        ) as client:
            return _validate_response(client.get(url), media_kind=media_kind)
    finally:
        if controller is not None:
            try:
                if original_node:
                    controller.put(
                        f"{controller_url}/proxies/GLOBAL",
                        json={"name": original_node},
                    ).raise_for_status()
            finally:
                controller.close()


def _download_via_configured_proxy(url: str, timeout: float, *, media_kind: str) -> bytes:
    # Selecting a Clash group is process-external state. Serialize the complete
    # switch/download/restore transaction so concurrent renders cannot restore
    # another render's temporary selection.
    with _DOWNLOAD_PROXY_LOCK:
        return _download_via_configured_proxy_unlocked(url, timeout, media_kind=media_kind)


def _download_sync(url: str, timeout: float, *, media_kind: str) -> bytes:
    try:
        return _download_direct_sync(url, timeout, media_kind=media_kind)
    except (httpx.HTTPError, OSError, RuntimeError) as direct_error:
        try:
            return _download_via_configured_proxy(url, timeout, media_kind=media_kind)
        except (httpx.HTTPError, OSError, RuntimeError) as proxy_error:
            raise RuntimeError(
                f"{media_kind} 已生成，但下载链路不可用。"
                f"直连错误：{direct_error}；代理错误：{proxy_error}"
            ) from proxy_error


async def download_bytes(url: str, timeout: float = 180, *, media_kind: str = "video") -> bytes:
    return await asyncio.to_thread(_download_sync, url, timeout, media_kind=media_kind)
