import asyncio
from datetime import datetime, timedelta, timezone

from mercari_service import MercariItem
from monitoring import MonitoringService
from repository import UserRepositoryFactory


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

    result = asyncio.run(monitor.check_all(NOW + timedelta(minutes=1)))

    assert service.calls == [("camera", 50)]
    assert set(result) == {
        ("Telegram:PrivateMessage:1", "camera"),
        ("Telegram:PrivateMessage:2", "camera"),
    }
    assert asyncio.run(monitor.check_all(NOW + timedelta(minutes=1))) == {}
