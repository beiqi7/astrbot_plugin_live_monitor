"""平台检测基类与通用类型。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


PLATFORM_NAMES: dict[str, str] = {
    "bilibili": "B站",
    "douyu": "斗鱼",
    "huya": "虎牙",
    "douyin": "抖音",
}

# 用户输入别名 -> 标准平台 id
_PLATFORM_ALIASES: dict[str, str] = {
    "bilibili": "bilibili",
    "bili": "bilibili",
    "b站": "bilibili",
    "哔哩哔哩": "bilibili",
    "哔哩": "bilibili",
    "bl": "bilibili",
    "douyu": "douyu",
    "斗鱼": "douyu",
    "dyu": "douyu",
    "huya": "huya",
    "虎牙": "huya",
    "hy": "huya",
    "douyin": "douyin",
    "抖音": "douyin",
    "tiktok": "douyin",
    "dy": "douyin",
}


def normalize_platform(name: str) -> str | None:
    """将用户输入规范为平台 id，无法识别返回 None。"""
    key = (name or "").strip().lower()
    if not key:
        return None
    # 中文别名保持原样再试一次
    if key in _PLATFORM_ALIASES:
        return _PLATFORM_ALIASES[key]
    raw = (name or "").strip()
    return _PLATFORM_ALIASES.get(raw)


@dataclass
class LiveInfo:
    """统一的直播间信息。"""

    platform: str
    room_id: str
    is_live: bool
    title: str = ""
    real_name: str = ""  # 平台主播名
    cover_url: str = ""
    url: str = ""
    raw: dict[str, Any] = field(default_factory=dict)
    error: str = ""  # 非空表示查询失败

    @property
    def platform_name(self) -> str:
        return PLATFORM_NAMES.get(self.platform, self.platform)

    @property
    def ok(self) -> bool:
        return not self.error


class PlatformChecker(ABC):
    """平台检测器抽象基类。"""

    platform: str = ""

    def __init__(self, timeout: float = 12.0, cookies: str = ""):
        self.timeout = timeout
        self.cookies = cookies or ""

    def _headers(self) -> dict[str, str]:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        if self.cookies:
            headers["Cookie"] = self.cookies
        return headers

    @abstractmethod
    async def fetch(self, room_id: str) -> LiveInfo:
        """查询房间状态。"""

    def room_url(self, room_id: str) -> str:
        return ""
