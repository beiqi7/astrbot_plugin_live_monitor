"""从插件 WebUI 配置同步监控直播间与推送目标。"""

from __future__ import annotations

import time
from typing import Any, Callable

from astrbot.api import logger

from ..platforms import check_room, extract_room_id, fetch_options, normalize_platform
from ..platforms.base import LiveInfo
from .storage import DataManager, RoomRecord, room_key


DEFAULT_UMO_PREFIX = "default:GroupMessage:"


def extract_umo_prefix(umo: str) -> str:
    """从完整会话 ID 提取前缀，如 default:GroupMessage:xxx → default:GroupMessage:"""
    s = (umo or "").strip()
    if s.count(":") < 2:
        return ""
    parts = s.split(":")
    return f"{parts[0]}:{parts[1]}:"


def extract_group_id_from_umo(umo: str) -> str:
    """从会话 ID 尽量提取群号（兼容 unique_session 的 群号_用户号）。"""
    s = (umo or "").strip()
    if s.count(":") < 2:
        return s.replace("群", "").strip()
    session = s.split(":", 2)[2]
    # unique_session: groupid_userid
    if "_" in session:
        left, right = session.split("_", 1)
        if left.isdigit():
            return left
        if right.isdigit():
            return right
    return session


def parse_notify_targets(
    text: str, umo_prefix: str = DEFAULT_UMO_PREFIX
) -> list[str]:
    """解析推送目标：优先纯群号，也兼容完整会话 ID。"""
    prefix = (umo_prefix or DEFAULT_UMO_PREFIX).strip()
    if prefix and not prefix.endswith(":"):
        prefix = prefix + ":"

    result: list[str] = []
    seen: set[str] = set()
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # 完整 unified_msg_origin: platform:type:id （至少两个冒号）
        if line.count(":") >= 2:
            umo = line
        else:
            # 纯群号（推荐）
            gid = line.replace("群", "").strip()
            umo = f"{prefix}{gid}"
        if umo not in seen:
            seen.add(umo)
            result.append(umo)
    return result


def parse_rooms_from_config(cfg: dict[str, Any]) -> list[dict[str, str]]:
    """合并 template_list 与批量文本中的直播间。"""
    rooms: list[dict[str, str]] = []
    seen: set[str] = set()

    def _add(platform: str, room_id: str, nickname: str = "") -> None:
        pid = normalize_platform(platform) or (platform or "").strip().lower()
        rid = extract_room_id(str(room_id or "").strip(), pid)
        if not pid or not rid:
            return
        if pid not in ("bilibili", "douyu", "huya", "douyin"):
            # normalize 失败时再试一次
            pid2 = normalize_platform(platform)
            if not pid2:
                logger.warning(f"配置中跳过未知平台: {platform} / {rid}")
                return
            pid = pid2
        key = room_key(pid, rid)
        if key in seen:
            # 后写的昵称可覆盖
            for r in rooms:
                if r["platform"] == pid and r["room_id"] == rid:
                    if nickname:
                        r["nickname"] = nickname
                    break
            return
        seen.add(key)
        rooms.append(
            {
                "platform": pid,
                "room_id": rid,
                "nickname": (nickname or "").strip(),
            }
        )

    # 1) WebUI 可视化列表 template_list
    raw_list = cfg.get("monitor_rooms") or []
    if isinstance(raw_list, list):
        for item in raw_list:
            if not isinstance(item, dict):
                continue
            _add(
                str(item.get("platform", "")),
                str(item.get("room_id", "")),
                str(item.get("nickname", "") or ""),
            )

    # 2) 批量文本：平台 房间号 [昵称...]
    text = str(cfg.get("rooms_text", "") or "")
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            logger.warning(f"批量直播间格式无效（需：平台 房间号 [昵称]）: {line}")
            continue
        plat, rid = parts[0], parts[1]
        nick = " ".join(parts[2:]) if len(parts) > 2 else ""
        _add(plat, rid, nick)

    return rooms


