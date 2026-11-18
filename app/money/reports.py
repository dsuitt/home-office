"""Read-side maths: plan vs actual, net worth, recurring charges, transfer pairing."""
from __future__ import annotations

import calendar
import statistics
from datetime import date, timedelta

from . import db, learn

# Rows that count toward budgets and reports.
LIVE = "t.is_split=0 AND t.hidden=0"


def plan_for(month: str) -> dict[int, int]:
    """Budget for a month. A month nobody has edited inherits each category's latest earlier amount."""
    out: dict[int, int] = {}
    for r in db.rows(
        "SELECT b.category_id, b.amount_cents FROM budgets b JOIN "
        "(SELECT category_id, MAX(month) m FROM budgets WHERE month<=? GROUP BY category_id) x "
        "ON x.category_id=b.category_id AND x.m=b.month", (month,)):
        out[r["category_id"]] = r["amount_cents"]
    return out


def actual_expr() -> str:
    """Income counts what came in; spending nets refunds; goals count only money sent to them."""
    return ("SUM(CASE WHEN c.bucket='income' THEN t.amount_cents "
            "WHEN c.bucket='goal' THEN CASE WHEN t.amount_cents<0 THEN -t.amount_cents ELSE 0 END "
            "ELSE -t.amount_cents END)")


def year(y: int) -> dict:
    cats = db.rows("SELECT * FROM categories WHERE archived=0 OR id IN (SELECT DISTINCT category_id FROM transactions "
                   "WHERE substr(date,1,4)=?) ORDER BY sort, id", (str(y),))
    act: dict[int, list] = {c["id"]: [0] * 12 for c in cats}
    for r in db.rows(
        f"SELECT t.category_id cid, CAST(substr(t.date,6,2) AS INTEGER) m, {actual_expr()} v "
        f"FROM transactions t JOIN categories c ON c.id=t.category_id "
        f"WHERE {LIVE} AND substr(t.date,1,4)=? GROUP BY cid, m", (str(y),)):
        if r["cid"] in act:
            act[r["cid"]][r["m"] - 1] = r["v"] or 0
    today = date.today()
    last = 11 if y < today.year else (today.month - 1 if y == today.year else -1)
    plans = [plan_for(f"{y}-{m:02d}") for m in range(1, 13)]
    lines = []
    for c in cats:
        line = {
            "id": c["id"], "name": c["name"], "bucket": c["bucket"], "kind": c["kind"], "archived": c["archived"],
            "plan": [plans[m].get(c["id"], 0) for m in range(12)],
            "act": [act[c["id"]][m] if m <= last else None for m in range(12)],
            "rollover_from": c["rollover_from"], "avail": None,
        }
        if c["rollover_from"]:
            line["avail"] = _rollover(c, y, line["plan"], act[c["id"]], last)
        lines.append(line)
    unsorted = db.rows(
        "SELECT CAST(substr(date,6,2) AS INTEGER) m, COUNT(*) n, SUM(status='needs_you') ask, SUM(status='suggested') sug "
        "FROM transactions t WHERE is_split=0 AND hidden=0 AND status IN ('needs_you','suggested') AND substr(date,1,4)=? GROUP BY m",
        (str(y),))
    return {"year": y, "last": last, "lines": lines, "open": {r["m"] - 1: {"ask": r["ask"], "sug": r["sug"]} for r in unsorted}}


def _rollover(c: dict, y: int, plan: list[int], act: list[int], last: int) -> list:
    """What's available each month for a rollover category: this month's plan plus everything
    planned-but-unspent (or minus anything overspent) since the month rollover was turned on."""
    sy, sm = (int(x) for x in c["rollover_from"].split("-"))
    carry = 0
    if sy < y:                           # balance brought in from earlier years
        spent = db.one(
            f"SELECT COALESCE({actual_expr()},0) v FROM transactions t JOIN categories c ON c.id=t.category_id "
            f"WHERE {LIVE} AND t.category_id=? AND t.date>=? AND t.date<?", (c["id"], f"{sy}-{sm:02d}-01", f"{y}-01-01"))["v"]
        yy, mm, planned = sy, sm, 0
        while yy < y:
            planned += plan_for(f"{yy}-{mm:02d}").get(c["id"], 0)
            yy, mm = (yy + 1, 1) if mm == 12 else (yy, mm + 1)
        carry = planned - spent
    out = []
    for m in range(12):
        if (y, m + 1) < (sy, sm):
            out.append(None)
            continue
        out.append(carry + plan[m])
        carry += plan[m] - (act[m] if m <= last else 0)   # months still ahead: assume the plan, nothing spent yet
    return out


