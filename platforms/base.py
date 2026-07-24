"""平台检测基类与通用类型。"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx


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

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# 从完整直播间链接中提取房间号
_URL_PATTERNS: dict[str, re.Pattern[str]] = {
    "bilibili": re.compile(
        r"(?:live\.bilibili\.com/)(?:blanc/)?(\d+)", re.I
    ),
    "douyu": re.compile(r"(?:douyu\.com/)(?:topic/\w+\?rid=)?(\w+)", re.I),
    "huya": re.compile(r"(?:huya\.com/)([A-Za-z0-9_-]+)", re.I),
    "douyin": re.compile(r"(?:live\.douyin\.com/)(\d+)", re.I),
}


def normalize_platform(name: str) -> str | None:
    """将用户输入规范为平台 id，无法识别返回 None。"""
    key = (name or "").strip().lower()
    if not key:
        return None
    if key in _PLATFORM_ALIASES:
        return _PLATFORM_ALIASES[key]
    raw = (name or "").strip()
    return _PLATFORM_ALIASES.get(raw)


def normalize_cover_url(url: str) -> str:
    """规范化封面 URL：协议相对补全、http 升 https、去空白。"""
    u = (url or "").strip()
    if not u:
        return ""
    if u.startswith("//"):
        u = "https:" + u
    elif u.startswith("http://"):
        u = "https://" + u[7:]
    # 去掉常见查询噪声中的空格
    return u.replace(" ", "")


def extract_room_id(raw: str, platform: str | None = None) -> str:
    """从房间号或完整直播间 URL 中提取 room_id / web_rid。"""
    s = (raw or "").strip()
    if not s:
        return ""

    # 已是纯房间号
    if "://" not in s and "/" not in s and "?" not in s and " " not in s:
        return s

    text = s if "://" in s else ("https://" + s.lstrip("/"))
    pid = (platform or "").strip().lower() or None

    # 平台专用正则
    candidates = [pid] if pid and pid in _URL_PATTERNS else list(_URL_PATTERNS)
    for p in candidates:
        if not p:
            continue
        m = _URL_PATTERNS[p].search(text)
        if m:
            return m.group(1)

    # 通用：取 path 最后一段数字/字母
    try:
        parsed = urlparse(text)
        path = (parsed.path or "").strip("/")
        if path:
            seg = path.split("/")[-1]
            if seg:
                return seg
        # douyin 有时 web_rid 在 query
        qs = parse_qs(parsed.query or "")
        for key in ("web_rid", "room_id", "rid", "id"):
            if key in qs and qs[key]:
                return str(qs[key][0])
    except Exception:
        pass

    return s


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

    def finalize(self) -> "LiveInfo":
        """规范化字段后返回自身，便于 fetch 末尾统一调用。"""
        self.cover_url = normalize_cover_url(self.cover_url)
        self.title = (self.title or "").strip()
        self.real_name = (self.real_name or "").strip()
        self.room_id = str(self.room_id or "").strip()
        if self.url:
            self.url = self.url.strip()
        return self


class PlatformChecker(ABC):
    """平台检测器抽象基类。"""

    platform: str = ""

    def __init__(self, timeout: float = 12.0, cookies: str = ""):
        self.timeout = timeout
        self.cookies = cookies or ""

    def _headers(self, *, referer: str = "") -> dict[str, str]:
        headers = {
            "User-Agent": _DEFAULT_UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        if referer:
            headers["Referer"] = referer
        if self.cookies:
            headers["Cookie"] = self.cookies
        return headers

    def _client(self, *, headers: dict[str, str] | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self.timeout,
            headers=headers or self._headers(),
            follow_redirects=True,
        )

    @staticmethod
    def _http_error(status: int) -> str:
        return f"HTTP {status}"

    @abstractmethod
    async def fetch(self, room_id: str) -> LiveInfo:
        """查询房间状态。"""

    def room_url(self, room_id: str) -> str:
        return ""

    def normalize_input_room_id(self, room_id: str) -> str:
        return extract_room_id(room_id, self.platform)
