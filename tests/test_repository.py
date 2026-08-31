from datetime import datetime, timezone
from pathlib import Path
import sys
import types

PACKAGE_NAME = "mercari_plugin_under_test"
package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(Path(__file__).resolve().parents[1])]
sys.modules.setdefault(PACKAGE_NAME, package)

from mercari_plugin_under_test.mercari_service import MercariItem
from mercari_plugin_under_test.repository import UserRepositoryFactory


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


def test_pending_items_are_not_seen_until_delivery_is_confirmed(tmp_path):
    repository = UserRepositoryFactory(tmp_path / "users").for_umo("Telegram:PrivateMessage:1")
    repository.subscribe("camera", "Telegram:PrivateMessage:1", NOW)
    repository.save_scan("camera", [item("baseline")], NOW)
    before = repository.get_subscription("camera")

    assert repository.queue_new_items("camera", [item("manual")], NOW) == [item("manual")]
    assert repository.queue_new_items("camera", [item("manual")], NOW) == []
    assert [notification.item for notification in repository.pending_notifications("camera")] == [item("manual")]
    assert repository.confirm_notifications_sent("camera", ["manual"], NOW) == 1

    after = repository.get_subscription("camera")
    assert after is not None and before is not None
    assert after.last_check_time == before.last_check_time
    assert repository.pending_notifications("camera") == []
    assert repository.queue_new_items("camera", [item("manual")], NOW) == []
