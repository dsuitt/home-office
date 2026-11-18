# home-office

Tools to run a wedding photography studio's money (and our household money) with less dread: transactions sort themselves, she answers only what the tool can't figure out, and each month ends with a clear "done."

## What's here

| Path | What it is |
| --- | --- |
| `app/` | **The household money app** (working code). Start with `app/README.md` |
| `docs/plan.md` | Build plan: decisions, phased roadmap, open questions |
| `docs/backend-flow.md` | Backend pipeline, data model and API surface |
| `docs/setup.md` | GCP and local setup checklist |
| `mockups/money-tool-options.html` | First round: 3 tools × 3 layout options each |
| `mockups/studio-dashboard.html` | Studio dashboard, modeled on the budget template she likes, including the Saving For wishlist |
| `mockups/home-dashboard.html` | Household dashboard in the same style |

The mockups are self-contained HTML. Open one in a browser to click through it. All numbers in them are made up.

## Published mockups (private links, share from the page's Share menu)

- Options round: https://claude.ai/artifact/Dj7SweZTPbKFMs5Rr2Movt
- Studio dashboard: https://claude.ai/artifact/BFae3ozfV9hErhLrbEmeNg
- Home dashboard: https://claude.ai/artifact/6pL9KgeXNXPtfPiQsAQf2D

## Status

- **Home app: built and working** in `app/` (`cd app && make demo` to click through it with made-up data). It uses Plaid directly for the household accounts, which replaces the "Monarch for home" row in `docs/plan.md`. Not yet tried against a real Plaid account.
- **Studio book:** still at the planning stage. See `docs/plan.md`.
