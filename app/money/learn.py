"""The categorizer. It learns from what you actually do.

Order of precedence for a new transaction:
  1. A rule you wrote (always wins).
  2. Transfer detection (card payments, moves between your own accounts).
  3. Your own history for this merchant (the `decisions` table):
       - consistent history  -> sorted automatically, with the reason shown
       - mixed history       -> suggested, you confirm
       - history that depends on the amount (e.g. Amazon, Venmo) is matched by amount first
  4. Your history for a similar merchant (same leading words).
  5. The bank's own category guess -> only ever a suggestion.
  6. Otherwise: needs you.

Every answer you give is a new decision, so one correction is enough to change what happens
next time: the newest decisions outweigh older ones.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import db

# Noise the banks add around merchant names.
_PREFIX = re.compile(
    r"^(?:(?:SQ|TST|PY|SP|PP|CKE|IN|FS|BT|DD|WPY|GDP|EB|ACT|ZSK|CHK|PAYPAL|GOOGLE|APL\s?PAY|AMZ\s?PAY|POS|DEBIT|PURCHASE|"
    r"CHECKCARD|CHECK CARD|RECURRING|VISA|ACH|ONLINE|WEB|PREAUTHORIZED|PMT|EXT TRNSFR|DEBIT CARD|CARD)\b[\s*:\-]*)+",
    re.I,
)
_STATES = (
    "AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|"
    "OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC"
)
_TRAILING_STATE = re.compile(rf"\s+(?:[A-Z][A-Za-z.]+\s+)?(?:{_STATES})$")
_STOP = {"INC", "LLC", "CO", "CORP", "LTD", "THE", "COM", "WWW", "USA", "US", "STORE", "PAYMENT", "PURCHASE", "ONLINE"}


def merchant_key(raw: str) -> str:
    """Collapse a bank description to a stable key: 'SQ *BLUE BOTTLE #1123 OAKLAND CA' -> 'BLUE BOTTLE'."""
    s = raw.upper().strip()
    s = s.replace("’", "'")
    for _ in range(2):
        s = _PREFIX.sub("", s).strip()
    s = re.sub(r"https?://|WWW\.", " ", s)
    s = re.sub(r"\.(COM|NET|ORG|CO|IO)\b", " ", s)
    s = _TRAILING_STATE.sub("", s)
    s = re.sub(r"[#*]\s*[\w-]*\d[\w-]*", " ", s)   # store numbers / reference ids
    s = re.sub(r"\b[\w-]*\d{3,}[\w-]*\b", " ", s)  # any long digit run
    s = re.sub(r"\b\d{1,2}/\d{1,2}(/\d{2,4})?\b", " ", s)
    s = re.sub(r"[^A-Z&' ]+", " ", s)
    words = [w for w in s.split() if w not in _STOP and len(w) > 1 or w == "&"]
    key = " ".join(words[:4]).strip(" &'")
    return key or raw.upper().strip()[:40]


def pretty(key: str) -> str:
    small = {"AND", "OF", "THE", "&"}
    return " ".join(w.lower() if w in small and i else (w if len(w) <= 3 and w.isalpha() and w not in small else w.capitalize())
                    for i, w in enumerate(key.split()))


@dataclass
class Guess:
    category_id: int | None
    status: str          # auto | suggested | needs_you
    reason: str
    rename: str = ""
    hide: bool = False


_TRANSFER_WORDS = re.compile(r"\b(TRANSFER|XFER|TRNSFR|ZELLE TO SELF|ACH TRANSFER)\b", re.I)
_CARD_PAY = re.compile(r"(EPAYMENT|E-PAYMENT|AUTOPAY|CARD PAYMENT|PAYMENT THANK YOU|PAYMENT - THANK|CRCARDPMT|ONLINE PAYMENT|CREDIT CRD)", re.I)


def _cat_id(name: str) -> int | None:
    r = db.one("SELECT id FROM categories WHERE name=?", (name,))
    return r["id"] if r else None


def _cat_name(cid: int) -> str:
    r = db.one("SELECT name FROM categories WHERE id=?", (cid,))
    return r["name"] if r else "?"


def match_rule(raw: str, merchant: str, amount_cents: int, account_id: int | None) -> dict | None:
    hay = f"{raw} {merchant}".upper()
    best = None
    for r in db.rows("SELECT * FROM rules"):
        p = r["pattern"].upper().strip()
        if not p:
            continue
        ok = (p == raw.upper().strip() or p == merchant.upper().strip()) if r["match"] == "exact" else p in hay
        a = abs(amount_cents)
        if ok and r["min_cents"] is not None and a < r["min_cents"]:
            ok = False
        if ok and r["max_cents"] is not None and a > r["max_cents"]:
            ok = False
        if ok and r["account_id"] and r["account_id"] != account_id:
            ok = False
        if ok:
            # most specific wins: constrained rules first, then the longest pattern
            score = (int(r["min_cents"] is not None or r["max_cents"] is not None) + int(bool(r["account_id"])), len(p))
            if best is None or score > best[0]:
                best = (score, r)
    return best[1] if best else None


def _vote(decs: list[dict]) -> tuple[int, float, int, int]:
    """Recency-weighted vote over decisions (newest first). Returns (category, share, votes_for, total)."""
    weights: dict[int, float] = {}
    counts: dict[int, int] = {}
    for i, d in enumerate(decs[:12]):
        w = 0.75 ** i
        weights[d["category_id"]] = weights.get(d["category_id"], 0) + w
        counts[d["category_id"]] = counts.get(d["category_id"], 0) + 1
    top = max(weights, key=lambda c: weights[c])
    return top, weights[top] / sum(weights.values()), counts[top], min(len(decs), 12)


def from_history(mkey: str, amount_cents: int) -> Guess | None:
    decs = db.rows("SELECT category_id, amount_cents FROM decisions WHERE mkey=? ORDER BY id DESC LIMIT 40", (mkey,))
    if not decs:
        return None
    name = pretty(mkey)
    cats = {d["category_id"] for d in decs[:12]}
    if len(cats) > 1:
        # Mixed merchant. See whether the amount settles it (rent via Venmo vs. dinner via Venmo).
        a = abs(amount_cents)
        near = [d for d in decs if abs(abs(d["amount_cents"]) - a) <= max(100, a * 0.03)]
        if near:
            top, share, n, total = _vote(near)
            if share >= 0.8:
                st = "auto" if n >= 2 else "suggested"
                return Guess(top, st, f"{name} for about this amount has gone to {_cat_name(top)} {n} of {total} times.")
    top, share, n, total = _vote(decs)
    cname = _cat_name(top)
    if len(cats) == 1:
        times = "the last time" if total == 1 else f"all {total} times" if total < 12 else "every time"
        return Guess(top, "auto", f"You put {name} in {cname} {times}.")
    if share >= 0.7 and decs[0]["category_id"] == top and (len(decs) < 2 or decs[1]["category_id"] == top):
        return Guess(top, "auto", f"You usually put {name} in {cname} ({n} of the last {total}).")
    return Guess(top, "suggested", f"{name} is usually {cname} ({n} of the last {total}), but not always.")


def from_similar(mkey: str) -> Guess | None:
    words = mkey.split()
    if not words or len(words[0]) < 4:
        return None
    stem = " ".join(words[:2]) if len(words) > 1 else words[0]
    for prefix in dict.fromkeys([stem, words[0]]):
        decs = db.rows(
            "SELECT category_id, amount_cents, mkey FROM decisions WHERE mkey LIKE ? AND mkey<>? ORDER BY id DESC LIMIT 12",
            (prefix + "%", mkey),
        )
        if decs:
            top, share, n, total = _vote(decs)
            if share >= 0.7:
                return Guess(top, "suggested", f"Looks like {pretty(decs[0]['mkey'])}, which you put in {_cat_name(top)}.")
    return None


# Plaid personal_finance_category.primary -> our default category names. Suggestions only.
PLAID_MAP = {
    "INCOME": "Paycheck", "FOOD_AND_DRINK": "Eating out", "GENERAL_MERCHANDISE": "Household",
    "HOME_IMPROVEMENT": "Household", "MEDICAL": "Health", "PERSONAL_CARE": "Beauty & self-care",
    "ENTERTAINMENT": "Fun & activities", "TRAVEL": "Travel", "TRANSPORTATION": "Car & gas",
    "RENT_AND_UTILITIES": "Utilities", "GENERAL_SERVICES": "Misc", "GOVERNMENT_AND_NON_PROFIT": "Misc",
    "BANK_FEES": "Misc", "LOAN_PAYMENTS": "Credit card payment",
    "TRANSFER_IN": "Transfer between my accounts", "TRANSFER_OUT": "Transfer between my accounts",
}
PLAID_DETAILED = {
    "FOOD_AND_DRINK_GROCERIES": "Groceries", "FOOD_AND_DRINK_COFFEE": "Coffee",
    "GENERAL_MERCHANDISE_CLOTHING_AND_ACCESSORIES": "Clothes", "GENERAL_MERCHANDISE_GIFTS_AND_NOVELTIES": "Gifts",
    "TRANSPORTATION_GAS": "Car & gas", "RENT_AND_UTILITIES_TELEPHONE": "Phone",
    "LOAN_PAYMENTS_MORTGAGE_PAYMENT": "Mortgage", "LOAN_PAYMENTS_CAR_PAYMENT": "Car loan",
    "LOAN_PAYMENTS_STUDENT_LOAN_PAYMENT": "Student loan", "INCOME_INTEREST_EARNED": "Interest & dividends",
    "INCOME_DIVIDENDS": "Interest & dividends", "GENERAL_SERVICES_INSURANCE": "Insurance",
    "ENTERTAINMENT_TV_AND_MOVIES": "Subscriptions", "ENTERTAINMENT_MUSIC_AND_AUDIO": "Subscriptions",
}


def from_source(source_cat: str) -> Guess | None:
    if not source_cat:
        return None
    name = None
    if _cat_id(source_cat):                      # an import that already uses our category names
        name = source_cat
    else:
        key = source_cat.upper().replace(" ", "_")
        name = PLAID_DETAILED.get(key) or next((v for k, v in PLAID_MAP.items() if key.startswith(k)), None)
    cid = _cat_id(name) if name else None
    if cid:
        return Guess(cid, "suggested", f"The bank's guess: {name}. You haven't sorted this one before.")
    return None


def guess(raw: str, merchant: str, mkey: str, amount_cents: int, account_id: int | None = None,
          source_cat: str = "", account_grp: str = "") -> Guess:
    rule = match_rule(raw, merchant, amount_cents, account_id)
    if rule:
        return Guess(rule["category_id"], "auto", f"Your rule: “{rule['pattern']}”.", rule["rename_to"], bool(rule["hide"]))
    hist = from_history(mkey, amount_cents)
    if hist and hist.status == "auto":
        return hist
    # Transfers are structural, so they outrank a weak suggestion but not your consistent history.
    if _CARD_PAY.search(raw) and (account_grp == "credit" or amount_cents < 0):
        cid = _cat_id("Credit card payment")
        if cid:
            return Guess(cid, "auto", "Card payment. Not counted as spending twice.")
    if _TRANSFER_WORDS.search(raw) and not hist:
        cid = _cat_id("Transfer between my accounts")
        if cid:
            return Guess(cid, "suggested", "Looks like a move between your own accounts.")
    if hist:
        return hist
    return from_similar(mkey) or from_source(source_cat) or Guess(None, "needs_you", "New to me. What is it?")


def apply_guess(con, txid: int) -> None:
    t = con.execute(
        "SELECT t.*, a.grp FROM transactions t JOIN accounts a ON a.id=t.account_id WHERE t.id=?", (txid,)
    ).fetchone()
    g = guess(t["raw"], t["merchant"], t["mkey"], t["amount_cents"], t["account_id"], t["source_cat"], t["grp"])
    con.execute(
        "UPDATE transactions SET category_id=?, status=?, reason=?, merchant=CASE WHEN ?<>'' THEN ? ELSE merchant END, "
        "hidden=CASE WHEN ? THEN 1 ELSE hidden END WHERE id=?",
        (g.category_id, g.status, g.reason, g.rename, g.rename, int(g.hide), txid),
    )
    if g.reason.startswith("Your rule") and (rule := match_rule(t["raw"], t["merchant"], t["amount_cents"], t["account_id"])):
        con.execute("UPDATE rules SET hits=hits+1 WHERE id=?", (rule["id"],))


def record(con, mkey: str, category_id: int, amount_cents: int, account_id: int | None, who: str = "") -> None:
    con.execute(
        "INSERT INTO decisions(mkey,category_id,amount_cents,account_id,who) VALUES(?,?,?,?,?)",
        (mkey, category_id, amount_cents, account_id, who),
    )


def resort_open(con, mkey: str | None = None) -> int:
    """Re-run the categorizer over everything a person hasn't confirmed. Returns how many changed."""
    q = "SELECT id, category_id, status FROM transactions WHERE status<>'done' AND is_split=0 AND parent_id IS NULL"
    args: tuple = ()
    if mkey:
        q += " AND mkey=?"
        args = (mkey,)
    changed = 0
    for r in con.execute(q, args).fetchall():
        apply_guess(con, r["id"])
        n = con.execute("SELECT category_id, status FROM transactions WHERE id=?", (r["id"],)).fetchone()
        changed += int((n["category_id"], n["status"]) != (r["category_id"], r["status"]))
    return changed


def learned() -> list[dict]:
    """What the categorizer currently believes, per merchant, for the 'What I've learned' screen."""
    out = []
    for m in db.rows("SELECT mkey, COUNT(*) n, MAX(at) last FROM decisions GROUP BY mkey ORDER BY n DESC, mkey"):
        g = from_history(m["mkey"], 0)
        by = db.rows(
            "SELECT c.name, COUNT(*) n FROM decisions d JOIN categories c ON c.id=d.category_id WHERE d.mkey=? "
            "GROUP BY c.id ORDER BY n DESC", (m["mkey"],))
        out.append({
            "mkey": m["mkey"], "merchant": pretty(m["mkey"]), "times": m["n"], "last": m["last"],
            "category_id": g.category_id if g else None, "auto": bool(g and g.status == "auto"),
            "reason": g.reason if g else "", "breakdown": by,
        })
    return out
