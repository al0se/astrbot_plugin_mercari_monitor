import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import types

PACKAGE_NAME = "mercari_plugin_under_test"
package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(Path(__file__).resolve().parents[1])]
sys.modules.setdefault(PACKAGE_NAME, package)

from mercari_plugin_under_test.mercari_service import MercariItem
from mercari_plugin_under_test.monitoring import MonitoringService
from mercari_plugin_under_test.repository import UserRepositoryFactory


NOW = datetime(2026, 8, 29, tzinfo=timezone.utc)


class FakeSearchService:
    def __init__(self):
        self.calls = []

    def search(self, keyword, result_limit):
        self.calls.append((keyword, result_limit))
        return [MercariItem("new", "New camera", 1000, "https://example.com/new", NOW)]


def test_check_all_coalesces_one_keyword_and_keeps_users_separate(tmp_path):
    factory = UserRepositoryFactory(tmp_path / "users")
    first = factory.for_umo("Telegram:PrivateMessage:1")
    second = factory.for_umo("Telegram:PrivateMessage:2")
    first.subscribe("camera", "Telegram:PrivateMessage:1", NOW)
    second.subscribe("camera", "Telegram:PrivateMessage:2", NOW)
    service = FakeSearchService()
    monitor = MonitoringService(factory, service, result_limit=50)

    asyncio.run(monitor.check_all(NOW))

    assert service.calls == [("camera", 50)]
    assert [entry.item.id for entry in first.pending_notifications("camera")] == ["new"]
    assert [entry.item.id for entry in second.pending_notifications("camera")] == ["new"]
    asyncio.run(monitor.check_all(NOW))
    assert service.calls == [("camera", 50)]


def test_next_hourly_slot_runs_even_if_the_previous_check_finished_after_the_hour(tmp_path):
    factory = UserRepositoryFactory(tmp_path / "users")
    repository = factory.for_umo("Telegram:PrivateMessage:1")
    repository.subscribe("camera", "Telegram:PrivateMessage:1", NOW)
    repository.save_scheduled_scan("camera", [], NOW, NOW + timedelta(seconds=1))
    service = FakeSearchService()
    monitor = MonitoringService(factory, service, result_limit=50)

    asyncio.run(monitor.check_all(NOW + timedelta(hours=1)))

    assert service.calls == [("camera", 50)]
    assert [entry.item.id for entry in repository.pending_notifications("camera")] == ["new"]


def test_manual_refresh_returns_only_unseen_items_without_delaying_hourly_check(tmp_path):
    factory = UserRepositoryFactory(tmp_path / "users")
    repository = factory.for_umo("Telegram:PrivateMessage:1")
    repository.subscribe("camera", "Telegram:PrivateMessage:1", NOW)
    repository.save_scan("camera", [MercariItem("baseline", "Baseline", 100, "https://example.com/baseline", NOW)], NOW)
    before = repository.get_subscription("camera")
    monitor = MonitoringService(factory, FakeSearchService(), result_limit=50)

    assert asyncio.run(monitor.refresh_subscription(repository, "camera"))[0].id == "new"
    assert asyncio.run(monitor.refresh_subscription(repository, "camera")) == []
    assert repository.get_subscription("camera").last_check_time == before.last_check_time
    assert [entry.item.id for entry in repository.pending_notifications("camera")] == ["new"]
