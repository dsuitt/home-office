import os

os.environ["MONEY_DB"] = ":memory:"
os.environ["SECRET_KEY"] = "test-secret-long-enough"
os.environ["ALLOW_NO_AUTH"] = "1"
os.environ["PLAID_CLIENT_ID"] = "cid"
os.environ["PLAID_SECRET"] = "sec"

import httpx
import pytest
from fastapi.testclient import TestClient

from money import db, ingest, learn, plaid, reports
from money.main import app

H = {"x-requested-with": "money"}


@pytest.fixture(autouse=True)
def fresh():
    db.reset(":memory:")
    yield


@pytest.fixture
def api():
    return TestClient(app, headers=H)


def cat(name):
    return db.one("SELECT id FROM categories WHERE name=?", (name,))["id"]


def add_tx(raw, dollars, day="2026-10-05", acct=None):
    with db.tx() as con:
        if acct is None:
            a = con.execute("SELECT id FROM accounts LIMIT 1").fetchone()
            acct = a["id"] if a else con.execute("INSERT INTO accounts(name) VALUES('Checking')").lastrowid
        mkey = learn.merchant_key(raw)
        cur = con.execute("INSERT INTO transactions(account_id,date,raw,merchant,mkey,amount_cents) VALUES(?,?,?,?,?,?)",
                          (acct, day, raw, learn.pretty(mkey), mkey, int(round(dollars * 100))))
        learn.apply_guess(con, cur.lastrowid)
        return cur.lastrowid


def tx(i):
    return db.one("SELECT * FROM transactions WHERE id=?", (i,))


# ---------------------------------------------------------------- merchant keys
@pytest.mark.parametrize("a,b", [
    ("SQ *BLUE BOTTLE COFFEE #1123 OAKLAND CA", "SQ *BLUE BOTTLE COFFEE"),
    ("TRADER JOE'S #552 RIVERSIDE CA", "TRADER JOE'S #101"),
    ("TARGET 00012345", "TARGET 00099881"),
    ("SHELL OIL 57444121", "SHELL OIL 5741"),
    ("NETFLIX.COM", "Netflix"),
    ("TST* OLIVE & VINE", "OLIVE & VINE"),
])
def test_merchant_key_is_stable(a, b):
    assert learn.merchant_key(a) == learn.merchant_key(b)


def test_merchant_keys_stay_distinct():
    assert learn.merchant_key("SHELL OIL 123456") != learn.merchant_key("SEPHORA #1182")


# ---------------------------------------------------------------- learning
def test_one_answer_teaches_the_next_one(api):
    first = add_tx("SQ *FARMHOUSE BAKERY #22", -18.5)
    assert tx(first)["status"] == "needs_you"
    r = api.patch(f"/api/transactions/{first}", json={"category_id": cat("Coffee")})
    assert r.status_code == 200
    nxt = add_tx("SQ *FARMHOUSE BAKERY #31 RIVERSIDE CA", -7.25, "2026-10-09")
    t = tx(nxt)
    assert (t["category_id"], t["status"]) == (cat("Coffee"), "auto")
    assert "Farmhouse Bakery" in t["reason"] and "Coffee" in t["reason"]


def test_answer_resorts_other_open_rows_from_same_merchant(api):
    a = add_tx("DELTA AIR 0062341", -400)
    b = add_tx("DELTA AIR 0099999", -210, "2026-10-08")
    out = api.patch(f"/api/transactions/{a}", json={"category_id": cat("Travel")}).json()
    assert out["resorted"] == 1
    assert tx(b)["category_id"] == cat("Travel") and tx(b)["status"] == "auto"


def test_correction_changes_future_behaviour(api):
    for d in ("01", "02", "03"):
        i = add_tx("TARGET 0001234", -50, f"2026-09-{d}")
        api.patch(f"/api/transactions/{i}", json={"category_id": cat("Household")})
    # she changes her mind twice in a row: the newest answers win
    for d in ("04", "05"):
        i = add_tx("TARGET 0001234", -50, f"2026-10-{d}")
        api.patch(f"/api/transactions/{i}", json={"category_id": cat("Groceries")})
    t = tx(add_tx("TARGET 0009999", -61, "2026-10-20"))
    assert t["category_id"] == cat("Groceries")


