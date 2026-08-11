# Sound — сервис управления обзвоном

Тестовое задание: очередь обзвона, вебхуки от телефонии и LLM-разбор звонка со стримом в браузер.

Стек: FastAPI + asyncpg + PostgreSQL 16 (RLS) · React 18 + TypeScript + Vite + Tailwind · Docker Compose.

Неочевидные решения — в [`ASSUMPTIONS.md`](ASSUMPTIONS.md). Правила для агентов — в [`AGENTS.md`](AGENTS.md). Что делал ИИ — в [`AI-USAGE.md`](AI-USAGE.md).

## Как запустить

Из корня репозитория:

```bash
cp .env.example .env   # если ещё нет
docker compose up -d
```

| Сервис | Адрес |
|--------|--------|
| API | `http://localhost:8080` |
| Health | `GET /healthz` → `200` |
| PostgreSQL | `localhost:54329`, `postgres:postgres@…/postgres` |
| Web UI | `http://localhost:5173` |

Миграции накатываются сами при старте API — до того, как откроется порт. Этот контракт нельзя ломать: иначе задание не сдано.

### Dev-моки (CRM + LLM)

```bash
docker compose --profile dev up -d
```

Моки на `:8090`. Без профиля `dev` они не поднимаются — чтобы не мешать проверочному стенду.

### UI на хосте (`yarn` / `pnpm`)

API и БД — в Docker, фронт — локально. Vite проксирует `/api`, `/rpc`, `/dev`, `/healthz`, `/webhooks` на `http://localhost:8080` (см. `apps/web/vite.config.ts`; свой адрес — через `VITE_API_PROXY`).

```bash
docker compose up -d api db          # при необходимости: docker compose stop web
cd apps/web
cp -n .env.example .env              # опционально
yarn install   # или: pnpm install
yarn dev       # или: pnpm dev  → http://localhost:5173
```

Если `:5173` уже занят контейнером `web` — остановите его (`docker compose stop web`) или смените порт Vite.

## Переменные окружения

Обязательные (полный список — в `.env.example`):

| Переменная | Зачем | Dev-заглушка |
|---|---|---|
| `WEBHOOK_SECRET` | HMAC-SHA256 для `X-Signature` вебхуков | `dev-webhook-secret` |
| `JWT_SECRET` | Подпись JWT | `dev-jwt-secret-change-me-32bytes!!` |
| `CRM_URL` | Куда ходит CRM-поллер | `http://mocks:8090/crm` |
| `PROVIDER_URL` | Базовый URL LLM-провайдера (ТЗ 7.1) | `http://mocks:8090` |

Compose ещё задаёт DSN БД, пароли ролей `app_user` / `app_webhook` и флаг `DEV_TOKEN_ENABLED` (по умолчанию `true`).

## Что сделано

**Часть 1 — ядро обзвона**
- `POST /rpc/claim_next_contact` — контакт выдаёт SQL-функция (`SKIP LOCKED`, RLS)
- `POST /webhooks/calls` — HMAC по сырому телу, дедуп, sequence/terminal-гарды, буфер для неизвестных `call_id`
- `provider-link` / `abort`, терминальный триггер, CRM outbox + фоновый поллер, reaper зависших попыток

**Часть 2 — разбор звонка**
- `POST /api/analyses`, SSE `GET /api/analyses/:id/stream` (с `Last-Event-ID`), cancel
- Стрим к провайдеру, чанки в БД, partial-результат
- Web UI с `data-state` на странице и в карточке разбора: queued / streaming / reconnecting / done / partial / error / cancelled / idle

**UI сверх ТЗ §7.4**
- `GET /api/call_attempts` — список попыток организации; клик открывает детали
- `GET /api/call_attempts/:id` — таймлайн статусов, LLM-анализы, CRM outbox
- `GET /api/call_attempts/stream` — SSE-лента `attempt` / `crm` / `analysis`

## Сиды

```bash
make seed
```

