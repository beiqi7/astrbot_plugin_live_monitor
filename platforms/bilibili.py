"""B站直播状态检测。"""

from __future__ import annotations

import httpx

from astrbot.api import logger

from .base import LiveInfo, PlatformChecker


class BilibiliChecker(PlatformChecker):
    platform = "bilibili"

    def room_url(self, room_id: str) -> str:
        return f"https://live.bilibili.com/{room_id}"

    async def fetch(self, room_id: str) -> LiveInfo:
        room_id = str(room_id).strip()
        info = LiveInfo(
            platform=self.platform,
            room_id=room_id,
            is_live=False,
            url=self.room_url(room_id),
        )
        headers = self._headers()
        headers["Referer"] = "https://live.bilibili.com/"

        try:
            async with httpx.AsyncClient(timeout=self.timeout, headers=headers) as client:
                # 房间详情：状态 / 标题 / 封面 / uid
                r = await client.get(
                    "https://api.live.bilibili.com/room/v1/Room/get_info",
                    params={"room_id": room_id},
                )
                data = r.json()
                if data.get("code") != 0:
                    info.error = data.get("message") or f"code={data.get('code')}"
                    return info

                room = data.get("data") or {}
                info.is_live = int(room.get("live_status") or 0) == 1
                info.title = str(room.get("title") or "")
                info.cover_url = str(
                    room.get("user_cover") or room.get("keyframe") or room.get("cover") or ""
                )
                info.room_id = str(room.get("room_id") or room_id)
                info.url = self.room_url(info.room_id)
                uid = room.get("uid")
                info.raw = {"room": room}

                # 主播名
                if uid:
                    try:
                        r2 = await client.get(
                            "https://api.live.bilibili.com/live_user/v1/Master/info",
                            params={"uid": uid},
                        )
                        master = (r2.json().get("data") or {}).get("info") or {}
                        info.real_name = str(master.get("uname") or "")
                        info.raw["master"] = master
                    except Exception as e:
                        logger.debug(f"B站获取主播名失败 room={room_id}: {e}")

                if not info.real_name:
                    info.real_name = str(room.get("uname") or "") or f"房间{room_id}"

        except Exception as e:
            logger.warning(f"B站查询失败 room={room_id}: {e}")
            info.error = str(e)

        return info
