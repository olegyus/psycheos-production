# CLAUDE.md — PsycheOS Backend

## Project Overview

PsycheOS Backend is a single FastAPI service that handles Telegram webhooks for **5 Telegram bots** powering a psychological AI assistant platform for specialists (psychologists, coaches, etc.).

- **Framework**: FastAPI + async SQLAlchemy (asyncpg)
- **Database**: PostgreSQL via Supabase (connection pooler in production)
- **Telegram**: `python-telegram-bot` 21.x (webhook mode only, no polling)
- **AI**: Anthropic Claude API ✅ интегрирован — Simulator, Conceptualizer, Interpreter используют `claude-sonnet-4-5-20250929`; Screen v2 — при генерации отчёта (3 Claude-вызова: structural_report + session_bridge + client_summary)
- **Monitoring**: Sentry
- **Deployment**: Railway (Procfile-based)
- **Current phase**: Phase 6 **COMPLETE** + Screen report enhancements ✅ (client_summary + DOCX bug fixes); next: Phase 7

---

## Repository Structure

```
psycheos-production/
├── app/
│   ├── main.py               # FastAPI app entry point; registers all webhook routers
│   ├── config.py             # All settings via pydantic-settings (env vars)
│   ├── database.py           # Async SQLAlchemy engine + session factory
│   ├── models/
│   │   ├── user.py                    # User (specialist/client) — table: users
│   │   ├── invite.py                  # Invite tokens — table: invites
│   │   ├── context.py                 # Case/client context — table: contexts
│   │   ├── bot_chat_state.py          # FSM state per (bot, chat) — table: bot_chat_state
│   │   ├── telegram_dedup.py          # Dedup table — table: telegram_update_dedup
│   │   └── screening_assessment.py    # Screen v2 assessment — table: screening_assessment ✅
│   ├── webhooks/
│   │   ├── router_factory.py    # Generic webhook router factory (shared pipeline)
│   │   ├── common.py            # Shared logic: secret verify, dedup, FSM load/save
│   │   ├── pro.py               # Pro bot handler (Phase 2 — full implementation)
│   │   ├── interpretator.py     # Interpretator bot (Phase 4 ✅ migrated)
│   │   ├── conceptualizator.py  # Conceptualizator bot (Phase 4 ✅ migrated)
│   │   ├── screen.py            # Screen v2 bot — full FSM handler ✅
│   │   ├── simulator.py         # Simulator bot — full FSM handler ✅
│   │   └── stubs.py             # (пустой — все боты мигрированы)
│   ├── services/
│   │   ├── interpreter/         # Interpreter service modules
│   │   ├── conceptualizer/      # Conceptualizer service modules
│   │   │   ├── enums.py         #   SessionStateEnum, HypothesisType, PsycheLevelEnum, …
│   │   │   ├── models.py        #   Pydantic v2: SessionState, Hypothesis, LayerA/B/C, …
│   │   │   ├── decision_policy.py #  PriorityChecker + QuestionGenerator + selector
│   │   │   ├── analysis.py      #   Async hypothesis extraction via Claude
│   │   │   └── output.py        #   Async three-layer output assembly via Claude
│   │   ├── screen/              # Screen v2 service modules ✅
│   │   │   ├── engine.py        #   ScreeningEngine: vector aggregation, tension matrix, rigidity, confidence ✅
│   │   │   ├── weight_matrix.py #   PHASE1_SCREENS (6) + PHASE2_TEMPLATES (20 nodes) with axis/layer weights ✅
│   │   │   ├── screen_bank.py   #   get_phase1_screen / get_phase2_template / get_all_phase2_nodes ✅
│   │   │   ├── prompts.py       #   6 Claude prompts (router/constructor/report/client_report/session_bridge/stop) + assemble_prompt() ✅
│   │   │   ├── orchestrator.py  #   ScreenOrchestrator: 3-phase flow, Claude routing, stop decision ✅
│   │   │   └── report.py        #   generate_full_report / generate_client_summary / format_report_txt / generate_report_docx ✅
│   │   └── simulator/           # Simulator service modules ✅
│   │       ├── schemas.py       #   Pydantic v2: SessionData, FSMState, SpecialistProfile, TSIComponents, …
│   │       ├── cases.py         #   BUILTIN_CASES (3 встроенных кейса)
│   │       ├── goals.py         #   GOAL_LABELS, MODE_LABELS
│   │       ├── system_prompt.py #   build_system_prompt(case, goal, mode) → str
│   │       ├── formatter.py     #   parse_claude_response, format_for_telegram, build_iteration_log
│   │       └── report_generator.py # generate_report_docx() → io.BytesIO
│   └── utils/
│       └── idempotency.py    # Idempotency key builder (format from Dev Spec Appendix C)
├── scripts/
│   └── set_webhooks.py       # One-shot script to register webhooks with Telegram API
├── tests/
│   ├── __init__.py
│   └── test_engine.py        # 31 unit tests for ScreeningEngine ✅
├── Procfile                  # Railway: uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
├── requirements.txt
└── .gitignore
```

