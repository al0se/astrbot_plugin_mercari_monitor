"""AstrBot entry point for private Mercari new-listing subscriptions."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .mercari_service import MercariItem, MercariSearchService, MercariUpstreamError
from .monitoring import MonitoringService
from .repository import UserRepository, UserRepositoryFactory

BEIJING_TIMEZONE = ZoneInfo("Asia/Shanghai")


class MercariMonitor(Star):
    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.config = config
        self._validate_config()
        data_dir = Path(get_astrbot_data_path()) / "plugin_data" / self.name / "users"
        self.repositories = UserRepositoryFactory(data_dir)
        self.monitor = MonitoringService(
            self.repositories,
            MercariSearchService(),
            int(self.config["search_result_limit"]),
        )
        self._scheduler_task: asyncio.Task | None = None
        self._next_scheduled_at: datetime | None = None

    @filter.on_astrbot_loaded()
    async def on_astrbot_loaded(self):
        """Start the worker only after AstrBot's event loop and adapters are ready."""
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())
        self._scheduler_task.add_done_callback(self._report_scheduler_exit)

    @filter.command("mercari")
    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    async def mercari(self, event: AstrMessageEvent):
        """Mercari 新品监控：搜索、订阅、刷新、订阅列表、取消订阅。"""
        if event.get_group_id():
            yield event.plain_result("此插件目前只支持私聊使用。")
            return
        parts = event.message_str.strip().split(maxsplit=2)
        if parts and parts[0].lstrip("/").lower() == "mercari":
            parts = parts[1:]
        action = parts[0] if parts else ""
        keyword = parts[1].strip() if len(parts) > 1 else ""
        if action in {"帮助", "help", ""}:
            yield event.plain_result(_help_text())
            return
        if action == "搜索":
            if not keyword:
                yield event.plain_result("用法：/mercari 搜索 <关键词>")
                return
            try:
                items = await self.monitor.search_now(keyword)
            except MercariUpstreamError:
                yield event.plain_result("Mercari 查询暂时失败，请稍后重试。")
                return
            yield event.plain_result(_search_text(keyword, items))
            return

        repository = self.repositories.for_umo(event.unified_msg_origin)
        if action == "订阅":
            if not keyword:
                yield event.plain_result("用法：/mercari 订阅 <关键词>")
                return
            if not repository.subscribe(keyword, event.unified_msg_origin, datetime.now(timezone.utc)):
                yield event.plain_result(f"已订阅「{keyword}」。")
                return
            try:
                baseline_count = await self.monitor.establish_baseline(repository, keyword)
            except MercariUpstreamError:
                repository.unsubscribe(keyword)
                yield event.plain_result("初始查询失败，未创建订阅；请稍后重试。")
                return
            yield event.plain_result(f"已订阅「{keyword}」，已记录 {baseline_count} 个当前商品作为基线。后续每小时只推送新品。")
        elif action == "刷新":
            if not keyword:
                yield event.plain_result("用法：/mercari 刷新 <关键词>")
                return
            try:
                new_items = await self.monitor.refresh_subscription(repository, keyword)
            except KeyError:
                yield event.plain_result(f"尚未订阅「{keyword}」。")
                return
            except MercariUpstreamError:
                yield event.plain_result("Mercari 查询暂时失败，请稍后重试。")
                return
            yield event.plain_result(_refresh_text(keyword, new_items))
        elif action in {"订阅列表", "列表"}:
            subscriptions = repository.subscriptions()
            yield event.plain_result(_subscription_list_text(subscriptions))
        elif action == "状态":
            yield event.plain_result(_status_text(self._next_scheduled_at, repository.subscriptions()))
        elif action in {"取消订阅", "取消"}:
            if not keyword:
                yield event.plain_result("用法：/mercari 取消订阅 <关键词>")
                return
            yield event.plain_result(f"已取消订阅「{keyword}」。" if repository.unsubscribe(keyword) else f"尚未订阅「{keyword}」。")
        else:
            yield event.plain_result(_help_text())

    async def _scheduler_loop(self) -> None:
        logger.info("Mercari hourly scheduler started")
        if bool(self.config["check_on_startup"]):
            await self._run_scheduled_check(_current_beijing_hour())
        while True:
            scheduled_slot = _next_beijing_hour()
            self._next_scheduled_at = scheduled_slot
            logger.info("Mercari next scheduled check: %s", scheduled_slot.isoformat())
            await _sleep_until(scheduled_slot)
            while True:
                try:
                    await self._run_scheduled_check(scheduled_slot)
                    break
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Mercari scheduled slot failed; retrying in 60 seconds")
                    await asyncio.sleep(60)

    async def _run_scheduled_check(self, scheduled_slot: datetime) -> None:
        logger.info("Mercari scheduled check started for slot %s", scheduled_slot.isoformat())
        results = await self.monitor.check_all(scheduled_slot)
        for (umo, keyword), items in results.items():
            await self.context.send_message(
                umo,
                MessageChain().message(_new_items_text(keyword, items, int(self.config["max_push_items"]))),
            )
        logger.info("Mercari scheduled slot completed; pushed to %d keyword subscriptions", len(results))

    async def terminate(self) -> None:
        if self._scheduler_task is None:
            return
        self._scheduler_task.cancel()
        try:
            await self._scheduler_task
        except asyncio.CancelledError:
            pass

    def _report_scheduler_exit(self, task: asyncio.Task) -> None:
        if task.cancelled():
            logger.info("Mercari hourly scheduler stopped")
            return
        error = task.exception()
        if error is not None:
            logger.error("Mercari hourly scheduler exited unexpectedly: %s", error)

    def _validate_config(self) -> None:
        if not 10 <= int(self.config["search_result_limit"]) <= 100:
            raise ValueError("search_result_limit must be between 10 and 100")
        if int(self.config["max_push_items"]) < 0:
            raise ValueError("max_push_items must be zero or greater")


