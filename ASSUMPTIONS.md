# ASSUMPTIONS.md

Решения по местам, которые задание явно не определяет. Формат: решение → обоснование.

---

## 1. Стек и состав репозитория

- **Монорепо**: `apps/api` (Python 3.12 + FastAPI/uvicorn, зависимости — `pyproject.toml` + uv), `apps/web` (React 18 + TypeScript + Vite + TailwindCSS, pnpm), `packages/shared` (схема результата), `apps/mocks` (стенды разработки, тоже FastAPI, один контейнер), `spec/` (OpenAPI-контракты).
- **Spec-driven design**: контракты пишутся до реализации — `spec/api.openapi.yaml` (наш HTTP API: все эндпоинты, семантика кодов, HMAC/JWT) и `spec/provider.openapi.yaml` (контракт ТЗ 7.1, его реализует мок). Спека — контракт рекорда: реализация обязана ей соответствовать, расхождение фиксируется правкой спеки в том же коммите. Спека ссылается на `packages/shared/analysis-result.schema.json` — правка схемы результата остаётся точечной.
- **Схема результата разбора** (задание 7.3) — JSON Schema в `packages/shared/analysis-result.schema.json`, единый источник истины. Из неё кодогенерацией получаются модели обеих сторон: Pydantic для сервера (`datamodel-code-generator`), TS-типы + zod для клиента (`json-schema-to-zod`). Сервер валидирует финальный `done` провайдера Pydantic-моделью, клиент — zod. Генерированный код коммитится, кодген — шаг `make codegen`.
- **PostgREST / Supabase не используются.** `POST /rpc/claim_next_contact` — тонкий HTTP-адаптер FastAPI над DB-функцией `claim_next_contact(...)`. Причины: нужен контроль над raw body для HMAC-подписи вебхуков (`await request.body()` до парсинга JSON), SSE-стриминг, сквозные correlation-id в логах. Выдача по-прежнему реализована функцией на стороне БД, как требует задание, — HTTP-слой только аутентифицирует и проксирует.
- **Доступ к БД** — asyncpg (пул соединений; `SET LOCAL app.org_id` в каждой транзакции для RLS; LISTEN/NOTIFY для SSE-подписок). **Миграции** — пронумерованные SQL-файлы в `apps/api/migrations`, применяются API при старте (таблица `schema_migrations`), до открытия порта. Alembic не берём: схема фиксирована заданием, ручной SQL прозрачнее для ревью.

## 2. Модель данных: дополнения к фиксированной схеме

Задание фиксирует имена таблиц и перечисленные поля; всё остальное — на усмотрение.

**`contacts`** дополнительно:
- `locked_attempt_id uuid` — активная (незавершённая) попытка, захватившая контакт. `NULL` = контакт доступен. Выставляется в `claim_next_contact`, очищается триггером при терминальном статусе попытки. Убирает дорогой `EXISTS` по `call_attempts` на каждый claim.

**`call_attempts`** дополнительно:
- `last_applied_sequence bigint NOT NULL DEFAULT 0` — последний применённый `sequence` события провайдера. Источник истины для защиты от переупорядочивания.
- `transcript text` — транскрипт завершённого звонка, заполняется из `data.transcript` терминального события `call.completed`. Разбор (часть 2) запускается только при наличии транскрипта, иначе `409`.

**`webhook_events`** дополнительно:
- `applied_at timestamptz` — `NULL` означает «принято, но не применено» (буфер: см. §4).
- `call_attempt_id uuid` — попытка, к которой событие применено (NULL, пока не применено).

**Новые таблицы:**
- `crm_outbox (id, attempt_id, status, outcome, created_at, delivered_at, attempts int, last_error text)` — transactional outbox для уведомлений CRM.
- `analyses (id, org_id, call_attempt_id, status, result jsonb, partial jsonb, error text, created_at, ...)` — задачи разбора.
- `analysis_chunks (analysis_id, seq, field, delta, id ...)` — накопленные чанки стрима; основа для `Last-Event-ID`-возобновления SSE.

## 3. Привязка `provider_call_id` к попытке

`claim_next_contact` создаёт попытку со статусом `queued` и `provider_call_id = NULL`. Два независимых механизма привязки:

1. **Явный endpoint**: `POST /api/call_attempts/:id/provider-link { provider_call_id }` (JWT роли `worker`). Идемпотентен: повтор с тем же значением → `200`; другое значение → `409`; чужая организация → `404` (через RLS). Привязка и «подметание» буфера событий (§4) — одна транзакция.
2. **Fallback через client reference**: воркер передаёт `attempt_id` провайдеру при старте звонка, провайдер эхом возвращает его в `data.attempt_id` каждого события. Обработчик вебхука, не найдя попытку по `provider_call_id`, ищет по `data.attempt_id` и линкует сам. Спасает сценарий «звонок стартовал, воркер умер до provider-link».

