"""Query coalescing and per-user new-listing detection."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from typing import Callable

from .mercari_service import MercariItem, MercariSearchService
from .repository import UserRepository, UserRepositoryFactory


class MonitoringService:
    def __init__(self, repositories: UserRepositoryFactory, search_service: MercariSearchService, result_limit: int) -> None:
        self.repositories = repositories
        self.search_service = search_service
        self.result_limit = result_limit

    async def establish_baseline(self, repository: UserRepository, keyword: str) -> int:
        items = await self._search(keyword)
        repository.save_scan(keyword, items, datetime.now(timezone.utc))
        return len(items)

    async def refresh_subscription(self, repository: UserRepository, keyword: str) -> list[MercariItem]:
        """Queue only unseen items without changing the hourly monitor schedule."""
        if repository.get_subscription(keyword) is None:
            raise KeyError(keyword)
        items = await self._search(keyword)
        refreshed_at = datetime.now(timezone.utc)
        return repository.queue_new_items(keyword, items, refreshed_at)

    async def search_now(self, keyword: str) -> list[MercariItem]:
        return await self._search(keyword)

    async def check_all(self, scheduled_slot: datetime) -> None:
        """Queue unseen items for one fixed hourly slot; delivery is handled separately."""
        subscribers: dict[str, list[tuple[UserRepository, str, str]]] = defaultdict(list)
        for repository in self.repositories.all_repositories():
            for subscription in repository.subscriptions():
                if subscription.last_scheduled_slot is None or subscription.last_scheduled_slot < scheduled_slot:
                    subscribers[subscription.keyword].append((repository, subscription.keyword, subscription.unified_msg_origin))

        for keyword, targets in subscribers.items():
            items = await self._search(keyword)
            checked_at = datetime.now(timezone.utc)
            for repository, _, _ in targets:
                repository.save_scheduled_scan(keyword, items, scheduled_slot, checked_at)

    async def _search(self, keyword: str) -> list[MercariItem]:
        return await asyncio.to_thread(self.search_service.search, keyword, self.result_limit)
