"""Persistent store for invite requests: email, time, and optional recall-alert categories. Nothing else.

Production uses the Render Postgres in render.yaml (DATABASE_URL, internal connections only). Without DATABASE_URL,
invite requests are closed; tests use an in-memory SQLite database with the same table.
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
from typing import Any

EMAIL = re.compile(r"^[^@\s<>\"',;]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$")

# Category keys people can pick for recall alerts, with the words shown on the page.
ALERT_CATEGORIES: dict[str, str] = {
    "crib_bassinet_sleeper": "Cribs, bassinets and sleepers", "stroller": "Strollers", "high_chair": "High chairs",
    "play_yard": "Play yards", "toy": "Toys", "other_child": "Other baby and kids' gear", "furniture": "Furniture",
    "heating_cooling": "Heaters and fans", "kitchen": "Kitchen", "electrical_battery": "Chargers and batteries",
    "tools_outdoor": "Tools and outdoor", "sports_rec": "Sports and bikes", "home": "Home", "apparel": "Clothing",
    "appliance_fixture": "Appliances",
}

# Anonymous daily totals: (UTC day, metric name from a fixed list, count). Nothing per person or per visit.
_COUNTS_DDL = """CREATE TABLE IF NOT EXISTS daily_counts (
    day TEXT NOT NULL,
    metric TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, metric)
)"""
_ADD_COUNT = ("INSERT INTO daily_counts (day, metric, count) VALUES ({p}, {p}, {p}) "
              "ON CONFLICT (day, metric) DO UPDATE SET count = daily_counts.count + excluded.count")

_DDL = """CREATE TABLE IF NOT EXISTS invite_requests (
    email TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    alerts BOOLEAN NOT NULL DEFAULT FALSE,
    categories TEXT NOT NULL DEFAULT ''
)"""
_UPSERT = ("INSERT INTO invite_requests (email, created_at, alerts, categories) VALUES ({p}, {p}, {p}, {p}) "
           "ON CONFLICT (email) DO UPDATE SET created_at = excluded.created_at, alerts = excluded.alerts, "
           "categories = excluded.categories")


class Store:
    def __init__(self, url: str | None) -> None:
        self.url = url
        self._lock = threading.Lock()
        self._sqlite: sqlite3.Connection | None = None
        self._ready = False

    @property
    def available(self) -> bool:
        return bool(self.url)

    def _connect(self) -> Any:
        if self.url == "sqlite://memory":
            if self._sqlite is None:
                self._sqlite = sqlite3.connect(":memory:", check_same_thread=False)
            return self._sqlite
        import psycopg  # only needed on the hosted service

        return psycopg.connect(self.url, connect_timeout=10)

    def _run(self, sql: str, args: tuple = (), fetch: bool = False) -> list[tuple]:
        p = "?" if self.url == "sqlite://memory" else "%s"
        with self._lock:
            conn = self._connect()
            try:
                if not self._ready:
                    conn.execute(_DDL)
                    conn.execute(_COUNTS_DDL)
                    self._ready = True
                cur = conn.execute(sql.format(p=p), args)
                rows = cur.fetchall() if fetch else []
                conn.commit()
                return rows
            finally:
                if self.url != "sqlite://memory":
                    conn.close()

    def add(self, email: str, alerts: bool, categories: list[str]) -> None:
        created = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self._run(_UPSERT, (email.strip().lower(), created, alerts, ",".join(categories)))

    def add_counts(self, rows: list[tuple[str, str, int]]) -> None:
        for day, metric, n in rows:
            self._run(_ADD_COUNT, (day, metric, n))

    def counts(self, since_day: str) -> list[tuple]:
        return self._run("SELECT day, metric, count FROM daily_counts WHERE day >= {p} ORDER BY day, metric",
                         (since_day,), fetch=True)

    def all(self) -> list[tuple]:
        return self._run("SELECT email, created_at, alerts, categories FROM invite_requests ORDER BY created_at",
                         fetch=True)


def from_env() -> Store:
    return Store(os.environ.get("DATABASE_URL") or None)
