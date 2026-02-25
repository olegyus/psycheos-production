# CLAUDE.md — PsycheOS Backend

## Project Overview

PsycheOS Backend is a single FastAPI service that handles Telegram webhooks for **5 Telegram bots** powering a psychological AI assistant platform for specialists (psychologists, coaches, etc.).

- **Framework**: FastAPI + async SQLAlchemy (asyncpg)
- **Database**: PostgreSQL via Supabase (connection pooler in production)
- **Telegram**: `python-telegram-bot` 21.x (webhook mode only, no polling)
- **AI**: Anthropic Claude API (integrated in future phases)
- **Monitoring**: Sentry
- **Deployment**: Railway (Procfile-based)
- **Current phase**: Phase 7 done (Billing ✅) → Phase 6b Screen v2 next

---

## Repository Structure

```
psycheos-production/
├── app/
│   ├── main.py               # FastAPI app entry point; registers all webhook routers
│   ├── config.py             # All settings via pydantic-settings (env vars)
│   ├── database.py           # Async SQLAlchemy engine + session factory
│   ├── models/
│   │   ├── user.py           # User (specialist/client) — table: users
│   │   ├── invite.py         # Invite tokens — table: invites
│   │   ├── context.py        # Case/client context — table: contexts
│   │   ├── bot_chat_state.py # FSM state per (bot, chat) — table: bot_chat_state
│   │   ├── telegram_dedup.py # Dedup table — table: telegram_update_dedup
│   │   ├── link_token.py     # Link tokens (Phase 3) — table: link_tokens
│   │   ├── artifact.py       # Tool-bot outputs (Phase 5) — table: artifacts
│   │   ├── job.py            # Async job queue (Phase 6) — table: jobs
│   │   ├── outbox_message.py # Telegram outbox (Phase 6) — table: outbox_messages
│   │   ├── wallet.py         # Per-user Stars balance (Phase 7) — table: wallets
│   │   ├── usage_ledger.py   # Stars audit log (Phase 7) — table: usage_ledger
│   │   └── ai_rate.py        # Pre-calc pricing (Phase 7) — table: ai_rates
│   ├── webhooks/
│   │   ├── router_factory.py    # Generic webhook router factory (shared pipeline)
│   │   ├── common.py            # Shared logic: secret verify, dedup, FSM load/save
│   │   ├── pro.py               # Pro bot handler (Phase 2 — full implementation)
│   │   ├── interpretator.py     # Interpretator bot (Phase 4 ✅ migrated)
│   │   ├── conceptualizator.py  # Conceptualizator bot (Phase 4 ✅ migrated)
│   │   ├── stubs.py             # Screen (stub)
│   │   └── simulator.py         # Simulator bot handler
│   ├── services/
│   │   ├── interpreter/         # Interpreter service modules
│   │   ├── conceptualizer/      # Conceptualizer service modules
│   │   │   ├── enums.py         #   SessionStateEnum, HypothesisType, PsycheLevelEnum, …
│   │   │   ├── models.py        #   Pydantic v2: SessionState, Hypothesis, LayerA/B/C, …
│   │   │   ├── decision_policy.py #  PriorityChecker + QuestionGenerator + selector
│   │   │   ├── analysis.py      #   Async hypothesis extraction via Claude
│   │   │   └── output.py        #   Async three-layer output assembly via Claude
│   │   ├── pro/                 # Pro bot services (Sprint B+)
│   │   │   └── reference_prompt.py  #   REFERENCE_SYSTEM_PROMPT (loads key_psycheos.md)
│   │   ├── job_queue.py         # enqueue / claim_next / mark_done / mark_failed
│   │   ├── outbox.py            # enqueue_message / dispatch_one / make_inline_keyboard / make_document_payload
│   │   ├── artifacts.py         # save_artifact — ON CONFLICT DO NOTHING
│   │   └── billing.py           # Stars accounting: reserve/commit/cancel/credit; commit_by_run_id / cancel_by_run_id
│   ├── worker/
│   │   ├── __init__.py          # Package marker
│   │   ├── __main__.py          # Entry point: python -m app.worker (event loop)
│   │   └── handlers/
│   │       ├── __init__.py      # REGISTRY dict: job_type → handler
│   │       ├── pro.py           # handle_pro_reference (Claude Haiku reference chat)
│   │       ├── interpretator.py # handle_interp_photo / interp_intake / interp_run
│   │       ├── conceptualizator.py # handle_concept_hypothesis / concept_output
│   │       └── simulator.py     # handle_sim_launch / sim_launch_custom / sim_report
│   ├── data/
│   │   └── key_psycheos.md      # PsycheOS theory base — used by reference chat system prompt
│   ├── routers/
│   │   ├── links.py          # POST /v1/links/issue|verify (Phase 3)
│   │   └── artifacts.py      # GET /v1/artifacts[/{id}] (Phase 5)
│   └── utils/
│       └── idempotency.py    # Idempotency key builder (format from Dev Spec Appendix C)
├── scripts/
│   └── set_webhooks.py       # One-shot script to register webhooks with Telegram API
├── Procfile                  # Railway: uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
├── requirements.txt
└── .gitignore
```