def test_single_flip_becomes_a_suggestion_not_an_auto_sort(api):
    for d in ("01", "02", "03", "04"):
        i = add_tx("AMAZON MKTPLACE PMTS", -30, f"2026-09-{d}")
        api.patch(f"/api/transactions/{i}", json={"category_id": cat("Household")})
    i = add_tx("AMAZON MKTPLACE PMTS", -30, "2026-10-01")
    api.patch(f"/api/transactions/{i}", json={"category_id": cat("Gifts")})
    t = tx(add_tx("AMAZON MKTPLACE PMTS", -45, "2026-10-20"))
    assert t["status"] == "suggested" and t["category_id"] is not None


def test_amount_separates_a_mixed_merchant(api):
    for d in ("01", "02", "03"):
        i = add_tx("VENMO *K LEE", -1200, f"2026-0{d[1]}-01")
        api.patch(f"/api/transactions/{i}", json={"category_id": cat("Mortgage")})
        j = add_tx("VENMO *K LEE", -25 - int(d), f"2026-0{d[1]}-15")
        api.patch(f"/api/transactions/{j}", json={"category_id": cat("Eating out")})
    t = tx(add_tx("VENMO *K LEE", -1200, "2026-10-01"))
    assert (t["category_id"], t["status"]) == (cat("Mortgage"), "auto")


def test_rule_beats_history(api):
    i = add_tx("COSTCO WHSE #0478", -200)
    api.patch(f"/api/transactions/{i}", json={"category_id": cat("Groceries")})
    api.post("/api/rules", json={"pattern": "costco gas", "category_id": cat("Car & gas")})
    assert tx(add_tx("COSTCO GAS #0478", -48))["category_id"] == cat("Car & gas")
    assert tx(add_tx("COSTCO WHSE #0478", -180))["category_id"] == cat("Groceries")


def test_bank_guess_is_only_a_suggestion():
    g = learn.guess("JOES DINER", "Joes Diner", "JOES DINER", -2000, None, "FOOD_AND_DRINK_RESTAURANT")
    assert g.status == "suggested" and g.category_id == cat("Eating out")


def test_auto_sorted_rows_do_not_teach(api):
    i = add_tx("NETFLIX.COM", -22.99)
    api.patch(f"/api/transactions/{i}", json={"category_id": cat("Subscriptions")})
    add_tx("NETFLIX.COM", -22.99, "2026-11-04")
    assert db.one("SELECT COUNT(*) n FROM decisions")["n"] == 1


def test_recategorize_all_and_forget(api):
    ids = [add_tx("CHIPOTLE 2291", -30, f"2026-10-0{d}") for d in (1, 2, 3)]
    api.patch(f"/api/transactions/{ids[0]}", json={"category_id": cat("Eating out")})
    api.post("/api/merchants/recategorize", json={"mkey": "CHIPOTLE", "category_id": cat("Misc"), "always": True})
    assert {tx(i)["category_id"] for i in ids} == {cat("Misc")}
    assert tx(add_tx("CHIPOTLE 9", -12))["category_id"] == cat("Misc")
    api.delete("/api/learned/CHIPOTLE")
    assert tx(add_tx("CHIPOTLE 10", -12))["status"] == "needs_you"


# ---------------------------------------------------------------- splits, budget, reports
def test_split_must_add_up_and_feeds_reports(api):
    i = add_tx("AMAZON MKTPLACE", -100, "2026-10-12")
    bad = api.post(f"/api/transactions/{i}/split", json={"parts": [{"category_id": cat("Gifts"), "amount_cents": -5000}]})
    assert bad.status_code == 400
    ok = api.post(f"/api/transactions/{i}/split", json={"parts": [{"category_id": cat("Gifts"), "amount_cents": -6000},
                                                                  {"category_id": cat("Household"), "amount_cents": -4000}]})
    assert ok.status_code == 200
    y = api.get("/api/year/2026").json()
    by = {l["name"]: l for l in y["lines"]}
    assert by["Gifts"]["act"][9] == 6000 and by["Household"]["act"][9] == 4000
    api.post(f"/api/transactions/{i}/split", json={"parts": []})
    assert tx(i)["is_split"] == 0


