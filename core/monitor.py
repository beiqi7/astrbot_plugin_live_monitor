"""定时轮询直播状态。"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from astrbot.api import logger

from ..platforms import check_room
from ..platforms.base import LiveInfo
from .storage import DataManager, RoomRecord

if TYPE_CHECKING:
    from .notifier import Notifier


OnChange = Callable[[RoomRecord, LiveInfo, str], Awaitable[None]]


class LiveMonitor:
    """后台轮询监控器。"""

    def __init__(
        self,
        data: DataManager,
        notifier: "Notifier",
        config_getter: Callable[[], dict[str, Any]],
    ):
        self.data = data
        self.notifier = notifier
        self.config_getter = config_getter
        self._task: asyncio.Task | None = None
        self._running = False
        # 首次检查只同步状态，不推送，避免重载插件刷屏
        self._primed: set[str] = set()

    def _cfg(self) -> dict[str, Any]:
        cfg = self.config_getter() or {}
        return cfg

    def _interval(self) -> int:
        try:
            v = int(self._cfg().get("check_interval", 60) or 60)
        except Exception:
            v = 60
        return max(15, v)

    def _timeout(self) -> float:
        try:
            return float(self._cfg().get("request_timeout", 12) or 12)
        except Exception:
            return 12.0

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="live_monitor_loop")
        logger.info("直播监控轮询已启动")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("直播监控轮询已停止")

    async def _loop(self) -> None:
        # 启动后稍等，避免与插件加载抢资源
        await asyncio.sleep(3)
        while self._running:
            try:
                # 每轮前尝试同步 WebUI 配置（用户改设置后无需重载插件）
                await self._sync_config_if_needed()
                await self.poll_once()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"直播监控轮询异常: {e}")
            try:
                await asyncio.sleep(self._interval())
            except asyncio.CancelledError:
                break

    async def _sync_config_if_needed(self) -> None:
        try:
            from .config_sync import sync_rooms_from_config

            cfg = self._cfg()
            await sync_rooms_from_config(
                self.data,
                cfg,
                mark_primed=lambda k: self._primed.add(k),
            )
        except Exception as e:
            logger.debug(f"轮询前配置同步跳过: {e}")

    async def poll_once(self) -> None:
        rooms = list(self.data.all_rooms().values())
        if not rooms:
            return

        cfg = self._cfg()
        self.notifier.reload_config(cfg)
        timeout = self._timeout()
        bili_cookie = str(cfg.get("bilibili_cookie", "") or "")
        dy_cookie = str(cfg.get("douyin_cookie", "") or "")
        global_at_all = bool(cfg.get("at_all_on_live", False))

        # 并发检查，限制并发数
        sem = asyncio.Semaphore(5)

        async def _one(rec: RoomRecord) -> None:
            async with sem:
                await self._check_room(
                    rec,
                    timeout=timeout,
                    bilibili_cookie=bili_cookie,
                    douyin_cookie=dy_cookie,
                    global_at_all=global_at_all,
                )

        await asyncio.gather(*[_one(r) for r in rooms], return_exceptions=True)

    async def _check_room(
        self,
        rec: RoomRecord,
        *,
        timeout: float,
        bilibili_cookie: str,
        douyin_cookie: str,
        global_at_all: bool,
    ) -> None:
        key = f"{rec.platform}:{rec.room_id}"
        info = await check_room(
            rec.platform,
            rec.room_id,
            timeout=timeout,
            bilibili_cookie=bilibili_cookie,
            douyin_cookie=douyin_cookie,
        )
        if not info.ok:
            logger.debug(f"检查失败 {key}: {info.error}")
            return

        was_live = rec.is_live
        first = key not in self._primed

        # 更新缓存信息
        if first:
            # 首次仅同步状态
            self.data.update_live_state(
                rec.platform,
                rec.room_id,
                is_live=info.is_live,
                title=info.title,
                real_name=info.real_name,
                cover_url=info.cover_url,
                url=info.url,
                live_start_ts=time.time() if info.is_live else 0.0,
            )
            self._primed.add(key)
            logger.info(
                f"同步直播状态 {key}: {'直播中' if info.is_live else '未开播'} "
                f"({info.real_name})"
            )
            return

        if info.is_live and not was_live:
            # 开播
            updated = self.data.update_live_state(
                rec.platform,
                rec.room_id,
                is_live=True,
                title=info.title,
                real_name=info.real_name,
                cover_url=info.cover_url,
                url=info.url,
                live_start_ts=time.time(),
            )
            logger.info(f"检测到开播: {key} {info.real_name} - {info.title}")
            if updated:
                await self.notifier.notify_live(updated, info, global_at_all=global_at_all)

        elif (not info.is_live) and was_live:
            # 关播
            start = rec.live_start_ts or 0
            duration = (time.time() - start) if start > 0 else 0
            updated = self.data.update_live_state(
                rec.platform,
                rec.room_id,
                is_live=False,
                title=info.title,
                real_name=info.real_name,
                cover_url=info.cover_url,
                url=info.url,
                live_start_ts=0.0,
            )
            logger.info(f"检测到关播: {key} {info.real_name}")
            if updated:
                await self.notifier.notify_offline(updated, info, duration)

        else:
            # 状态未变，刷新标题等
            self.data.update_live_state(
                rec.platform,
                rec.room_id,
                is_live=info.is_live,
                title=info.title or rec.title,
                real_name=info.real_name or rec.real_name,
                cover_url=info.cover_url or rec.cover_url,
                url=info.url or rec.url,
            )

    async def check_one_now(
        self, platform: str, room_id: str
    ) -> LiveInfo:
        cfg = self._cfg()
        return await check_room(
            platform,
            room_id,
            timeout=self._timeout(),
            bilibili_cookie=str(cfg.get("bilibili_cookie", "") or ""),
            douyin_cookie=str(cfg.get("douyin_cookie", "") or ""),
        )
