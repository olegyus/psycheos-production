# CLAUDE.md — PsycheOS Backend

## Project Overview

PsycheOS Backend is a single FastAPI service that handles Telegram webhooks for **5 Telegram bots** powering a psychological AI assistant platform for specialists (psychologists, coaches, etc.).

- **Framework**: FastAPI + async SQLAlchemy (asyncpg)
- **Database**: PostgreSQL via Supabase (connection pooler in production)
- **Telegram**: `python-telegram-bot` 21.x (webhook mode only, no polling)
- **AI**: Anthropic Claude API (integrated in future phases)
- **Monitoring**: Sentry
- **Deployment**: Railway (Procfile-based)
- **Current phase**: Phase 2 — Pro bot fully implemented; other bots are stubs awaiting Phase 4

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
│   │   └── telegram_dedup.py # Dedup table — table: telegram_update_dedup
│   ├── webhooks/
│   │   ├── router_factory.py # Generic webhook router factory (shared pipeline)
│   │   ├── common.py         # Shared logic: secret verify, dedup, FSM load/save
│   │   ├── pro.py            # Pro bot handler (Phase 2 — full implementation)
│   │   └── stubs.py          # Screen/Interpretator/Conceptualizator/Simulator (stubs)
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
| `pro`             | Specialist management | Phase 2 done | `webhooks/pro.py` |
| `screen`          | Client-facing         | Stub (Phase 4) | `webhooks/stubs.py` |
| `interpretator`   | AI diagnostic tool    | Stub (Phase 4) | `webhooks/stubs.py` |
| `conceptualizator`| Conceptualization     | Stub (Phase 4) | `webhooks/stubs.py` |
| `simulator`       | Simulation            | Stub (Phase 4) | `webhooks/stubs.py` |

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

- **Process**: `web: uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}` (Procfile)
- **Environment**: Set all env vars in Railway dashboard
- **Database**: Supabase PostgreSQL — use pooler URL for the app, direct URL for migrations only
- **Scaling**: Keep `DB_POOL_SIZE` low (5) per replica — Supabase free tier has connection limits
- **Sentry**: Set `SENTRY_DSN` for error tracking; `environment` auto-set based on `DEBUG` flag
- **Webhook registration**: Run `python -m scripts.set_webhooks` after each new deployment if the URL changes

---

## Development Phases

| Phase | Description                                                  | Status     |
|-------|--------------------------------------------------------------|------------|
| 1     | Project skeleton, DB schema, webhook pipeline                | Done       |
| 2     | Pro bot: invite-only registration, cases, admin panel        | Done       |
| 3     | Case tool launcher UI (Pro bot)                              | Planned    |
| 4     | Screen/Interpretator/Conceptualizator/Simulator full logic   | Planned    |
| 5     | Claude AI integration for analysis tools                     | Planned    |
| 6     | Client-side (Screen bot) session flow                        | Planned    |
| 7     | Billing (Telegram Stars)                                     | Planned    |

---

## Health Check

```
GET /health → {"status": "ok", "version": "0.1.0"}
```

No authentication required. Used by Railway for healthchecks.
