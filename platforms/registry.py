"""平台注册与统一查询入口。"""

from __future__ import annotations

import asyncio
from typing import Any

from .base import (
    LiveInfo,
    PlatformChecker,
    PLATFORM_NAMES,
    extract_room_id,
    normalize_platform,
)
from .bilibili import BilibiliChecker
from .douyu import DouyuChecker
from .huya import HuyaChecker
from .douyin import DouyinChecker

_CHECKER_CLS: dict[str, type[PlatformChecker]] = {
    "bilibili": BilibiliChecker,
    "douyu": DouyuChecker,
    "huya": HuyaChecker,
    "douyin": DouyinChecker,
}


def list_platforms() -> list[tuple[str, str]]:
    return [(k, PLATFORM_NAMES[k]) for k in _CHECKER_CLS]


def fetch_options(cfg: dict[str, Any]) -> dict[str, Any]:
    """从插件配置提取 check_room 需要的请求参数。"""
    try:
        timeout = float(cfg.get("request_timeout", 12) or 12)
    except (TypeError, ValueError):
        timeout = 12.0
    return {
        "timeout": timeout,
        "bilibili_cookie": str(cfg.get("bilibili_cookie", "") or ""),
        "douyin_cookie": str(cfg.get("douyin_cookie", "") or ""),
    }


def get_checker(
    platform: str,
    timeout: float = 12.0,
    bilibili_cookie: str = "",
    douyin_cookie: str = "",
    **_: Any,
) -> PlatformChecker | None:
    pid = normalize_platform(platform) or (platform or "").strip().lower()
    cls = _CHECKER_CLS.get(pid)
    if not cls:
        return None
    cookies = ""
    if pid == "bilibili":
        cookies = bilibili_cookie or ""
    elif pid == "douyin":
        cookies = douyin_cookie or ""
    return cls(timeout=timeout, cookies=cookies)


def _is_retryable(error: str) -> bool:
    """判断错误是否值得重试（瞬时网络/限流/5xx），业务错误不重试。"""
    e = (error or "").strip().lower()
    if not e:
        return False
    if "timed out" in e or "timeout" in e:
        return True
    if "http 5" in e:  # 500/502/503/504
        return True
    if "http 429" in e:
        return True
    if any(
        s in e
        for s in (
            "connecterror",
            "connectionerror",
            "remoteprotocolerror",
            "readerror",
            "pooltimeout",
            "proxyerror",
            "sslerror",
            "network",
            "connection reset",
            "eof occurred",
        )
    ):
        return True
    return False


async def check_room(
    platform: str,
    room_id: str,
    timeout: float = 12.0,
    bilibili_cookie: str = "",
    douyin_cookie: str = "",
    *,
    max_retries: int = 2,
    retry_backoff: float = 1.0,
) -> LiveInfo:
    """查询房间状态，对瞬时错误自动指数退避重试。

    业务级错误（房间不存在、4xx、不支持的平台等）不重试，直接返回。
    """
    checker = get_checker(
        platform,
        timeout=timeout,
        bilibili_cookie=bilibili_cookie,
        douyin_cookie=douyin_cookie,
    )
    rid = extract_room_id(str(room_id), checker.platform if checker else None)
    if not checker:
        return LiveInfo(
            platform=platform,
            room_id=rid or str(room_id),
            is_live=False,
            error=f"不支持的平台: {platform}",
        )
    if not rid:
        return LiveInfo(
            platform=checker.platform,
            room_id="",
            is_live=False,
            error="房间号为空",
        )

    info: LiveInfo | None = None
    for attempt in range(max_retries + 1):
        try:
            info = await checker.fetch(rid)
        except Exception as e:
            info = LiveInfo(
                platform=checker.platform,
                room_id=rid,
                is_live=False,
                error=f"{type(e).__name__}: {e}",
            )
        if info.ok or not _is_retryable(info.error) or attempt == max_retries:
            return info
        delay = retry_backoff * (2**attempt)
        await asyncio.sleep(delay)
    return info  # type: ignore[return-value]

