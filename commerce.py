"""Durable local order, entitlement, session, and webhook storage.

SQLite is suitable for the local development server. The server deliberately
disables commerce on Vercel because its filesystem is not durable; production
requires replacing this module with a managed database implementation.
"""

import hashlib
import secrets
import sqlite3
import time
from contextlib import contextmanager


SESSION_TTL_SECONDS = 30 * 24 * 60 * 60


class CommerceStore:
    def __init__(self, database_path):
        self.database_path = str(database_path)
        self.initialize()

    @contextmanager
    def connection(self):
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self):
        with self.connection() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS orders (
                    order_id TEXT PRIMARY KEY,
                    product_id TEXT NOT NULL,
                    amount INTEGER NOT NULL,
                    currency TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'created',
                    payment_id TEXT UNIQUE,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS webhook_events (
                    event_id TEXT PRIMARY KEY,
                    received_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS entitlements (
                    order_id TEXT NOT NULL,
                    product_id TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    PRIMARY KEY (order_id, product_id)
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    expires_at INTEGER NOT NULL,
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS session_entitlements (
                    token_hash TEXT NOT NULL,
                    product_id TEXT NOT NULL,
                    PRIMARY KEY (token_hash, product_id)
                );
                CREATE TABLE IF NOT EXISTS checkout_sessions (
                    order_id TEXT PRIMARY KEY,
                    token_hash TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS checkout_sessions_token ON checkout_sessions(token_hash);
            """)

    def create_order(self, order_id, product_id, amount, currency):
        now = int(time.time())
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO orders (order_id, product_id, amount, currency, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (order_id, product_id, amount, currency, now, now),
            )

    def get_order(self, order_id):
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
            return dict(row) if row else None

    def bind_checkout_session(self, order_id, previous_token=""):
        """Bind an unpaid order to its browser; this grants no product access."""
        now = int(time.time())
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            token_hash = hashlib.sha256(previous_token.encode()).hexdigest()
            session = connection.execute(
                "SELECT 1 FROM sessions WHERE token_hash = ? AND expires_at > ?",
                (token_hash, now),
            ).fetchone()
            token = previous_token if session else secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            connection.execute(
                "INSERT OR IGNORE INTO sessions (token_hash, expires_at, created_at) VALUES (?, ?, ?)",
                (token_hash, now + SESSION_TTL_SECONDS, now),
            )
            connection.execute(
                "INSERT INTO checkout_sessions (order_id, token_hash) VALUES (?, ?)",
                (order_id, token_hash),
            )
        return token

    def mark_payment_captured(self, order_id, payment_id):
        now = int(time.time())
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            order = connection.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
            if not order:
                return None
            if order["payment_id"] and order["payment_id"] != payment_id:
                return None
            connection.execute(
                "UPDATE orders SET status = 'paid', payment_id = ?, updated_at = ? WHERE order_id = ?",
                (payment_id, now, order_id),
            )
            connection.execute(
                "INSERT OR IGNORE INTO entitlements (order_id, product_id, created_at) VALUES (?, ?, ?)",
                (order_id, order["product_id"], now),
            )
            return dict(order)

    def mark_webhook_processed(self, event_id):
        if not event_id:
            return False
        with self.connection() as connection:
            try:
                connection.execute("INSERT INTO webhook_events (event_id, received_at) VALUES (?, ?)", (event_id, int(time.time())))
                return True
            except sqlite3.IntegrityError:
                return False

    def issue_session_for_order(self, order_id, previous_token=""):
        now = int(time.time())
        expires_at = now + SESSION_TTL_SECONDS
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            products = connection.execute(
                "SELECT product_id FROM entitlements WHERE order_id = ?", (order_id,)
            ).fetchall()
            if not products:
                return None
            previous_hash = hashlib.sha256(previous_token.encode("utf-8")).hexdigest()
            previous = connection.execute(
                "SELECT 1 FROM sessions WHERE token_hash = ? AND expires_at > ?",
                (previous_hash, now),
            ).fetchone()
            if previous:
                # Reuse a valid session so separate tabs and subsequent purchases
                # preserve all earlier purchases rather than replacing them.
                token, token_hash = previous_token, previous_hash
                connection.execute("UPDATE sessions SET expires_at = ? WHERE token_hash = ?", (expires_at, token_hash))
            connection.execute(
                "INSERT OR IGNORE INTO sessions (token_hash, expires_at, created_at) VALUES (?, ?, ?)",
                (token_hash, expires_at, now),
            )
            connection.executemany(
                "INSERT OR IGNORE INTO session_entitlements (token_hash, product_id) VALUES (?, ?)",
                ((token_hash, product["product_id"]) for product in products),
            )
        return token, expires_at

    def has_access(self, token, product_id):
        if not token or not product_id:
            return False
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self.connection() as connection:
            row = connection.execute(
                """SELECT 1 FROM sessions AS s
                   WHERE s.token_hash = ? AND s.expires_at > ?
                   AND (EXISTS (
                     SELECT 1 FROM session_entitlements AS e
                     WHERE e.token_hash = s.token_hash AND e.product_id IN (?, 'saga-combo')
                   ) OR EXISTS (
                     SELECT 1 FROM checkout_sessions AS c
                     JOIN entitlements AS e ON e.order_id = c.order_id
                     WHERE c.token_hash = s.token_hash AND e.product_id IN (?, 'saga-combo')
                   )) LIMIT 1""",
                (token_hash, int(time.time()), product_id, product_id),
            ).fetchone()
            return bool(row)
