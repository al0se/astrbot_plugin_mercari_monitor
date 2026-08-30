"""Newest-first Mercari Japan listing search without the legacy mercari package."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import uuid
from typing import Any

import requests

from .patches import post_json

SEARCH_URL = "https://api.mercari.jp/v2/entities:search"


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
    """Fetch up to 100 newest on-sale listings through Mercari's web API."""

    def search(self, keyword: str, result_limit: int) -> list[MercariItem]:
        try:
            response = post_json(SEARCH_URL, {
                "userId": f"MERCARI_BOT_{uuid.uuid4()}", "pageSize": min(result_limit, 100),
                "pageToken": "v1:0", "searchSessionId": f"MERCARI_BOT_{uuid.uuid4()}",
                "indexRouting": "INDEX_ROUTING_UNSPECIFIED",
                "searchCondition": {
                    "keyword": keyword, "sort": "SORT_CREATED_TIME", "order": "ORDER_DESC",
                    "status": ["STATUS_ON_SALE"], "excludeKeyword": "",
                },
                "withAuction": True,
                "defaultDatasets": ["DATASET_TYPE_MERCARI", "DATASET_TYPE_BEYOND"],
            })
        except requests.RequestException as error:
            raise MercariUpstreamError("Mercari search request failed") from error
        return [_item_from_response(item) for item in response.get("items", []) if item.get("id")]


def _item_from_response(item: dict[str, Any]) -> MercariItem:
    thumbnails = item.get("thumbnails") or []
    return MercariItem(
        id=str(item["id"]), title=str(item.get("name", "")), price=int(item.get("price", 0) or 0),
        url=_item_url(item), created_time=_as_datetime(item.get("created")),
        image_url=str(thumbnails[0]) if thumbnails else None,
    )


def _item_url(item: dict[str, Any]) -> str:
    """Use the Shops route for Mercari Shops (BEYOND) listings."""
    path = "shops/product" if item.get("itemType") == "ITEM_TYPE_BEYOND" or item.get("shop") else "item"
    return f"https://jp.mercari.com/{path}/{item['id']}"
