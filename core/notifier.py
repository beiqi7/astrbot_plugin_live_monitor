"""通知构建与发送（含封面、可选 LLM 文案、主动推送）。"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from astrbot.api import logger
from astrbot.api.message_components import AtAll, Image, Plain

from ..platforms.base import LiveInfo, PLATFORM_NAMES
from .defaults import (
    DEFAULT_LIVE_TEMPLATE,
    DEFAULT_LLM_PROMPT,
    DEFAULT_LLM_SYSTEM,
    DEFAULT_OFFLINE_TEMPLATE,
)
from .storage import RoomRecord

if TYPE_CHECKING:
    from astrbot.api.star import Context


def format_duration(seconds: float) -> str:
    if seconds <= 0:
        return "未知"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}小时{m}分钟"
    if m > 0:
        return f"{m}分钟"
    return f"{s}秒"


def _truthy(val: Any, default: bool = False) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    s = str(val).strip().lower()
    if s in ("1", "true", "yes", "on", "开启", "开", "y"):
        return True
    if s in ("0", "false", "no", "off", "关闭", "关", "n", ""):
        return False
    return default


class Notifier:
    def __init__(self, context: "Context", config: dict[str, Any] | None = None):
        self.context = context
        self.config = config or {}

    def reload_config(self, config: dict[str, Any]) -> None:
        self.config = config or {}

    def _cfg(self, key: str, default: Any = None) -> Any:
        if hasattr(self.config, "get"):
            return self.config.get(key, default)
        return default

    def llm_enabled(self) -> bool:
        """LLM 文案是否开启（可选功能，默认关闭）。"""
        return _truthy(self._cfg("enable_llm", False), False)

    def render_template(self, template: str, mapping: dict[str, str]) -> str:
        text = template or ""
        for k, v in mapping.items():
            text = text.replace("{" + k + "}", str(v) if v is not None else "")
        return text

    def build_vars(
        self,
        record: RoomRecord,
        info: LiveInfo | None = None,
        duration_seconds: float = 0,
    ) -> dict[str, str]:
        platform = record.platform
        platform_name = PLATFORM_NAMES.get(platform, platform)
        nickname = record.display_name()
        real_name = (info.real_name if info and info.real_name else record.real_name) or nickname
        title = (info.title if info and info.title else record.title) or "（无标题）"
        url = (info.url if info and info.url else record.url) or ""
        cover = (info.cover_url if info and info.cover_url else record.cover_url) or ""
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        return {
            "platform": platform_name,
            "nickname": nickname,
            "real_name": real_name,
            "title": title,
            "room_id": str(record.room_id),
            "url": url,
            "cover": cover,
            "time": now,
            "duration": format_duration(duration_seconds),
        }

    def build_live_text(self, record: RoomRecord, info: LiveInfo | None = None) -> str:
        variables = self.build_vars(record, info)
        tpl = str(self._cfg("live_template") or DEFAULT_LIVE_TEMPLATE)
        return self.render_template(tpl, variables)

    def build_offline_text(
        self, record: RoomRecord, info: LiveInfo | None = None, duration_seconds: float = 0
    ) -> str:
        variables = self.build_vars(record, info, duration_seconds)
        tpl = str(self._cfg("offline_template") or DEFAULT_OFFLINE_TEMPLATE)
        return self.render_template(tpl, variables)

    def _resolve_provider(self, umo: str | None = None):
        """解析 LLM Provider：优先配置的 provider_id，否则用当前默认。"""
        provider_id = str(self._cfg("llm_provider_id", "") or "").strip()
        if provider_id:
            try:
                p = self.context.get_provider_by_id(provider_id)
                if p:
                    return p
                logger.warning(f"未找到 LLM 提供商 id={provider_id}，尝试默认提供商")
            except Exception as e:
                logger.warning(f"get_provider_by_id 失败: {e}")

        try:
            if umo:
                return self.context.get_using_provider(umo=umo)
            return self.context.get_using_provider()
        except TypeError:
            try:
                return self.context.get_using_provider()
            except Exception as e:
                logger.warning(f"get_using_provider 失败: {e}")
        except Exception as e:
            logger.warning(f"获取默认 LLM Provider 失败: {e}")
        return None

    @staticmethod
    def _extract_llm_text(resp: Any) -> str:
        if resp is None:
            return ""
        if isinstance(resp, str):
            text = resp
        else:
            text = (
                getattr(resp, "completion_text", None)
                or getattr(resp, "result", None)
                or ""
            )
            if not text and getattr(resp, "result_chain", None) is not None:
                text = str(resp.result_chain)
        text = (text or "").strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        return text

    async def generate_llm_live_text(
        self,
        record: RoomRecord,
        info: LiveInfo | None = None,
        *,
        umo: str | None = None,
    ) -> str | None:
        """可选：调用 LLM 生成开播文案。未开启或失败返回 None（由模板兜底）。"""
        if not self.llm_enabled():
            return None

        provider = self._resolve_provider(umo=umo)
        if not provider:
            logger.warning("LLM 已开启但无可用 Provider，回退模板文案")
            return None

        variables = self.build_vars(record, info)
        prompt_tpl = str(self._cfg("llm_prompt") or DEFAULT_LLM_PROMPT)
        prompt = self.render_template(prompt_tpl, variables)
        session_id = f"livecheck_{record.platform}_{record.room_id}"

        try:
            resp = None
            last_err: Exception | None = None

            # 兼容不同 AstrBot 版本的调用方式
            call_specs: list[dict[str, Any]] = [
                {
                    "prompt": prompt,
                    "session_id": session_id,
                    "contexts": [],
                    "image_urls": [],
                    "system_prompt": DEFAULT_LLM_SYSTEM,
                },
                {"prompt": prompt, "session_id": session_id},
                {"prompt": prompt},
            ]

            for kwargs in call_specs:
                try:
                    resp = await provider.text_chat(**kwargs)
                    if resp is not None:
                        break
                except TypeError as e:
                    last_err = e
                    continue
                except Exception as e:
                    last_err = e
                    continue

            # 新 API: context.llm_generate
            if resp is None and hasattr(self.context, "llm_generate"):
                try:
                    provider_id = str(self._cfg("llm_provider_id", "") or "").strip()
                    if (
                        not provider_id
                        and umo
                        and hasattr(self.context, "get_current_chat_provider_id")
                    ):
                        try:
                            provider_id = await self.context.get_current_chat_provider_id(
                                umo=umo
                            )
                        except Exception:
                            provider_id = ""
                    gen_kwargs: dict[str, Any] = {"prompt": prompt}
                    if provider_id:
                        gen_kwargs["chat_provider_id"] = provider_id
                    resp = await self.context.llm_generate(**gen_kwargs)
                except Exception as e:
                    last_err = e

            text = self._extract_llm_text(resp)
            if text:
                return text
            if last_err:
                logger.warning(f"LLM 调用失败，回退模板: {last_err}")
            return None
        except Exception as e:
            logger.warning(f"LLM 生成开播文案失败，回退模板: {e}")
            return None

    async def build_live_message(
        self,
        record: RoomRecord,
        info: LiveInfo | None = None,
        *,
        umo: str | None = None,
    ) -> str:
        if self.llm_enabled():
            llm_text = await self.generate_llm_live_text(record, info, umo=umo)
            if llm_text:
                return llm_text
        return self.build_live_text(record, info)

    def _cover_url(self, record: RoomRecord, info: LiveInfo | None) -> str:
        if not _truthy(self._cfg("enable_cover", True), True):
            return ""
        if info and info.cover_url:
            return info.cover_url
        return record.cover_url or ""

    def _make_image(self, cover_url: str) -> Any | None:
        if not cover_url:
            return None
        try:
            if hasattr(Image, "fromURL"):
                return Image.fromURL(cover_url)
            if hasattr(Image, "from_url"):
                return Image.from_url(cover_url)  # type: ignore[attr-defined]
            return Image(file=cover_url)
        except Exception as e:
            logger.warning(f"构建封面图组件失败: {e}")
            return None

    def build_chain(
        self,
        text: str,
        *,
        cover_url: str = "",
        at_all: bool = False,
    ) -> list[Any]:
        chain: list[Any] = []
        if at_all:
            try:
                chain.append(AtAll())
                chain.append(Plain("\n"))
            except Exception:
                pass
        img = self._make_image(cover_url)
        if img is not None:
            chain.append(img)
            chain.append(Plain("\n" + text if text else ""))
        else:
            chain.append(Plain(text or ""))
        return chain

    def _to_message_chain(self, chain: list[Any]):
        """构造 AstrBot 可发送的 MessageChain（多版本兼容）。"""
        try:
            from astrbot.api.event import MessageChain

            try:
                return MessageChain(chain)
            except TypeError:
                mc = MessageChain()
                for seg in chain:
                    name = seg.__class__.__name__
                    if name == "Plain":
                        t = getattr(seg, "text", None) or ""
                        if hasattr(mc, "message"):
                            mc = mc.message(t)
                        elif hasattr(mc, "chain"):
                            mc.chain.append(seg)
                    elif name == "Image":
                        url = getattr(seg, "url", None) or getattr(seg, "file", None) or ""
                        url = str(url)
                        if url.startswith("http") and hasattr(mc, "url_image"):
                            mc = mc.url_image(url)
                        elif url and hasattr(mc, "file_image"):
                            mc = mc.file_image(url)
                        elif hasattr(mc, "chain"):
                            mc.chain.append(seg)
                    elif name == "AtAll":
                        if hasattr(mc, "at_all"):
                            mc = mc.at_all()
                        elif hasattr(mc, "chain"):
                            mc.chain.append(seg)
                    elif hasattr(mc, "chain"):
                        mc.chain.append(seg)
                return mc
        except Exception as e:
            logger.debug(f"api.event.MessageChain 不可用: {e}")

        try:
            from astrbot.core.message.message_event_result import MessageChain as MC2

            return MC2(chain)
        except Exception as e:
            logger.debug(f"core MessageChain 不可用: {e}")

        try:
            from astrbot.api.event import MessageEventResult

            result = MessageEventResult()
            for seg in chain:
                result.chain.append(seg)
            return result
        except Exception as e:
            logger.debug(f"MessageEventResult 不可用: {e}")

        return chain

    async def send(
        self,
        umo: str,
        text: str,
        *,
        cover_url: str = "",
        at_all: bool = False,
    ) -> bool:
        """主动发送消息到指定会话（unified_msg_origin）。"""
        if not umo:
            logger.error("主动推送失败: unified_msg_origin 为空")
            return False

        chain = self.build_chain(text, cover_url=cover_url, at_all=at_all)
        message = self._to_message_chain(chain)

        errors: list[str] = []
        for payload in (message, chain):
            try:
                await self.context.send_message(umo, payload)
                logger.info(f"直播通知已主动发送 -> {umo}")
                return True
            except Exception as e:
                errors.append(str(e))

        try:
            await self.context.send_message(unified_msg_origin=umo, message=message)
            logger.info(f"直播通知已主动发送(kw) -> {umo}")
            return True
        except Exception as e:
            errors.append(str(e))

        logger.error(f"直播通知发送失败 ({umo}): {' | '.join(errors)}")
        return False

    async def notify_live(
        self,
        record: RoomRecord,
        info: LiveInfo | None,
        *,
        global_at_all: bool = False,
    ) -> None:
        cover = self._cover_url(record, info)
        first_umo = next(iter(record.subscribers), None)
        text = await self.build_live_message(record, info, umo=first_umo)

        ok, fail = 0, 0
        for umo in list(record.subscribers):
            settings = record.session_settings.get(umo) or {}
            if settings.get("enabled", True) is False:
                continue
            at_all = bool(settings.get("at_all", global_at_all))
            if await self.send(umo, text, cover_url=cover, at_all=at_all):
                ok += 1
            else:
                fail += 1
        logger.info(
            f"开播通知完成 {record.platform}:{record.room_id} 成功={ok} 失败={fail}"
        )

    async def notify_offline(
        self,
        record: RoomRecord,
        info: LiveInfo | None,
        duration_seconds: float,
    ) -> None:
        if not _truthy(self._cfg("notify_offline", True), True):
            return
        text = self.build_offline_text(record, info, duration_seconds)
        cover = self._cover_url(record, info)
        ok, fail = 0, 0
        for umo in list(record.subscribers):
            settings = record.session_settings.get(umo) or {}
            if settings.get("enabled", True) is False:
                continue
            if await self.send(umo, text, cover_url=cover, at_all=False):
                ok += 1
            else:
                fail += 1
        logger.info(
            f"关播通知完成 {record.platform}:{record.room_id} 成功={ok} 失败={fail}"
        )