---

## The 5 Bots

| Bot ID            | Role                  | Status       | Handler file      |
|-------------------|-----------------------|--------------|-------------------|
| `pro`             | Specialist management | Phase 2 done       | `webhooks/pro.py`             |
| `screen`          | Client-facing         | **Phase 6+ ✅ done** | `webhooks/screen.py`         |
| `interpretator`   | AI diagnostic tool    | **Phase 5 ✅ done** | `webhooks/interpretator.py`  |
| `conceptualizator`| Conceptualization     | **Phase 4 ✅ done** | `webhooks/conceptualizator.py` |
| `simulator`       | Simulation            | **Phase 4 ✅ done** | `webhooks/simulator.py`       |

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

## Interpretator Bot FSM States

| State               | Trigger                             | Description                                                   |
|---------------------|-------------------------------------|---------------------------------------------------------------|
| `active`            | `/start {jti}` verified             | Session open; awaiting first material from specialist         |
| `intake`            | Claude asks clarifying Q in INTAKE  | Awaiting specialist's answer before material check            |
| `clarification_loop`| `completeness != "sufficient"`      | Material partial/fragmentary; Claude asks phenomenological Qs (max 2 iterations) |
| `completed`         | Interpretation sent                 | Session closed; further messages rejected                     |

Flow: `active` → specialist types material → `_run_intake` (Claude INTAKE prompt, may set `intake`) → `_run_material_check` (Claude MATERIAL_CHECK prompt) → if sufficient: `_run_interpretation`; else → `clarification_loop` (Claude CLARIFICATION_LOOP prompt, max 2 rounds) → `_run_interpretation` → `completed`.

---

## Screen v2 Report Pipeline

`generate_full_report(state, claude_client)` выполняет 3 последовательных Claude-вызова:

```
Claude call 1 → structural_report   (REPORT_GENERATOR_PROMPT,  max_tokens=1500, model=sonnet)
Claude call 2 → interview_protocol  (SESSION_BRIDGE_PROMPT,    max_tokens=1500, model=sonnet)
Claude call 3 → client_summary      (CLIENT_REPORT_PROMPT,     max_tokens=500,  model=sonnet)
```

`report_json` содержит все три результата. `generate_report_docx` строит DOCX с секциями:
1. Профиль осей регуляции (таблица)
2. Доминирующие слои (таблица)
3. Ключевые сочетания L×A
4. Индекс гибкости
5. Пояснение — `structural_report`, рендеренный через `_render_markdown_text()`
6. Ориентиры для первой сессии — `interview_protocol`
7. Профиль для клиента — `client_summary`, рендеренный через `_render_markdown_text()` (только если непустой)

`_render_markdown_text(doc, text)` — inner-функция внутри `generate_report_docx`: строки `## ` → `_heading(level=3)` с удалением числового префикса `"N. "` / `"N) "`; остальные непустые строки → `_para()`.

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

**Alembic migrations (production):** На данный момент существует только одна миграция — `0001_create_link_tokens.py`. Таблица `screening_assessment` создана через `create_all` (не через Alembic). Перед следующим `alembic upgrade head` нужно сгенерировать `0002_create_screening_assessment.py`.

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

