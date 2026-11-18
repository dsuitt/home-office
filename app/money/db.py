"""SQLite storage. All money is integer cents, signed from the account's view (+ in, - out)."""
from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY,
  plaid_item_id TEXT UNIQUE,
  access_token TEXT NOT NULL,          -- encrypted
  institution TEXT NOT NULL DEFAULT '',
  cursor TEXT,
  status TEXT NOT NULL DEFAULT 'ok',   -- ok | needs_reauth | error
  last_sync TEXT,
  error TEXT
);

CREATE TABLE IF NOT EXISTS accounts (
  id INTEGER PRIMARY KEY,
  item_id INTEGER REFERENCES items(id) ON DELETE SET NULL,
  plaid_account_id TEXT UNIQUE,
  name TEXT NOT NULL,
  grp TEXT NOT NULL DEFAULT 'cash',    -- cash | credit | loan | investment | property
  subtype TEXT NOT NULL DEFAULT '',
  mask TEXT NOT NULL DEFAULT '',
  purpose TEXT NOT NULL DEFAULT '',
  balance_cents INTEGER NOT NULL DEFAULT 0,   -- signed: debts are negative
  manual INTEGER NOT NULL DEFAULT 0,
  hidden INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS balances (
  account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  day TEXT NOT NULL,
  balance_cents INTEGER NOT NULL,
  PRIMARY KEY (account_id, day)
);

CREATE TABLE IF NOT EXISTS categories (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  bucket TEXT NOT NULL,                -- income | ess | non | goal | transfer
  kind TEXT NOT NULL DEFAULT 'expense',-- income | expense | save | invest | debt | transfer
  sort INTEGER NOT NULL DEFAULT 0,
  archived INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS transactions (
  id INTEGER PRIMARY KEY,
  account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  ext_id TEXT UNIQUE,                  -- plaid transaction id, or a hash for imports
  date TEXT NOT NULL,                  -- YYYY-MM-DD
  raw TEXT NOT NULL,                   -- bank description, never edited
  merchant TEXT NOT NULL,              -- display name (editable)
  mkey TEXT NOT NULL,                  -- normalized merchant key the learner uses
  amount_cents INTEGER NOT NULL,
  category_id INTEGER REFERENCES categories(id),
  status TEXT NOT NULL DEFAULT 'needs_you', -- auto | suggested | needs_you | done
  reason TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  tags TEXT NOT NULL DEFAULT '',       -- comma separated
  hidden INTEGER NOT NULL DEFAULT 0,   -- left out of budgets and reports
  pending INTEGER NOT NULL DEFAULT 0,
  parent_id INTEGER REFERENCES transactions(id) ON DELETE CASCADE, -- set on split children
  is_split INTEGER NOT NULL DEFAULT 0, -- set on a split parent (excluded from totals)
  source_cat TEXT NOT NULL DEFAULT '', -- the bank's/Plaid's own guess
  source TEXT NOT NULL DEFAULT 'plaid'
);
CREATE INDEX IF NOT EXISTS tx_date ON transactions(date);
CREATE INDEX IF NOT EXISTS tx_mkey ON transactions(mkey);

-- Every category choice a person makes. This is what the categorizer learns from.
CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY,
  mkey TEXT NOT NULL,
  category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
  amount_cents INTEGER NOT NULL,
  account_id INTEGER,
  at TEXT NOT NULL DEFAULT (datetime('now')),
  who TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS dec_mkey ON decisions(mkey);

CREATE TABLE IF NOT EXISTS rules (
  id INTEGER PRIMARY KEY,
  pattern TEXT NOT NULL,               -- matched against raw description + merchant, case-insensitive
  match TEXT NOT NULL DEFAULT 'contains', -- contains | exact
  min_cents INTEGER,                   -- optional bounds on abs(amount)
  max_cents INTEGER,
  account_id INTEGER,
  category_id INTEGER REFERENCES categories(id) ON DELETE CASCADE,
  rename_to TEXT NOT NULL DEFAULT '',
  hide INTEGER NOT NULL DEFAULT 0,
  hits INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS budgets (
  month TEXT NOT NULL,                 -- YYYY-MM; a month with no row inherits the latest earlier one
  category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
  amount_cents INTEGER NOT NULL,
  PRIMARY KEY (month, category_id)
);

CREATE TABLE IF NOT EXISTS goals (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  target_cents INTEGER NOT NULL,
  saved_cents INTEGER NOT NULL DEFAULT 0,
  monthly_cents INTEGER NOT NULL DEFAULT 0,
  category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL, -- contributions sorted here count
  sort INTEGER NOT NULL DEFAULT 0,
  done INTEGER NOT NULL DEFAULT 0,
  as_of TEXT NOT NULL DEFAULT (date('now'))  -- saved_cents was true on this day; later contributions add to it
);

-- Yearly / one-off bills typed in by hand ("Bills at a glance"). Monthly ones are detected.
CREATE TABLE IF NOT EXISTS bills (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  month INTEGER NOT NULL,              -- 1-12
  amount_cents INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS reflections (month TEXT PRIMARY KEY, body TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS holdings (
  id INTEGER PRIMARY KEY,
  account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  ticker TEXT NOT NULL DEFAULT '',
  quantity REAL NOT NULL DEFAULT 0,
  value_cents INTEGER NOT NULL DEFAULT 0,
  cost_cents INTEGER
);
"""

# name, bucket, kind
DEFAULT_CATEGORIES = [
    ("Paycheck", "income", "income"), ("Studio owner's pay", "income", "income"),
    ("Interest & dividends", "income", "income"), ("Other income", "income", "income"),
    ("Mortgage", "ess", "expense"), ("Utilities", "ess", "expense"), ("Groceries", "ess", "expense"),
    ("Insurance", "ess", "expense"), ("Car & gas", "ess", "expense"), ("Phone", "ess", "expense"),
    ("Health", "ess", "expense"), ("Household", "ess", "expense"), ("Subscriptions", "ess", "expense"),
    ("Eating out", "non", "expense"), ("Coffee", "non", "expense"), ("Clothes", "non", "expense"),
    ("Beauty & self-care", "non", "expense"), ("Fun & activities", "non", "expense"),
    ("Travel", "non", "expense"), ("Gifts", "non", "expense"), ("Misc", "non", "expense"),
    ("Emergency fund", "goal", "save"), ("Trip fund", "goal", "save"), ("Car fund", "goal", "save"),
    ("Roth IRAs", "goal", "invest"), ("Brokerage", "goal", "invest"),
    ("Car loan", "goal", "debt"), ("Student loan", "goal", "debt"),
    ("Transfer between my accounts", "transfer", "transfer"),
    ("Credit card payment", "transfer", "transfer"),
]

BUCKETS = [
    {"key": "income", "name": "Income", "hint": "Money coming in"},
    {"key": "ess", "name": "Essentials", "hint": "Bills and needs"},
    {"key": "non", "name": "Non-essentials", "hint": "Wants and fun"},
    {"key": "goal", "name": "Financial goals", "hint": "Savings, investing, debt payoff"},
    {"key": "transfer", "name": "Transfers", "hint": "Not counted as income or spending"},
]

_local = threading.local()


def db_path() -> str:
    return os.environ.get("MONEY_DB", str(Path(__file__).resolve().parent.parent / "data" / "money.db"))


def connect(path: str | None = None) -> sqlite3.Connection:
    path = path or db_path()
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    if not con.execute("SELECT 1 FROM categories LIMIT 1").fetchone():
        con.executemany(
            "INSERT INTO categories(name,bucket,kind,sort) VALUES(?,?,?,?)",
            [(n, b, k, i) for i, (n, b, k) in enumerate(DEFAULT_CATEGORIES)],
        )
    con.commit()
    return con


_con: sqlite3.Connection | None = None
_lock = threading.RLock()


def get() -> sqlite3.Connection:
    global _con
    if _con is None:
        _con = connect()
    return _con


def reset(path: str | None = None) -> sqlite3.Connection:
    """Point the app at a fresh connection (used by tests and demo seeding)."""
    global _con
    if _con is not None:
        _con.close()
    _con = connect(path)
    return _con


@contextmanager
def tx():
    """One writer at a time; commit on success."""
    con = get()
    with _lock:
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise


def rows(sql: str, args: tuple | list = ()) -> list[dict]:
    with _lock:
        return [dict(r) for r in get().execute(sql, args).fetchall()]


def one(sql: str, args: tuple | list = ()) -> dict | None:
    with _lock:
        r = get().execute(sql, args).fetchone()
    return dict(r) if r else None


def setting(key: str, default: str | None = None) -> str | None:
    r = one("SELECT value FROM settings WHERE key=?", (key,))
    return r["value"] if r else default


def set_setting(key: str, value: str) -> None:
    with tx() as con:
        con.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
