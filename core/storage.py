"""直播间订阅数据持久化。"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from astrbot.api import logger


def room_key(platform: str, room_id: str) -> str:
    return f"{platform}:{room_id}"


@dataclass
class RoomRecord:
    platform: str
    room_id: str
    nickname: str = ""  # 用户自定义昵称
    real_name: str = ""  # 平台真实名
    title: str = ""
    cover_url: str = ""
    url: str = ""
    is_live: bool = False
    live_start_ts: float = 0.0  # 开播时间戳
    last_check_ts: float = 0.0
    subscribers: list[str] = field(default_factory=list)  # unified_msg_origin 列表
    # 每个会话的设置: {umo: {"at_all": bool, "enabled": bool}}
    session_settings: dict[str, dict[str, Any]] = field(default_factory=dict)
    added_by: str = ""
    added_time: str = ""
    from_config: bool = False  # 是否由插件 WebUI 配置同步

    def display_name(self) -> str:
        return self.nickname or self.real_name or f"{self.platform}:{self.room_id}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RoomRecord":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        filtered = {k: v for k, v in data.items() if k in known}
        filtered.setdefault("subscribers", [])
        filtered.setdefault("session_settings", {})
        return cls(**filtered)


class DataManager:
    """JSON 文件存储。"""

    def __init__(self, data_path: Path):
        self.data_path = Path(data_path)
        self.data_path.parent.mkdir(parents=True, exist_ok=True)
        self.rooms: dict[str, RoomRecord] = {}
        self._dirty: bool = False
        self.load()

    def load(self) -> None:
        if not self.data_path.exists():
            self.rooms = {}
            return
        try:
            raw = json.loads(self.data_path.read_text(encoding="utf-8"))
            rooms = raw.get("rooms") or {}
            self.rooms = {
                k: RoomRecord.from_dict(v) for k, v in rooms.items()
            }
        except Exception as e:
            logger.error(f"加载直播监控数据失败: {e}")
            self.rooms = {}

    def save(self) -> None:
        try:
            payload = {
                "rooms": {k: v.to_dict() for k, v in self.rooms.items()},
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            self.data_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self._dirty = False
        except Exception as e:
            logger.error(f"保存直播监控数据失败: {e}")

    def flush(self) -> None:
        """仅当有未落盘修改时才写盘，用于轮询末尾批量持久化。"""
        if self._dirty:
            self.save()

    def get(self, platform: str, room_id: str) -> RoomRecord | None:
        return self.rooms.get(room_key(platform, room_id))

    def get_by_key(self, key: str) -> RoomRecord | None:
        return self.rooms.get(key)

    def all_rooms(self) -> dict[str, RoomRecord]:
        return dict(self.rooms)

    def add_room(self, record: RoomRecord) -> None:
        self.rooms[room_key(record.platform, record.room_id)] = record
        self.save()

    def remove_room(self, platform: str, room_id: str) -> bool:
        key = room_key(platform, room_id)
        if key not in self.rooms:
            return False
        del self.rooms[key]
        self.save()
        return True

    def subscribe(self, platform: str, room_id: str, umo: str) -> bool:
        rec = self.get(platform, room_id)
        if not rec:
            return False
        if umo in rec.subscribers:
            return False
        rec.subscribers.append(umo)
        rec.session_settings.setdefault(umo, {"at_all": False, "enabled": True})
        self.save()
        return True

    def unsubscribe(self, platform: str, room_id: str, umo: str) -> bool:
        rec = self.get(platform, room_id)
        if not rec or umo not in rec.subscribers:
            return False
        rec.subscribers = [s for s in rec.subscribers if s != umo]
        rec.session_settings.pop(umo, None)
        self.save()
        return True

    def set_nickname(self, platform: str, room_id: str, nickname: str) -> bool:
        rec = self.get(platform, room_id)
        if not rec:
            return False
        rec.nickname = nickname
        self.save()
        return True

    def update_session_setting(
        self, platform: str, room_id: str, umo: str, **kwargs: Any
    ) -> bool:
        rec = self.get(platform, room_id)
        if not rec or umo not in rec.subscribers:
            return False
        settings = rec.session_settings.setdefault(umo, {"at_all": False, "enabled": True})
        settings.update(kwargs)
        self.save()
        return True

    def update_live_state(
        self,
        platform: str,
        room_id: str,
        *,
        is_live: bool,
        title: str = "",
        real_name: str = "",
        cover_url: str = "",
        url: str = "",
        live_start_ts: float | None = None,
        persist: bool = True,
    ) -> RoomRecord | None:
        rec = self.get(platform, room_id)
        if not rec:
            return None
        rec.is_live = is_live
        if title:
            rec.title = title
        if real_name:
            rec.real_name = real_name
        if cover_url:
            rec.cover_url = cover_url
        if url:
            rec.url = url
        rec.last_check_ts = time.time()
        if live_start_ts is not None:
            rec.live_start_ts = live_start_ts
        if persist:
            self.save()
        else:
            self._dirty = True
        return rec

    def rooms_for_session(self, umo: str) -> list[RoomRecord]:
        return [r for r in self.rooms.values() if umo in r.subscribers]
