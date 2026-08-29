"""AstrBot entry point for private Mercari new-listing subscriptions."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from mercari_service import MercariItem, MercariSearchService, MercariUpstreamError
from monitoring import MonitoringService
from repository import UserRepository, UserRepositoryFactory


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

    @filter.on_astrbot_loaded()
    async def on_astrbot_loaded(self):
        """Start the worker only after AstrBot's event loop and adapters are ready."""
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())

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
            yield event.plain_result(_new_items_text(keyword, new_items, int(self.config["max_push_items"])))
        elif action in {"订阅列表", "列表"}:
            subscriptions = repository.subscriptions()
            yield event.plain_result(_subscription_list_text(subscriptions))
        elif action in {"取消订阅", "取消"}:
            if not keyword:
                yield event.plain_result("用法：/mercari 取消订阅 <关键词>")
                return
            yield event.plain_result(f"已取消订阅「{keyword}」。" if repository.unsubscribe(keyword) else f"尚未订阅「{keyword}」。")
        else:
            yield event.plain_result(_help_text())

    async def _scheduler_loop(self) -> None:
        if bool(self.config["check_on_startup"]):
            await self._run_scheduled_check()
        interval = timedelta(minutes=int(self.config["check_interval_minutes"]))
        while True:
            await asyncio.sleep(interval.total_seconds())
            await self._run_scheduled_check()

    async def _run_scheduled_check(self) -> None:
        try:
            due_before = datetime.now(timezone.utc) - timedelta(minutes=int(self.config["check_interval_minutes"]))
            results = await self.monitor.check_all(due_before)
            for (umo, keyword), items in results.items():
                if not items:
                    continue
                await self.context.send_message(
                    umo,
                    MessageChain().message(_new_items_text(keyword, items, int(self.config["max_push_items"]))),
                )
        except Exception:
            logger.exception("Mercari scheduled check failed")

    async def terminate(self) -> None:
        if self._scheduler_task is None:
            return
        self._scheduler_task.cancel()
        try:
            await self._scheduler_task
        except asyncio.CancelledError:
            pass

    def _validate_config(self) -> None:
        if not 10 <= int(self.config["search_result_limit"]) <= 100:
            raise ValueError("search_result_limit must be between 10 and 100")
        if int(self.config["check_interval_minutes"]) < 1:
            raise ValueError("check_interval_minutes must be at least 1")
        if int(self.config["max_push_items"]) < 1:
            raise ValueError("max_push_items must be at least 1")


def _help_text() -> str:
    return "\n".join([
        "Mercari 新品监控（仅私聊）",
        "/mercari 搜索 <关键词>：临时实时搜索",
        "/mercari 订阅 <关键词>：建立基线并每小时推送新品",
        "/mercari 刷新 <关键词>：立即检查本订阅的新商品",
        "/mercari 订阅列表：查看你的订阅",
        "/mercari 取消订阅 <关键词>：停止推送",
    ])


def _search_text(keyword: str, items: list[MercariItem]) -> str:
    if not items:
        return f"没有找到「{keyword}」的在售商品。"
    return _items_text(f"🔎 Mercari「{keyword}」搜索结果", items, len(items))


def _new_items_text(keyword: str, items: list[MercariItem], limit: int) -> str:
    if not items:
        return f"「{keyword}」本次没有发现新品。"
    return _items_text(f"🔔 Mercari「{keyword}」发现 {len(items)} 个新品", items, limit)


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
