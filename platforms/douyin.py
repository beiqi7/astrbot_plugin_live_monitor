"""抖音直播状态检测。"""

from __future__ import annotations

import httpx

from astrbot.api import logger

from .base import LiveInfo, PlatformChecker


class DouyinChecker(PlatformChecker):
    platform = "douyin"

    def room_url(self, room_id: str) -> str:
        return f"https://live.douyin.com/{room_id}"

    async def fetch(self, room_id: str) -> LiveInfo:
        """room_id 使用直播间 web_rid（链接 https://live.douyin.com/<web_rid> 中的数字）。"""
        web_rid = str(room_id).strip()
        info = LiveInfo(
            platform=self.platform,
            room_id=web_rid,
            is_live=False,
            url=self.room_url(web_rid),
        )
        headers = self._headers()
        headers["Referer"] = "https://live.douyin.com/"

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                headers=headers,
                follow_redirects=True,
            ) as client:
                # 先访问首页获取 ttwid 等 Cookie
                try:
                    await client.get(
                        "https://live.douyin.com/",
                        headers={**headers, "Accept": "text/html"},
                    )
                except Exception:
                    pass

                if self.cookies:
                    # 合并用户 Cookie
                    client.cookies.update(_parse_cookie_header(self.cookies))

                r = await client.get(
                    "https://live.douyin.com/webcast/room/web/enter/",
                    params={
                        "aid": "6383",
                        "app_name": "douyin_web",
                        "live_id": "1",
                        "device_platform": "web",
                        "language": "zh-CN",
                        "enter_from": "web_live",
                        "cookie_enabled": "true",
                        "screen_width": "1920",
                        "screen_height": "1080",
                        "browser_language": "zh-CN",
                        "browser_platform": "Win32",
                        "browser_name": "Chrome",
                        "browser_version": "122.0.0.0",
                        "web_rid": web_rid,
                    },
                )
                if r.status_code != 200:
                    info.error = f"HTTP {r.status_code}"
                    return info

                payload = r.json()
                if payload.get("status_code") not in (0, None):
                    info.error = payload.get("data") and str(payload.get("data")) or f"status_code={payload.get('status_code')}"
                    # 仍尝试解析

                data = payload.get("data") or {}
                rooms = data.get("data") or []
                user = data.get("user") or {}
                enter_id = data.get("enter_room_id") or ""

                if not rooms and not enter_id:
                    info.error = "房间不存在或无法访问（可尝试填写抖音 Cookie）"
                    # 仍返回结构，便于上层提示
                    info.real_name = str(user.get("nickname") or "")
                    return info

                room = rooms[0] if rooms else {}
                # status: 2 直播中，4 未开播/已结束
                status = int(room.get("status") or 0)
                info.is_live = status == 2
                info.title = str(room.get("title") or "")
                owner = room.get("owner") or {}
                info.real_name = str(
                    owner.get("nickname")
                    or user.get("nickname")
                    or f"房间{web_rid}"
                )
                cover = room.get("cover") or {}
                urls = cover.get("url_list") or []
                if urls:
                    info.cover_url = str(urls[0])
                elif owner.get("avatar_thumb"):
                    av = owner["avatar_thumb"].get("url_list") or []
                    if av:
                        info.cover_url = str(av[0])
                elif user.get("avatar_thumb"):
                    av = user["avatar_thumb"].get("url_list") or []
                    if av:
                        info.cover_url = str(av[0])

                info.room_id = web_rid
                info.url = self.room_url(web_rid)
                info.raw = {"room": room, "user": user, "room_status": data.get("room_status")}

                # 成功拿到房间后清除「不存在」类 error
                if rooms or enter_id:
                    info.error = ""

        except Exception as e:
            logger.warning(f"抖音查询失败 room={web_rid}: {e}")
            info.error = str(e)

        return info


def _parse_cookie_header(cookie: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for part in cookie.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        result[k.strip()] = v.strip()
    return result