def test_budget_carries_forward_until_edited(api):
    api.put("/api/budget/2026-03", json={str(cat("Groceries")): 90000})
    api.put("/api/budget/2026-08", json={str(cat("Groceries")): 95000})
    g = next(l for l in api.get("/api/year/2026").json()["lines"] if l["name"] == "Groceries")
    assert g["plan"][1] == 0 and g["plan"][2] == 90000 and g["plan"][6] == 90000 and g["plan"][7] == 95000 and g["plan"][11] == 95000


def test_refund_nets_against_spending_and_hidden_is_left_out(api):
    a = add_tx("NORDSTROM #0341", -120, "2026-10-02")
    api.patch(f"/api/transactions/{a}", json={"category_id": cat("Clothes")})
    add_tx("NORDSTROM #0341", 40, "2026-10-06")
    h = add_tx("NORDSTROM #0341", -500, "2026-10-07")
    api.patch(f"/api/transactions/{h}", json={"hidden": True})
    c = next(l for l in api.get("/api/year/2026").json()["lines"] if l["name"] == "Clothes")
    assert c["act"][9] == 8000


def test_transfer_pairing_does_not_double_count(api):
    with db.tx() as con:
        chk = con.execute("INSERT INTO accounts(name,grp) VALUES('Checking','cash')").lastrowid
        card = con.execute("INSERT INTO accounts(name,grp) VALUES('Amex','credit')").lastrowid
        sav = con.execute("INSERT INTO accounts(name,grp) VALUES('Savings','cash')").lastrowid
    out_card = add_tx("AMEX EPAYMENT ACH PMT", -1640, "2026-10-22", chk)
    in_card = add_tx("ONLINE PAYMENT - THANK YOU", 1640, "2026-10-23", card)
    out_sav = add_tx("TRANSFER TO ALLY SAVINGS", -1000, "2026-10-14", chk)
    in_sav = add_tx("DEPOSIT FROM CHECKING", 1000, "2026-10-14", sav)
    reports.after_ingest()
    assert tx(in_card)["category_id"] == cat("Credit card payment") and tx(out_card)["category_id"] == cat("Credit card payment")
    assert tx(in_sav)["category_id"] == cat("Transfer between my accounts") and tx(in_sav)["status"] == "auto"
    # she says the outflow is her emergency fund; only that side counts toward the goal
    api.patch(f"/api/transactions/{out_sav}", json={"category_id": cat("Emergency fund")})
    by = {l["name"]: l for l in api.get("/api/year/2026").json()["lines"]}
    assert by["Emergency fund"]["act"][9] == 100000
    assert sum(l["act"][9] or 0 for l in by.values() if l["bucket"] in ("ess", "non", "income")) == 0


def test_recurring_detection():
    for m in range(4, 11):
        add_tx("NETFLIX.COM", -22.99, f"2026-{m:02d}-04")
        add_tx("TRADER JOE'S #552", -80 - m * 7, f"2026-{m:02d}-{m + 3:02d}")
    rec = reports.recurring()
    assert [r["mkey"] for r in rec if r["cadence"] == "monthly" and r["amount_cents"] == 2299] == ["NETFLIX"]


# ---------------------------------------------------------------- CSV import
MONARCH = """Date,Merchant,Category,Account,Original Statement,Notes,Amount,Tags
2026-09-03,Trader Joe's,Groceries,Amex Gold,TRADER JOE'S #552 RIVERSIDE CA,,-112.40,
2026-09-10,Trader Joe's,Groceries,Amex Gold,TRADER JOE'S #552 RIVERSIDE CA,,-98.10,
2026-09-12,Rover,Pets,Amex Gold,ROVER.COM 8881234,dog sitter,-60.00,trip
2026-09-12,Rover,Pets,Amex Gold,ROVER.COM 8881234,dog sitter,-60.00,trip
"""


def test_monarch_csv_teaches_and_is_idempotent(api):
    r = api.post("/api/import/csv", files={"file": ("m.csv", MONARCH, "text/csv")}).json()
    assert r["added"] == 4 and r["taught"] == 4 and r["new_categories"] == ["Pets"]
    again = api.post("/api/import/csv", files={"file": ("m.csv", MONARCH, "text/csv")}).json()
    assert again["added"] == 0 and again["skipped"] == 4
    t = tx(add_tx("TRADER JOE'S #101 CORONA CA", -75, "2026-10-01"))
    assert (t["category_id"], t["status"]) == (cat("Groceries"), "auto")
    assert tx(add_tx("ROVER.COM 4445556", -45, "2026-10-02"))["category_id"] == cat("Pets")