Гарантия уникальности: частичный уникальный индекс `call_attempts(provider_call_id) WHERE provider_call_id IS NOT NULL` — один звонок провайдера = максимум одна попытка.

## 4. Обработка вебхуков

Доставка at-least-once, порядок не гарантируем, ответ отличный от 2xx = ретрай провайдера.

- **Идемпотентность**: `webhook_events.provider_event_id UNIQUE`; повторная доставка → `200` без побочных эффектов.
- **Неизвестный `type`** → `200`, событие сохраняется, не применяется. Отвечать `422` нельзя: провайдер будет ретраить вечно (poison message). Известные типы — справочник `KnownWebhookEventType` в спеке; входное поле enum не ограничено.
- **Неизвестный `call_id`** → `200`, событие сохраняется с `applied_at = NULL` (буфер). Отвечать ошибкой нельзя: провайдер будет ретраить, а привязка может случиться позже. Буфер «подметается» транзакцией provider-link / ленивой привязки в порядке `sequence`.
- **Переупорядочивание**: событие применяется только если `sequence > last_applied_sequence`. Устаревшие — сохраняются, не применяются.
- **Терминальный статус односторонний**: события по завершённой попытке сохраняются, но состояние не меняют (защита от регрессии поверх sequence-правила).
- **Конкурентность**: `pg_advisory_xact_lock(hashtext(provider_call_id))` в начале транзакции обработки — сериализует конкурентные вебхуки одного звонка и гонку «вебхук vs provider-link».
- **Вебхук никогда не создаёт `call_attempts`** — только привязывается к существующей попытке.
- **RLS на `webhook_events`**: у `app_user` доступ только к событиям, связанным с попыткой своей org (`EXISTS` по `call_attempts.provider_call_id` + `app.org_id`) — нужно для sweep буфера из `provider-link`. Роль `app_webhook` работает с `BYPASSRLS` (приём вебхуков без JWT-сессии).

## 5. Побочные эффекты и CRM

- Триггер `AFTER UPDATE` на `call_attempts` при переходе в терминальный статус (`completed`/`failed`/`no_answer`): `attempts_count + 1`, `last_attempt_at = ended_at`, `do_not_call` из `outcome`, очистка `contacts.locked_attempt_id`, **и запись в `crm_outbox`** — всё в одной транзакции с обновлением попытки.
- Уведомление CRM — фоновая asyncio-задача: поллинг `crm_outbox`, `POST CRM_URL` (`httpx`) с телом `{attempt_id, status, outcome}`, ретраи с экспоненциальным бэкоффом (`next_attempt_at`, потолок задержки **5 минут** / 300 с), дедлайн на запрос. Записи outbox **никогда не дропаются** — только откладываются до успешной доставки. Доступность CRM не влияет на приём вебхуков. Идемпотентность на стороне CRM обеспечивается `attempt_id` (естественный ключ).
- **Чтение outbox веб-UI**: политика RLS `SELECT` для `app_user` через linked `call_attempts` своей org (см. §9a); мутации из API запрещены.
- **`abort` и `stale_timeout` расходуют попытку**: триггер един для всех терминальных статусов, `attempts_count` инкрементируется, хотя до набора дело могло не дойти. Считаем честным (ресурс контакта был захвачен), но это допущение.

## 6. Воркер-дозвонщик: роль и жизненный цикл

Воркер — **только инициатор** звонка, stateless, статусами звонка не владеет. Цикл: `claim` → `provider.startCall(phone, client_ref=attempt_id)` → `provider-link` → следующий контакт. Синхронный отказ провайдера → `POST /api/call_attempts/:id/abort {reason}` → `failed`.

**Reaper**: фоновая asyncio-задача API переводит попытки, застрявшие в `queued`/`dialing`/`in_progress` дольше 30 минут (звонок физически не длится дольше), в `failed` с `outcome = {"reason": "stale_timeout"}` — иначе контакты умерших воркеров висели бы в блокировке вечно.

## 7. Доступ и изоляция организаций

