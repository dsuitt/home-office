"""CSV import (Monarch's export or any bank CSV), CSV export, and demo data."""
from __future__ import annotations

import csv
import hashlib
import io
import random
from datetime import date, datetime, timedelta

from . import db, learn, reports


def _pick(row: dict, *names: str) -> str:
    low = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
    for n in names:
        if low.get(n):
            return low[n]
    return ""


def _date(s: str) -> str | None:
    for f in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d", "%d-%b-%Y", "%b %d, %Y"):
        try:
            return datetime.strptime(s.strip(), f).date().isoformat()
        except ValueError:
            pass
    return None


def _money(s: str) -> int | None:
    s = s.replace("$", "").replace(",", "").strip()
    neg = s.startswith("(") and s.endswith(")")
    try:
        return int(round(float(s.strip("()")) * 100)) * (-1 if neg else 1)
    except ValueError:
        return None


def import_csv(text: str, default_account: str = "Imported", who: str = "") -> dict:
    """Import transactions. Rows that already carry a category are treated as your past decisions,
    which is how a Monarch export teaches the categorizer your habits on day one."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    added = skipped = taught = 0
    new_cats: list[str] = []
    with db.tx() as con:
        for row in reader:
            d = _date(_pick(row, "date", "posted date", "transaction date", "posting date"))
            amt = _money(_pick(row, "amount"))
            if amt is None:
                debit, credit = _money(_pick(row, "debit", "withdrawal")), _money(_pick(row, "credit", "deposit"))
                amt = (credit or 0) - abs(debit or 0) if (debit is not None or credit is not None) else None
            if not d or amt is None:
                skipped += 1
                continue
            name = _pick(row, "merchant", "name", "payee", "description")
            raw = _pick(row, "original statement", "original description", "description", "memo") or name
            acct_name = _pick(row, "account", "account name") or default_account
            cat_name = _pick(row, "category")
            ext = "csv:" + hashlib.sha256(f"{acct_name}|{d}|{amt}|{raw}".encode()).hexdigest()[:24]
            # the same line twice in one file is two real purchases: number them
            n = 0
            while con.execute("SELECT 1 FROM transactions WHERE ext_id=? AND source='csvnew'", (f"{ext}:{n}",)).fetchone():
                n += 1
            ext = f"{ext}:{n}"
            if con.execute("SELECT 1 FROM transactions WHERE ext_id=?", (ext,)).fetchone():
                skipped += 1
                continue
            a = con.execute("SELECT id FROM accounts WHERE name=?", (acct_name,)).fetchone()
            aid = a["id"] if a else con.execute(
                "INSERT INTO accounts(name, grp, subtype, manual) VALUES(?, 'cash', 'import', 1)", (acct_name,)).lastrowid
            cid = None
            if cat_name:
                c = con.execute("SELECT id FROM categories WHERE name=? COLLATE NOCASE", (cat_name,)).fetchone()
                if c:
                    cid = c["id"]
                else:
                    income = amt > 0
                    xfer = "transfer" in cat_name.lower() or "credit card payment" in cat_name.lower()
                    cid = con.execute(
                        "INSERT INTO categories(name,bucket,kind,sort) VALUES(?,?,?,900)",
                        (cat_name, "transfer" if xfer else "income" if income else "non",
                         "transfer" if xfer else "income" if income else "expense")).lastrowid
                    new_cats.append(cat_name)
            mkey = learn.merchant_key(raw)
            cur = con.execute(
                "INSERT INTO transactions(account_id, ext_id, date, raw, merchant, mkey, amount_cents, category_id, status, "
                "reason, note, tags, source) VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'csvnew')",
                (aid, ext, d, raw, name or learn.pretty(mkey), mkey, amt, cid, "done" if cid else "needs_you",
                 "Imported with your category." if cid else "", _pick(row, "notes", "note"), _pick(row, "tags")))
            if cid:
                learn.record(con, mkey, cid, amt, aid, who)
                alt = learn.merchant_key(name) if name else mkey
                if alt != mkey:          # Plaid may report either the statement text or the clean name
                    learn.record(con, alt, cid, amt, aid, who)
                taught += 1
            else:
                learn.apply_guess(con, cur.lastrowid)
            added += 1
        con.execute("UPDATE transactions SET source='csv' WHERE source='csvnew'")
        learn.resort_open(con)
        reports.pair_transfers(con)
    return {"added": added, "skipped": skipped, "taught": taught, "new_categories": sorted(set(new_cats))}


def export_csv() -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Date", "Merchant", "Category", "Account", "Original Statement", "Notes", "Amount", "Tags", "Status"])
    for t in db.rows(
        "SELECT t.*, c.name category, a.name account FROM transactions t LEFT JOIN categories c ON c.id=t.category_id "
        "JOIN accounts a ON a.id=t.account_id WHERE t.is_split=0 ORDER BY t.date DESC, t.id DESC"):
        w.writerow([t["date"], t["merchant"], t["category"] or "", t["account"], t["raw"], t["note"],
                    f"{t['amount_cents'] / 100:.2f}", t["tags"], t["status"]])
    return out.getvalue()


# ---------------------------------------------------------------------------- demo data

def seed_demo() -> None:
    """Made-up household so the app can be clicked through before any bank is linked."""
    if db.one("SELECT 1 FROM accounts LIMIT 1"):
        return
    rnd = random.Random(11)
    today = date.today()
    cat = {c["name"]: c["id"] for c in db.rows("SELECT id,name FROM categories")}
    with db.tx() as con:
        def acct(name, grp, bal, purpose="", sub=""):
            return con.execute(
                "INSERT INTO accounts(name,grp,subtype,purpose,balance_cents,manual,updated_at) VALUES(?,?,?,?,?,1,?)",
                (name, grp, sub or "demo", purpose, bal, today.isoformat())).lastrowid
        chk = acct("Home Checking", "cash", 624000, "Bills & everyday spending")
        sav = acct("Ally Savings", "cash", 2370000, "Emergency fund + trip + car fund")
        amex = acct("Amex Gold", "credit", -162000, "Everyday spending")
        acct("Chase Freedom", "credit", 0, "Backup card")
        mort = acct("Mortgage", "loan", -31240000, "6.1% · 2051")
        carl = acct("Car loan", "loan", -1430000, "4.9% · 2029")
        stl = acct("Student loan", "loan", -980000, "5.5% · 2031")
        roth = acct("Roth IRA (you)", "investment", 4120000, "Retirement")
        roth2 = acct("Roth IRA (her)", "investment", 2280000, "Retirement")
        k401 = acct("401(k)", "investment", 8840000, "Retirement")
        brk = acct("Brokerage", "investment", 3690000, "Long-term")
        house = acct("Home (estimated)", "property", 46300000, "Estimate you can edit")
        car = acct("Car (estimated)", "property", 1850000, "Estimate you can edit")

        # month-end balance history, walking back from today
        drift = {chk: (0, 30000), sav: (165000, 20000), amex: (0, 45000), mort: (52000, 0), carl: (42000, 0), stl: (47000, 0),
                 roth: (58000, 60000), roth2: (58000, 40000), k401: (140000, 150000), brk: (130000, 90000), house: (70000, 0), car: (-25000, 0)}
        for aid, (step, noise) in drift.items():
            bal = con.execute("SELECT balance_cents FROM accounts WHERE id=?", (aid,)).fetchone()[0]
            con.execute("INSERT INTO balances VALUES(?,?,?)", (aid, today.isoformat(), bal))
            y, m = today.year, today.month
            for _ in range(13):
                m -= 1
                if m == 0:
                    y, m = y - 1, 12
                bal = bal - step + rnd.randint(-noise, noise)
                con.execute("INSERT OR REPLACE INTO balances VALUES(?,?,?)", (aid, reports.month_end(y, m), bal))

        # name, raw description, account, category, typical dollars, spread, times per month, day (0 = any)
        merchants = [
            ("Payroll", "PAYROLL DEPOSIT ACME CORP", chk, "Paycheck", 3700, 0, 2, 0),
            ("Studio owner's pay", "TRANSFER FROM STUDIO CHK", chk, "Studio owner's pay", 4500, 0, 1, 1),
            ("Mortgage", "MORTGAGE AUTOPAY WELLS", chk, "Mortgage", -2400, 0, 1, 1),
            ("Duke Energy", "DUKE ENERGY BILL PAY", chk, "Utilities", -185, .25, 1, 6),
            ("City Water", "CITY OF RIVERSIDE WATER", chk, "Utilities", -72, .2, 1, 12),
            ("Trader Joe's", "TRADER JOE'S #552 RIVERSIDE CA", amex, "Groceries", -105, .35, 4, 0),
            ("Costco", "COSTCO WHSE #0478", amex, "Groceries", -210, .3, 2, 0),
            ("State Farm", "STATE FARM INSURANCE", chk, "Insurance", -280, 0, 1, 9),
            ("Shell", "SHELL OIL 57444121", amex, "Car & gas", -58, .25, 4, 0),
            ("Verizon", "VERIZON WIRELESS PAYMENTS", amex, "Phone", -95, 0, 1, 17),
            ("CVS", "CVS/PHARMACY #08821", amex, "Health", -34, .6, 2, 0),
            ("Target", "TARGET 00012345", amex, "Household", -84, .5, 3, 0),
            ("Netflix", "NETFLIX.COM", amex, "Subscriptions", -22.99, 0, 1, 4),
            ("Spotify", "SPOTIFY USA", amex, "Subscriptions", -16.99, 0, 1, 8),
            ("Olive & Vine", "TST* OLIVE & VINE", amex, "Eating out", -92, .35, 2, 0),
            ("Chipotle", "CHIPOTLE 2291", amex, "Eating out", -31, .3, 3, 0),
            ("Blue Bottle Coffee", "SQ *BLUE BOTTLE COFFEE", amex, "Coffee", -11, .4, 5, 0),
            ("Nordstrom", "NORDSTROM #0341", amex, "Clothes", -120, .6, 1, 0),
            ("Sephora", "SEPHORA #1182", amex, "Beauty & self-care", -64, .4, 1, 0),
            ("AMC Theatres", "AMC 0562 ONLINE", amex, "Fun & activities", -42, .5, 2, 0),
            ("Etsy", "ETSY.COM", amex, "Gifts", -38, .6, 1, 0),
            ("Honda Financial", "HONDA FINANCIAL PMT", chk, "Car loan", -485, 0, 1, 25),
            ("Nelnet", "NELNET STUDENT LOAN", chk, "Student loan", -534, 0, 1, 20),
            ("Vanguard", "VANGUARD BUY", chk, "Brokerage", -1300, 0, 1, 20),
            ("Vanguard Roth", "VANGUARD ROTH CONTRIB", chk, "Roth IRAs", -1166, 0, 1, 21),
        ]
        ext = 0

        def add(d, aid, raw, name, dollars, cname, status="done", reason=""):
            nonlocal ext
            ext += 1
            mkey = learn.merchant_key(raw)
            cents = int(round(dollars * 100))
            cid = cat.get(cname) if cname else None
            con.execute(
                "INSERT INTO transactions(account_id, ext_id, date, raw, merchant, mkey, amount_cents, category_id, status, reason, source) "
                "VALUES(?,?,?,?,?,?,?,?,?,?, 'demo')",
                (aid, f"demo:{ext}", d.isoformat(), raw, name, mkey, cents, cid, status, reason))
            if status == "done" and cid:
                learn.record(con, mkey, cid, cents, aid, "demo")
            return con.execute("SELECT last_insert_rowid()").fetchone()[0]

        start = date(today.year - 1, today.month, 1)
        d0 = start
        while d0 <= today:
            dim = reports.calendar.monthrange(d0.year, d0.month)[1]
            this_month = (d0.year, d0.month) == (today.year, today.month)
            for name, raw, aid, cname, dollars, spread, times, day in merchants:
                for k in range(times):
                    dd = day if day else rnd.randint(1, dim)
                    if name == "Payroll":
                        dd = 1 if k == 0 else 15
                    d = date(d0.year, d0.month, min(dd, dim))
                    if d > today:
                        continue
                    amt = round(dollars * (1 + rnd.uniform(-spread, spread)), 2)
                    if this_month:       # leave the current month for the categorizer to sort, like a fresh sync
                        add(d, aid, raw, name, amt, None, "needs_you")
                    else:
                        add(d, aid, raw, name, amt, cname)
            # savings moves and the card payment, both sides
            for label, raw_out, dollars, cname, dest in (("Ally Savings", "TRANSFER TO ALLY SAVINGS", 1000, "Emergency fund", sav),
                                                          ("Ally Savings", "TRANSFER TO ALLY TRIP FUND", 650, "Trip fund", sav),
                                                          ("Amex", "AMEX EPAYMENT ACH PMT", 1640, "Credit card payment", amex)):
                d = date(d0.year, d0.month, min(22 if dest == amex else 14, dim))
                if d <= today:
                    st = "needs_you" if this_month else "done"
                    add(d, chk, raw_out, label, -dollars, None if this_month else cname, st)
                    add(d, dest, "ONLINE PAYMENT - THANK YOU" if dest == amex else "TRANSFER FROM CHECKING", label, dollars,
                        None if this_month else ("Credit card payment" if dest == amex else "Transfer between my accounts"), st)
            d0 = date(d0.year + (d0.month == 12), d0.month % 12 + 1, 1)

        # a few things the categorizer has never seen, and one mixed merchant
        for back, aid, raw, name, dollars in ((1, amex, "VENMO *K LEE", "Venmo", -45.00), (2, amex, "AMAZON MKTPLACE PMTS", "Amazon", -146.89),
                                              (3, amex, "SQ *FARMHOUSE BAKERY", "Farmhouse Bakery", -18.50), (4, amex, "DELTA AIR 0062341", "Delta", -412.60)):
            add(today - timedelta(days=min(back, today.day - 1)), aid, raw, name, dollars, None, "needs_you")
        for i, cname in enumerate(["Household", "Gifts", "Household", "Clothes", "Household"]):
            add(today - timedelta(days=40 + 30 * i), amex, "AMAZON MKTPLACE PMTS", "Amazon", -rnd.randint(20, 140), cname)

        for r in con.execute("SELECT id FROM transactions WHERE status<>'done'").fetchall():
            learn.apply_guess(con, r["id"])
        reports.pair_transfers(con)

        month0 = f"{start.year}-{start.month:02d}"
        plan = {"Paycheck": 7400, "Studio owner's pay": 4500, "Mortgage": 2400, "Utilities": 320, "Groceries": 900, "Insurance": 280,
                "Car & gas": 450, "Phone": 95, "Health": 200, "Household": 150, "Subscriptions": 90, "Eating out": 350, "Coffee": 60,
                "Clothes": 150, "Beauty & self-care": 120, "Fun & activities": 150, "Travel": 300, "Gifts": 150, "Misc": 100,
                "Emergency fund": 1000, "Trip fund": 650, "Car fund": 500, "Roth IRAs": 1166, "Brokerage": 1300, "Car loan": 485, "Student loan": 534}
        for n, v in plan.items():
            con.execute("INSERT INTO budgets VALUES(?,?,?)", (month0, cat[n], v * 100))
        for i, (n, t, s, mth, c) in enumerate([("Emergency fund", 18000, 14200, 1000, "Emergency fund"), ("Italy trip, June", 6000, 3900, 650, "Trip fund"),
                                               ("New car fund", 10000, 2600, 500, "Car fund")]):
            con.execute("INSERT INTO goals(name,target_cents,saved_cents,monthly_cents,category_id,sort) VALUES(?,?,?,?,?,?)",
                        (n, t * 100, s * 100, mth * 100, cat[c], i))
        for n, mth, v in [("Car registration", 1, 165), ("Anniversary dinner", 2, 200), ("Amazon Prime", 3, 139), ("Tax filing", 4, 0),
                          ("Mother's Day", 5, 120), ("Italy trip", 6, 4200), ("Car insurance (6 mo)", 7, 840), ("Costco membership", 8, 65),
                          ("Back-to-school", 9, 150), ("Halloween", 10, 80), ("Holiday travel", 11, 600), ("Thanksgiving", 11, 150),
                          ("Holiday gifts", 12, 800), ("Car insurance (6 mo)", 12, 840)]:
            con.execute("INSERT INTO bills(name,month,amount_cents) VALUES(?,?,?)", (n, mth, v * 100))
        for aid, rowsh in ((brk, [("Vanguard Total Stock Market ETF", "VTI", 92.4, 2460000, 1980000), ("Vanguard Total Intl Stock ETF", "VXUS", 140, 880000, 810000),
                                  ("Apple Inc.", "AAPL", 15, 350000, 210000)]),
                           (roth, [("Vanguard Target Retirement 2055", "VFFVX", 720.5, 4120000, 3350000)]),
                           (roth2, [("Vanguard Target Retirement 2055", "VFFVX", 398.7, 2280000, 1990000)]),
                           (k401, [("Fidelity 500 Index", "FXAIX", 455.2, 8840000, 6400000)])):
            for n, tk, q, v, c in rowsh:
                con.execute("INSERT INTO holdings(account_id,name,ticker,quantity,value_cents,cost_cents) VALUES(?,?,?,?,?,?)", (aid, n, tk, q, v, c))
    db.set_setting("demo", "1")
