"""AstrBot 多平台直播监控插件。

指令前缀: /livecheck
支持虎牙 / 斗鱼 / 抖音 / B站，开播关播主动推送到群，封面图、可选 LLM 文案、自定义昵称。
"""

from __future__ import annotations

import time
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .core.config_sync import sync_rooms_from_config
from .core.monitor import LiveMonitor
from .core.notifier import Notifier
from .core.storage import DataManager, RoomRecord, room_key
from .platforms import (
    PLATFORM_NAMES,
    check_room,
    extract_room_id,
    list_platforms,
    normalize_platform,
)


HELP_TEXT = """✨ livecheck · 多平台直播结界
━━━━━━━━━━━━━━━━
【推荐】在 AstrBot 管理面板 → 本插件设置里：
  · 监控直播间：点添加
  · 推送目标：填 QQ 群号（每行一个）
  · 保存后自动同步，无需指令

【指令订阅】
/livecheck add <平台> <房间号> [昵称]
/livecheck del <平台> <房间号>
/livecheck list · list all
/livecheck nickname <平台> <房间号> <昵称>
/livecheck check [平台 房间号]  · 不带参数列出正在直播

【会话设置】
/livecheck atall <平台> <房间号> [on|off]
/livecheck mute <平台> <房间号> [on|off]
/livecheck sync     立即从设置同步直播间
/livecheck testpush 测试主动推送
/livecheck status
/livecheck help

【平台】bilibili/b站 · douyu/斗鱼 · huya/虎牙 · douyin/抖音
房间号可直接粘贴直播间链接，本喵会自动提取喵
抖音填 live.douyin.com 的 web_rid 数字或整段链接
"""


CFG_KEYS = [
    "monitor_rooms",
    "rooms_text",
    "notify_targets",
    "umo_prefix",
    "check_interval",
    "enable_cover",
    "notify_offline",
    "enable_llm",
    "llm_provider_id",
    "llm_prompt",
    "live_template",
    "offline_template",
    "at_all_on_live",
    "bilibili_cookie",
    "douyin_cookie",
    "request_timeout",
]