---

## The 5 Bots

| Bot ID            | Role                  | Status       | Handler file      |
|-------------------|-----------------------|--------------|-------------------|
| `pro`             | Specialist management | Phase 2 + Sprint B ✅ | `webhooks/pro.py`             |
| `screen`          | Client-facing         | Stub (Phase 4 next)   | `webhooks/stubs.py`           |
| `interpretator`   | AI diagnostic tool    | **Phase 4 ✅ done**   | `webhooks/interpretator.py`   |
| `conceptualizator`| Conceptualization     | **Phase 4 ✅ done**   | `webhooks/conceptualizator.py`|
| `simulator`       | Simulation            | Phase 4 (migrating)   | `webhooks/simulator.py`       |

Each bot has its own Telegram token and webhook secret, all in env vars.

---

## Database Models

### `users` — Specialists and clients
- `user_id` UUID PK (gen_random_uuid)
- `telegram_id` BigInteger UNIQUE — Telegram user ID
- `role` — `"specialist"` | `"client"`
- `status` — `"active"` | `"blocked"`
- `username`, `full_name` — from Telegram profile

### `invites` — Access control
- `token` String PK — short random hex (16 chars, `secrets.token_hex(8)`)
- `created_by` — admin telegram_id
- `max_uses`, `used_count` — one-time use by default
- `expires_at` — 7-day TTL set on creation

### `contexts` — Cases/clients
- `context_id` UUID PK
- `specialist_user_id` FK → `users.user_id`
- `client_ref` — specialist's internal label for the client (e.g., name or code)
- `status` — `"active"` | `"archived"`

### `bot_chat_state` — FSM state per (bot, chat)
- PK: `(bot_id, chat_id)`
- `state` String — current FSM state name (e.g., `"main_menu"`, `"waiting_case_name"`)
- `state_payload` JSONB — arbitrary step-local data
- `context_id` UUID nullable — active case being worked on
- `role` — `"specialist"` | `"client"`
- Survives process restarts and replica switches

### `telegram_update_dedup` — Exactly-once processing
- PK: `(bot_id, update_id)` — prevents double-processing on webhook retries
- INSERT ... ON CONFLICT DO NOTHING — if rowcount=0 → duplicate, skip

### `artifacts` — Persisted tool-bot session outputs (Phase 5)
- `artifact_id` UUID PK (gen_random_uuid)
- `context_id` UUID FK → `contexts.context_id` ON DELETE CASCADE
- `service_id` VARCHAR — `"interpretator"` | `"conceptualizator"` | `"simulator"`
- `run_id` UUID — = `link_token.jti`; idempotency key per session
- `specialist_telegram_id` BigInteger — denormalised Telegram ID (avoids user JOIN)
- `payload` JSONB — full structured output (service-specific)
- `summary` TEXT nullable — 1-2 line description shown in Pro bot list
- `created_at` TIMESTAMPTZ
- UNIQUE(run_id, service_id) — one artifact per run, idempotent on retry
- INDEX(context_id, created_at DESC) — primary list access pattern