- JWT HS256, claims `sub`, `org_id`, `role` (`worker` | `authenticated`). Проверяем подпись и `exp` (если есть). Принимаем `Authorization: Bearer` (воркеры) **или** HttpOnly cookie `dev_token` (веб-UI). **Выдача токенов за пределами сервиса** — стенд подписывает сам нашим `JWT_SECRET`; для разработки есть `POST /dev/token` (ставит cookie), `POST /dev/logout`, `GET /dev/session`, включённые только флагом окружения.
- **Матрица ролей** (дублируется в `x-roles` операций спеки): claim — обе роли; `provider-link`/`abort` — только `worker`; `/api/analyses*` — обе роли. Недопущенная роль → `403 forbidden`; чужая организация → `404` (изоляция ≠ роли).
- **Машиночитаемые ошибки**: все ошибки API — `{code, detail}`, `code` из enum `ErrorCode` в спеке (`unauthorized`, `token_expired`, `invalid_signature`, `forbidden`, `not_found`, `already_linked`, `not_queued`, `no_transcript`, `attempt_not_completed`, `analysis_terminal`, `validation_error`). Клиенты и стенд опираются на `code`, не на текст.
- **Изоляция — PostgreSQL RLS**, а не только проверки в коде: приложение работает под non-superuser ролью `app_user`, на всех tenant-таблицах политика `org_id = current_setting('app.org_id')::uuid`, значение ставится `SET LOCAL` из JWT в каждой транзакции. Это закрывает требование «ни напрямую через БД-роль».
- `claim_next_contact` — `SECURITY DEFINER` функция, org берёт из `app.org_id` и проверяет явно.
- Обработчик вебхуков (HMAC, без JWT) идёт под отдельной service-ролью `app_webhook` с `BYPASSRLS` — событие само по себе не привязано к JWT-сессии. Политика `webhook_events` для `app_user` — см. §4.

## 8. Производительность claim (100 мс p95 @ 2 млн контактов)

- Частичный индекс `contacts(campaign_id) WHERE do_not_call = false AND locked_attempt_id IS NULL` — кандидаты выбираются по индексу без сканирования таблицы.
- `SELECT ... FOR UPDATE SKIP LOCKED` — параллельные воркеры (5–20) не блокируют друг друга.
- Фильтр таймзоны вычислим per-row: `(now() AT TIME ZONE timezone)::time >= '09:00' AND < '20:00'`.
- Проверка «4 часа с последней попытки»: `last_attempt_at IS NULL OR last_attempt_at <= now() - interval '4 hours'`.
- Claim выполняется в одной функции БД = одна транзакция = атомарность «выбрал + создал попытку + залочил контакт».
- Неизвестная/чужая кампания → `404 not_found`.
- Кампания своей org со `status <> active` → `200` с `contact: null` (не 404): очередь для неё пуста, это не ошибка доступа.
- `contact: null` также при отсутствии подходящих контактов у активной кампании.

## 9. Разбор звонка (часть 2)

- Транскрипт берётся из `call_attempts.transcript` (см. §2); передать его в теле `POST /api/analyses` нельзя — только `call_attempt_id`.
- **SSE-возобновление через БД**: каждый чанк LLM-стрима пишется в `analysis_chunks` с монотонным `seq`. Подключение `GET /api/analyses/:id/stream` с `Last-Event-ID` сначала отдаёт из БД всё с `id > Last-Event-ID`, затем подписывается на новые чанки (LISTEN/NOTIFY + периодический re-read как страховка). Генерация при переподключении клиента **не перезапускается никогда** — клиент дочитывает журнал.
- Стрим-консюмер — asyncio-задача в процессе API, читает SSE провайдера через `httpx.AsyncClient` (таймауты, обрывы). Провайдер может вернуть `429 + Retry-After`, оборвать соединение, прислать мусор: консюмер ретраит по `Retry-After` (429) и с бэкоффом (обрыв), но только если по анализу ещё нет терминального результата; финальный `done` валидируется Pydantic-моделью, сгенерированной из `packages/shared`, невалидный результат → `error` с сохранением partial.
- **Рестарт API**: при старте диспетчера анализы в `queued` снова подхватываются; статусы `streaming` переводятся в `error("interrupted_restart")` fail-closed — чанки/`partial` сохраняются, генерация для них **не** перезапускается.
- **Resume после обрыва к провайдеру**: консюмер переоткрывает стрим, пропускает первые K уже сохранённых чанков и сверяет префикс (`field`/`delta`); mismatch → `error` с сохранением partial.
- Отмена (`POST /:id/cancel`) выставляет флаг в БД; консюмер завершает соединение с провайдером и помечает анализ `cancelled`. Идемпотентна.
- **Механика SSE**: первая строка — `retry: 3000`; heartbeat `: ping` каждые 15 с молчания; SSE-поле `id:` несут только `chunk`; подключение к терминальному анализу → replay журнала + терминальное событие и закрытие; дисконнект клиента ≠ отмена; конкурентные подписки разрешены. Спека определяет события `chunk` / `done` / `error`; для `cancelled` наружу уходит `event: error` с `{"code":"cancelled",...}` (отдельного SSE-типа `cancelled` нет).
- **`partial` — `PartialAnalysisResult`**: форма схемы 7.3 со всеми необязательными полями. Склейка: дельты одного поля конкатенируются как текст; `objections`/`lead_score`/`confidence` стримятся JSON-представлением; поле появляется в `partial`, когда текст парсится.
- Повторный `POST /api/analyses` на ту же попытку создаёт **новый** разбор (переанализ легитимен); дедупликации по `call_attempt_id` нет.
- Авторизация SSE: `EventSource` не умеет заголовки, поэтому клиент использует fetch-based SSE (`@microsoft/fetch-event-source`) с cookie `dev_token` (`credentials: 'include'`) или `Authorization: Bearer`. Чужая организация на всех `/api/analyses*` → `404` (RLS).
- **Приоритет UI `data-state`** (сверху вниз): `cancelled` → `error` (пустой partial) → `partial` (error + непустой partial) → `done` → `reconnecting` → `streaming` → `queued`; до старта разбора — `idle`. Атрибут `data-state` ставится на корневой контейнер страницы (стенд ТЗ 7.4) и дублируется на секции разбора внутри выбранной карточки; видимый бейдж — только в карточке.

