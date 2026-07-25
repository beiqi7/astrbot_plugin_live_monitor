"""定时轮询直播状态。"""

from __future__ import annotations

import asyncio
import random
import time
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from astrbot.api import logger

from ..platforms import check_room
from ..platforms.base import LiveInfo
from .storage import DataManager, RoomRecord, room_key

if TYPE_CHECKING:
    from .notifier import Notifier


OnChange = Callable[[RoomRecord, LiveInfo, str], Awaitable[None]]

# 连续失败达到阈值后进入冷却，避免持续冲击失效/被封房间
_FAIL_THRESHOLD = 3
# 冷却基础秒数，按失败轮次指数递增：base * 2^(tier-1)，上限 _COOLDOWN_MAX
_COOLDOWN_BASE = 60.0
_COOLDOWN_MAX = 1800.0


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
        # 每房间连续失败计数与冷却到期时间戳
        self._fail_counts: dict[str, int] = {}
        self._cooldown_until: dict[str, float] = {}

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
                # 加 ±2s 抖动，避免与平台限流窗口长期同步
                jitter = random.uniform(-2.0, 2.0)
                await asyncio.sleep(max(5, self._interval() + jitter))
            except asyncio.CancelledError:
                break

    async def _sync_config_if_needed(self) -> None:
        try:
            from .config_sync import sync_rooms_from_config

            cfg = self._cfg()
            stats = await sync_rooms_from_config(
                self.data,
                cfg,
                mark_primed=lambda k: self._primed.add(k),
            )
            # 轮询中新同步到的已开播房间，立刻推送
            live_to_notify = stats.get("live_to_notify") or []
            if live_to_notify:
                global_at_all = bool(cfg.get("at_all_on_live", False))
                self.notifier.reload_config(cfg)
                for rec, info in live_to_notify:
                    try:
                        n = await self.notifier.notify_live(
                            rec, info, global_at_all=global_at_all
                        )
                        logger.info(
                            f"轮询同步新加已开播房间已推送: "
                            f"{rec.platform}:{rec.room_id} 成功={n}"
                        )
                    except Exception as e:
                        logger.warning(
                            f"轮询同步推送失败 {rec.platform}:{rec.room_id}: {e}"
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

        # 清理已不在监控列表的房间的失败/冷却记录
        live_keys = {room_key(r.platform, r.room_id) for r in rooms}
        for k in list(self._fail_counts.keys()):
            if k not in live_keys:
                self._fail_counts.pop(k, None)
        for k in list(self._cooldown_until.keys()):
            if k not in live_keys:
                self._cooldown_until.pop(k, None)

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

        # 本轮所有刷新统一落盘一次（转移/订阅变更已即时落盘）
        try:
            self.data.flush()
        except Exception as e:
            logger.debug(f"轮询末尾落盘失败: {e}")

    async def _check_room(
        self,
        rec: RoomRecord,
        *,
        timeout: float,
        bilibili_cookie: str,
        douyin_cookie: str,
        global_at_all: bool,
    ) -> None:
        key = room_key(rec.platform, rec.room_id)

        # 冷却期内跳过，避免持续冲击失效房间
        cooldown_until = self._cooldown_until.get(key, 0.0)
        if cooldown_until and time.time() < cooldown_until:
            logger.debug(f"房间 {key} 失败冷却中，跳过本轮（至 {time.strftime('%H:%M:%S', time.localtime(cooldown_until))}）")
            return
        if cooldown_until:
            self._cooldown_until.pop(key, None)

        info = await check_room(
            rec.platform,
            rec.room_id,
            timeout=timeout,
            bilibili_cookie=bilibili_cookie,
            douyin_cookie=douyin_cookie,
        )
        if not info.ok:
            self._record_failure(key, info.error or "未知错误")
            return

        # 成功：重置失败计数
        self._fail_counts.pop(key, None)
        self._cooldown_until.pop(key, None)

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
            # 开播（状态变更即时落盘，防止崩溃后重复推送）
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
            # 关播（状态变更即时落盘）
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
            # 状态未变，仅刷新标题等，延迟到本轮末尾统一落盘
            self.data.update_live_state(
                rec.platform,
                rec.room_id,
                is_live=info.is_live,
                title=info.title or rec.title,
                real_name=info.real_name or rec.real_name,
                cover_url=info.cover_url or rec.cover_url,
                url=info.url or rec.url,
                persist=False,
            )

    def _record_failure(self, key: str, error: str) -> None:
        """记录一次检查失败，超过阈值后进入指数退避冷却。"""
        count = self._fail_counts.get(key, 0) + 1
        self._fail_counts[key] = count

        if count < _FAIL_THRESHOLD:
            logger.debug(f"检查失败 {key} ({count}/{_FAIL_THRESHOLD}): {error}")
            return

        tier = count - _FAIL_THRESHOLD + 1
        cooldown = min(_COOLDOWN_BASE * (2 ** (tier - 1)), _COOLDOWN_MAX)
        self._cooldown_until[key] = time.time() + cooldown
        logger.warning(
            f"房间 {key} 连续失败 {count} 次，进入冷却 {int(cooldown)}s: {error}"
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
