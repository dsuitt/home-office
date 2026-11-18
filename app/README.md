# Home Money

A household money page for the two of us: Monarch's features, the dashboard tabs from `mockups/home-dashboard.html`, and a sorter that learns from what we actually pick.

## Try it in one minute (no bank needed)

```bash
cd app
make demo
```

Open http://localhost:8000. It starts with a made-up household. The current month arrives "fresh from the bank" so you can watch it sort itself from the earlier months' answers.

## What's in it

| Tab | What it does |
| --- | --- |
| Financial Snapshot | Every account by group, bills at a glance by month, what's due in the next 30 days |
| Categories & Goals | Zero-based monthly plan ("every dollar has a job"), editable in place, optional rollover per category (↻), savings goals with a ready date |
| Income & Expense Log | Every transaction. Search, filter, change category, split, notes, tags, hide, rename. On a phone it turns into cards, plus a one-at-a-time review |
| Annual Spending Plan | Plan / actual / difference for all 12 months |
| Month in Review | KPIs, planned vs. actual, spending summary, reflection notes (saved), one-tap plan adjustments |
| Net Worth | Month-by-month tracker for every account, plus investment holdings |
| Dashboard Summary | Year to date, income vs. spending, a cash-flow diagram (income → buckets → categories), top categories and merchants |
| Reports | Any date range, spending / income / goals, grouped by category, bucket, merchant, account or tag, with a month-by-month table and trend |
| Recurring | Bills and subscriptions found by their rhythm, with price-change flags and yearly cost |
| What I've Learned | Every merchant it knows, where it sends it and why; fix or forget any of them; rules |
| Settings | Bank connections (Plaid), CSV import/export, accounts, categories |

## How the sorting learns

Code: `money/learn.py`. For each new transaction, in order:

1. **A rule you wrote** always wins.
2. **Your own history for that merchant.** Every category you pick or confirm is stored as a decision. If you've been consistent, it sorts the next one on its own and says why ("You put Trader Joe's in Groceries all 9 times"). Recent answers weigh more, so two corrections in a row flip it.
3. **Mixed merchants** (Amazon, Venmo): it checks whether the *amount* settles it. If not, it suggests the most likely category and waits for you instead of guessing.
4. **Transfers and card payments** are matched across your accounts so nothing is counted twice.
5. **The bank's own category** is only ever a suggestion, never applied on its own.
6. Otherwise it asks.

When you change a category it immediately re-sorts everything from that merchant you haven't confirmed yet, and offers to change the earlier ones too. Only your choices teach it; things it sorted by itself don't count as evidence.

**Day one:** in Monarch, download your transactions as CSV and import the file in Settings. Every row comes in with the category you gave it, so the sorter starts out already knowing your habits. When Plaid later brings in the same history, the imported rows are matched up rather than doubled.

## Connecting real banks (Plaid)

1. Create a Plaid account, copy the client id and secret from the dashboard.
2. `cp .env.example .env` and fill in `SECRET_KEY`, `APP_USERS`, `PLAID_CLIENT_ID`, `PLAID_SECRET`, `PLAID_ENV`.
3. `make dev`, open Settings, press **Connect a bank**.

Start with `PLAID_ENV=sandbox` (fake bank: user `user_good`, password `pass_good`), then switch to `production` with your production secret. Banks that sign in through their own site (Chase, Capital One and others) also need `PLAID_REDIRECT_URI` set to an https address you've registered in the Plaid dashboard.

It syncs every 6 hours while running (`SYNC_EVERY_HOURS`), and on the **Sync now** button.

## Putting it somewhere you can both reach

The data is one SQLite file (`MONEY_DB`), so it needs a machine with a disk that stays put:

- **A computer at home that's always on**, reached through Tailscale. Simplest and free.
- **A small cloud VM** (Google's free e2-micro works): `docker build -t money . && docker run -d --restart=always -p 8000:8000 -v money-data:/data --env-file .env money`, with Caddy in front for https.

Before it's reachable from the internet: set `APP_USERS` and a long `SECRET_KEY`, and serve it over https only. The `.db` file is the whole thing. While the app runs it copies it into `backups/` beside the database once a day and keeps the newest 14; copy that folder somewhere else now and then.

## Tests

```bash
make test
```

Covers the merchant-name cleanup, every learning path, splits, budgets, transfer matching, recurring detection, CSV import, Plaid sync (against a mocked Plaid), and sign-in.
