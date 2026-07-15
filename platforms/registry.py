"""平台注册与统一查询入口。"""

from __future__ import annotations

from typing import Any

from .base import LiveInfo, PlatformChecker, PLATFORM_NAMES, normalize_platform
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


async def check_room(
    platform: str,
    room_id: str,
    timeout: float = 12.0,
    bilibili_cookie: str = "",
    douyin_cookie: str = "",
) -> LiveInfo:
    checker = get_checker(
        platform,
        timeout=timeout,
        bilibili_cookie=bilibili_cookie,
        douyin_cookie=douyin_cookie,
    )
    if not checker:
        return LiveInfo(
            platform=platform,
            room_id=str(room_id),
            is_live=False,
            error=f"不支持的平台: {platform}",
        )
    return await checker.fetch(str(room_id))
