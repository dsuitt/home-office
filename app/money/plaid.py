"""Thin Plaid REST client (no SDK) plus the sync that applies Plaid data to our tables."""
from __future__ import annotations

import base64
import hashlib
import os
from datetime import date, datetime, timezone

import httpx
from cryptography.fernet import Fernet

from . import db, learn

HOSTS = {"sandbox": "https://sandbox.plaid.com", "production": "https://production.plaid.com"}


class PlaidError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


def configured() -> bool:
    return bool(os.environ.get("PLAID_CLIENT_ID") and os.environ.get("PLAID_SECRET"))


def env_name() -> str:
    return os.environ.get("PLAID_ENV", "sandbox")


def _fernet() -> Fernet:
    secret = os.environ.get("SECRET_KEY", "")
    if not secret:
        raise RuntimeError("SECRET_KEY is not set; it encrypts the bank tokens at rest.")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))


def seal(token: str) -> str:
    return _fernet().encrypt(token.encode()).decode()


def unseal(blob: str) -> str:
    return _fernet().decrypt(blob.encode()).decode()


def call(path: str, body: dict, client: httpx.Client | None = None) -> dict:
    payload = {"client_id": os.environ["PLAID_CLIENT_ID"], "secret": os.environ["PLAID_SECRET"], **body}
    c = client or httpx.Client(timeout=40)
    try:
        r = c.post(HOSTS[env_name()] + path, json=payload)
    finally:
        if client is None:
            c.close()
    data = r.json()
    if r.status_code >= 400:
        raise PlaidError(data.get("error_code", str(r.status_code)), data.get("error_message", "Plaid error"))
    return data


def link_token(user_id: str, access_token: str | None = None, client: httpx.Client | None = None) -> str:
    body: dict = {
        "user": {"client_user_id": user_id},
        "client_name": "Home Money",
        "language": "en",
        "country_codes": ["US"],
    }
    if access_token:                     # update mode: re-authenticate an existing bank login
        body["access_token"] = access_token
    else:
        body["products"] = ["transactions"]
        body["optional_products"] = ["investments", "liabilities"]
        body["transactions"] = {"days_requested": 730}
    if os.environ.get("PLAID_REDIRECT_URI"):
        body["redirect_uri"] = os.environ["PLAID_REDIRECT_URI"]
    return call("/link/token/create", body, client)["link_token"]


def exchange(public_token: str, institution: str, client: httpx.Client | None = None) -> int:
    data = call("/item/public_token/exchange", {"public_token": public_token}, client)
    with db.tx() as con:
        con.execute(
            "INSERT INTO items(plaid_item_id, access_token, institution) VALUES(?,?,?) "
            "ON CONFLICT(plaid_item_id) DO UPDATE SET access_token=excluded.access_token, status='ok', error=NULL",
            (data["item_id"], seal(data["access_token"]), institution),
        )
        return con.execute("SELECT id FROM items WHERE plaid_item_id=?", (data["item_id"],)).fetchone()["id"]


GROUPS = {"depository": "cash", "credit": "credit", "loan": "loan", "investment": "investment", "brokerage": "investment"}


def _cents(x) -> int:
    return int(round(float(x or 0) * 100))


