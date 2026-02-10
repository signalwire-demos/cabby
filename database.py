"""SQLite database layer for Cabby taxi booking agent."""

import sqlite3
import logging
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, db_path):
        self.db_path = db_path

    @contextmanager
    def _connect(self):
        """Context manager that guarantees connection cleanup."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
        finally:
            conn.close()

    def initialize(self):
        """Create tables idempotently."""
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS customers (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    phone         TEXT UNIQUE NOT NULL,
                    name          TEXT NOT NULL,
                    home_address  TEXT,
                    home_lat      REAL,
                    home_lng      REAL,
                    work_address  TEXT,
                    work_lat      REAL,
                    work_lng      REAL,
                    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS trips (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    customer_id         INTEGER NOT NULL REFERENCES customers(id),
                    pickup_address      TEXT NOT NULL,
                    pickup_lat          REAL NOT NULL,
                    pickup_lng          REAL NOT NULL,
                    destination_address TEXT NOT NULL,
                    destination_lat     REAL NOT NULL,
                    destination_lng     REAL NOT NULL,
                    distance_meters     INTEGER,
                    duration_seconds    INTEGER,
                    fare_estimate       REAL,
                    status              TEXT NOT NULL DEFAULT 'pending'
                                        CHECK(status IN ('pending','confirmed','completed','cancelled')),
                    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
                );

                CREATE INDEX IF NOT EXISTS idx_trips_customer_status ON trips(customer_id, status);
                CREATE INDEX IF NOT EXISTS idx_customers_phone ON customers(phone);
            """)
            conn.commit()
            logger.info(f"Database initialized at {self.db_path}")

    def get_customer_by_phone(self, phone):
        """Look up customer by phone number."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM customers WHERE phone = ?", (phone,)
            ).fetchone()
            return dict(row) if row else None

    def create_customer(self, phone, name):
        """Register a new customer."""
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO customers (phone, name) VALUES (?, ?)",
                (phone, name)
            )
            conn.commit()
            customer_id = cursor.lastrowid
            row = conn.execute(
                "SELECT * FROM customers WHERE id = ?", (customer_id,)
            ).fetchone()
            return dict(row) if row else None

    def update_customer_address(self, customer_id, address_type, address, lat, lng):
        """Save home or work address for a customer."""
        if address_type not in ("home", "work"):
            raise ValueError("address_type must be 'home' or 'work'")
        with self._connect() as conn:
            conn.execute(
                f"UPDATE customers SET {address_type}_address = ?, "
                f"{address_type}_lat = ?, {address_type}_lng = ? WHERE id = ?",
                (address, lat, lng, customer_id)
            )
            conn.commit()

    def create_trip(self, customer_id, pickup_address, pickup_lat, pickup_lng,
                    destination_address, destination_lat, destination_lng,
                    distance_meters, duration_seconds, fare_estimate):
        """Create a new trip booking."""
        with self._connect() as conn:
            cursor = conn.execute(
                """INSERT INTO trips (customer_id, pickup_address, pickup_lat, pickup_lng,
                   destination_address, destination_lat, destination_lng,
                   distance_meters, duration_seconds, fare_estimate)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (customer_id, pickup_address, pickup_lat, pickup_lng,
                 destination_address, destination_lat, destination_lng,
                 distance_meters, duration_seconds, fare_estimate)
            )
            conn.commit()
            trip_id = cursor.lastrowid
            row = conn.execute("SELECT * FROM trips WHERE id = ?", (trip_id,)).fetchone()
            return dict(row) if row else None

    def get_recent_trips(self, customer_id, limit=5):
        """Get recent trips for a customer."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM trips WHERE customer_id = ? ORDER BY created_at DESC LIMIT ?",
                (customer_id, limit)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_pending_trip(self, customer_id):
        """Get the current pending trip for a customer."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM trips WHERE customer_id = ? AND status = 'pending' "
                "ORDER BY created_at DESC LIMIT 1",
                (customer_id,)
            ).fetchone()
            return dict(row) if row else None

    def cancel_trip(self, trip_id):
        """Cancel a pending trip."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE trips SET status = 'cancelled', updated_at = datetime('now') "
                "WHERE id = ? AND status = 'pending'",
                (trip_id,)
            )
            conn.commit()
