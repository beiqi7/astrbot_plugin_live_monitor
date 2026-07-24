"""斗鱼直播状态检测。"""

from __future__ import annotations

from astrbot.api import logger

from .base import LiveInfo, PlatformChecker


class DouyuChecker(PlatformChecker):
    platform = "douyu"

    def room_url(self, room_id: str) -> str:
        return f"https://www.douyu.com/{room_id}"

    async def fetch(self, room_id: str) -> LiveInfo:
        room_id = self.normalize_input_room_id(room_id)
        info = LiveInfo(
            platform=self.platform,
            room_id=room_id,
            is_live=False,
            url=self.room_url(room_id),
        )
        headers = self._headers(referer="https://www.douyu.com/")

        try:
            async with self._client(headers=headers) as client:
                r = await client.get(f"https://www.douyu.com/betard/{room_id}")
                if r.status_code != 200:
                    info.error = self._http_error(r.status_code)
                    return info.finalize()

                payload = r.json()
                room = payload.get("room") or {}
                if not room:
                    info.error = "房间不存在或接口无数据"
                    return info.finalize()

                # show_status: 1 开播，2 未开播
                info.is_live = int(room.get("show_status") or 0) == 1
                info.title = str(room.get("room_name") or "")
                info.real_name = str(
                    room.get("owner_name") or room.get("nickname") or f"房间{room_id}"
                )
                info.cover_url = str(
                    room.get("room_pic") or room.get("owner_avatar") or ""
                )
                info.room_id = str(room.get("room_id") or room_id)
                info.url = self.room_url(info.room_id)
                info.raw = room

        except Exception as e:
            logger.warning(f"斗鱼查询失败 room={room_id}: {e}")
            info.error = f"{type(e).__name__}: {e}"

        return info.finalize()
