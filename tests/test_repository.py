from datetime import datetime, timezone

from mercari_service import MercariItem
from repository import UserRepositoryFactory


NOW = datetime(2026, 8, 29, tzinfo=timezone.utc)


def item(item_id="one"):
    return MercariItem(item_id, "Camera", 1000, f"https://example.com/{item_id}", NOW)


def test_each_umo_receives_an_independent_database(tmp_path):
    factory = UserRepositoryFactory(tmp_path / "users")
    first = factory.for_umo("Telegram:PrivateMessage:1")
    second = factory.for_umo("Telegram:PrivateMessage:2")
    assert first.path != second.path
    first.subscribe("camera", "Telegram:PrivateMessage:1", NOW)
    assert first.subscriptions()[0].keyword == "camera"
    assert second.subscriptions() == []


def test_save_scan_only_returns_new_items(tmp_path):
    repository = UserRepositoryFactory(tmp_path / "users").for_umo("Telegram:PrivateMessage:1")
    repository.subscribe("camera", "Telegram:PrivateMessage:1", NOW)
    assert repository.save_scan("camera", [item()], NOW) == [item()]
    assert repository.save_scan("camera", [item()], NOW) == []
    assert repository.save_scan("camera", [item("two")], NOW) == [item("two")]
