# Build Plan: Studio & Home Money Tool

The plan for a tool that takes over the bookkeeping chores for a one-person wedding photography studio (a single-member LLC) and for the household. No code yet. This is what we agree on before writing any.

## Goals

1. **The monthly bookkeeping becomes finite.** Every month has a clear start, a known number of items to review, and a checklist that ends with "the month is done."
2. **The tool does the sorting.** Transactions come in automatically. Transfers, known merchants and owner's pay are handled without her. She answers only what the tool can't work out.
3. **She can trust the numbers.** Every account reconciles to its statement, and the tax set-aside is visible and on track. That removes the "did I do this right?" worry.
4. **It replaces the bookkeeper's reports:** quarterly P&L, where the money goes, what to cut, and progress toward goals.
5. **Home and studio stay separate but connected.** Owner's pay leaves the studio and arrives at home as one steady monthly amount.
6. **"Saving for" goals.** Business purchases she wants, each with a cost, money saved so far, a monthly contribution and a projected purchase month.

## Design principles (for her)

- **Calm.** Few notifications, batched, at times she picks. A monthly ritual instead of a stream of pings.
- **Finite.** Always show "X left" and a finish line. Nothing is open-ended.
- **No surprises.** Automations explain themselves ("sorted because it matches your Adobe rule"). Nothing is changed or sent without her seeing it.
- **Plain language.** "Studio or home?" instead of "reclassify to owner's draw."
- **Familiar layout.** Same tabs and sheet-like look as the budget template she likes: navy headers, plan / actual / difference.
- **One source of truth.** One place for each number. Xero, if kept, is fed by this tool, not edited separately.
- **Reliable over clever.** Wedding season is the worst time for a broken tool. Prefer boring tech and a manual fallback (CSV import).

## Decisions (recommendations)

| # | Decision | Recommendation | Why |
|---|---|---|---|
| 1 | Backend stack | **Python + FastAPI on Cloud Run** | It's your strongest language, it's a good fit for data and ML, and Cloud Run costs ~nothing when idle. |
| 2 | Database | **Cloud SQL Postgres** in prod, **SQLite** locally | The data is relational (accounts, transactions, splits, plans), the volume is small, and it needs real transactions. BigQuery comes later, for analytics only. |
| 3 | Xero | **Keep it as a read-only backstop for one tax season**, then decide with her CPA | The tool becomes the place she works. If the CPA is happy with a Schedule C packet from this tool, cancel Xero (about $40–60/mo saved). |
| 4 | Bank data | **Start with CSV/OFX statement import, add Plaid in Phase 3** | CSV works on day one with every bank and costs nothing. Plaid removes the monthly download step but needs a production application and has a per-account cost. Both feed the same pipeline. |
| 5 | Front end | **Web app** (mobile-friendly), built from the dashboard mockups | Google Sheets as the UI is tempting because she likes the template, but two-way sync is fragile. A web app can look like the sheet and still offer the card-by-card review on her phone. Add a "download as spreadsheet / PDF packet" export for the paper habit. |
| 6 | Dashboards | **Separate Studio and Home dashboards** sharing one backend | Cleaner for her. Each has the same tab structure, so she only learns it once. |
| 7 | AI | **Claude API for category suggestions and plain-English summaries**, never auto-applied without a rule | Suggestions go to her review queue. Rules (her answers) are what auto-sort. |
| 8 | Auth | **Google sign-in (Identity-Aware Proxy, IAP)** for just the two of you | No passwords to manage, and nothing is publicly exposed. |

## Roadmap

### Phase 0: Setup (≈1 weekend)
- GCP projects, repo, CI and local dev environment, as described in `setup.md`.
- **Done when:** a "hello" service deploys to Cloud Run dev behind IAP from a GitHub push.

### Phase 1: Engine (≈2–3 weekends)
- Data model, CSV/OFX import, dedupe, transfer matching (including the owner's-pay bridge), rules, review queue API, splits, plans, "Saving for" goals, month-close checks, core reports.
- Seed categories for both books, with studio categories mapped to Schedule C lines.
- **Done when:** importing one real month of statements for all accounts leaves under 20% of transactions in "needs you," and every account reconciles.

### Phase 2: Web UI (≈3–4 weekends)
- Studio and Home dashboards based on the mockups: Snapshot, Categories & Goals, Log, Annual Plan, Month in Review, Saving For (studio), Taxes & Profit (studio), Net Worth (home), Dashboard.
- Card-by-card review on her phone. Printable PDF review packet.
- **Done when:** she closes a full month alone, on her phone or laptop, without asking you anything.

### Phase 3: Automation (≈2–3 weekends)
- Plaid sync (nightly), Claude suggestions, receipt photo capture, a batched weekly digest.
- Optional: pull invoices from Dubsado and payouts from Pic-Time to match income to clients.
- **Done when:** a month needs no downloads and her review takes under 15 minutes.

### Phase 4: Reports & taxes (≈2 weekends)
- Quarterly letter report (LLM-written), estimated tax calculator with payment reminders, per-wedding profit, year-end CPA packet (Schedule C totals, mileage, 1099 contractor list, gear purchases).
- BigQuery export for your own analysis (pricing, seasonality, lead sources).
- **Done when:** her CPA accepts the year-end packet without needing Xero.

## Open questions

**For her**
- Which mockup options felt best for sorting transactions and for reports? (See the first mockups page.)
- Are the studio bucket names right: Running costs / Growth / Set-asides?
- Does she want the reflection questions in Month in Review, or should they be skipped?
- Phone or laptop for the monthly close?
- What's on the "Saving for" list, and what are her priorities?

**For you**
- How far behind is the bookkeeping, and do we need a catch-up import of past months?
- The full list of the 7–8 accounts: which are studio, which are home, and which are mixed?
- Does the CPA need Xero access, and when is their deadline?
- How are second shooters paid (Venmo, Zelle, check)? This affects the 1099 list.
- How do Dubsado and Pic-Time payments arrive (Stripe, PayPal, direct)?
- Would she open a studio-only credit card? It removes most "studio or home?" questions.

## Rough monthly cost (estimates, verify before committing)

| Item | Estimate | Notes |
|---|---|---|
| Cloud Run | $0–5 | Scales to zero. Two users. |
| Cloud SQL Postgres (smallest shared-core) | ~$10–15 | The biggest fixed cost. Could start on SQLite + GCS backup to defer it. |
| Cloud Scheduler, Secret Manager, logging | ~$0–2 | Mostly within free tiers. |
| Plaid (Phase 3, ~8 accounts) | ~$5–20 | Per-account pricing varies by plan and needs a production application. Check current pricing. |
| Claude API (suggestions + summaries) | ~$1–5 | Small volumes: a few hundred transactions/month and a few reports. |
| **Total** | **~$15–45/mo** | For comparison: a bookkeeper or Xero alone usually costs more. |
