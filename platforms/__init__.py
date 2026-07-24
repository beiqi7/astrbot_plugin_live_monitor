"""多平台直播状态检测。"""

from .base import (
    LiveInfo,
    PlatformChecker,
    PLATFORM_NAMES,
    extract_room_id,
    normalize_cover_url,
    normalize_platform,
)
from .registry import get_checker, check_room, list_platforms

__all__ = [
    "LiveInfo",
    "PlatformChecker",
    "PLATFORM_NAMES",
    "normalize_platform",
    "normalize_cover_url",
    "extract_room_id",
    "get_checker",
    "check_room",
    "list_platforms",
]