async def sync_rooms_from_config(
    data: DataManager,
    cfg: dict[str, Any],
    *,
    mark_primed: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """将配置中的直播间同步进 DataManager。

    - 配置里有的房间：写入/更新，并挂上 notify_targets 订阅
    - 仅来自配置、且已从配置删除的房间：移除
    - 纯指令添加的房间（from_config=False）：不受配置删除影响
    - 新加入且已在直播的房间：记入 live_to_notify，由调用方推送

    Returns:
        dict with added/updated/removed/total_config/notify_count/live_to_notify
    """
    room_defs = parse_rooms_from_config(cfg)
    notify = parse_notify_targets(
        str(cfg.get("notify_targets", "") or ""),
        str(cfg.get("umo_prefix", "") or DEFAULT_UMO_PREFIX),
    )
    opts = fetch_options(cfg)

    config_keys: set[str] = set()
    added = updated = 0
    live_to_notify: list[tuple[RoomRecord, LiveInfo]] = []

    if room_defs and not notify:
        logger.warning(
            "livecheck: 设置里已添加直播间，但「推送目标」为空，"
            "开播将无人接收。请填写完整会话 ID（群里 /livecheck testpush 可查看）。"
        )
    elif notify:
        logger.info(f"livecheck 推送目标: {notify}")

    for item in room_defs:
        pid = item["platform"]
        rid = item["room_id"]
        nick = item.get("nickname") or ""
        key = room_key(pid, rid)
        config_keys.add(key)

        existing = data.get(pid, rid)
        if existing:
            changed = False
            newly_subbed: list[str] = []
            existing.from_config = True
            if nick and existing.nickname != nick:
                existing.nickname = nick
                changed = True
            for umo in notify:
                if umo not in existing.subscribers:
                    existing.subscribers.append(umo)
                    existing.session_settings.setdefault(
                        umo, {"at_all": False, "enabled": True}
                    )
                    newly_subbed.append(umo)
                    changed = True
            if changed:
                data.save()
                updated += 1
            # 新挂上的推送目标 + 房间已在播 → 只给新目标推一次
            if newly_subbed and existing.is_live:
                live_to_notify.append(
                    (
                        existing.with_subscribers(newly_subbed),
                        LiveInfo.from_cache(existing),
                    )
                )
            continue

        # 新房间：拉一次信息
        info = await check_room(pid, rid, **opts)
        if not info.ok and not info.real_name and not info.title:
            logger.warning(
                f"配置直播间查询失败，仍会加入监控 {pid}:{rid} — {info.error}"
            )

        session_settings = {
            umo: {"at_all": False, "enabled": True} for umo in notify
        }
        rec = RoomRecord(
            platform=pid,
            room_id=str(info.room_id or rid),
            nickname=nick,
            real_name=info.real_name or "",
            title=info.title or "",
            cover_url=info.cover_url or "",
            url=info.url or "",
            is_live=bool(info.is_live),
            live_start_ts=time.time() if info.is_live else 0.0,
            last_check_ts=time.time(),
            subscribers=list(notify),
            session_settings=session_settings,
            added_by="config",
            added_time=time.strftime("%Y-%m-%d %H:%M:%S"),
            from_config=True,
        )
        data.add_room(rec)
        if mark_primed:
            mark_primed(room_key(rec.platform, rec.room_id))
        added += 1
        # 新加入且已在播：交给调用方立刻推送（否则 prime 后轮询不会推）
        if rec.is_live and rec.subscribers:
            live_to_notify.append((rec, info))
        logger.info(
            f"从配置添加直播间: {rec.display_name()} ({pid}:{rec.room_id}) "
            f"推送会话={len(notify)} 直播中={rec.is_live}"
        )

    # 删除：曾经是配置房间，但配置里已去掉
    removed = 0
    for key, rec in list(data.all_rooms().items()):
        if not rec.from_config:
            continue
        if key in config_keys:
            continue
        # 若还有非配置来源的订阅需求——from_config 房间整房删除
        # 指令后来又 sub 的会话也会一起删；用户可重新 add
        data.remove_room(rec.platform, rec.room_id)
        removed += 1
        logger.info(f"配置中已移除直播间，删除监控: {key}")

    return {
        "added": added,
        "updated": updated,
        "removed": removed,
        "total_config": len(config_keys),
        "notify_count": len(notify),
        "live_to_notify": live_to_notify,
        "notify_targets": notify,
    }