def report(start: str, end: str, kind: str = "spending", group: str = "category") -> dict:
    """Totals between two dates, grouped by category / merchant / account / bucket / tag, with a month-by-month breakdown."""
    buckets = {"spending": ("ess", "non"), "income": ("income",), "goals": ("goal",)}.get(kind, ("ess", "non"))
    col = {"category": ("c.id", "c.name"), "merchant": ("t.mkey", "MAX(t.merchant)"), "account": ("a.id", "a.name"),
           "bucket": ("c.bucket", "c.bucket")}.get(group, ("c.id", "c.name"))
    marks = ",".join("?" * len(buckets))
    base = (f"FROM transactions t JOIN categories c ON c.id=t.category_id JOIN accounts a ON a.id=t.account_id "
            f"WHERE {LIVE} AND c.bucket IN ({marks}) AND t.date>=? AND t.date<=?")
    args = (*buckets, start, end)
    groups: dict = {}
    if group == "tag":
        for r in db.rows(f"SELECT t.tags, substr(t.date,1,7) ym, {actual_expr()} v, COUNT(*) n {base} GROUP BY t.tags, ym", args):
            for tag in [x.strip() for x in r["tags"].split(",") if x.strip()] or ["(no tag)"]:
                g = groups.setdefault(tag, {"key": tag, "name": tag, "total": 0, "n": 0, "months": {}})
                g["total"] += r["v"] or 0
                g["n"] += r["n"]
                g["months"][r["ym"]] = g["months"].get(r["ym"], 0) + (r["v"] or 0)
    else:
        for r in db.rows(f"SELECT {col[0]} k, {col[1]} name, substr(t.date,1,7) ym, {actual_expr()} v, COUNT(*) n {base} GROUP BY k, ym", args):
            g = groups.setdefault(r["k"], {"key": r["k"], "name": r["name"], "total": 0, "n": 0, "months": {}})
            g["name"] = r["name"]
            g["total"] += r["v"] or 0
            g["n"] += r["n"]
            g["months"][r["ym"]] = r["v"] or 0
    months = []
    y, m = int(start[:4]), int(start[5:7])
    while f"{y}-{m:02d}" <= end[:7]:
        months.append(f"{y}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    if group == "bucket":
        names = {b["key"]: b["name"] for b in db.BUCKETS}
        for g in groups.values():
            g["name"] = names.get(g["key"], g["key"])
    out = sorted(groups.values(), key=lambda g: -g["total"])
    biggest = db.rows(f"SELECT t.id, t.date, t.merchant, t.amount_cents, c.name category {base} "
                      f"ORDER BY abs(t.amount_cents) DESC LIMIT 10", args)
    return {"months": months, "groups": out, "total": sum(g["total"] for g in out), "biggest": biggest}


def month_end(y: int, m: int) -> str:
    return date(y, m, calendar.monthrange(y, m)[1]).isoformat()


def networth(months: int = 12) -> dict:
    accounts = db.rows("SELECT * FROM accounts WHERE hidden=0 ORDER BY grp, id")
    today = date.today()
    ends: list[tuple[str, str]] = []
    y, m = today.year, today.month
    for _ in range(months):
        ends.append((f"{y}-{m:02d}", min(month_end(y, m), today.isoformat())))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    ends.reverse()
    series = []
    for a in accounts:
        known = db.rows("SELECT day, balance_cents FROM balances WHERE account_id=? ORDER BY day", (a["id"],))
        first_known = known[0]["day"] if known else None
        first_tx = db.one("SELECT MIN(date) d FROM transactions WHERE account_id=?", (a["id"],))["d"]
        vals = []
        for _label, end in ends:
            prior = [k for k in known if k["day"] <= end]
            if prior:
                vals.append(prior[-1]["balance_cents"])
            elif a["grp"] in ("cash", "credit") and first_tx and first_known and end >= first_tx:
                # Rebuild history from transactions: balance then = first known balance - everything since.
                since = db.one(
                    "SELECT COALESCE(SUM(amount_cents),0) s FROM transactions WHERE account_id=? AND date>? AND date<=? "
                    "AND is_split=0 AND pending=0", (a["id"], end, first_known))["s"]
                vals.append(known[0]["balance_cents"] - since)
            else:
                vals.append(None)
        series.append({"id": a["id"], "name": a["name"], "grp": a["grp"], "values": vals})
    totals = []
    for i in range(len(ends)):
        vs = [s["values"][i] for s in series if s["values"][i] is not None]
        assets = sum(v for v in vs if v > 0)
        debts = sum(v for v in vs if v < 0)
        totals.append({"assets": assets, "debts": debts, "net": assets + debts, "n": len(vs)})
    return {"months": [e[0] for e in ends], "accounts": series, "totals": totals}


def _cadence(gaps: list[int]) -> tuple[str, int] | None:
    if not gaps:
        return None
    g = statistics.median(gaps)
    for name, days, tol in (("weekly", 7, 2), ("every 2 weeks", 14, 3), ("monthly", 30, 6), ("quarterly", 91, 12), ("yearly", 365, 25)):
        if abs(g - days) <= tol and all(abs(x - days) <= tol * 2 for x in gaps):
            return name, days
    return None


def recurring() -> list[dict]:
    """Bills and subscriptions found by rhythm: same merchant, steady gap, similar amount."""
    out = []
    since = (date.today() - timedelta(days=800)).isoformat()
    groups: dict[str, list[dict]] = {}
    for t in db.rows(
        "SELECT t.mkey, t.merchant, t.date, t.amount_cents, t.category_id, c.name category, c.bucket FROM transactions t "
        "LEFT JOIN categories c ON c.id=t.category_id WHERE t.is_split=0 AND t.hidden=0 AND t.date>=? ORDER BY t.date", (since,)):
        if t["bucket"] == "transfer":
            continue
        groups.setdefault(t["mkey"] + ("+" if t["amount_cents"] > 0 else "-"), []).append(t)
    for txs in groups.values():
        if len(txs) < 2:
            continue
        days = [date.fromisoformat(t["date"]) for t in txs]
        gaps = [(b - a).days for a, b in zip(days, days[1:]) if (b - a).days > 2]
        cad = _cadence(gaps)
        if not cad or (cad[0] != "yearly" and len(txs) < 3):
            continue
        amts = [abs(t["amount_cents"]) for t in txs[-6:]]
        med = statistics.median(amts)
        if med == 0 or any(abs(a - med) > max(med * 0.35, 500) for a in amts):
            continue
        nxt = days[-1] + timedelta(days=cad[1] if cad[1] not in (30, 91, 365) else 0)
        if cad[1] in (30, 91, 365):
            months = {30: 1, 91: 3, 365: 12}[cad[1]]
            y, m = days[-1].year, days[-1].month + months
            y, m = y + (m - 1) // 12, (m - 1) % 12 + 1
            nxt = date(y, m, min(days[-1].day, calendar.monthrange(y, m)[1]))
        if (date.today() - nxt).days > cad[1] * 1.5 + 10:
            continue                     # it stopped
        last = txs[-1]
        out.append({
            "mkey": last["mkey"], "merchant": last["merchant"], "cadence": cad[0], "income": last["amount_cents"] > 0,
            "amount_cents": int(med), "last_amount_cents": abs(last["amount_cents"]), "last": last["date"],
            "next": nxt.isoformat(), "category": last["category"], "category_id": last["category_id"], "count": len(txs),
            "changed": abs(abs(last["amount_cents"]) - med) > max(med * 0.05, 100),
            "per_year_cents": int(med * {7: 52, 14: 26, 30: 12, 91: 4, 365: 1}[cad[1]]),
        })
    out.sort(key=lambda r: r["next"])
    return out


def pair_transfers(con) -> int:
    """Match an outflow in one of your accounts with the same-sized inflow in another within 3 days.

    The receiving side becomes a transfer so the money isn't counted twice. The sending side is left to your
    history (it might be 'Emergency fund' or 'Car loan'), falling back to a transfer / card payment.
    """
    xfer = con.execute("SELECT id FROM categories WHERE name='Transfer between my accounts'").fetchone()
    card = con.execute("SELECT id FROM categories WHERE name='Credit card payment'").fetchone()
    if not xfer:
        return 0
    open_in = con.execute(
        "SELECT t.id, t.account_id, t.date, t.amount_cents, a.grp, a.name FROM transactions t JOIN accounts a ON a.id=t.account_id "
        "WHERE t.amount_cents>0 AND t.is_split=0 AND t.parent_id IS NULL AND t.reason NOT LIKE 'Other half%' "
        # never override an inflow your own history already sorted (a paycheck that happens to equal a bill)
        "AND (t.status IN ('needs_you','suggested') OR (t.status='auto' AND t.reason LIKE 'Payment received on the card%'))"
    ).fetchall()
    n = 0
    used: set[int] = set()
    for i in open_in:
        d = date.fromisoformat(i["date"])
        lo, hi = (d - timedelta(days=4)).isoformat(), (d + timedelta(days=3)).isoformat()
        cands = con.execute(
            "SELECT t.id, t.status, t.category_id, t.date, a.name, a.grp FROM transactions t JOIN accounts a ON a.id=t.account_id "
            "WHERE t.amount_cents=? AND t.account_id<>? AND t.date BETWEEN ? AND ? AND t.is_split=0 AND t.parent_id IS NULL",
            (-i["amount_cents"], i["account_id"], lo, hi)).fetchall()
        cands = [c for c in cands if c["id"] not in used]
        if not cands:
            continue
        o = min(cands, key=lambda c: abs((date.fromisoformat(c["date"]) - d).days))
        used.add(o["id"])
        is_card = i["grp"] == "credit"
        con.execute("UPDATE transactions SET category_id=?, status='auto', reason=? WHERE id=?",
                    ((card["id"] if is_card and card else xfer["id"]),
                     f"Other half of the move from {o['name']}. Not counted twice.", i["id"]))
        if o["status"] in ("needs_you", "suggested"):
            # only fill in the sending side when your history had no confident answer
            cid = card["id"] if is_card and card else (o["category_id"] or xfer["id"])
            st = "auto" if is_card else "suggested"
            why = (f"Card payment to {i['name']}. Not counted as spending twice." if is_card
                   else f"Went to {i['name']}. A transfer, or one of your goals?")
            con.execute("UPDATE transactions SET category_id=?, status=?, reason=? WHERE id=?", (cid, st, why, o["id"]))
        n += 1
    return n


def after_ingest() -> None:
    with db.tx() as con:
        pair_transfers(con)


def merchants(month: str | None = None) -> list[dict]:
    where, args = (" AND substr(t.date,1,7)=?", (month,)) if month else ("", ())
    return db.rows(
        f"SELECT t.mkey, MAX(t.merchant) merchant, COUNT(*) n, -SUM(t.amount_cents) spent FROM transactions t "
        f"JOIN categories c ON c.id=t.category_id WHERE {LIVE} AND c.bucket IN ('ess','non'){where} "
        f"GROUP BY t.mkey ORDER BY spent DESC LIMIT 25", args)


__all__ = ["year", "networth", "recurring", "pair_transfers", "after_ingest", "plan_for", "merchants", "learn"]