| Phase | Description                                                                        | Status          |
|-------|------------------------------------------------------------------------------------|-----------------|
| 1     | Project skeleton, DB schema, webhook pipeline                                      | Done            |
| 2     | Pro bot: invite-only registration, cases, admin panel                              | Done            |
| 3     | Link tokens (passes), run_id, tool launcher in Pro, verify in tool bots            | **Done**        |
| 4     | Screen/Interpretator/Conceptualizator/Simulator full logic                         | **COMPLETE** ✅ (все 5 ботов мигрированы) |
| 5     | Interpreter Claude gaps: `_run_material_check` + `clarification_loop` FSM state + `clarifications_received` | **COMPLETE** ✅ |
| 6     | Screen bot: bug fix `_notify_specialist` + `asked_nodes` dedup + UX (typing, phase transitions, Phase 1 progress) | **COMPLETE** ✅ |
| 6+    | Screen report: `CLIENT_REPORT_PROMPT` + `generate_client_summary` + DOCX fixes (markdown render, max_tokens, client section) | **COMPLETE** ✅ |
| 7     | Billing (Telegram Stars)                                                           | Planned         |

---

## Health Check

```
GET /health → {"status": "ok", "version": "0.1.0"}
```

No authentication required. Used by Railway for healthchecks.

---

## Статус ботов (актуальный)

| Бот              | Статус                    | Примечание                                                                                                    |
|------------------|---------------------------|---------------------------------------------------------------------------------------------------------------|
| Pro              | Требует v2                | Центральный хаб: регистрация, оплата, выход на остальные боты (tool-боты), ИИ-справочник по системе. Текущая версия не адаптирована под продакшн |
| Screen           | ✅ Phase 6+ DONE          | Phase 6 ✅ + report enhancements ✅: CLIENT_REPORT_PROMPT, generate_client_summary, DOCX markdown render, client section in DOCX, max_tokens=1500 |
| Interpreter      | ✅ Phase 5 DONE           | `app/webhooks/interpretator.py`; все Claude-гэпы закрыты: material_check + clarification_loop + clarifications_received |
| Conceptualizer   | ✅ Мигрирован (Phase 4)   | `app/webhooks/conceptualizator.py` + `app/services/conceptualizer/`; оригинал: `./psycheos-conceptualizer`  |
| Simulator        | ✅ Мигрирован (Phase 4)   | `app/webhooks/simulator.py` + `app/services/simulator/`; оригинал: `./psycheos-simulator`                   |

---

## Порядок работы

1. ✅ Interpreter — мигрирован (`app/webhooks/interpretator.py`)
2. ✅ Conceptualizer — мигрирован (`app/webhooks/conceptualizator.py` + `app/services/conceptualizer/`)
3. ✅ Screen v2 — ЗАВЕРШЁН:
   - ✅ Step 1: DB model `screening_assessment`
   - ✅ Step 2: `app/services/screen/engine.py` — ScreeningEngine (31 тест, 31 pass)
   - ✅ Step 3: `weight_matrix.py` (6 экранов, 20 узлов) + `screen_bank.py`
   - ✅ Step 4: `prompts.py` — 5 Claude промптов + `assemble_prompt()`
   - ✅ Step 5: `orchestrator.py` — ScreenOrchestrator (3 фазы, Claude routing, stop decision)
   - ✅ Step 6: `report.py` — generate_full_report / format_report_txt / generate_report_docx
   - ✅ Step 7: `webhooks/screen.py` — полный FSM-обработчик клиентского бота
   - ✅ Step 8: `webhooks/pro.py` — screen_menu/create/results коллбэки; кнопка «📊 Скрининг»
   - ✅ Step 9: `main.py` + `models/__init__.py` — интеграция Screen v2
4. ✅ Simulator — мигрирован (`app/webhooks/simulator.py` + `app/services/simulator/`)
5. ✅ Interpreter — Claude-гэпы закрыты (Phase 5):
   - ✅ Gap 3: `clarifications_received[]` заполняется при ответах в состояниях `intake` и `clarification_loop`
   - ✅ Gap 1: `_run_material_check()` с `MATERIAL_CHECK_PROMPT`; JSON-ответ `completeness`; роутинг в `clarification_loop` если не "sufficient"
   - ✅ Gap 2: FSM-состояние `clarification_loop` с `CLARIFICATION_LOOP_PROMPT`; max 2 итерации, затем fallthrough в `_run_interpretation`
