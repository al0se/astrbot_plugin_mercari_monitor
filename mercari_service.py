"""Mercari query adapter used by the AstrBot plugin."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import islice
from typing import Any

import requests

import patches  # noqa: F401  # Install Mercari's DPoP/retry compatibility patch first.
from mercari import MercariOrder, MercariSearchStatus, MercariSort, search


class MercariUpstreamError(RuntimeError):
    """Mercari did not complete a search successfully."""


@dataclass(frozen=True)
class MercariItem:
    id: str
    title: str
    price: int
    url: str
    created_time: datetime
    image_url: str | None = None


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc)
    if isinstance(value, str):
        try:
            return datetime.fromtimestamp(float(value), timezone.utc)
        except (OSError, OverflowError, ValueError):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                pass
    return datetime.fromtimestamp(0, timezone.utc)


class MercariSearchService:
    """Fetch newest on-sale Mercari Japan listings."""

    def search(self, keyword: str, result_limit: int) -> list[MercariItem]:
        try:
            raw_items = search(
                keyword,
                sort=MercariSort.SORT_CREATED_TIME,
                order=MercariOrder.ORDER_DESC,
                status=MercariSearchStatus.ON_SALE,
            )
            items: list[MercariItem] = []
            for raw_item in islice(raw_items, result_limit):
                item_id = str(getattr(raw_item, "id", "")).strip()
                if not item_id:
                    continue
                items.append(
                    MercariItem(
                        id=item_id,
                        title=str(getattr(raw_item, "productName", "")),
                        price=int(getattr(raw_item, "price", 0) or 0),
                        url=str(getattr(raw_item, "productURL", "")),
                        created_time=_as_datetime(getattr(raw_item, "created", None)),
                        image_url=getattr(raw_item, "imageURL", None),
                    )
                )
            return items
        except requests.RequestException as error:
            raise MercariUpstreamError("Mercari search request failed") from error
