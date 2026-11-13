# Environment Setup Plan

Everything to put in place before the first line of code. You can follow it as a checklist.

## GCP project layout

| Project | Purpose |
|---|---|
| `money-dev` | Experiments, CI deploys from feature branches, fake or sample data only |
| `money-prod` | Real data. Deploys only from `main`. Locked down. |

Keep them in their own folder (or a separate billing account) away from work projects. Set a **budget alert** on each (e.g. $25 and $50/month).

## Services to enable (both projects)

```
run.googleapis.com
sqladmin.googleapis.com
secretmanager.googleapis.com
cloudscheduler.googleapis.com
artifactregistry.googleapis.com
iap.googleapis.com
storage.googleapis.com
cloudbuild.googleapis.com          # only if building images in GCP
iamcredentials.googleapis.com      # Workload Identity Federation for GitHub
bigquery.googleapis.com            # Phase 4
```

## Service accounts and IAM

| Service account | Used by | Roles |
|---|---|---|
| `money-app` | Cloud Run service | Cloud SQL Client, Secret Manager Secret Accessor (only its secrets), Storage Object Admin on the app bucket |
| `money-jobs` | Cloud Run Jobs (sync, digest) | Same as `money-app` |
| `money-scheduler` | Cloud Scheduler | Cloud Run Invoker on the jobs |
| `github-deployer` | GitHub Actions via Workload Identity Federation (no JSON keys) | Artifact Registry Writer, Cloud Run Admin (scoped), Service Account User on `money-app` / `money-jobs` |

People: you as Owner on both projects. She gets **no GCP access**, only app access through IAP (the "IAP-secured Web App User" role on the prod service for the two Google accounts).

## Secrets (Secret Manager, names only)

| Secret | Phase |
|---|---|
| `db-password` | 1 |
| `app-session-key` | 2 |
| `anthropic-api-key` | 3 |
| `plaid-client-id`, `plaid-secret` | 3 |
| `plaid-access-tokens` (or a table encrypted with a KMS key) | 3 |
| `notify-webhook` (email/SMS provider for the digest) | 3 |

Never commit secrets. Locally, use a git-ignored `.env` file.

## Data stores

- **Cloud SQL Postgres 16**, smallest shared-core tier, single zone, automatic daily backups + point-in-time recovery. Private IP or the Cloud SQL connector, with no public IP. Database `money`, app user `money_app`.
- **Cloud Storage** bucket `<project>-money-files`: statement uploads, receipts, generated PDFs. Uniform access, no public access, lifecycle rule to move uploads to Nearline after 90 days.
- Cost-saving alternative for dev: SQLite file in the container plus a nightly copy to GCS. Not for prod.

## Cloud Run

- Service `money-api` (FastAPI). Min instances 0, max 2, 512 MiB, region near you (e.g. `us-central1`).
- Ingress restricted to internal + load balancer, with **IAP** in front, so only your two Google accounts can reach it.
- Jobs: `sync-banks`, `month-start`, `weekly-digest`, `tax-reminders`, `export-bq`, each triggered by Cloud Scheduler with the `money-scheduler` account.

## CI/CD (GitHub Actions)

```
on: pull_request  ->  lint (ruff) + type check (mypy) + tests (pytest)
on: push to main  ->  tests -> build image -> push to Artifact Registry
                      -> deploy money-api to money-dev
                      -> manual approval (GitHub environment "prod")
                      -> run DB migrations (Alembic) as a Cloud Run Job
                      -> deploy to money-prod
```

Authenticate to GCP with **Workload Identity Federation** (OIDC from GitHub). There are no service account keys in GitHub.

## Local development

- Python 3.12, `uv` (or `pip` + venv), `ruff`, `pytest`.
- SQLite by default (`DATABASE_URL=sqlite:///./money.db`). Optional Docker Postgres to match prod.
- Fake data: generated sample statements in `app/sample_data/` (made up, never real).
- `make dev` runs the API with reload. `make test` runs the tests. `make seed` loads categories and sample goals.

## Repo layout (when code starts)

```
home-office/
  README.md
  docs/            plan.md, backend-flow.md, setup.md
  mockups/         clickable HTML mockups
  app/
    pyproject.toml
    money/         models, ingest, engine (normalize, transfers, rules, suggest), reports, close, api
    migrations/    Alembic
    tests/
    sample_data/   fake CSVs only
  web/             Phase 2 front end
  infra/           gcloud scripts or Terraform for the setup below
  .github/workflows/
```

## Checklist

1. [ ] Create a billing budget and alerts.
2. [ ] Create `money-dev` and `money-prod` projects and enable the services above.
3. [ ] Create Artifact Registry repo `money` (Docker) in each project.
4. [ ] Create the service accounts and grant the roles above.
5. [ ] Set up Workload Identity Federation for this GitHub repo. Add `GCP_PROJECT`, `WIF_PROVIDER` and `DEPLOYER_SA` as GitHub Actions variables (not secrets).
6. [ ] Create the Cloud SQL instance, database and user. Store the password in `db-password`.
7. [ ] Create the GCS bucket.
8. [ ] Deploy a placeholder Cloud Run service. Turn on IAP and add both Google accounts.
9. [ ] Add GitHub environment `prod` with required approval.
10. [ ] Local: install Python 3.12 and uv, clone the repo, create `.env` from `.env.example`.
11. [ ] Write `infra/` scripts for steps 2–8 so the setup can be rebuilt.
12. [ ] Phase 3 only: apply for Plaid production access, create the Anthropic API key, and add both to Secret Manager.

## Data safety notes

- Real financial data lives only in `money-prod`. Dev uses fake data.
- No bank passwords are ever stored. Plaid tokens go in Secret Manager or a KMS-encrypted table.
- Backups are on, and a restore is tested once before real use.
- Export everything to CSV from the app at any time, so there's no lock-in.