Появится demo org/campaign/контакты и completed-попытка с транскриптом (id — в stdout JSON). В обычный `docker compose up` сиды не входят — только вручную.

## Dev-токен

При `DEV_TOKEN_ENABLED=true`:

```bash
# JWT в теле ответа + HttpOnly cookie `dev_token` (для веб-UI)
curl -s -c - -X POST http://localhost:8080/dev/token \
  -H 'content-type: application/json' \
  -d '{"sub":"dev","org_id":"00000000-0000-4000-8000-000000000001","role":"authenticated"}'

# Статус сессии / выход
curl -s -b cookies.txt http://localhost:8080/dev/session
curl -s -b cookies.txt -c cookies.txt -X POST http://localhost:8080/dev/logout
```

Роли: `worker` | `authenticated`. API принимает `Authorization: Bearer` или cookie `dev_token`. Веб-UI держит сессию только в cookie, не в `localStorage`.

## Ручные сценарии

1. **Claim** — после `make seed`: `POST /rpc/claim_next_contact` с JWT и `campaign_id` из сида → контакт + `attempt_id` (или `contact: null`, если очередь пуста / кампания не `active`).
2. **Webhook** — `POST /webhooks/calls` с валидной `X-Signature: sha256=…` по сырому телу; битая подпись → `401`. События до `provider-link` буферизуются (`200`), после линка применяются по `sequence`.
3. **Analysis stream** — `docker compose --profile dev up -d`, сид, JWT, `POST /api/analyses` с `call_attempt_id` completed-попытки → SSE до `done`. В UI на `:5173` бейдж `data-state` внутри карточки разбора.
4. **Список звонков** — на `:5173` получите токен (`authenticated`) → список; клик по строке → таймлайн, блоки LLM и CRM; live через `/api/call_attempts/stream`. «Запустить разбор» только для `completed`.
5. **E2E** — полный путь: контакт → телефония (буфер / дедуп / out-of-order / 401) → CRM-ретраи → LLM (`CHAOS_429` / `CHAOS_BREAK` / `CHAOS_INVALID` / cancel):

```bash
docker compose --profile dev up -d --build
make seed
make e2e-flow          # ~1–2 мин, exit ≠ 0 при любом FAIL
```

## Нагрузка и SLA

Цели: claim ≤ 100 мс p95 на ~2 млн контактов; вебхук ≤ 1 с p99 при 50 rps.

```bash
make load-seed          # COUNT=2000000 по умолчанию
make load-claim         # параллельные воркеры
make load-webhook       # ~50 rps
```

На машине разработки (после `make load-seed` на 2 млн):

| Метрика | Цель | Факт |
|---------|------|------|
| claim p95 (10 воркеров, 200 запросов) | ≤ 100 мс | **17.3 мс** |
| webhook p99 (50 rps, 60 с, 0 ошибок) | ≤ 1 с | **5.5 мс** |

Цифры зависят от железа — прогоняйте локально после `load-seed`.

## Тесты

```bash
make test
```

Pytest по `apps/api` (claim / webhook / terminal + happy-path анализа). Нужна живая БД из compose; для стрим-теста — ещё и моки.

Ещё полезное:

```bash
make codegen   # Pydantic из packages/shared/analysis-result.schema.json
make up / up-dev / down / logs
```

## Структура репозитория

```
apps/api/        FastAPI, миграции, фоновые задачи
apps/web/        React + Vite UI
apps/mocks/      мок LLM и CRM (profile: dev)
packages/shared/ analysis-result.schema.json
spec/            OpenAPI (api + provider)
scripts/         seed, load-*
```

## Известные ограничения

- CRM-поллер за тик берёт одну запись outbox — для объёма задания хватает; под высокой нагрузкой станет узким местом.
- Resume стрима сверяет префикс сохранённых чанков: у недетерминированного провайдера возможен `prefix mismatch` с сохранением partial (мок стенда детерминирован).
- SLA в CI не гоняется — только скриптами `make load-*` на конкретной машине.
