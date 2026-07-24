"""B站直播状态检测。"""

from __future__ import annotations

from astrbot.api import logger

from .base import LiveInfo, PlatformChecker


class BilibiliChecker(PlatformChecker):
    platform = "bilibili"

    def room_url(self, room_id: str) -> str:
        return f"https://live.bilibili.com/{room_id}"

    async def fetch(self, room_id: str) -> LiveInfo:
        room_id = self.normalize_input_room_id(room_id)
        info = LiveInfo(
            platform=self.platform,
            room_id=room_id,
            is_live=False,
            url=self.room_url(room_id),
        )
        headers = self._headers(referer="https://live.bilibili.com/")

        try:
            async with self._client(headers=headers) as client:
                r = await client.get(
                    "https://api.live.bilibili.com/room/v1/Room/get_info",
                    params={"room_id": room_id},
                )
                if r.status_code != 200:
                    info.error = self._http_error(r.status_code)
                    return info.finalize()

                data = r.json()
                if data.get("code") != 0:
                    info.error = data.get("message") or f"code={data.get('code')}"
                    return info.finalize()

                room = data.get("data") or {}
                info.is_live = int(room.get("live_status") or 0) == 1
                info.title = str(room.get("title") or "")
                info.cover_url = str(
                    room.get("user_cover")
                    or room.get("keyframe")
                    or room.get("cover")
                    or ""
                )
                info.room_id = str(room.get("room_id") or room_id)
                info.url = self.room_url(info.room_id)
                uid = room.get("uid")
                info.raw = {"room": room}

                if uid:
                    try:
                        r2 = await client.get(
                            "https://api.live.bilibili.com/live_user/v1/Master/info",
                            params={"uid": uid},
                        )
                        if r2.status_code == 200:
                            master = (r2.json().get("data") or {}).get("info") or {}
                            info.real_name = str(master.get("uname") or "")
                            info.raw["master"] = master
                    except Exception as e:
                        logger.debug(f"B站获取主播名失败 room={room_id}: {e}")

                if not info.real_name:
                    info.real_name = str(room.get("uname") or "") or f"房间{room_id}"

        except Exception as e:
            logger.warning(f"B站查询失败 room={room_id}: {e}")
            info.error = f"{type(e).__name__}: {e}"

        return info.finalize()
