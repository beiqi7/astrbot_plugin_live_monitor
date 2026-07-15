"""多平台直播状态检测。"""

from .base import LiveInfo, PlatformChecker, normalize_platform, PLATFORM_NAMES
from .registry import get_checker, check_room, list_platforms

__all__ = [
    "LiveInfo",
    "PlatformChecker",
    "normalize_platform",
    "PLATFORM_NAMES",
    "get_checker",
    "check_room",
    "list_platforms",
]
