.PHONY: dev demo test
dev:   ## run against your real data (app/.env)
	uv run uvicorn money.main:app --reload --port 8000
demo:  ## run with a made-up sample household, no bank needed
	DEMO=1 MONEY_DB=data/demo.db uv run uvicorn money.main:app --reload --port 8000
test:
	uv run pytest -q