def _seconds_until_next_beijing_hour(now: datetime | None = None) -> float:
    """Return the delay until the next full hour in the Asia/Shanghai timezone."""
    current = now.astimezone(BEIJING_TIMEZONE) if now else datetime.now(BEIJING_TIMEZONE)
    return max((_next_beijing_hour(current) - current).total_seconds(), 0.0)


def _next_beijing_hour(now: datetime | None = None) -> datetime:
    current = now.astimezone(BEIJING_TIMEZONE) if now else datetime.now(BEIJING_TIMEZONE)
    return current.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)


def _current_beijing_hour(now: datetime | None = None) -> datetime:
    current = now.astimezone(BEIJING_TIMEZONE) if now else datetime.now(BEIJING_TIMEZONE)
    return current.replace(minute=0, second=0, microsecond=0)


async def _sleep_until(scheduled_slot: datetime) -> None:
    """Avoid treating a slightly early wake-up as a different hourly batch."""
    while True:
        remaining = (scheduled_slot - datetime.now(BEIJING_TIMEZONE)).total_seconds()
        if remaining <= 0:
            return
        await asyncio.sleep(remaining)


def _help_text() -> str:
    return "\n".join([
        "Mercari 新品监控（仅私聊）",
        "/mercari 搜索 <关键词>：临时实时搜索",
        "/mercari 订阅 <关键词>：建立基线并每小时推送新品",
        "/mercari 刷新 <关键词>：立即检查本订阅的新商品",
        "/mercari 订阅列表：查看你的订阅",
        "/mercari 状态：查看下次整点检查时间",
        "/mercari 取消订阅 <关键词>：停止推送",
    ])


def _search_text(keyword: str, items: list[MercariItem]) -> str:
    if not items:
        return f"没有找到「{keyword}」的在售商品。"
    return _items_text(f"🔎 Mercari「{keyword}」搜索结果", items, len(items))


def _new_items_text(keyword: str, items: list[MercariItem], max_push_items: int) -> str:
    if not items:
        return f"「{keyword}」本次没有发现新品。"
    limit = len(items) if max_push_items == 0 else max_push_items
    return _items_text(f"🔔 Mercari「{keyword}」发现 {len(items)} 个新品", items, limit)


def _refresh_text(keyword: str, items: list[MercariItem]) -> str:
    if not items:
        return f"「{keyword}」刷新完成，没有发现新品。"
    return _items_text(
        f"🔄 Mercari「{keyword}」发现 {len(items)} 个新品",
        items,
        len(items),
    )


def _items_text(title: str, items: list[MercariItem], limit: int) -> str:
    lines = [title]
    for index, item in enumerate(items[:limit], start=1):
        lines.extend((f"{index}. {item.title} · ¥{item.price:,}", item.url))
    if len(items) > limit:
        lines.append(f"另有 {len(items) - limit} 个新品已记录，未展开推送。")
    return "\n".join(lines)


def _subscription_list_text(subscriptions) -> str:
    if not subscriptions:
        return "你还没有订阅任何关键词。"
    return "你的 Mercari 订阅：\n" + "\n".join(f"- {item.keyword}" for item in subscriptions)


def _status_text(next_scheduled_at: datetime | None, subscriptions) -> str:
    next_time = "尚未启动" if next_scheduled_at is None else next_scheduled_at.strftime("%Y-%m-%d %H:%M:%S 北京时间")
    lines = [f"定时器下次检查：{next_time}", f"当前启用订阅：{len(subscriptions)}"]
    lines.extend(f"- {subscription.keyword}" for subscription in subscriptions)
    return "\n".join(lines)