6. ✅ Screen — UX/bug fixes (Phase 6):
   - ✅ Fix 1: `_notify_specialist` — брать `specialist_user_id` (BigInteger Telegram ID) из `ScreeningAssessment`, не из `Context`
   - ✅ Fix 2: `asked_nodes` dedup — `node`+`phase` в `response_history`; Phase 2/3 routing исключает уже заданные узлы
   - ✅ Fix 3: `send_chat_action("typing")` + `⏳ Анализирую...` перед генерацией отчёта
   - ✅ Fix 4: Сообщения при переходе между фазами (phase1→phase2, phase2→phase3)
   - ✅ Fix 5: `_show_multi_select(header=...)` — "📋 Вопрос N из 6" в Phase 1
7. ✅ Screen report — client summary + DOCX fixes (Phase 6+):
   - ✅ `CLIENT_REPORT_PROMPT` добавлен в `prompts.py` как Prompt 5; `PHASE2_STOP_PROMPT` переименован в Prompt 6; добавлен в `_PROMPT_REGISTRY["client_report"]`
   - ✅ `generate_client_summary(state, claude_client) → str` — 3-й Claude-вызов в `generate_full_report`; контекст: `StructuralSummary` + `Confidence`; max_tokens=500; fallback → `""`
   - ✅ `report_json["client_summary"]` — новый ключ, обратно совместим (старые отчёты без ключа не ломаются)
   - ✅ Bug fix: `max_tokens` для `structural_report` 800 → 1500 (русский текст ~1.5–2 токена/слово)
   - ✅ Bug fix: `_render_markdown_text(doc, text)` — inner-функция в `generate_report_docx`; парсит `##`-заголовки → `_heading(level=3)` + body → `_para()`; числовые префиксы `"N. "` / `"N) "` удаляются
   - ✅ Bug fix: `_para(doc, structural)` заменён на `_render_markdown_text(doc, structural)` в секции «Пояснение»
   - ✅ Секция «Профиль для клиента» добавлена в DOCX перед `buf.save()`; использует `_render_markdown_text`; пропускается если `client_summary` пустой

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
- **Screen v2 callback pattern в Pro:** `screen_menu_{context_id}` → статус + кнопки; `screen_create_{context_id}` → создать ScreeningAssessment + token; `screen_results_{assessment_id}` → отправить txt/json/docx
- **Screen FSM states:** `idle/None` → `/start {jti}` → verify token + load assessment; `active` → start_screening; `phase1/phase2/phase3` → toggle_{i} + confirm_selection; `completed` → финал
- **specialist_user_id в ScreeningAssessment:** Telegram ID специалиста (BigInteger), используется для уведомления через Pro-бота при завершении скрининга
- **Хранение сессии в tool-ботах:** Redis отсутствует; полное состояние сессии (Pydantic-модель) сериализуется в `state_payload["session"]` через `model.model_dump(mode="json")` и восстанавливается через `Model.model_validate(data)`. `bot_chat_state.state` дублирует `session.state.value` для маршрутизации без десериализации
- **Pydantic v2:** все сервисные модели (`app/services/*/models.py`) используют Pydantic v2 API (`model_dump`, `model_validate`). Совместимость v1-стиля (`class Config`) в оригинальных ботах не переносится
- **Simulator FSM states:** `setup` (setup_step: mode→case→goal / upload→crisis→goal_practice) → `active` (реплики специалиста → Claude) → `complete`; сессия в `state_payload["session"]` (SessionData), профиль в `state_payload["profile"]` (SpecialistProfile, накопительно)
- **Simulator report:** `generate_report_docx()` возвращает `io.BytesIO` (не путь к файлу); отправляется через `InputFile(buf, filename=...)` как `.docx`
- **Simulator PRACTICE mode:** `custom_prompt` (system prompt + данные специалиста) хранится в `state_payload["custom_prompt"]`; при каждом запросе к Claude берётся оттуда
- **screening_assessment и Alembic:** таблица `screening_assessment` не имеет Alembic-миграции — создаётся через `Base.metadata.create_all` при старте. Миграции Alembic: существует только `0001_create_link_tokens.py`. Следующая генерация: `alembic revision --autogenerate -m "add screening_assessment"` → `0002_...`
- **Claude model (Phase 4):** все tool-боты (Simulator, Conceptualizer, Interpreter, Screen report) используют `claude-sonnet-4-5-20250929` через `AsyncAnthropic` напрямую (не через обёртку). Модель задаётся константой `_ANTHROPIC_MODEL` в каждом модуле.
- **Interpreter FSM states (Phase 5):** `active` → specialist types → `_run_intake` (может перейти в `intake` если Claude задал уточняющий вопрос) → `_run_material_check` (completeness: "sufficient" | "partial" | "fragmentary") → если sufficient: `_run_interpretation` → `completed`; иначе → `clarification_loop` (max `_MAX_CLARIFICATION_ITERATIONS = 2` итерации, затем принудительный fallthrough в interpretation). `clarifications_received[]` в payload накапливает ответы специалиста в обоих состояниях `intake` и `clarification_loop`.
- **Interpreter `_run_material_check` JSON parsing:** Claude получает инструкцию вернуть `{"completeness": "sufficient|partial|fragmentary", "message": "..."}` — парсинг через `_parse_completeness()` с JSON-first, keyword-fallback (fragmentary / partial / default sufficient) подходом.
- **Screen `asked_nodes` deduplication (Phase 6):** каждый entry в `response_history` дополнен полями `node` (str) и `phase` (int). ScreeningEngine игнорирует лишние ключи (читает только `axis_weights`/`layer_weights` через `.get()`). `_asked_nodes(state)` извлекает set узлов из response_history где phase in (2, 3). `_fallback_node(state, exclude)` итерирует сначала ambiguity_zones, затем all_nodes, пропуская exclude; при исчерпании — wrap к первому узлу. Phase 2/3 routing принимает Claude-предложение только если оно не в exclude.
- **Screen `_show_multi_select` header (Phase 6):** опциональный параметр `header: str | None = None`; если задан — prepend к тексту вопроса как `"{header}\n\n{question}"`. В Phase 1 оба call-site передают `f"📋 Вопрос {screen_index + 1} из 6"`. Phase 2/3 передают `None` (переменная длина фаз).
- **Screen `_notify_specialist` fix (Phase 6):** функция принимает `assessment_id_str` + `context_id`; загружает `ScreeningAssessment` по UUID, берёт `assessment.specialist_user_id` (BigInteger Telegram ID) для `chat_id` в Pro-боте. `Context` загружается отдельно только для получения `client_ref` (label). `Context.specialist_user_id` — UUID FK, НЕ Telegram ID.
- **Screen report `CLIENT_REPORT_PROMPT` (Phase 6+):** Prompt 5 в `prompts.py`; запрет использовать axis/layer/score-терминологию; 5 фиксированных секций на русском языке; обращение на "вы"; max 350 слов. Ключ `"client_report"` в `_PROMPT_REGISTRY`. Промпт содержит явные if-then правила трансляции: central_axis, vertical_integration, strategy_repetition, adaptive_depth, Confidence < 0.85.
- **Screen report `generate_client_summary` (Phase 6+):** async, принимает `state` + `claude_client`; контекст = `StructuralSummary` (из `build_structural_summary(state)`) + `Confidence`; `max_tokens=500`; при ошибке возвращает `""` (не ломает пайплайн). Вызывается как 3-й Claude-вызов внутри `generate_full_report` после session bridge.
- **Screen report `max_tokens` для structural_report (Phase 6+):** 800 → 1500. Русский текст ≈ 1.5–2 токена/слово; 600-слов отчёт требует ~1200 токенов.
- **Screen report DOCX `_render_markdown_text` (Phase 6+):** inner-функция внутри `generate_report_docx`, имеет доступ к `_heading` и `_para` (замыкания). Парсинг построчно: строка `## …` → `_heading(level=3)` + strip числового префикса `"N. "` / `"N) "`; пустая строка → skip; остальное → `_para()`. Используется для `structural_report` (секция «Пояснение») и `client_summary` (секция «Профиль для клиента»).
- **Screen report DOCX секция «Профиль для клиента» (Phase 6+):** добавляется последней перед `buf.save()`; пропускается (`if client_summary`) если строка пустая — backward-compatible с отчётами без client_summary в report_json.
