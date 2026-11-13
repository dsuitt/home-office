# Build Plan: Studio & Home Money Tool

The plan for a tool that takes over the bookkeeping chores for a one-person wedding photography studio (a single-member LLC) and for the household. No code yet. This is what we agree on before writing any.

## Goals

1. **The monthly bookkeeping becomes finite.** Every month has a clear start, a known number of items to review, and a checklist that ends with "the month is done."
2. **The tool does the sorting.** Transactions come in automatically. Transfers, known merchants and owner's pay are handled without her. She answers only what the tool can't work out.
3. **Nothing already done gets redone.** Everything she has already reconciled in Xero comes in with its category, and teaches the tool her habits.
4. **She can trust the numbers.** Every account reconciles to its statement, and the tax set-aside is visible and on track.
5. **It replaces the bookkeeper's reports:** quarterly P&L, where the money goes, what to cut, and progress toward goals.
6. **Home and studio stay separate but connected.** Owner's pay leaves the studio and arrives at home as one steady monthly amount.
7. **"Saving for" goals.** Business purchases she wants, each with a cost, money saved so far, a monthly contribution and a projected purchase month.
8. **Free to run.** Stay inside Google Cloud free tiers and free API plans wherever possible.

## Design principles (for her)

- **Calm.** Few notifications, batched, at times she picks. A monthly ritual instead of a stream of pings.
- **Finite.** Always show "X left" and a finish line.
- **No surprises.** Automations explain themselves ("sorted because it matches your Adobe rule"). Rules learned from her Xero history are shown to her once for approval before they're used.
- **Plain language.** "Studio or home?" instead of "reclassify to owner's draw."
- **Familiar layout.** Same tabs and sheet-like look as the budget template she likes.
- **Reliable over clever.** Every automatic source has a manual fallback (CSV download), so a broken connection costs one download, not a missed month.

## Decisions

| # | Decision | Choice | Why |
|---|---|---|---|
| 1 | Backend | **Python + FastAPI on Cloud Run** | Your strongest language. Scales to zero, so it's free when idle. |
| 2 | Database | **Firestore (Native mode)** | Free tier (1 GiB, 50k reads / 20k writes a day) is far beyond our needs. No always-on instance like Cloud SQL. Reports compute in Python on a month or year of data, plus small precomputed summary documents. |
| 3 | Sign-in | **Firebase Authentication (Google sign-in)**, allowlist of our two emails | Free. Replaces Identity-Aware Proxy, which needs a paid load balancer on Cloud Run. |
| 4 | Front door | **API Gateway** in front of a private Cloud Run service | The gateway checks the Firebase token before anything reaches the app, and only the gateway can call Cloud Run. First 2M calls a month are free. |
| 5 | Studio bank data | **Plaid, free Trial plan** (up to 10 bank logins) | The same network Xero now uses for US bank feeds. Nightly sync. CSV download as the fallback. |
| 6 | Studio history | **One-time import of reconciled lines from Xero, then nightly while she still uses Xero** | Xero's API only exposes reconciled lines, which is exactly the finished work we want to keep. Unreconciled lines come from Plaid. |
| 7 | Home bank data | **Monarch, through the community Python library** (`monarchmoneycommunity`) | Monarch already connects to the household accounts. Monarch's CSV download is the fallback. Unofficial, so it may break; Plaid can take over home too if it does. |
| 8 | Xero going forward | **Keep for this tax season, then decide with her CPA** | If the CPA accepts our Schedule C packet, cancel Xero. If not, we can push sorted transactions into Xero later. |
| 9 | Front end | **Web app** built from the dashboard mockups | Looks like the template, plus card-by-card review on her phone and a spreadsheet/PDF export. |
| 10 | Dashboards | **Separate Studio and Home dashboards**, one backend | Cleaner. Same tab structure, so she learns it once. |
| 11 | AI | **Claude API for suggestions and plain-English summaries**, never auto-applied | Suggestions go to her review queue. Only rules she approved sort automatically. |
| 12 | Environments | **One GCP project** (`money-prod`) + Firebase emulators locally | Each project's free tier is separate, but a second cloud project adds work without much benefit for two users. |

## Roadmap

### Phase 0: Setup (≈1 weekend)
- GCP project, Firebase, API Gateway, CI and local dev, as described in `setup.md`.
- **Plaid coverage check:** look up each of her banks in Plaid before building anything (see `backend-flow.md`).
- **Done when:** a "hello" endpoint answers through API Gateway for our two Google accounts only, deployed from a GitHub push.

### Phase 1: Connections + engine (≈3–4 weekends)
- Xero history import: account and category mapping, reconciled lines, proposed rules for her approval.
- Plaid link for each studio bank, nightly sync, history backfill, overlap matching with Xero.
- Monarch sync for home. CSV import for both books as the fallback.
- Engine: normalize, dedupe, transfer matching (including the owner's-pay bridge), rules, review queue, splits, plans, "Saving for" goals, month-close checks, core reports.
- **Done when:** one real month for every account arrives with no downloads, Xero-reconciled lines arrive already sorted, under 20% of the rest needs her, and every account reconciles.

### Phase 2: Web UI (≈3–4 weekends)
- Studio and Home dashboards based on the mockups. Card-by-card review on her phone. Printable PDF review packet.
- **Done when:** she closes a full month alone without asking you anything.

### Phase 3: Smarter (≈2 weekends)
- Claude suggestions, receipt photo capture, weekly digest.
- Optional: Dubsado and Pic-Time data to match income to clients.
- **Done when:** her monthly review takes under 15 minutes.

### Phase 4: Reports & taxes (≈2 weekends)
- Quarterly letter report, estimated tax reminders, per-wedding profit, year-end CPA packet (Schedule C totals, mileage, 1099 contractor list, gear purchases), BigQuery export for your own analysis.
- **Done when:** her CPA accepts the year-end packet without Xero.

## Open questions

**For you**
- The full list of studio banks and cards, by institution, for the Plaid coverage check. Include payment apps (Stripe, PayPal, Venmo, Square) if money passes through them.
- How far is Xero reconciled? Per account, roughly through which month?
- Does the CPA need Xero access, and when is their deadline?
- How are second shooters paid (Venmo, Zelle, check)? This affects the 1099 list.
- Would she open a studio-only credit card? It removes most "studio or home?" questions.

**For her**
- Which mockup options felt best for sorting transactions and for reports?
- Are the studio bucket names right: Running costs / Growth / Set-asides?
- Does she want the reflection questions in Month in Review?
- What's on the "Saving for" list, and in what order?

## Monthly cost (estimates, verify before relying on them)

| Item | Estimate | Notes |
|---|---|---|
| Cloud Run, Firestore, API Gateway, Scheduler, Cloud Storage | $0 | Within free tiers at our volume. |
| Secret Manager | ~$0–0.50 | 6 secret versions free; a few more cost pennies. |
| Plaid | $0 | Trial plan, up to 10 bank logins. If a bank or the Trial terms don't work out, SimpleFIN Bridge is about $15 a year. |
| Xero API | $0 | Free Starter tier (up to 5 connections). |
| Monarch | $0 extra | Already paid for. |
| Claude API | ~$1–5 | Small volumes. |
| **Total** | **~$0–5/mo** | Set a **$5 budget alert** so a mistake can't run up a bill. |