def upsert_accounts(con, item_id: int, accounts: list[dict]) -> None:
    today = date.today().isoformat()
    for a in accounts:
        grp = GROUPS.get(a.get("type", ""), "cash")
        bal = _cents((a.get("balances") or {}).get("current"))
        if grp in ("credit", "loan"):
            bal = -bal
        con.execute(
            "INSERT INTO accounts(item_id, plaid_account_id, name, grp, subtype, mask, balance_cents, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(plaid_account_id) DO UPDATE SET item_id=excluded.item_id, "
            "balance_cents=excluded.balance_cents, updated_at=excluded.updated_at, mask=excluded.mask",
            (item_id, a["account_id"], a.get("official_name") or a.get("name") or "Account", grp,
             a.get("subtype") or "", a.get("mask") or "", bal, datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
        aid = con.execute("SELECT id FROM accounts WHERE plaid_account_id=?", (a["account_id"],)).fetchone()["id"]
        con.execute(
            "INSERT INTO balances(account_id, day, balance_cents) VALUES(?,?,?) "
            "ON CONFLICT(account_id, day) DO UPDATE SET balance_cents=excluded.balance_cents", (aid, today, bal))


def _upsert_txn(con, t: dict) -> bool:
    """Insert or update one Plaid transaction. Returns True when it is new."""
    acct = con.execute("SELECT id FROM accounts WHERE plaid_account_id=?", (t["account_id"],)).fetchone()
    if not acct:
        return False
    raw = t.get("original_description") or t.get("name") or ""
    pfc = t.get("personal_finance_category") or {}
    source_cat = pfc.get("detailed") or pfc.get("primary") or ""
    amount = -_cents(t["amount"])        # Plaid: positive is money out. Ours: positive is money in.
    mkey = learn.merchant_key(t.get("merchant_name") or raw)
    merchant = t.get("merchant_name") or learn.pretty(mkey)
    existing = con.execute("SELECT id, status FROM transactions WHERE ext_id=?", (t["transaction_id"],)).fetchone()
    # A pending charge that settles arrives as a new id pointing back at the pending one: keep our row (and her answer).
    if not existing and t.get("pending_transaction_id"):
        existing = con.execute("SELECT id, status FROM transactions WHERE ext_id=?", (t["pending_transaction_id"],)).fetchone()
    if not existing and not t.get("pending"):
        # History you imported from a CSV describes the same money: adopt that row (and its category) instead of doubling it.
        existing = con.execute(
            "SELECT id, status FROM transactions WHERE source='csv' AND ext_id LIKE 'csv:%' AND amount_cents=? "
            "AND date BETWEEN date(?, '-3 day') AND date(?, '+3 day') ORDER BY (mkey=?) DESC, abs(julianday(date)-julianday(?)) LIMIT 1",
            (amount, t["date"], t["date"], mkey, t["date"])).fetchone()
        if existing:
            con.execute("UPDATE transactions SET account_id=?, source='plaid' WHERE id=?", (acct["id"], existing["id"]))
    if existing:
        con.execute(
            "UPDATE transactions SET ext_id=?, date=?, raw=?, amount_cents=?, pending=?, source_cat=? WHERE id=?",
            (t["transaction_id"], t["date"], raw, amount, int(bool(t.get("pending"))), source_cat, existing["id"]))
        return False
    cur = con.execute(
        "INSERT INTO transactions(account_id, ext_id, date, raw, merchant, mkey, amount_cents, pending, source_cat, source) "
        "VALUES(?,?,?,?,?,?,?,?,?, 'plaid')",
        (acct["id"], t["transaction_id"], t["date"], raw, merchant, mkey, amount, int(bool(t.get("pending"))), source_cat))
    learn.apply_guess(con, cur.lastrowid)
    return True


def sync_item(item_id: int, client: httpx.Client | None = None) -> dict:
    item = db.one("SELECT * FROM items WHERE id=?", (item_id,))
    token = unseal(item["access_token"])
    added = modified = removed = 0
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        accts = call("/accounts/get", {"access_token": token}, client)
        with db.tx() as con:
            upsert_accounts(con, item_id, accts["accounts"])
        cursor = item["cursor"]
        while True:
            body = {"access_token": token, "count": 500,
                    "options": {"include_original_description": True}}
            if cursor:
                body["cursor"] = cursor
            page = call("/transactions/sync", body, client)
            with db.tx() as con:
                for t in page.get("added", []):
                    added += int(_upsert_txn(con, t))
                for t in page.get("modified", []):
                    _upsert_txn(con, t)
                    modified += 1
                for t in page.get("removed", []):
                    # never delete something a person already answered unless its settled twin replaced it
                    cur = con.execute("DELETE FROM transactions WHERE ext_id=?", (t["transaction_id"],))
                    removed += cur.rowcount
                cursor = page["next_cursor"]
                con.execute("UPDATE items SET cursor=? WHERE id=?", (cursor, item_id))
            if not page.get("has_more"):
                break
        _sync_holdings(item_id, token, client)
        with db.tx() as con:
            # CSV-import placeholder accounts whose history has all been adopted by a linked account
            con.execute("DELETE FROM accounts WHERE subtype='import' AND balance_cents=0 "
                        "AND id NOT IN (SELECT DISTINCT account_id FROM transactions)")
            from . import reports
            reports.pair_transfers(con)
            con.execute("UPDATE items SET status='ok', error=NULL, last_sync=? WHERE id=?", (now, item_id))
    except PlaidError as e:
        status = "needs_reauth" if e.code in ("ITEM_LOGIN_REQUIRED", "PENDING_EXPIRATION", "PENDING_DISCONNECT") else "error"
        with db.tx() as con:
            con.execute("UPDATE items SET status=?, error=?, last_sync=? WHERE id=?", (status, str(e), now, item_id))
        return {"item": item_id, "ok": False, "error": str(e), "status": status}
    return {"item": item_id, "ok": True, "added": added, "modified": modified, "removed": removed}


def _sync_holdings(item_id: int, token: str, client: httpx.Client | None) -> None:
    if not db.one("SELECT 1 FROM accounts WHERE item_id=? AND grp='investment'", (item_id,)):
        return
    try:
        data = call("/investments/holdings/get", {"access_token": token}, client)
    except PlaidError:
        return                           # product not enabled for this bank: balances still come through
    secs = {s["security_id"]: s for s in data.get("securities", [])}
    with db.tx() as con:
        ids = [r["id"] for r in con.execute("SELECT id FROM accounts WHERE item_id=?", (item_id,)).fetchall()]
        con.execute(f"DELETE FROM holdings WHERE account_id IN ({','.join('?' * len(ids))})", ids)
        for h in data.get("holdings", []):
            acct = con.execute("SELECT id FROM accounts WHERE plaid_account_id=?", (h["account_id"],)).fetchone()
            s = secs.get(h.get("security_id"), {})
            if acct:
                con.execute(
                    "INSERT INTO holdings(account_id,name,ticker,quantity,value_cents,cost_cents) VALUES(?,?,?,?,?,?)",
                    (acct["id"], s.get("name") or "Holding", s.get("ticker_symbol") or "", h.get("quantity") or 0,
                     _cents(h.get("institution_value")), _cents(h["cost_basis"]) if h.get("cost_basis") is not None else None))


def sync_all(client: httpx.Client | None = None) -> list[dict]:
    return [sync_item(i["id"], client) for i in db.rows("SELECT id FROM items")]


def remove_item(item_id: int, client: httpx.Client | None = None) -> None:
    item = db.one("SELECT * FROM items WHERE id=?", (item_id,))
    if not item:
        return
    try:
        call("/item/remove", {"access_token": unseal(item["access_token"])}, client)
    except PlaidError:
        pass
    with db.tx() as con:
        con.execute("DELETE FROM items WHERE id=?", (item_id,))
