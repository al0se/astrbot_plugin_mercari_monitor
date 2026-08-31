"""One SQLite database per private AstrBot conversation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import hashlib
import sqlite3

from .mercari_service import MercariItem


@dataclass(frozen=True)
class Subscription:
    keyword: str
    unified_msg_origin: str
    created_time: datetime
    last_check_time: datetime | None
    last_scheduled_slot: datetime | None


@dataclass(frozen=True)
class PendingNotification:
    keyword: str
    item: MercariItem
    queued_time: datetime


class UserRepository:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS subscriptions (
                    keyword TEXT PRIMARY KEY,
                    unified_msg_origin TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_time TEXT NOT NULL,
                    last_check_time TEXT,
                    last_scheduled_slot TEXT
                );
                CREATE TABLE IF NOT EXISTS seen_items (
                    item_id TEXT NOT NULL,
                    keyword TEXT NOT NULL,
                    title TEXT NOT NULL,
                    price INTEGER NOT NULL,
                    url TEXT NOT NULL,
                    image_url TEXT,
                    created_time TEXT NOT NULL,
                    first_seen_time TEXT NOT NULL,
                    PRIMARY KEY (item_id, keyword)
                );
                CREATE INDEX IF NOT EXISTS seen_items_keyword_idx
                    ON seen_items(keyword);
                CREATE TABLE IF NOT EXISTS pending_notifications (
                    item_id TEXT NOT NULL,
                    keyword TEXT NOT NULL,
                    title TEXT NOT NULL,
                    price INTEGER NOT NULL,
                    url TEXT NOT NULL,
                    image_url TEXT,
                    created_time TEXT NOT NULL,
                    queued_time TEXT NOT NULL,
                    PRIMARY KEY (item_id, keyword)
                );
                CREATE INDEX IF NOT EXISTS pending_notifications_keyword_idx
                    ON pending_notifications(keyword, queued_time);
                CREATE TABLE IF NOT EXISTS manual_refresh_items (
                    item_id TEXT NOT NULL,
                    keyword TEXT NOT NULL,
                    title TEXT NOT NULL,
                    price INTEGER NOT NULL,
                    url TEXT NOT NULL,
                    image_url TEXT,
                    created_time TEXT NOT NULL,
                    last_refreshed_time TEXT NOT NULL,
                    PRIMARY KEY (item_id, keyword)
                );
                """
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(subscriptions)")}
            if "last_scheduled_slot" not in columns:
                connection.execute("ALTER TABLE subscriptions ADD COLUMN last_scheduled_slot TEXT")

    def subscribe(self, keyword: str, umo: str, now: datetime) -> bool:
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT enabled FROM subscriptions WHERE keyword = ?", (keyword,)
            ).fetchone()
            if existing is not None and existing["enabled"]:
                return False
            if existing is None:
                connection.execute(
                    "INSERT INTO subscriptions(keyword, unified_msg_origin, created_time) VALUES (?, ?, ?)",
                    (keyword, umo, now.isoformat()),
                )
            else:
                connection.execute(
                    """UPDATE subscriptions SET enabled = 1, unified_msg_origin = ?,
                    last_check_time = NULL, last_scheduled_slot = NULL WHERE keyword = ?""",
                    (umo, keyword),
                )
        return True

    def unsubscribe(self, keyword: str) -> bool:
        with self._connect() as connection:
            changed = connection.execute(
                "UPDATE subscriptions SET enabled = 0 WHERE keyword = ? AND enabled = 1", (keyword,)
            ).rowcount
            if changed:
                connection.execute("DELETE FROM pending_notifications WHERE keyword = ?", (keyword,))
        return changed == 1

    def subscriptions(self) -> list[Subscription]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT keyword, unified_msg_origin, created_time, last_check_time, last_scheduled_slot
                FROM subscriptions WHERE enabled = 1 ORDER BY created_time"""
            ).fetchall()
        return [_subscription_from_row(row) for row in rows]

    def get_subscription(self, keyword: str) -> Subscription | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT keyword, unified_msg_origin, created_time, last_check_time, last_scheduled_slot
                FROM subscriptions WHERE keyword = ? AND enabled = 1""",
                (keyword,),
            ).fetchone()
        return None if row is None else _subscription_from_row(row)

    def save_scan(self, keyword: str, items: list[MercariItem], checked_at: datetime) -> list[MercariItem]:
        """Save scan results and return items unseen by this user for this keyword."""
        new_items = self.mark_seen_items(keyword, items, checked_at)
        with self._connect() as connection:
            connection.execute(
                "UPDATE subscriptions SET last_check_time = ? WHERE keyword = ?",
                (checked_at.isoformat(), keyword),
            )
        return new_items

    def save_scheduled_scan(
        self, keyword: str, items: list[MercariItem], scheduled_slot: datetime, checked_at: datetime
    ) -> list[MercariItem]:
        """Queue unseen items for one fixed hourly slot without marking them notified."""
        with self._connect() as connection:
            new_items = self._queue_new_items(connection, keyword, items, checked_at)
            connection.execute(
                """UPDATE subscriptions SET last_check_time = ?, last_scheduled_slot = ?
                WHERE keyword = ?""",
                (checked_at.isoformat(), scheduled_slot.isoformat(), keyword),
            )
        return new_items

    def queue_new_items(self, keyword: str, items: list[MercariItem], queued_at: datetime) -> list[MercariItem]:
        """Persist unseen items as pending; only delivery confirmation moves them to seen_items."""
        with self._connect() as connection:
            return self._queue_new_items(connection, keyword, items, queued_at)

    def _queue_new_items(
        self, connection: sqlite3.Connection, keyword: str, items: list[MercariItem], queued_at: datetime
    ) -> list[MercariItem]:
        known_ids = {
            row["item_id"]
            for row in connection.execute(
                """SELECT item_id FROM seen_items WHERE keyword = ?
                UNION SELECT item_id FROM pending_notifications WHERE keyword = ?""",
                (keyword, keyword),
            )
        }
        new_items = [item for item in items if item.id not in known_ids]
        for item in new_items:
            connection.execute(
                """INSERT OR IGNORE INTO pending_notifications
                (item_id, keyword, title, price, url, image_url, created_time, queued_time)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (item.id, keyword, item.title, item.price, item.url, item.image_url,
                 item.created_time.isoformat(), queued_at.isoformat()),
            )
        return new_items

    def pending_notifications(self, keyword: str, limit: int = 0) -> list[PendingNotification]:
        query = """SELECT item_id, keyword, title, price, url, image_url, created_time, queued_time
        FROM pending_notifications WHERE keyword = ? ORDER BY queued_time, item_id"""
        params: tuple[object, ...] = (keyword,)
        if limit > 0:
            query += " LIMIT ?"
            params = (keyword, limit)
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [_pending_notification_from_row(row) for row in rows]

    def pending_count(self, keyword: str) -> int:
        with self._connect() as connection:
            return connection.execute(
                "SELECT COUNT(*) FROM pending_notifications WHERE keyword = ?", (keyword,)
            ).fetchone()[0]

    def confirm_notifications_sent(self, keyword: str, item_ids: list[str], sent_at: datetime) -> int:
        """Move only successfully accepted outgoing items from the queue to the notified set."""
        if not item_ids:
            return 0
        placeholders = ", ".join("?" for _ in item_ids)
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT item_id, title, price, url, image_url, created_time
                FROM pending_notifications WHERE keyword = ? AND item_id IN ({placeholders})""",
                (keyword, *item_ids),
            ).fetchall()
            for row in rows:
                connection.execute(
                    """INSERT OR IGNORE INTO seen_items
                    (item_id, keyword, title, price, url, image_url, created_time, first_seen_time)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (row["item_id"], keyword, row["title"], row["price"], row["url"], row["image_url"],
                     row["created_time"], sent_at.isoformat()),
                )
            connection.execute(
                f"DELETE FROM pending_notifications WHERE keyword = ? AND item_id IN ({placeholders})",
                (keyword, *item_ids),
            )
        return len(rows)

    def mark_seen_items(self, keyword: str, items: list[MercariItem], seen_at: datetime) -> list[MercariItem]:
        """Add items to the monitor's seen set without changing its check time."""
        with self._connect() as connection:
            existing_ids = {
                row["item_id"]
                for row in connection.execute(
                    "SELECT item_id FROM seen_items WHERE keyword = ?", (keyword,)
                )
            }
            new_items = [item for item in items if item.id not in existing_ids]
            for item in new_items:
                connection.execute(
                    """INSERT OR IGNORE INTO seen_items
                    (item_id, keyword, title, price, url, image_url, created_time, first_seen_time)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item.id, keyword, item.title, item.price, item.url, item.image_url,
                     item.created_time.isoformat(), seen_at.isoformat()),
                )
        return new_items

    def save_manual_refresh(self, keyword: str, items: list[MercariItem], refreshed_at: datetime) -> None:
        """Persist a user's manual result snapshot without changing monitoring state."""
        with self._connect() as connection:
            for item in items:
                connection.execute(
                    """INSERT INTO manual_refresh_items
                    (item_id, keyword, title, price, url, image_url, created_time, last_refreshed_time)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(item_id, keyword) DO UPDATE SET
                        title=excluded.title, price=excluded.price, url=excluded.url,
                        image_url=excluded.image_url, created_time=excluded.created_time,
                        last_refreshed_time=excluded.last_refreshed_time""",
                    (item.id, keyword, item.title, item.price, item.url, item.image_url,
                     item.created_time.isoformat(), refreshed_at.isoformat()),
                )


class UserRepositoryFactory:
    """Maps UMO values to opaque per-user database filenames."""

    def __init__(self, users_dir: Path) -> None:
        self.users_dir = users_dir
        self.users_dir.mkdir(parents=True, exist_ok=True)

    def for_umo(self, umo: str) -> UserRepository:
        digest = hashlib.sha256(umo.encode("utf-8")).hexdigest()
        return UserRepository(self.users_dir / f"{digest}.db")

    def all_repositories(self) -> list[UserRepository]:
        return [UserRepository(path) for path in self.users_dir.glob("*.db")]


def _subscription_from_row(row: sqlite3.Row) -> Subscription:
    return Subscription(
        keyword=row["keyword"],
        unified_msg_origin=row["unified_msg_origin"],
        created_time=datetime.fromisoformat(row["created_time"]),
        last_check_time=(datetime.fromisoformat(row["last_check_time"]) if row["last_check_time"] else None),
        last_scheduled_slot=(
            datetime.fromisoformat(row["last_scheduled_slot"]) if row["last_scheduled_slot"] else None
        ),
    )


def _pending_notification_from_row(row: sqlite3.Row) -> PendingNotification:
    return PendingNotification(
        keyword=row["keyword"],
        item=MercariItem(
            id=row["item_id"], title=row["title"], price=row["price"], url=row["url"],
            image_url=row["image_url"], created_time=datetime.fromisoformat(row["created_time"]),
        ),
        queued_time=datetime.fromisoformat(row["queued_time"]),
    )
