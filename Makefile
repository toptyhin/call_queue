.PHONY: up down logs seed codegen test lint typecheck load-seed load-claim load-webhook

up:
	docker compose up -d --build

up-dev:
	docker compose --profile dev up -d --build

down:
	docker compose down

logs:
	docker compose logs -f api

seed:
	DATABASE_URL=postgresql://postgres:postgres@localhost:54329/postgres \
		uv run --project apps/api python scripts/seed.py

codegen:
	cd apps/api && uv run datamodel-codegen \
		--input ../../packages/shared/analysis-result.schema.json \
		--input-file-type jsonschema \
		--output app/generated/analysis_result.py \
		--output-model-type pydantic_v2.BaseModel \
		--target-python-version 3.12 \
		--use-standard-collections \
		--use-union-operator

test:
	cd apps/api && uv run pytest -q

lint:
	cd apps/api && uv run ruff check .

typecheck:
	cd apps/api && uv run mypy

load-seed:
	DATABASE_URL=postgresql://postgres:postgres@localhost:54329/postgres \
		uv run --project apps/api python scripts/seed_load.py --count $${COUNT:-2000000}

load-claim:
	JWT_SECRET=$${JWT_SECRET:-dev-jwt-secret-change-me-32bytes!!} \
		uv run --project apps/api python scripts/load_claim.py --workers 10 --requests 200

load-webhook:
	WEBHOOK_SECRET=$${WEBHOOK_SECRET:-dev-webhook-secret} \
		uv run --project apps/api python scripts/load_webhook.py --rps 50 --seconds 60