## 9a. Веб-UI: список звонков и таймлайн (расширение сверх ТЗ)

ТЗ (§7.4) требует одну страницу запуска разбора и живой просмотр с `data-state`. Дополнительно (не оценивается стендом, но контрактуется в OpenAPI):

- `GET /api/call_attempts` — курсорный список попыток org (`created_at DESC, id DESC`); роль `authenticated`. Опциональные серверные фильтры (AND): `status` (exact), `phone` (префикс `contacts.phone_e164`, pattern `^\+?[0-9]{1,15}$`), `created_from` / `created_to` (`created_at >= from`, `created_at < to`). Совместимы с курсором. Отдельные индексы под фильтры не вводим: объём — per-org срез RLS, сортировка/курсор закрыты `idx_call_attempts_org_created`.
- `GET /api/call_attempts/{id}` — детали: контакт/кампания, `status_history`, связанные `analyses`, состояние CRM outbox.
- `GET /api/call_attempts/stream` — org SSE: события `attempt` / `crm` / `analysis` (LISTEN/NOTIFY + re-read под RLS; механика как у analyses: `retry: 3000`, `: ping`). Клиент применяет live-`attempt` к списку только если элемент матчит активные фильтры (иначе удаляет из текущего среза).

**История статусов звонка** строится без отдельного журнала: синтетическая точка `queued` (`source=claim` от `call_attempts.created_at`) + применённые `webhook_events` (маппинг типов ТЗ → статусы) + точка `abort`, если попытка `failed` без терминального вебхука.

**CRM в UI**: `crm_outbox` читается `app_user` через RLS-политику `SELECT` по linked attempt (INSERT по-прежнему только SECURITY DEFINER-триггер; поллер — BYPASSRLS). Состояния UI: `pending` / `retrying` / `delivered`.

Атрибут `data-state` остаётся на контейнере разбора (страница + секция в карточке) и не смешивается со статусами списка звонков.

## 10. Запуск и окружение

- `GET /healthz` — чистый liveness (`200`, если HTTP-сервер жив). Готовность обеспечивается порядком старта: ожидание БД → миграции → открытие порта. Стенд, дождавшийся `200`, получает готовый сервис.
- **Сиды** — только вручную (`make seed`), в `docker compose up` не применяются: стенд наполняет БД сам.
- **Моки** (`apps/mocks`, FastAPI, один контейнер): мок LLM-провайдера по контракту 7.1 (обязателен — «стенд для разработки вы пишете сами») и мок CRM (логирует тела, отвечает `200`). Поднимаются compose-профилем `dev` и не стартуют при обычном `docker compose up -d`, чтобы не мешать проверочному стенду.
- **Локальный UI**: `yarn dev` / `pnpm dev` в `apps/web` проксирует same-origin запросы на Docker API (`VITE_API_PROXY`, по умолчанию `http://localhost:8080`); в compose-сервисе `web` target — `http://api:8080`.
- **Фоновые задачи** (CRM-поллер, reaper, стрим-консюмеры анализов) — supervised asyncio-таски в процессе API, стартуют после миграций. Для нагрузки задания (50 rps вебхуки, единицы параллельных стримов) отдельный worker-контейнер избыточен; граница выноса — код уже изолирован в сервисном слое.
- `.env` читается только приложением (compose подхватывает его автоматически): `WEBHOOK_SECRET`, `JWT_SECRET`, `CRM_URL`, `PROVIDER_URL`. Значения по умолчанию в репозитории — dev-заглушки, стенд подменяет файл.

## 11. Логи

Структурные JSON-логи (structlog, JSONRenderer). Correlation-id: для вебхука — `provider_event_id` (плюс `call_id`, `attempt_id` после матчинга), сквозной от приёма до записи в БД и до записи в `crm_outbox`; для анализов — `analysis_id`/`request_id`. Все HTTP-ответы включают заголовок `x-request-id` (эхо клиентского, если прислан, иначе сгенерированный uuid); контекст протаскивается через `contextvars` — фоновые задачи (CRM-поллер, reaper, стрим-консюмеры) логируют со своими id.
