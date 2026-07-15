"""虎牙直播状态检测。"""

from __future__ import annotations

import httpx

from astrbot.api import logger

from .base import LiveInfo, PlatformChecker


class HuyaChecker(PlatformChecker):
    platform = "huya"

    def room_url(self, room_id: str) -> str:
        return f"https://www.huya.com/{room_id}"

    async def fetch(self, room_id: str) -> LiveInfo:
        room_id = str(room_id).strip()
        info = LiveInfo(
            platform=self.platform,
            room_id=room_id,
            is_live=False,
            url=self.room_url(room_id),
        )
        headers = self._headers()
        headers["Referer"] = "https://www.huya.com/"

        try:
            async with httpx.AsyncClient(timeout=self.timeout, headers=headers) as client:
                r = await client.get(
                    "https://mp.huya.com/cache.php",
                    params={"m": "Live", "do": "profileRoom", "roomid": room_id},
                )
                if r.status_code != 200:
                    info.error = f"HTTP {r.status_code}"
                    return info

                payload = r.json()
                data = payload.get("data")
                if not data:
                    info.error = payload.get("message") or "房间不存在或接口无数据"
                    return info

                live_status = str(data.get("liveStatus") or data.get("realLiveStatus") or "").upper()
                # ON / REPLAY / OFF
                info.is_live = live_status == "ON"

                live_data = data.get("liveData") or {}
                profile = data.get("profileInfo") or {}

                info.title = str(
                    live_data.get("introduction")
                    or live_data.get("roomName")
                    or live_data.get("liveDesc")
                    or ""
                )
                info.real_name = str(
                    profile.get("nick")
                    or live_data.get("nick")
                    or f"房间{room_id}"
                )
                info.cover_url = str(
                    live_data.get("screenshot")
                    or live_data.get("cover")
                    or profile.get("avatar180")
                    or profile.get("avatar")
                    or ""
                )
                rid = live_data.get("profileRoom") or room_id
                info.room_id = str(rid)
                info.url = self.room_url(info.room_id)
                info.raw = data

        except Exception as e:
            logger.warning(f"虎牙查询失败 room={room_id}: {e}")
            info.error = str(e)

        return info