# ---------------------------------------------------------------- Plaid sync (mocked transport)
def plaid_mock(pages):
    calls = []

    def handler(req: httpx.Request):
        import json
        body = json.loads(req.content)
        calls.append((req.url.path, body))
        if req.url.path == "/item/public_token/exchange":
            return httpx.Response(200, json={"item_id": "item-1", "access_token": "access-secret"})
        if req.url.path == "/accounts/get":
            return httpx.Response(200, json={"accounts": [
                {"account_id": "acc-chk", "name": "Checking", "type": "depository", "subtype": "checking", "mask": "1111", "balances": {"current": 5000.5}},
                {"account_id": "acc-cc", "name": "Card", "type": "credit", "subtype": "credit card", "mask": "2222", "balances": {"current": 300}}]})
        if req.url.path == "/transactions/sync":
            return httpx.Response(200, json=pages.pop(0))
        if req.url.path == "/link/token/create":
            return httpx.Response(200, json={"link_token": "link-sandbox-abc"})
        return httpx.Response(400, json={"error_code": "ITEM_LOGIN_REQUIRED", "error_message": "login"})

    return httpx.Client(transport=httpx.MockTransport(handler)), calls


def ptx(i, amount, name, d="2026-10-05", acct="acc-cc", **kw):
    return {"transaction_id": i, "account_id": acct, "amount": amount, "date": d, "name": name, "pending": False, **kw}


def test_plaid_sync_signs_dedupes_and_keeps_answers(api):
    pages = [
        {"added": [ptx("p1", 12.5, "SQ *BLUE BOTTLE", pending=True), ptx("t2", -3700, "PAYROLL", acct="acc-chk",
                                                                          personal_finance_category={"primary": "INCOME", "detailed": "INCOME_WAGES"})],
         "modified": [], "removed": [], "next_cursor": "c1", "has_more": True},
        {"added": [], "modified": [], "removed": [], "next_cursor": "c2", "has_more": False},
    ]
    client, calls = plaid_mock(pages)
    item = plaid.exchange("public-x", "Test Bank", client)
    stored = db.one("SELECT access_token FROM items")["access_token"]
    assert "access-secret" not in stored and plaid.unseal(stored) == "access-secret"
    res = plaid.sync_item(item, client)
    assert res == {"item": item, "ok": True, "added": 2, "modified": 0, "removed": 0}
    card = db.one("SELECT * FROM accounts WHERE plaid_account_id='acc-cc'")
    assert card["balance_cents"] == -30000 and card["grp"] == "credit"
    coffee = db.one("SELECT * FROM transactions WHERE ext_id='p1'")
    assert coffee["amount_cents"] == -1250 and coffee["pending"] == 1
    pay = db.one("SELECT * FROM transactions WHERE ext_id='t2'")
    assert pay["amount_cents"] == 370000 and pay["status"] == "suggested" and pay["category_id"] == cat("Paycheck")
    api.patch(f"/api/transactions/{coffee['id']}", json={"category_id": cat("Coffee")})

    # the pending charge settles under a new id, with a tip added; her answer must survive
    pages.append({"added": [ptx("t1", 14.5, "SQ *BLUE BOTTLE", pending_transaction_id="p1")], "modified": [],
                  "removed": [{"transaction_id": "p1"}], "next_cursor": "c3", "has_more": False})
    plaid.sync_item(item, client)
    sync_calls = [b for p, b in calls if p == "/transactions/sync"]
    assert sync_calls[-1]["cursor"] == "c2"
    rows = db.rows("SELECT * FROM transactions WHERE mkey='BLUE BOTTLE'")
    assert len(rows) == 1 and rows[0]["ext_id"] == "t1" and rows[0]["amount_cents"] == -1450
    assert rows[0]["category_id"] == cat("Coffee") and rows[0]["status"] == "done" and rows[0]["pending"] == 0