---

## Webhook Processing Pipeline

Every incoming Telegram update goes through this pipeline (in `router_factory.py`):

```
POST /webhook/{bot_id}
  1. Verify X-Telegram-Bot-Api-Secret-Token header → 403 if invalid
  2. Parse JSON → telegram.Update object
  3. Extract chat_id, user_id
  4. Deduplicate by (bot_id, update_id) → return 200 if duplicate
  5. Load BotChatState from DB for (bot_id, chat_id)
  6. Call bot-specific handler(update, bot, db, state, chat_id, user_id)
  7. db.commit()
  8. Return {"ok": True} (always 200 — never let Telegram retry app errors)
```

**Critical**: Always return HTTP 200 to Telegram even on handler exceptions. Errors are logged to Sentry. Returning non-200 causes Telegram to retry the update indefinitely.

---

## Pro Bot FSM States

| State                | Trigger                         | Description                        |
|----------------------|---------------------------------|------------------------------------|
| `main_menu`          | `/start` (registered user)      | Main specialist menu               |
| `admin_panel`        | `/admin` (admin only)           | Admin panel                        |
| `waiting_case_name`  | "➕ Новый кейс" button          | Waiting for specialist to type case name |
| `waiting_invite_note`| "🔗 Создать приглашение" button | Waiting for admin to type invite note |
| `reference_chat`     | "📚 Справочник" button          | Multi-turn Q&A with Claude Haiku about PsycheOS theory; history in `state_payload["reference_history"]` (last 10 pairs) |

---

## Configuration (Environment Variables)

All settings are loaded via pydantic-settings from `.env` file (never committed).

```env
# Database
DATABASE_URL_POOLER=postgresql+asyncpg://...  # Used at runtime (Supabase pooler, port 6543)
DATABASE_URL=postgresql+asyncpg://...          # Direct URL — only for Alembic migrations

DB_POOL_SIZE=5        # Per-process pool (keep low — multiple replicas share Supabase)
DB_MAX_OVERFLOW=5

# Monitoring
SENTRY_DSN=           # Optional — enables Sentry if set

# AI
ANTHROPIC_API_KEY=

# Telegram bot tokens
TG_TOKEN_PRO=
TG_TOKEN_SCREEN=
TG_TOKEN_INTERPRETATOR=
TG_TOKEN_CONCEPTUALIZATOR=
TG_TOKEN_SIMULATOR=

# Telegram webhook secrets (random strings, set per-bot)
TG_WEBHOOK_SECRET_PRO=
TG_WEBHOOK_SECRET_SCREEN=
TG_WEBHOOK_SECRET_INTERPRETATOR=
TG_WEBHOOK_SECRET_CONCEPTUALIZATOR=
TG_WEBHOOK_SECRET_SIMULATOR=

# Admin
ADMIN_IDS=123456789,987654321  # Comma-separated Telegram user IDs

# App
WEBHOOK_BASE_URL=https://your-app.railway.app
DEBUG=false
```

`settings.admin_ids` returns a `set[int]`. `settings.bot_config` returns `{bot_id: (token, secret)}`.

---

## Development Workflow

### Local Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env  # Fill in your secrets
```

### Run Locally

```bash
uvicorn app.main:app --reload --port 8000
```

Tables are created automatically on startup via `Base.metadata.create_all` (lifespan event). No migrations needed for new local environments.

### Database Migrations (Alembic)

Use `DATABASE_URL` (direct connection, not pooler) for migrations:

```bash
# Generate migration
alembic revision --autogenerate -m "describe change"

