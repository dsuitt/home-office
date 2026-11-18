"""FastAPI app: JSON API under /api, the single-page front end at /."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import db, ingest, learn, plaid, reports

STATIC = Path(__file__).parent / "static"

# Read app/.env if present (KEY=value lines), without overriding anything already set.
_env = Path(__file__).resolve().parent.parent / ".env"
if _env.exists():
    for _line in _env.read_text().splitlines():
        if "=" in _line and not _line.lstrip().startswith("#"):
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
app = FastAPI(title="Home Money", docs_url=None, redoc_url=None, openapi_url=None)

# ------------------------------------------------------------------ sign-in
COOKIE = "money_session"
_fails: dict[str, list[float]] = {}


def users() -> dict[str, str]:
    """APP_USERS='dan:long-password,sam:other-password'. Empty means no sign-in (local use only)."""
    out = {}
    for part in os.environ.get("APP_USERS", "").split(","):
        if ":" in part:
            n, p = part.split(":", 1)
            out[n.strip().lower()] = p.strip()
    return out


def _sign(msg: str) -> str:
    key = os.environ.get("SECRET_KEY", "dev-only-secret").encode()
    return hmac.new(key, msg.encode(), hashlib.sha256).hexdigest()


def make_cookie(name: str) -> str:
    body = f"{name}|{int(time.time()) + 30 * 86400}"
    return f"{body}|{_sign(body)}"


def who(request: Request) -> str:
    if not users():
        # No sign-in configured: only this computer may use it, so a forgotten setting can't expose the data.
        host = request.client.host if request.client else ""
        if host not in ("127.0.0.1", "::1", "localhost") and os.environ.get("ALLOW_NO_AUTH") != "1":
            raise HTTPException(401, "Set APP_USERS on the server to sign in from another device.")
        return "you"
    raw = request.cookies.get(COOKIE, "")
    try:
        name, exp, sig = raw.split("|")
    except ValueError:
        raise HTTPException(401, "Sign in")
    if not hmac.compare_digest(sig, _sign(f"{name}|{exp}")) or int(exp) < time.time() or name not in users():
        raise HTTPException(401, "Sign in")
    return name


@app.middleware("http")
async def guard(request: Request, call_next):
    """Everything under /api needs a session except login; writes must come from our own page."""
    path = request.url.path
    if path.startswith("/api/") and path not in ("/api/login", "/api/session"):
        try:
            request.state.who = who(request)
        except HTTPException as e:
            return JSONResponse({"detail": e.detail}, status_code=e.status_code)
        if request.method not in ("GET", "HEAD") and request.headers.get("x-requested-with") != "money":
            return JSONResponse({"detail": "Bad request origin"}, status_code=403)
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["X-Frame-Options"] = "DENY"
    # Bank descriptions are outside text: scripts may only come from this app and Plaid's Link.
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' https://cdn.plaid.com; frame-src https://cdn.plaid.com; "
        "connect-src 'self' https://*.plaid.com; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; img-src 'self' data:; base-uri 'none'; form-action 'self'")
    if path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


def user(request: Request) -> str:
    return getattr(request.state, "who", "you")


@app.post("/api/login")
def login(request: Request, response: Response, body: dict = Body(...)):
    ip = request.client.host if request.client else "?"
    recent = [t for t in _fails.get(ip, []) if t > time.time() - 900]
    if len(recent) >= 8:
        raise HTTPException(429, "Too many tries. Wait 15 minutes.")
    name = str(body.get("name", "")).strip().lower()
    pw = users().get(name)
    if not pw or not hmac.compare_digest(pw.encode(), str(body.get("password", "")).encode()):
        _fails[ip] = recent + [time.time()]
        raise HTTPException(401, "That name and password don't match.")
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(COOKIE, make_cookie(name), max_age=30 * 86400, httponly=True, samesite="lax", secure=secure)
    return {"name": name}


@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE)
    return {}


@app.get("/api/session")
def session(request: Request):
    try:
        return {"name": who(request), "auth": bool(users())}
    except HTTPException as e:
        return {"name": None, "auth": True, "setup": None if users() else e.detail}


# ------------------------------------------------------------------ bootstrap
@app.get("/api/boot")
def boot(request: Request):
    months = db.one("SELECT MIN(substr(date,1,7)) lo, MAX(substr(date,1,7)) hi FROM transactions")
    counts = db.one("SELECT COALESCE(SUM(status='needs_you'),0) ask, COALESCE(SUM(status='suggested'),0) sug, COUNT(*) n "
                    "FROM transactions WHERE is_split=0 AND hidden=0")
    return {
        "user": user(request), "auth": bool(users()), "today": date.today().isoformat(),
        "buckets": db.BUCKETS,
        "categories": db.rows("SELECT * FROM categories ORDER BY sort, id"),
        "accounts": db.rows("SELECT a.*, i.institution, i.status item_status FROM accounts a LEFT JOIN items i ON i.id=a.item_id ORDER BY a.id"),
        "items": db.rows("SELECT id, institution, status, last_sync, error FROM items"),
        "months": months, "counts": counts,
        "plaid": {"configured": plaid.configured(), "env": plaid.env_name()},
        "demo": db.setting("demo") == "1",
        "tags": sorted({x.strip() for r in db.rows("SELECT tags FROM transactions WHERE tags<>''") for x in r["tags"].split(",") if x.strip()}),
    }


# ------------------------------------------------------------------ transactions
TX_SELECT = ("SELECT t.*, a.name account, c.name category, c.bucket FROM transactions t JOIN accounts a ON a.id=t.account_id "
             "LEFT JOIN categories c ON c.id=t.category_id")


@app.get("/api/transactions")
def transactions(month: str = "", q: str = "", status: str = "", account: int = 0, category: int = 0, tag: str = "",
                 mkey: str = "", uncategorized: int = 0, limit: int = 500, offset: int = 0):
    where, args = ["t.is_split=0"], []
    if month:
        where.append("substr(t.date,1,7)=?"); args.append(month)
    if status == "open":
        where.append("t.status IN ('needs_you','suggested')")
    elif status:
        where.append("t.status=?"); args.append(status)
    if account:
        where.append("t.account_id=?"); args.append(account)
    if category:
        where.append("t.category_id=?"); args.append(category)
    if uncategorized:
        where.append("t.category_id IS NULL")
    if mkey:
        where.append("t.mkey=?"); args.append(mkey)
    if tag:
        where.append("(',' || t.tags || ',') LIKE ?"); args.append(f"%,{tag},%")
    if q:
        num = q.replace("$", "").replace(",", "")
        clause = "(t.merchant LIKE ? OR t.raw LIKE ? OR t.note LIKE ? OR c.name LIKE ? OR t.tags LIKE ?"
        args += [f"%{q}%"] * 5
        try:
            cents = int(round(abs(float(num)) * 100))
            clause += " OR abs(t.amount_cents)=?"; args.append(cents)
        except ValueError:
            pass
        where.append(clause + ")")
    w = " AND ".join(where)
    total = db.one(f"SELECT COUNT(*) n, COALESCE(SUM(t.amount_cents),0) s FROM transactions t LEFT JOIN categories c ON c.id=t.category_id WHERE {w}", args)
    items = db.rows(f"{TX_SELECT} WHERE {w} ORDER BY t.date DESC, t.id DESC LIMIT ? OFFSET ?", args + [min(limit, 2000), offset])
    return {"items": items, "total": total["n"], "sum_cents": total["s"]}


def _tx(txid: int) -> dict:
    t = db.one(f"{TX_SELECT} WHERE t.id=?", (txid,))
    if not t:
        raise HTTPException(404, "No such transaction")
    return t


@app.patch("/api/transactions/{txid}")
def edit_transaction(txid: int, request: Request, body: dict = Body(...)):
    t = _tx(txid)
    out: dict = {}
    with db.tx() as con:
        if "category_id" in body:
            cid = body["category_id"]
            if cid:
                con.execute("UPDATE transactions SET category_id=?, status='done', reason='You chose this.' WHERE id=?", (cid, txid))
                if not t["parent_id"]:
                    learn.record(con, t["mkey"], cid, t["amount_cents"], t["account_id"], user(request))
                    # The lesson applies right away to everything from this merchant you haven't confirmed yet...
                    out["resorted"] = learn.resort_open(con, t["mkey"])
                    # ...and we report earlier, already-confirmed ones that disagree so you can change them in one go.
                    out["others"] = con.execute(
                        "SELECT COUNT(*) FROM transactions WHERE mkey=? AND id<>? AND status='done' AND is_split=0 AND parent_id IS NULL "
                        "AND (category_id IS NULL OR category_id<>?)", (t["mkey"], txid, cid)).fetchone()[0]
            else:
                con.execute("UPDATE transactions SET category_id=NULL, status='needs_you', reason='' WHERE id=?", (txid,))
        for f in ("note", "tags", "merchant"):
            if f in body:
                con.execute(f"UPDATE transactions SET {f}=? WHERE id=?", (str(body[f]).strip(), txid))
        if "hidden" in body:
            con.execute("UPDATE transactions SET hidden=? WHERE id=?", (int(bool(body["hidden"])), txid))
        if "date" in body and t["source"] == "manual":
            con.execute("UPDATE transactions SET date=? WHERE id=?", (body["date"], txid))
    return {"tx": _tx(txid), **out}


@app.post("/api/transactions/{txid}/confirm")
def confirm(txid: int, request: Request):
    t = _tx(txid)
    if not t["category_id"]:
        raise HTTPException(400, "Pick a category first")
    with db.tx() as con:
        con.execute("UPDATE transactions SET status='done' WHERE id=?", (txid,))
        if not t["parent_id"] and t["status"] != "done":
            learn.record(con, t["mkey"], t["category_id"], t["amount_cents"], t["account_id"], user(request))
            learn.resort_open(con, t["mkey"])
    return {"tx": _tx(txid)}


@app.post("/api/transactions/confirm-all")
def confirm_all(request: Request, body: dict = Body(...)):
    """Accept every suggestion in a month (or everything sorted for you) in one go."""
    month = body.get("month", "")
    statuses = ("suggested", "auto") if body.get("include_auto") else ("suggested",)
    n = 0
    with db.tx() as con:
        for t in con.execute(
            f"SELECT * FROM transactions WHERE status IN ({','.join('?' * len(statuses))}) AND category_id IS NOT NULL "
            "AND is_split=0 AND substr(date,1,7) LIKE ?", (*statuses, month or "%")).fetchall():
            con.execute("UPDATE transactions SET status='done' WHERE id=?", (t["id"],))
            if not t["parent_id"]:
                learn.record(con, t["mkey"], t["category_id"], t["amount_cents"], t["account_id"], user(request))
            n += 1
    return {"confirmed": n}


@app.post("/api/merchants/recategorize")
def recategorize(request: Request, body: dict = Body(...)):
    """Change every transaction from one merchant, past ones included."""
    mkey, cid = body["mkey"], int(body["category_id"])
    with db.tx() as con:
        rows_ = con.execute("SELECT id, amount_cents, account_id, category_id FROM transactions WHERE mkey=? AND is_split=0 AND parent_id IS NULL", (mkey,)).fetchall()
        for r in rows_:
            if r["category_id"] != cid:
                learn.record(con, mkey, cid, r["amount_cents"], r["account_id"], user(request))
        con.execute("UPDATE transactions SET category_id=?, status='done', reason='You chose this for every one.' "
                    "WHERE mkey=? AND is_split=0 AND parent_id IS NULL", (cid, mkey))
        if body.get("always"):
            # wipe mixed history so the answer is unambiguous from now on
            con.execute("DELETE FROM decisions WHERE mkey=? AND category_id<>?", (mkey, cid))
    return {"changed": len(rows_)}


@app.post("/api/transactions/{txid}/split")
def split(txid: int, body: dict = Body(...)):
    t = _tx(txid)
    if t["parent_id"]:
        raise HTTPException(400, "This is already part of a split")
    parts = body.get("parts") or []
    with db.tx() as con:
        con.execute("DELETE FROM transactions WHERE parent_id=?", (txid,))
        if not parts:                    # un-split
            con.execute("UPDATE transactions SET is_split=0 WHERE id=?", (txid,))
        else:
            if sum(int(p["amount_cents"]) for p in parts) != t["amount_cents"]:
                raise HTTPException(400, "The parts must add up to the full amount")
            for p in parts:
                con.execute(
                    "INSERT INTO transactions(account_id, date, raw, merchant, mkey, amount_cents, category_id, status, reason, note, parent_id, source) "
                    "VALUES(?,?,?,?,?,?,?, 'done', 'Part of a split.', ?, ?, ?)",
                    (t["account_id"], t["date"], t["raw"], t["merchant"], t["mkey"], int(p["amount_cents"]), p.get("category_id") or None,
                     str(p.get("note", "")), txid, t["source"]))
            con.execute("UPDATE transactions SET is_split=1, status='done' WHERE id=?", (txid,))
    return {"ok": True}


@app.get("/api/transactions/{txid}")
def get_transaction(txid: int):
    t = _tx(txid)
    parent = t["parent_id"] or txid
    hist = db.rows(
        "SELECT c.name, COUNT(*) n FROM decisions d JOIN categories c ON c.id=d.category_id WHERE d.mkey=? GROUP BY c.id ORDER BY n DESC", (t["mkey"],))
    return {"tx": t, "parts": db.rows(f"{TX_SELECT} WHERE t.parent_id=?", (parent,)), "history": hist,
            "same_merchant": db.one("SELECT COUNT(*) n FROM transactions WHERE mkey=? AND is_split=0", (t["mkey"],))["n"]}


@app.post("/api/transactions")
def add_transaction(request: Request, body: dict = Body(...)):
    raw = str(body.get("merchant", "")).strip() or "Manual entry"
    mkey = learn.merchant_key(raw)
    with db.tx() as con:
        cur = con.execute(
            "INSERT INTO transactions(account_id, date, raw, merchant, mkey, amount_cents, note, source) VALUES(?,?,?,?,?,?,?, 'manual')",
            (int(body["account_id"]), body["date"], raw, raw, mkey, int(body["amount_cents"]), str(body.get("note", ""))))
        if body.get("category_id"):
            con.execute("UPDATE transactions SET category_id=?, status='done', reason='You chose this.' WHERE id=?", (body["category_id"], cur.lastrowid))
            learn.record(con, mkey, body["category_id"], int(body["amount_cents"]), int(body["account_id"]), user(request))
        else:
            learn.apply_guess(con, cur.lastrowid)
    return {"id": cur.lastrowid}


@app.delete("/api/transactions/{txid}")
def delete_transaction(txid: int):
    t = _tx(txid)
    if t["source"] not in ("manual", "csv", "demo"):
        raise HTTPException(400, "Bank transactions can't be deleted, but you can hide them.")
    with db.tx() as con:
        con.execute("DELETE FROM transactions WHERE id=?", (txid,))
    return {}


# ------------------------------------------------------------------ learning + rules
@app.get("/api/learned")
def learned():
    return learn.learned()


@app.delete("/api/learned/{mkey:path}")
def forget(mkey: str):
    with db.tx() as con:
        con.execute("DELETE FROM decisions WHERE mkey=?", (mkey,))
        learn.resort_open(con, mkey)
    return {}


@app.get("/api/rules")
def rules():
    return db.rows("SELECT r.*, c.name category FROM rules r LEFT JOIN categories c ON c.id=r.category_id ORDER BY r.id DESC")


@app.post("/api/rules")
def add_rule(body: dict = Body(...)):
    if not str(body.get("pattern", "")).strip():
        raise HTTPException(400, "A rule needs some text to look for")
    with db.tx() as con:
        cur = con.execute(
            "INSERT INTO rules(pattern, match, min_cents, max_cents, account_id, category_id, rename_to, hide) VALUES(?,?,?,?,?,?,?,?)",
            (body["pattern"].strip(), body.get("match", "contains"), body.get("min_cents"), body.get("max_cents"),
             body.get("account_id") or None, body["category_id"], str(body.get("rename_to", "")), int(bool(body.get("hide")))))
        changed = learn.resort_open(con)
        if body.get("apply_to_past"):
            for t in con.execute("SELECT id, raw, merchant, amount_cents, account_id FROM transactions WHERE status='done' AND is_split=0 AND parent_id IS NULL").fetchall():
                r = learn.match_rule(t["raw"], t["merchant"], t["amount_cents"], t["account_id"])
                if r and r["id"] == cur.lastrowid:
                    con.execute("UPDATE transactions SET category_id=?, reason=? WHERE id=?", (r["category_id"], f"Your rule: “{r['pattern']}”.", t["id"]))
                    changed += 1
    return {"id": cur.lastrowid, "changed": changed}


@app.delete("/api/rules/{rid}")
def delete_rule(rid: int):
    with db.tx() as con:
        con.execute("DELETE FROM rules WHERE id=?", (rid,))
        learn.resort_open(con)
    return {}


# ------------------------------------------------------------------ categories, budget, goals, bills
@app.post("/api/categories")
def add_category(body: dict = Body(...)):
    name = str(body.get("name", "")).strip()
    if not name:
        raise HTTPException(400, "Name it first")
    if db.one("SELECT 1 FROM categories WHERE name=? COLLATE NOCASE", (name,)):
        raise HTTPException(400, "There's already a category with that name")
    bucket = body.get("bucket", "non")
    kind = body.get("kind") or {"income": "income", "goal": "save", "transfer": "transfer"}.get(bucket, "expense")
    with db.tx() as con:
        cur = con.execute("INSERT INTO categories(name,bucket,kind,sort) VALUES(?,?,?,(SELECT COALESCE(MAX(sort),0)+1 FROM categories))", (name, bucket, kind))
    return {"id": cur.lastrowid}


@app.patch("/api/categories/{cid}")
def edit_category(cid: int, body: dict = Body(...)):
    with db.tx() as con:
        for f in ("name", "bucket", "kind"):
            if f in body:
                con.execute(f"UPDATE categories SET {f}=? WHERE id=?", (str(body[f]).strip(), cid))
        for f in ("archived", "sort"):
            if f in body:
                con.execute(f"UPDATE categories SET {f}=? WHERE id=?", (int(body[f]), cid))
        if "rollover_from" in body:      # a month (YYYY-MM) turns rollover on from then; null turns it off
            con.execute("UPDATE categories SET rollover_from=? WHERE id=?", (body["rollover_from"] or None, cid))
        if body.get("merge_into"):       # move everything to another category, then remove this one
            into = int(body["merge_into"])
            con.execute("UPDATE transactions SET category_id=? WHERE category_id=?", (into, cid))
            con.execute("UPDATE decisions SET category_id=? WHERE category_id=?", (into, cid))
            con.execute("UPDATE rules SET category_id=? WHERE category_id=?", (into, cid))
            con.execute("DELETE FROM categories WHERE id=?", (cid,))
    return {}


@app.get("/api/year/{y}")
def year(y: int):
    return reports.year(y)


@app.get("/api/report")
def report(start: str, end: str, kind: str = "spending", group: str = "category"):
    if len(start) != 10 or len(end) != 10 or start > end:
        raise HTTPException(400, "Pick a start date on or before the end date")
    return reports.report(start, end, kind, group)


@app.put("/api/budget/{month}")
def set_budget(month: str, body: dict = Body(...)):
    """Body: {category_id: cents}. Applies to this month and every later month nobody has edited."""
    with db.tx() as con:
        for cid, cents in body.items():
            con.execute("INSERT INTO budgets VALUES(?,?,?) ON CONFLICT(month, category_id) DO UPDATE SET amount_cents=excluded.amount_cents",
                        (month, int(cid), int(cents)))
    return {}


@app.get("/api/goals")
def goals():
    out = db.rows("SELECT g.*, c.name category FROM goals g LEFT JOIN categories c ON c.id=g.category_id ORDER BY g.done, g.sort, g.id")
    for g in out:
        # money sorted into the goal's category since the last time the saved amount was typed in
        since = db.one("SELECT COALESCE(SUM(-amount_cents),0) s FROM transactions WHERE category_id=? AND amount_cents<0 AND date>? "
                       "AND is_split=0 AND hidden=0", (g["category_id"], g["as_of"]))["s"] if g["category_id"] else 0
        g["so_far_cents"] = g["saved_cents"] + since
    return out


@app.post("/api/goals")
def add_goal(body: dict = Body(...)):
    with db.tx() as con:
        cur = con.execute("INSERT INTO goals(name,target_cents,saved_cents,monthly_cents,category_id,sort) VALUES(?,?,?,?,?,(SELECT COALESCE(MAX(sort),0)+1 FROM goals))",
                          (str(body["name"]).strip(), int(body["target_cents"]), int(body.get("saved_cents", 0)), int(body.get("monthly_cents", 0)), body.get("category_id") or None))
    return {"id": cur.lastrowid}


@app.patch("/api/goals/{gid}")
def edit_goal(gid: int, body: dict = Body(...)):
    with db.tx() as con:
        for f in ("name",):
            if f in body:
                con.execute(f"UPDATE goals SET {f}=? WHERE id=?", (str(body[f]).strip(), gid))
        for f in ("target_cents", "saved_cents", "monthly_cents", "sort", "done", "category_id"):
            if f in body:
                con.execute(f"UPDATE goals SET {f}=? WHERE id=?", (body[f] if body[f] is None else int(body[f]), gid))
        if "saved_cents" in body:
            con.execute("UPDATE goals SET as_of=date('now') WHERE id=?", (gid,))
    return {}


@app.delete("/api/goals/{gid}")
def delete_goal(gid: int):
    with db.tx() as con:
        con.execute("DELETE FROM goals WHERE id=?", (gid,))
    return {}


@app.get("/api/bills")
def bills():
    return {"yearly": db.rows("SELECT * FROM bills ORDER BY month, id"), "recurring": reports.recurring()}


@app.post("/api/bills")
def add_bill(body: dict = Body(...)):
    with db.tx() as con:
        cur = con.execute("INSERT INTO bills(name,month,amount_cents) VALUES(?,?,?)", (str(body["name"]).strip(), int(body["month"]), int(body.get("amount_cents", 0))))
    return {"id": cur.lastrowid}


@app.delete("/api/bills/{bid}")
def delete_bill(bid: int):
    with db.tx() as con:
        con.execute("DELETE FROM bills WHERE id=?", (bid,))
    return {}


@app.get("/api/reflection/{month}")
def get_reflection(month: str):
    r = db.one("SELECT body FROM reflections WHERE month=?", (month,))
    return json.loads(r["body"]) if r else {}


@app.put("/api/reflection/{month}")
def put_reflection(month: str, body: dict = Body(...)):
    with db.tx() as con:
        con.execute("INSERT INTO reflections VALUES(?,?) ON CONFLICT(month) DO UPDATE SET body=excluded.body", (month, json.dumps(body)[:20000]))
    return {}


# ------------------------------------------------------------------ accounts, net worth, investments
@app.get("/api/networth")
def networth(months: int = 12):
    return reports.networth(max(2, min(months, 60)))


@app.get("/api/holdings")
def holdings():
    return db.rows("SELECT h.*, a.name account FROM holdings h JOIN accounts a ON a.id=h.account_id ORDER BY h.value_cents DESC")


@app.get("/api/merchants")
def merchants(month: str = ""):
    return reports.merchants(month or None)


def _stamp_balance(con, aid: int, cents: int) -> None:
    con.execute("UPDATE accounts SET balance_cents=?, updated_at=? WHERE id=?", (cents, datetime.now(timezone.utc).isoformat(timespec="seconds"), aid))
    con.execute("INSERT INTO balances VALUES(?,?,?) ON CONFLICT(account_id, day) DO UPDATE SET balance_cents=excluded.balance_cents",
                (aid, date.today().isoformat(), cents))


@app.post("/api/accounts")
def add_account(body: dict = Body(...)):
    with db.tx() as con:
        cur = con.execute("INSERT INTO accounts(name, grp, purpose, manual) VALUES(?,?,?,1)",
                          (str(body["name"]).strip(), body.get("grp", "cash"), str(body.get("purpose", ""))))
        _stamp_balance(con, cur.lastrowid, int(body.get("balance_cents", 0)))
    return {"id": cur.lastrowid}


@app.patch("/api/accounts/{aid}")
def edit_account(aid: int, body: dict = Body(...)):
    a = db.one("SELECT * FROM accounts WHERE id=?", (aid,))
    if not a:
        raise HTTPException(404, "No such account")
    with db.tx() as con:
        for f in ("name", "purpose", "grp"):
            if f in body:
                con.execute(f"UPDATE accounts SET {f}=? WHERE id=?", (str(body[f]).strip(), aid))
        if "hidden" in body:
            con.execute("UPDATE accounts SET hidden=? WHERE id=?", (int(bool(body["hidden"])), aid))
        if "balance_cents" in body:
            if not a["manual"]:
                raise HTTPException(400, "This balance comes from the bank")
            _stamp_balance(con, aid, int(body["balance_cents"]))
    return {}


@app.delete("/api/accounts/{aid}")
def delete_account(aid: int):
    a = db.one("SELECT * FROM accounts WHERE id=?", (aid,))
    if a and not a["manual"]:
        raise HTTPException(400, "Disconnect the bank instead")
    with db.tx() as con:
        con.execute("DELETE FROM accounts WHERE id=?", (aid,))
    return {}


# ------------------------------------------------------------------ Plaid
@app.post("/api/plaid/link-token")
def plaid_link_token(request: Request, body: dict = Body(default={})):
    if not plaid.configured():
        raise HTTPException(400, "Plaid keys aren't set yet. See app/README.md.")
    token = None
    if body.get("item_id"):
        item = db.one("SELECT access_token FROM items WHERE id=?", (int(body["item_id"]),))
        token = plaid.unseal(item["access_token"]) if item else None
    try:
        return {"link_token": plaid.link_token("household", token)}
    except plaid.PlaidError as e:
        raise HTTPException(502, str(e))


@app.post("/api/plaid/exchange")
def plaid_exchange(body: dict = Body(...)):
    try:
        item_id = plaid.exchange(body["public_token"], str(body.get("institution", "")))
    except plaid.PlaidError as e:
        raise HTTPException(502, str(e))
    return plaid.sync_item(item_id)


@app.post("/api/sync")
def sync(body: dict = Body(default={})):
    if body.get("item_id"):
        return [plaid.sync_item(int(body["item_id"]))]
    return plaid.sync_all()


@app.delete("/api/items/{item_id}")
def remove_item(item_id: int):
    plaid.remove_item(item_id)
    return {}


# ------------------------------------------------------------------ import / export / demo
@app.post("/api/import/csv")
async def import_csv(request: Request, file: UploadFile, account: str = "Imported"):
    data = await file.read()
    if len(data) > 25_000_000:
        raise HTTPException(413, "That file is too big")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    return ingest.import_csv(text, account, user(request))


@app.get("/api/export.csv")
def export_csv():
    return PlainTextResponse(ingest.export_csv(), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="money-{date.today()}.csv"'})


@app.post("/api/demo/clear")
def clear_demo():
    """Wipe the made-up sample household before linking real accounts."""
    if db.setting("demo") != "1":
        raise HTTPException(400, "There's no sample data to clear")
    with db.tx() as con:
        for t in ("transactions", "decisions", "balances", "holdings", "accounts", "budgets", "goals", "bills", "rules", "reflections"):
            con.execute(f"DELETE FROM {t}")
        con.execute("DELETE FROM settings WHERE key='demo'")
    return {}


@app.get("/healthz")
def healthz():
    return {"ok": True}


def _auto_sync():
    """Every few hours while the app runs: back up the database, then pull from the banks."""
    hours = float(os.environ.get("SYNC_EVERY_HOURS", "6"))
    while hours > 0:
        try:
            db.backup()
        except Exception as e:
            print("backup failed:", e)
        time.sleep(hours * 3600)
        try:
            if plaid.configured():
                plaid.sync_all()
        except Exception as e:           # a bad night must not stop the next one
            print("auto-sync failed:", e)


@app.on_event("startup")
def startup():
    db.get()
    if os.environ.get("DEMO") == "1":
        ingest.seed_demo()
    if users() and len(os.environ.get("SECRET_KEY", "")) < 16:
        raise RuntimeError("Set SECRET_KEY to a long random string before turning sign-in on.")
    if db.db_path() != ":memory:":
        import threading
        threading.Thread(target=_auto_sync, daemon=True).start()


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=STATIC), name="static")