def test_plaid_adopts_imported_history_instead_of_doubling(api):
    api.post("/api/import/csv", files={"file": ("m.csv", MONARCH, "text/csv")})
    client, _ = plaid_mock([{"added": [ptx("t9", 112.40, "TRADER JOE'S #552 RIVERSIDE CA", d="2026-09-04")], "modified": [], "removed": [],
                             "next_cursor": "c1", "has_more": False}])
    item = plaid.exchange("public-x", "Test Bank", client)
    plaid.sync_item(item, client)
    rows = db.rows("SELECT t.*, a.name acct FROM transactions t JOIN accounts a ON a.id=t.account_id WHERE amount_cents=-11240")
    assert len(rows) == 1 and rows[0]["ext_id"] == "t9" and rows[0]["acct"] == "Card" and rows[0]["category_id"] == cat("Groceries")


def test_plaid_login_required_marks_item(api):
    client, _ = plaid_mock([])
    item = plaid.exchange("public-x", "Test Bank", client)

    def broken(req):
        return httpx.Response(400, json={"error_code": "ITEM_LOGIN_REQUIRED", "error_message": "the login details changed"})

    res = plaid.sync_item(item, httpx.Client(transport=httpx.MockTransport(broken)))
    assert res["ok"] is False and db.one("SELECT status FROM items")["status"] == "needs_reauth"


# ---------------------------------------------------------------- sign-in and guards
def test_sign_in_required_when_users_are_set(monkeypatch):
    monkeypatch.setenv("APP_USERS", "dan:correct horse,sam:battery staple")
    c = TestClient(app)
    assert c.get("/api/boot").status_code == 401
    assert c.post("/api/login", json={"name": "dan", "password": "nope"}).status_code == 401
    assert c.post("/api/login", json={"name": "Dan", "password": "correct horse"}).status_code == 200
    assert c.get("/api/boot").json()["user"] == "dan"
    assert c.post("/api/rules", json={"pattern": "x", "category_id": 1}).status_code == 403   # no custom header: cross-site
    c.cookies.set("money_session", "dan|9999999999|forged")
    assert c.get("/api/boot").status_code == 401


def test_demo_seed_and_reports(api):
    ingest.seed_demo()
    boot = api.get("/api/boot").json()
    assert boot["demo"] and boot["counts"]["n"] > 300
    open_ = db.one("SELECT SUM(status='auto') a, SUM(status='needs_you') q, COUNT(*) n FROM transactions WHERE status<>'done'")
    assert open_["a"] / open_["n"] > 0.7          # history sorts most of a fresh month on its own
    nw = api.get("/api/networth").json()
    assert len(nw["months"]) == 12 and nw["totals"][-1]["net"] > 0
    assert any(r["mkey"] == "NETFLIX" for r in api.get("/api/bills").json()["recurring"])
    assert api.get("/api/export.csv").text.startswith("Date,Merchant")
    assert api.post("/api/demo/clear").status_code == 200 and api.get("/api/boot").json()["counts"]["n"] == 0


def test_no_sign_in_means_this_computer_only(monkeypatch):
    monkeypatch.delenv("ALLOW_NO_AUTH")
    assert TestClient(app, client=("203.0.113.9", 5000)).get("/api/boot").status_code == 401
    assert TestClient(app, client=("127.0.0.1", 5000)).get("/api/boot").status_code == 200


def test_wording_alone_never_auto_sorts_a_bill_as_a_card_payment():
    with db.tx() as con:
        chk = con.execute("INSERT INTO accounts(name,grp) VALUES('Checking','cash')").lastrowid
    t = tx(add_tx("MORTGAGE AUTOPAY WELLS", -2400, "2026-10-01", chk))
    assert t["status"] == "needs_you"
    t = tx(add_tx("CHASE CREDIT CRD AUTOPAY", -900, "2026-10-02", chk))
    assert t["status"] == "suggested" and t["category_id"] == cat("Credit card payment")


def test_pairing_leaves_a_paycheck_your_history_sorted(api):
    with db.tx() as con:
        chk = con.execute("INSERT INTO accounts(name,grp) VALUES('Checking','cash')").lastrowid
        other = con.execute("INSERT INTO accounts(name,grp) VALUES('Joint','cash')").lastrowid
    first = add_tx("PAYROLL ACME", 2400, "2026-09-15", chk)
    api.patch(f"/api/transactions/{first}", json={"category_id": cat("Paycheck")})
    pay = add_tx("PAYROLL ACME", 2400, "2026-10-01", chk)
    add_tx("MORTGAGE AUTOPAY WELLS", -2400, "2026-10-01", other)
    reports.after_ingest()
    assert tx(pay)["category_id"] == cat("Paycheck")