# Apply migrations
alembic upgrade head
```

**Important**: Never use `DATABASE_URL_POOLER` with Alembic — it requires a direct connection.

### Registering Webhooks

After deploying to Railway, run once to register webhook URLs with Telegram:

```bash
python -m scripts.set_webhooks
```

Requires `WEBHOOK_BASE_URL` to be set. Registers all 5 bots with `drop_pending_updates=True`.

---

## Key Conventions

### Adding a New Bot Handler

1. Add tokens/secrets to `config.py` Settings class and env vars
2. Create handler function with signature:
   ```python
   async def handle_mybotname(
       update: Update, bot: Bot, db: AsyncSession,
       state: BotChatState | None, chat_id: int, user_id: int | None,
   ) -> None:
   ```
3. Import handler in `main.py` and add to `bot_handlers` dict
4. The router factory handles all infrastructure automatically

### FSM State Transitions

Always use `upsert_chat_state()` from `app/webhooks/common.py` to persist state changes:

```python
await upsert_chat_state(db, bot_id="pro", chat_id=chat_id, state="new_state", user_id=user_id)
```

Uses INSERT ... ON CONFLICT UPDATE — safe for concurrent requests.

### Database Queries

Use async SQLAlchemy patterns:
```python
result = await db.execute(select(Model).where(Model.field == value))
obj = result.scalar_one_or_none()
```

Never use synchronous SQLAlchemy methods. All DB work happens within the session injected by `get_db()`.

### Supabase Pooler Compatibility

The engine is configured with:
```python
connect_args={"statement_cache_size": 0, "prepared_statement_cache_size": 0}
```
This is **required** — Supabase's PgBouncer pooler does not support prepared statements.

### Idempotency Keys

Use `make_idempotency_key()` from `app/utils/idempotency.py` for deterministic keys:

```python
key = make_idempotency_key(
    scope=SCOPE_TG_UPDATE,
    service_id="pro",
    actor_id=f"tg:{user_id}",
    step=f"upd:{update_id}",
)
```

Format: `scope|service_id|run_id|context_id|actor_id|step|fingerprint`. No timestamps — they break idempotency.

---

## Deployment (Railway)

- **Processes** (Procfile):
  - `web: uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}`
  - `worker: python -m app.worker` — async Claude job processor (Phase 6)
- **Environment**: Set all env vars in Railway dashboard
- **Database**: Supabase PostgreSQL — use pooler URL for the app, direct URL for migrations only
- **Scaling**: Keep `DB_POOL_SIZE` low (5) per replica — Supabase free tier has connection limits; one worker process is sufficient for ≤30 users
- **Sentry**: Set `SENTRY_DSN` for error tracking; `environment` auto-set based on `DEBUG` flag
- **Webhook registration**: Run `python -m scripts.set_webhooks` after each new deployment if the URL changes

---

## Development Phases

| Phase      | Description                                                                        | Status          |
|------------|------------------------------------------------------------------------------------|-----------------|
| 1          | Project skeleton, DB schema, webhook pipeline                                      | ✅ Done         |
| 2          | Pro bot: invite-only registration, cases, admin panel                              | ✅ Done         |
| 3          | Link tokens (passes), run_id, tool launcher in Pro, verify in tool bots            | ✅ Done         |
| 4          | Screen/Interpretator/Conceptualizator/Simulator full logic                         | ✅ Done (Interpretator + Conceptualizator ✅; Simulator migrated ✅) |
| Sprint B   | Pro bot reference chat — Claude Haiku Q&A on PsycheOS theory                      | ✅ Done         |
| **5**      | **Artifacts — persistent storage of tool outputs; HTTP API; Pro bot integration**  | ✅ **Done**     |
| **6**      | **Worker + Outbox — async Claude jobs; webhooks return instant ack**               | ✅ **Done**     |
| 6b         | Screen v2 — new question bank, scales, client session flow                         | Planned         |
| **7**      | **Billing — Telegram Stars: wallets, reserve/commit/cancel, admin finance UI**     | ✅ **Done**     |

---

## Health Check

```
GET /health → {"status": "ok", "version": "0.1.0"}
```

No authentication required. Used by Railway for healthchecks.

---

## Статус ботов (актуальный)

| Бот              | Статус                      | Примечание                                                                                                         |
|------------------|-----------------------------|--------------------------------------------------------------------------------------------------------------------|
| Pro              | ✅ Phase 7 done             | Stars billing: reserve при launch, commit/cancel через worker. successful_payment → credit_stars. Админ: финансы + начисление Stars |
| Screen           | Stub → Phase 6b             | Новый банк вопросов, шкалы и логика работы — Phase 6b                                                             |
| Interpreter      | ✅ Phase 6 async            | Webhook enqueue interp_photo / interp_intake / interp_run. Результаты через outbox                                |
| Conceptualizer   | ✅ Phase 6 async            | Webhook enqueue concept_hypothesis / concept_output. Layer A/B/C через outbox                                     |
| Simulator        | ✅ Phase 6 async            | Launch/report через worker. Активный ход (_handle_specialist_message) — синхронный (trade-off UX)                 |

---

## Порядок работы

1. ✅ Interpreter — мигрирован (`app/webhooks/interpretator.py`)
2. ✅ Conceptualizer — мигрирован (`app/webhooks/conceptualizator.py` + `app/services/conceptualizer/`)
3. ✅ Simulator — мигрирован (`app/webhooks/simulator.py`)
4. ✅ Sprint B — Pro Справочник (`app/services/pro/reference_prompt.py`, `reference_chat` FSM)
5. ✅ **Phase 5 — Artifacts** (`artifacts` table, `save_artifact` service, hooks in 3 bots, `GET /v1/artifacts` API, Pro bot UI)
6. ✅ **Phase 6 — Worker + Outbox** (`jobs` + `outbox_messages` tables, `app/worker/`, рефакторинг 4 webhook-обработчиков, Procfile `worker:`)
7. ✅ **Phase 7 — Billing** (`wallets`, `usage_ledger`, `ai_rates` tables; `app/services/billing.py`; Stars reserve/commit/cancel в Pro + worker)
8. ⬜ Phase 6b — Screen v2 — новый банк вопросов + логика

---

## Принятые решения (НЕ МЕНЯТЬ)

- **LinkToken:** `jti` UUID как PK (`gen_random_uuid()`), `UNIQUE(service_id, run_id)` — одна активная сессия на пару (сервис, запуск), `subject_id` = `telegram_id` пользователя, которому выдан пропуск
- **Alembic:** async через `create_async_engine` + asyncpg, URL берётся из `settings.DATABASE_URL` (прямое соединение, не pooler)
- **Порядок применения миграций:** `alembic upgrade head` применяем только после завершения всех шагов Фазы 3 — не раньше
- **issue_link / verify_link:** вызываются напрямую из webhook-обработчиков (не через HTTP); HTTP-эндпоинты `/v1/links/*` — для тестирования и внешнего API
- **Правило 3.4:** `role=client` допустим только для `service_id=screen`; verify для любого другого сервиса с client-токеном → reject
- **TOKEN_TTL:** 24 часа
- **start_param:** `str(jti)` — полный UUID со скобками, вставляется напрямую в `t.me/BotName?start={jti}`
- **Deep-link формат:** `t.me/{bot_username}?start={jti}`, где `bot_username` берётся из `TG_USERNAME_*` env-переменных (без `@`)
- **Callback pattern в Pro:** `launch_{service_id}_{context_id}` (split по `_` с maxsplit=2, UUID без изменений)
- **run_id в FSM:** после успешного verify сохраняется в `BotChatState.state_payload["run_id"]`; `context_id` — в `BotChatState.context_id`
- **subject_id=0:** открытый токен для клиентского Screen — telegram_id клиента неизвестен в момент выдачи; `verify_link` пропускает проверку subject_id если `token.subject_id == 0`
- **Callback для клиентской ссылки:** `screen_link_{context_id}` (отдельный паттерн от `launch_`, т.к. разные role и subject_id)
- **Хранение сессии в tool-ботах:** Redis отсутствует; полное состояние сессии (Pydantic-модель) сериализуется в `state_payload["session"]` через `model.model_dump(mode="json")` и восстанавливается через `Model.model_validate(data)`. `bot_chat_state.state` дублирует `session.state.value` для маршрутизации без десериализации
- **Pydantic v2:** все сервисные модели (`app/services/*/models.py`) используют Pydantic v2 API (`model_dump`, `model_validate`). Совместимость v1-стиля (`class Config`) в оригинальных ботах не переносится
- **Reference chat history:** хранится в `state_payload["reference_history"]` как список `{"role": "user"|"assistant", "content": str}`. Передаётся в Claude API полностью при каждом запросе (windowed: последние 10 пар). Ошибка API → user-friendly сообщение + логирование; история при ошибке тоже сохраняется
- **reference_prompt.py:** загружает `app/data/key_psycheos.md` один раз при импорте модуля (`_THEORY_FILE.read_text()`). `REFERENCE_SYSTEM_PROMPT` — строковая константа. Обновление теоретической базы = обновление файла + редеплой
- **Модель для Справочника:** `claude-haiku-4-5-20251001`, `max_tokens=1024`. Haiku выбран как token-efficient для FAQ-паттерна; при необходимости глубокой аналитики — заменить на Sonnet
- **Artifacts — idempotency:** `UNIQUE(run_id, service_id)` + `INSERT ... ON CONFLICT DO NOTHING`. `run_id` = `link_token.jti` (UUID). Повторный webhook-вызов → тихое игнорирование дубля. `save_artifact` не бросает исключений — ошибки логируются, обработчик продолжает работу
- **Artifacts — specialist_telegram_id:** денормализованный BigInteger (Telegram ID). Избегает JOIN с `users` в tool-ботах; Pro bot фильтрует по `context_id`, не по `specialist_telegram_id`
- **Artifacts — payload structure:** интерпретатор: `{meta, txt_report, structured}`. Концептуализатор: `{layer_a, layer_b, layer_c, meta}`. Симулятор: `{tsi, cci, session_turns, report_text, profile}`. `report_text` в симуляторе сохраняется в обоих путях (.docx и fallback)
- **Artifacts — Pro UI routing:** `case_artifacts_{context_id}` и `artifact_{artifact_id}` обрабатываются **до** generic `if data.startswith("case_")` — иначе `case_artifacts_` перехватывается generic-обработчиком. Всегда добавлять специфичные `startswith` паттерны выше generic
- **Artifacts — HTTP API:** `GET /v1/artifacts?context_id=...` → список (без payload, max 20). `GET /v1/artifacts/{artifact_id}` → полный артефакт с payload. Авторизация отсутствует (внутренний API, аналогично `/v1/links/*`)
- **Worker — без Redis:** очередь заданий на базе PostgreSQL (`jobs` table), `FOR UPDATE SKIP LOCKED` — безопасный параллельный claim без внешних зависимостей
- **Worker — job lifecycle:** `pending → running → done | failed`. Экспоненциальный backoff при сбое: `30s × 2^(attempts-1)`, max 3 попытки. После исчерпания → `status='failed'`, `last_error` сохраняется
- **Worker — outbox:** отправка Telegram-сообщений через `outbox_messages` table. Поле `seq` гарантирует порядок нескольких сообщений одного job. Бинарные файлы (.docx, .txt, .json) хранятся в JSONB как base64 (`document_b64` ключ), декодируются в `_send()` перед отправкой
- **Worker — InlineKeyboardMarkup:** сериализуется в JSONB как `{"inline_keyboard": [[{"text": ..., "callback_data": ...}]]}`, десериализуется через `InlineKeyboardMarkup.de_json(data, bot)` в `dispatch_one()`
- **Worker — claim/execute разделены:** `claim_next()` коммитит переход в `running` отдельной транзакцией; handler запускается в новой сессии. При сбое handler — `mark_failed()` открывает ещё одну сессию. Rollback handler'а не откатывает claim
- **Worker — sim active turn синхронный:** `_handle_specialist_message` в `simulator.py` вызывает Claude напрямую (осознанный trade-off). Причина: высокая частота реплик, специалист ожидает мгновенного ответа
- **Worker — pro_reference:** webhook сохраняет user-turn в history, затем enqueue. Worker делает Claude-вызов, апдейтит историю (trim до 10 пар), сохраняет state, отправляет ответ через outbox
- **Worker — chained jobs:** `interp_intake` при принятии материала enqueue-ит `interp_run`. `concept_hypothesis` при `should_continue=False` enqueue-ит `concept_output`. Цепочки формируются внутри worker-обработчиков, не в webhook
- **Worker — job payload:** всегда содержит `state_payload` (dict из `bot_chat_state`) и `role`. Доп. поля: `image_b64`/`image_media_type` (interp_photo), `run_mode` (interp_run retry), `session` (conceptualizer/simulator), `message_text` (concept_hypothesis), `case_key`/`goal`/`mode`/`crisis` (sim_launch), `custom_data`/`crisis_value` (sim_launch_custom)
- **Worker — Procfile:** `worker: python -m app.worker`. На Railway — отдельный process type. Один воркер достаточен для ≤30 пользователей. Масштабировать горизонтально при росте нагрузки (FOR UPDATE SKIP LOCKED безопасен для N воркеров)
- **Billing — две фазы:** reserve при запуске инструмента (reserved_stars += N), commit при успехе terminal-job (balance -= N, reserved -= N, lifetime_out += N), cancel при permanent failure (reserved -= N). `available = balance_stars − reserved_stars`. Источник правды — колонки `wallets`, не сумма ledger
- **Billing — run_id как ключ:** резервирование привязывается к `link_token.jti` (UUID). Worker ищет `usage_ledger WHERE kind='reserve' AND run_id=run_id` для commit/cancel. Не требует изменений в link_token или job payload
- **Billing — idempotency:** `commit_by_run_id` / `cancel_by_run_id` сначала проверяют наличие `kind IN ('charge','refund')` для run_id — повторный вызов → return False без изменений
- **Billing — TERMINAL_JOB_TYPES:** `{interp_run, concept_output, sim_launch, sim_launch_custom}` — только эти типы триггерят commit/cancel. Промежуточные (interp_photo, interp_intake, concept_hypothesis) — нет
- **Billing — ai_rates seed:** interpretator/session=20⭐, conceptualizator/session=12⭐, simulator/session=15⭐, simulator/active_turn=3⭐, pro/reference=1⭐. Цены pre-calculated по формуле `ceil((in × in_$/tok + out × out_$/tok + total × 2/1M) × 1.2 / 0.01)`. Обновление цены = UPDATE ai_rates (без кода)
- **Billing — invoice при нехватке Stars:** `handle_launch_tool()` вызывает `bot.send_invoice(currency="XTR")`. Сумма = max(10, ceil(shortfall/10)×10). payload=`"topup:{user_id}"`. pre_checkout_query → auto-approve. successful_payment → `credit_stars(kind="topup")`
- **Billing — admin credit:** FSM state `waiting_admin_credit`. Формат ввода `telegram_id:stars`. `credit_stars(kind="admin_credit")` без проверки лимита. Доступно только из `settings.admin_ids`
- **Billing — без бесплатных fallback:** если запись в `ai_rates` отсутствует (`get_stars_price → None`) — инструмент запускается бесплатно (backward-compat). Это осознанное решение, не ошибка
- **extract_chat_id / pre_checkout_query:** `from_user.id` используется как `chat_id` для pre_checkout_query (в приватных чатах chat_id == user_id). Дедупликация по update_id работает корректно
