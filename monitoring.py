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
        """Save a manual snapshot without changing the hourly monitor baseline."""
        if repository.get_subscription(keyword) is None:
            raise KeyError(keyword)
        items = await self._search(keyword)
        repository.save_manual_refresh(keyword, items, datetime.now(timezone.utc))
        return items

    async def search_now(self, keyword: str) -> list[MercariItem]:
        return await self._search(keyword)

    async def check_all(self, due_before: datetime) -> dict[tuple[str, str], list[MercariItem]]:
        """Return new items keyed by (recipient UMO, keyword), querying each keyword once."""
        subscribers: dict[str, list[tuple[UserRepository, str, str]]] = defaultdict(list)
        for repository in self.repositories.all_repositories():
            for subscription in repository.subscriptions():
                if subscription.last_check_time is None or subscription.last_check_time <= due_before:
                    subscribers[subscription.keyword].append((repository, subscription.keyword, subscription.unified_msg_origin))

        found: dict[tuple[str, str], list[MercariItem]] = defaultdict(list)
        for keyword, targets in subscribers.items():
            items = await self._search(keyword)
            checked_at = datetime.now(timezone.utc)
            for repository, _, umo in targets:
                new_items = repository.save_scan(keyword, items, checked_at)
                if new_items:
                    found[(umo, keyword)].extend(new_items)
        return found

    async def _search(self, keyword: str) -> list[MercariItem]:
        return await asyncio.to_thread(self.search_service.search, keyword, self.result_limit)
