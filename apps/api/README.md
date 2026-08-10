# call-api

FastAPI-сервис управления обзвоном: claim контактов, вебхуки телефонии,
CRM outbox, LLM-разбор звонков (SSE) и read-API попыток для UI.

Часть монорепозитория [sound](../../README.md). Решения по неоднозначностям —
в [`ASSUMPTIONS.md`](../../ASSUMPTIONS.md).

## Запуск

Из корня репозитория (контракт сдачи):

```bash
cp .env.example .env   # если ещё нет
docker compose up -d
curl -sf http://localhost:8080/healthz
```

API слушает `:8080`, PostgreSQL — `localhost:54329`
(`postgres:postgres@…/postgres`). Миграции из `migrations/` применяются
автоматически при старте.

Локальная разработка без Docker (нужна уже поднятая БД):

```bash
cd apps/api
uv sync --group dev
uv run uvicorn app.main:app --host 0.0.0.0 --port 8080
```

## Тесты и проверки

```bash
# из корня
make test

# или из apps/api
uv run pytest -q
uvx ruff check .
uv run mypy
```

## Структура

```
app/
  main.py          # FastAPI app, lifespan, фоновые задачи
  auth.py          # JWT + роли
  db.py            # пулы asyncpg, tenant-транзакции (RLS)
  routers/         # HTTP-эндпоинты
  services/        # применение вебхуков, read-модели, partial-анализ
  tasks/           # CRM-поллер, reaper, stream-консюмер
  generated/       # codegen из packages/shared (не править руками)
migrations/        # пронумерованные SQL-миграции
tests/             # pytest + httpx ASGI
```

OpenAPI-контракт: [`spec/api.openapi.yaml`](../../spec/api.openapi.yaml).