@register(
    "astrbot_plugin_live_monitor",
    "Grok",
    "多平台直播监控 livecheck：虎牙/斗鱼/抖音/B站 开播关播主动推送",
    "1.4.0",
)
class LiveMonitorPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config or {}
        data_dir = Path(__file__).resolve().parent / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        self.data = DataManager(data_dir / "rooms.json")
        self.notifier = Notifier(context, self._cfg_dict())
        self.monitor = LiveMonitor(
            self.data,
            self.notifier,
            config_getter=self._cfg_dict,
        )

    def _cfg_dict(self) -> dict:
        cfg = self.config
        if cfg is None:
            return {}
        if isinstance(cfg, dict):
            return cfg
        try:
            return dict(cfg)
        except Exception:
            try:
                return {k: cfg.get(k) for k in CFG_KEYS}
            except Exception:
                return {}

    async def initialize(self):
        self.notifier.reload_config(self._cfg_dict())
        stats = await self._sync_config_rooms()
        await self.monitor.start()
        logger.info(
            f"livecheck 已加载，房间={len(self.data.all_rooms())} "
            f"(配置同步 +{stats.get('added', 0)} ~{stats.get('updated', 0)} "
            f"-{stats.get('removed', 0)}) "
            f"LLM={'开' if self.notifier.llm_enabled() else '关(模板)'}"
        )

    async def terminate(self):
        await self.monitor.stop()
        self.data.save()
        logger.info("livecheck 直播监控已卸载")

    async def _sync_config_rooms(self) -> dict:
        """从 WebUI 配置同步监控列表与推送目标。"""
        cfg = self._cfg_dict()
        self.notifier.reload_config(cfg)
        try:
            return await sync_rooms_from_config(
                self.data,
                cfg,
                mark_primed=lambda k: self.monitor._primed.add(k),
            )
        except Exception as e:
            logger.error(f"从配置同步直播间失败: {e}")
            return {"added": 0, "updated": 0, "removed": 0, "total_config": 0}

    # -------------------- helpers --------------------

    def _parse_platform(self, platform: str) -> str | None:
        return normalize_platform(platform)

    def _room_line(self, rec: RoomRecord, idx: int | None = None) -> str:
        status = "🔴直播中" if rec.is_live else "⚫未开播"
        prefix = f"{idx}. " if idx is not None else "• "
        title = rec.title or "-"
        if len(title) > 30:
            title = title[:30] + "…"
        return (
            f"{prefix}{rec.display_name()} [{PLATFORM_NAMES.get(rec.platform, rec.platform)}]\n"
            f"   房间: {rec.room_id} | {status}\n"
            f"   标题: {title}\n"
            f"   订阅: {len(rec.subscribers)}"
        )

    def _extra_nickname(self, event: AstrMessageEvent, *consumed: str) -> str:
        """从原始消息中截取指令参数之后的剩余文本作为昵称（支持空格）。"""
        text = (event.message_str or "").strip()
        parts = text.split()
        if not parts:
            return ""
        lower_parts = [p.lower() for p in parts]

        for token in ("add", "nickname", "nick"):
            if token in lower_parts:
                idx = lower_parts.index(token)
                if len(parts) > idx + 3:
                    return " ".join(parts[idx + 3 :]).strip()
                return ""

        if len(consumed) >= 2:
            rid = str(consumed[1])
            for i, p in enumerate(parts):
                if p == rid or p.lower() == rid.lower():
                    return " ".join(parts[i + 1 :]).strip()
        return ""

    # -------------------- command group: livecheck --------------------

    @filter.command_group("livecheck")
    def livecheck_group(self):
        """多平台直播监控 livecheck"""
        pass

    @livecheck_group.command("help")
    async def cmd_help(self, event: AstrMessageEvent):
        """查看帮助"""
        yield event.plain_result(HELP_TEXT)

    @livecheck_group.command("add")
    async def cmd_add(
        self,
        event: AstrMessageEvent,
        platform: str,
        room_id: str,
        nickname: str = "",
    ):
        """添加监控并订阅到当前会话

        Args:
            platform: 平台（bilibili/douyu/huya/douyin 或中文名）
            room_id: 房间号（抖音为 web_rid）
            nickname: 自定义昵称（可选，支持空格）
        """
        pid = self._parse_platform(platform)
        if not pid:
            plats = "、".join(f"{k}/{v}" for k, v in list_platforms())
            yield event.plain_result(f"⚠️ 未知平台「{platform}」\n支持: {plats}")
            return

        room_id = extract_room_id(str(room_id).strip(), pid)
        if not room_id:
            yield event.plain_result("⚠️ 请填写房间号或直播间链接")
            return

        extra = self._extra_nickname(event, platform, room_id)
        if extra:
            nickname = extra
        nickname = (nickname or "").strip()

        umo = event.unified_msg_origin
        if not umo:
            yield event.plain_result(
                "❌ 无法获取当前会话 ID（unified_msg_origin 为空），无法订阅推送"
            )
            return

        existing = self.data.get(pid, room_id)
        already_sub = bool(existing and umo in existing.subscribers)

        yield event.plain_result(f"⏳ 正在查询 {PLATFORM_NAMES[pid]} 房间 {room_id} …")

        cfg = self._cfg_dict()
        info = await check_room(
            pid,
            room_id,
            timeout=float(cfg.get("request_timeout", 12) or 12),
            bilibili_cookie=str(cfg.get("bilibili_cookie", "") or ""),
            douyin_cookie=str(cfg.get("douyin_cookie", "") or ""),
        )
        if not info.ok and not info.real_name and not info.title:
            yield event.plain_result(
                f"❌ 无法获取直播间信息\n原因: {info.error or '未知'}\n"
                f"请检查房间号；抖音可尝试在配置中填写 Cookie"
            )
            return

        # 已在监控列表：补订阅 + 刷新状态；已在播则推给当前会话
        if existing:
            if not already_sub:
                self.data.subscribe(pid, room_id, umo)
            if nickname:
                self.data.set_nickname(pid, room_id, nickname)
            if info.ok or info.real_name or info.title:
                self.data.update_live_state(
                    pid,
                    existing.room_id,
                    is_live=bool(info.is_live),
                    title=info.title or existing.title,
                    real_name=info.real_name or existing.real_name,
                    cover_url=info.cover_url or existing.cover_url,
                    url=info.url or existing.url,
                    live_start_ts=(
                        (existing.live_start_ts or time.time())
                        if info.is_live
                        else 0.0
                    ),
                )
            rec = self.data.get(pid, existing.room_id) or existing
            self.monitor._primed.add(room_key(rec.platform, rec.room_id))

            pushed = False
            push_fail = False
            if bool(info.is_live):
                try:
                    self.notifier.reload_config(cfg)
                    n = await self.notifier.notify_live(
                        rec,
                        info,
                        global_at_all=bool(cfg.get("at_all_on_live", False)),
                        only_umos=[umo],
                    )
                    pushed = n > 0
                    push_fail = not pushed
                except Exception as e:
                    logger.warning(f"订阅时推送开播通知失败: {e}")
                    push_fail = True

            status = "🔴 直播中" if info.is_live else "⚫ 未开播"
            head = (
                "⚠️ 当前会话已订阅，已刷新状态"
                if already_sub
                else "✅ 已订阅（将主动推送到本会话）"
            )
            push_note = ""
            if pushed:
                push_note = "\n📨 已推送开播通知到本会话"
            elif push_fail and info.is_live:
                push_note = "\n⚠️ 开播通知推送失败，请 /livecheck testpush 检查通道"
            yield event.plain_result(
                f"{head}\n"
                f"主播: {rec.display_name()}\n"
                f"平台: {PLATFORM_NAMES[pid]}\n"
                f"房间: {rec.room_id}\n"
                f"状态: {status}\n"
                f"会话: {umo}"
                f"{push_note}"
            )
            return

        rec = RoomRecord(
            platform=pid,
            room_id=str(info.room_id or room_id),
            nickname=nickname,  # 仅用户显式设置时写入自定义昵称
            real_name=info.real_name or "",
            title=info.title or "",
            cover_url=info.cover_url or "",
            url=info.url or "",
            is_live=bool(info.is_live),
            live_start_ts=time.time() if info.is_live else 0.0,
            last_check_ts=time.time(),
            subscribers=[umo],
            session_settings={umo: {"at_all": False, "enabled": True}},
            added_by=str(event.get_sender_id() or ""),
            added_time=time.strftime("%Y-%m-%d %H:%M:%S"),
        )
        # display_name 在 nickname 为空时用 real_name
        self.data.add_room(rec)

        # 已在直播：立即推送一次开播通知，再 prime 避免轮询重复推送
        pushed = False
        push_fail = False
        if rec.is_live:
            try:
                self.notifier.reload_config(cfg)
                n = await self.notifier.notify_live(
                    rec,
                    info,
                    global_at_all=bool(cfg.get("at_all_on_live", False)),
                    only_umos=[umo],
                )
                pushed = n > 0
                push_fail = not pushed
            except Exception as e:
                logger.warning(f"添加时推送开播通知失败: {e}")
                push_fail = True
        self.monitor._primed.add(room_key(rec.platform, rec.room_id))

        status = "🔴 直播中" if rec.is_live else "⚫ 未开播"
        tip = f"\n⚠️ 提示: {info.error}" if info.error else ""
        if pushed:
            push_note = "\n📨 已推送开播通知到本会话"
        elif push_fail and rec.is_live:
            push_note = "\n⚠️ 开播通知推送失败，请 /livecheck testpush 检查通道"
        else:
            push_note = ""

        yield event.plain_result(
            f"✅ 已添加监控并订阅\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"👤 显示名: {rec.display_name()}\n"
            f"📺 平台: {PLATFORM_NAMES[pid]}\n"
            f"🔢 房间: {rec.room_id}\n"
            f"📝 标题: {rec.title or '-'}\n"
            f"📡 状态: {status}\n"
            f"🔗 {rec.url}\n"
            f"📬 推送会话: {umo}"
            f"{tip}{push_note}\n\n"
            f"改昵称: /livecheck nickname {pid} {rec.room_id} <名>\n"
            f"测推送: /livecheck testpush"
        )

    @livecheck_group.command("del")
    async def cmd_del(self, event: AstrMessageEvent, platform: str, room_id: str):
        """取消订阅；若无其他订阅者则删除监控"""
        pid = self._parse_platform(platform)
        if not pid:
            yield event.plain_result(f"⚠️ 未知平台「{platform}」")
            return
        room_id = extract_room_id(str(room_id).strip(), pid)
        rec = self.data.get(pid, room_id)
        if not rec:
            yield event.plain_result(f"⚠️ 未找到 {PLATFORM_NAMES.get(pid, pid)} 房间 {room_id}")
            return

        umo = event.unified_msg_origin
        name = rec.display_name()
        if umo in rec.subscribers:
            self.data.unsubscribe(pid, room_id, umo)
            rec = self.data.get(pid, room_id)
            if rec and rec.subscribers:
                yield event.plain_result(
                    f"✅ 已取消当前会话对 {name} 的订阅\n"
                    f"（仍有 {len(rec.subscribers)} 个会话在监控）"
                )
                return

        if not rec or not rec.subscribers:
            self.data.remove_room(pid, room_id)
            self.monitor._primed.discard(room_key(pid, room_id))
            yield event.plain_result(f"✅ 已移除监控：{name} ({PLATFORM_NAMES[pid]} {room_id})")
        else:
            yield event.plain_result(
                f"⚠️ 当前会话未订阅该房间。\n"
                f"仍有 {len(rec.subscribers)} 个会话订阅中。\n"
                f"强制删除请用 /livecheck remove（管理员）"
            )

    @livecheck_group.command("remove")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_remove(self, event: AstrMessageEvent, platform: str, room_id: str):
        """管理员强制删除监控房间"""
        pid = self._parse_platform(platform)
        if not pid:
            yield event.plain_result(f"⚠️ 未知平台「{platform}」")
            return
        room_id = extract_room_id(str(room_id).strip(), pid)
        rec = self.data.get(pid, room_id)
        if not rec:
            yield event.plain_result("⚠️ 房间不在监控列表中")
            return
        name = rec.display_name()
        self.data.remove_room(pid, room_id)
        self.monitor._primed.discard(room_key(pid, room_id))
        yield event.plain_result(f"✅ 已强制移除：{name} ({PLATFORM_NAMES[pid]} {room_id})")

    @livecheck_group.command("list")
    async def cmd_list(self, event: AstrMessageEvent, scope: str = "here"):
        """查看列表

        Args:
            scope: here=当前会话订阅（默认） all=全部监控
        """
        scope = (scope or "here").lower()
        if scope in ("all", "全部", "global", "a"):
            rooms = list(self.data.all_rooms().values())
            title = "📋 全部监控列表"
        else:
            rooms = self.data.rooms_for_session(event.unified_msg_origin)
            title = "📋 当前会话订阅"

        if not rooms:
            yield event.plain_result(
                f"{title}\n暂无数据。\n使用 /livecheck add <平台> <房间号> 添加"
            )
            return

        lines = [title, "━━━━━━━━━━━━━━━━"]
        for i, rec in enumerate(rooms, 1):
            lines.append(self._room_line(rec, i))
        lines.append("━━━━━━━━━━━━━━━━")
        lines.append(f"共 {len(rooms)} 个 · /livecheck list all 看全部")
        yield event.plain_result("\n".join(lines))

    @livecheck_group.command("nickname")
    async def cmd_nickname(
        self,
        event: AstrMessageEvent,
        platform: str,
        room_id: str,
        nickname: str = "",
    ):
        """设置自定义昵称（支持空格）"""
        pid = self._parse_platform(platform)
        if not pid:
            yield event.plain_result(f"⚠️ 未知平台「{platform}」")
            return
        room_id = extract_room_id(str(room_id).strip(), pid)
        nick = self._extra_nickname(event, platform, room_id) or (nickname or "").strip()
        if not nick:
            yield event.plain_result("⚠️ 昵称不能为空")
            return
        rec = self.data.get(pid, room_id)
        if not rec:
            yield event.plain_result("⚠️ 房间不在监控列表中，请先 /livecheck add")
            return
        old = rec.display_name()
        self.data.set_nickname(pid, room_id, nick)
        yield event.plain_result(
            f"✅ 已设置昵称\n"
            f"{PLATFORM_NAMES[pid]} {room_id}\n"
            f"{old} → {nick}"
        )

    @livecheck_group.command("check")
    async def cmd_check(
        self,
        event: AstrMessageEvent,
        platform: str = "",
        room_id: str = "",
    ):
        """立即查询直播间状态

        无参数时列出所有正在直播的房间；带参数则查指定房间。
        """
        # 无参数：列出所有正在直播的房间
        if not platform and not room_id:
            rooms = self.data.all_rooms()
            live_rooms = [r for r in rooms.values() if r.is_live]
            if not live_rooms:
                yield event.plain_result(
                    "⚫ 当前没有正在直播的房间喵\n"
                    "共监控 {} 个房间".format(len(rooms))
                )
                return
            lines = [
                f"🔴 正在直播 ({len(live_rooms)}/{len(rooms)})",
                "━━━━━━━━━━━━━━━━",
            ]
            for i, rec in enumerate(live_rooms, 1):
                title = rec.title or "（无标题）"
                if len(title) > 30:
                    title = title[:30] + "…"
                lines.append(
                    f"{i}. {rec.display_name()} [{PLATFORM_NAMES.get(rec.platform, rec.platform)}]\n"
                    f"   🎬 {title}\n"
                    f"   🔗 {rec.url}"
                )
            lines.append("━━━━━━━━━━━━━━━━")
            lines.append("查详情: /livecheck check <平台> <房间号>")
            yield event.plain_result("\n".join(lines))
            return

        # 带参数：查指定房间
        pid = self._parse_platform(platform)
        if not pid:
            plats = "、".join(f"{k}/{v}" for k, v in list_platforms())
            yield event.plain_result(f"⚠️ 未知平台「{platform}」\n支持: {plats}")
            return
        room_id = extract_room_id(str(room_id).strip(), pid)
        if not room_id:
            yield event.plain_result("⚠️ 请填写房间号或直播间链接")
            return
        cfg = self._cfg_dict()
        info = await check_room(
            pid,
            room_id,
            timeout=float(cfg.get("request_timeout", 12) or 12),
            bilibili_cookie=str(cfg.get("bilibili_cookie", "") or ""),
            douyin_cookie=str(cfg.get("douyin_cookie", "") or ""),
        )
        rec = self.data.get(pid, room_id)
        name = rec.display_name() if rec else (info.real_name or room_id)

        if not info.ok and not info.real_name and not info.title:
            yield event.plain_result(f"❌ 查询失败: {info.error or '未知错误'}")
            return

        status = "🔴 正在播喵" if info.is_live else "⚫ 还没开播捏"
        text = (
            f"📡 侦察报告\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"👤 {name}\n"
            f"📺 {PLATFORM_NAMES[pid]} · 房间 {info.room_id or room_id}\n"
            f"🎬 {info.title or '（无标题）'}\n"
            f"📶 {status}\n"
            f"🔗 {info.url or '-'}"
        )
        if info.error:
            text += f"\n⚠️ {info.error}"

        if cfg.get("enable_cover", True) and info.cover_url:
            try:
                from astrbot.api.message_components import Image, Plain

                chain = []
                if hasattr(Image, "fromURL"):
                    chain.append(Image.fromURL(info.cover_url))
                else:
                    chain.append(Image(file=info.cover_url))
                chain.append(Plain("\n" + text))
                yield event.chain_result(chain)
                return
            except Exception as e:
                logger.debug(f"check 发送封面失败: {e}")

        yield event.plain_result(text)

    @livecheck_group.command("atall")
    async def cmd_atall(
        self,
        event: AstrMessageEvent,
        platform: str,
        room_id: str,
        enable: str = "",
    ):
        """设置开播 @全体成员"""
        pid = self._parse_platform(platform)
        if not pid:
            yield event.plain_result(f"⚠️ 未知平台「{platform}」")
            return
        room_id = extract_room_id(str(room_id).strip(), pid)
        rec = self.data.get(pid, room_id)
        umo = event.unified_msg_origin
        if not rec or umo not in rec.subscribers:
            yield event.plain_result("⚠️ 请先在当前会话订阅该房间")
            return

        cur = (rec.session_settings.get(umo) or {}).get("at_all", False)
        if enable.lower() in ("on", "1", "true", "开启", "开"):
            new = True
        elif enable.lower() in ("off", "0", "false", "关闭", "关"):
            new = False
        else:
            new = not cur

        self.data.update_session_setting(pid, room_id, umo, at_all=new)
        yield event.plain_result(
            f"✅ {rec.display_name()} 开播 @全体 已{'开启' if new else '关闭'}"
        )

    @livecheck_group.command("mute")
    async def cmd_mute(
        self,
        event: AstrMessageEvent,
        platform: str,
        room_id: str,
        enable: str = "",
    ):
        """静音/取消静音（当前会话不再接收该房间推送）"""
        pid = self._parse_platform(platform)
        if not pid:
            yield event.plain_result(f"⚠️ 未知平台「{platform}」")
            return
        room_id = extract_room_id(str(room_id).strip(), pid)
        rec = self.data.get(pid, room_id)
        umo = event.unified_msg_origin
        if not rec or umo not in rec.subscribers:
            yield event.plain_result("⚠️ 请先在当前会话订阅该房间")
            return

        cur_enabled = (rec.session_settings.get(umo) or {}).get("enabled", True)
        if enable.lower() in ("on", "1", "true", "开启", "开", "mute"):
            new_enabled = False
        elif enable.lower() in ("off", "0", "false", "关闭", "关", "unmute"):
            new_enabled = True
        else:
            new_enabled = not cur_enabled

        self.data.update_session_setting(pid, room_id, umo, enabled=new_enabled)
        yield event.plain_result(
            f"✅ {rec.display_name()} 推送已"
            f"{'静音' if not new_enabled else '恢复'}"
        )

    @livecheck_group.command("testpush")
    async def cmd_testpush(self, event: AstrMessageEvent):
        """向当前会话发送一条测试主动消息，验证开播推送通道"""
        umo = event.unified_msg_origin
        if not umo:
            yield event.plain_result(
                "❌ 无法获取 unified_msg_origin，当前平台可能不支持主动消息"
            )
            return

        self.notifier.reload_config(self._cfg_dict())
        text = (
            "✅ livecheck 主动推送测试成功喵～\n"
            "━━━━━━━━━━━━━━━━\n"
            "本喵已经摸到这个群的传送门啦！\n"
            "之后开播/关播都会从这里喊你们捏。\n"
            f"会话 ID:\n{umo}\n"
            f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            "━━━━━━━━━━━━━━━━\n"
            "测试完毕，可以去 /livecheck add 订阅主播啦！"
        )

        # 先用指令回复确认收到命令
        yield event.plain_result(f"⏳ 正在通过 send_message 向本会话发送测试消息…\n会话: {umo}")

        ok = await self.notifier.send(umo, text, cover_url="", at_all=False)
        if ok:
            # 再回一条指令结果（与主动消息是两条）
            yield event.plain_result(
                "✅ 主动发送接口调用成功。\n"
                "请确认上面是否还有一条「livecheck 主动推送测试」。\n"
                "若只有本条没有测试条，请检查协议端主动消息权限。"
            )
        else:
            yield event.plain_result(
                "❌ 主动发送失败，请查看 AstrBot 日志。\n"
                "常见原因：协议端禁止主动消息、会话 ID 失效、适配器不支持。\n"
                "OneBot/NapCat 一般支持群主动消息。"
            )

    @livecheck_group.command("status")
    async def cmd_status(self, event: AstrMessageEvent):
        """查看监控运行状态"""
        rooms = self.data.all_rooms()
        live_n = sum(1 for r in rooms.values() if r.is_live)
        subs = sum(len(r.subscribers) for r in rooms.values())
        cfg = self._cfg_dict()
        self.notifier.reload_config(cfg)
        interval = cfg.get("check_interval", 60)
        llm_on = self.notifier.llm_enabled()
        provider_id = str(cfg.get("llm_provider_id", "") or "").strip() or "（默认提供商）"
        umo = event.unified_msg_origin or "（无）"
        from .core.config_sync import parse_notify_targets, parse_rooms_from_config

        cfg_rooms = parse_rooms_from_config(cfg)
        cfg_notify = parse_notify_targets(
            str(cfg.get("notify_targets", "") or ""),
            str(cfg.get("umo_prefix", "") or "aiocqhttp:GroupMessage:"),
        )
        yield event.plain_result(
            f"📊 livecheck 结界状态\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📺 监控房间: {len(rooms)}\n"
            f"📝 设置中的直播间: {len(cfg_rooms)}\n"
            f"📬 设置中的推送目标: {len(cfg_notify)}\n"
            f"🔴 正在播: {live_n}\n"
            f"👥 订阅会话: {subs}\n"
            f"⏱️ 侦察间隔: {interval}s\n"
            f"🖼️ 封面: {'开' if cfg.get('enable_cover', True) else '关'}\n"
            f"🤖 LLM文案: {'开 · ' + provider_id if llm_on else '关（二次元模板，可选开）'}\n"
            f"📴 关播通知: {'开' if cfg.get('notify_offline', True) else '关'}\n"
            f"🔁 轮询: {'运行中喵' if self.monitor._running else '已停止'}\n"
            f"📬 当前会话: {umo}\n"
            f"提示: 设置改完可 /livecheck sync 立即生效"
        )

    @livecheck_group.command("sync")
    async def cmd_sync(self, event: AstrMessageEvent):
        """立即从插件设置同步直播间与推送目标"""
        yield event.plain_result("⏳ 正在从插件设置同步直播间喵…")
        stats = await self._sync_config_rooms()
        n_cfg = stats.get("total_config", 0)
        n_notify = stats.get("notify_count", 0)
        tip = ""
        if n_cfg and not n_notify:
            tip = (
                "\n⚠️ 设置里有直播间，但「推送目标」是空的，"
                "开播没地方推哦。请去插件设置填 QQ 群号。"
            )
        yield event.plain_result(
            f"✅ 同步完成\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📋 配置中的直播间: {n_cfg}\n"
            f"➕ 新加入: {stats.get('added', 0)}\n"
            f"🔄 已更新: {stats.get('updated', 0)}\n"
            f"➖ 已移除: {stats.get('removed', 0)}\n"
            f"📬 推送目标数: {n_notify}\n"
            f"📺 当前总监控: {len(self.data.all_rooms())}"
            f"{tip}"
        )

    @livecheck_group.command("poll")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_poll(self, event: AstrMessageEvent):
        """管理员：立即执行一轮轮询"""
        yield event.plain_result("⏳ 正在执行一轮状态检查…")
        await self._sync_config_rooms()
        await self.monitor.poll_once()
        yield event.plain_result("✅ 本轮检查完成")
