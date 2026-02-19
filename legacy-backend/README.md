# PsycheOS MVP

Система психологического скрининга клиентов до первой терапевтической сессии.

## Описание

PsycheOS помогает терапевтам подготовиться к первой сессии с новым клиентом. Система проводит автоматизированный скрининг через Telegram-бот и генерирует структурированный отчёт с рекомендациями для терапевта.

## Архитектура

```
┌─────────────────────────────────────┐
│  @PsycheOS_Pro (aiogram)            │
│  Telegram-бот для специалистов      │
└─────────────┬───────────────────────┘
              │ HTTP (httpx)
              ▼
┌─────────────────────────────────────┐
│  Backend API (FastAPI)              │
│  - REST endpoints                   │
│  - SQLite + SQLAlchemy              │
│  - Claude API integration           │
└─────────────┬───────────────────────┘
              │ HTTP (httpx)
              ▼
┌─────────────────────────────────────┐
│  @PsycheOS_Client (aiogram)         │
│  Telegram-бот для клиентов          │
└─────────────────────────────────────┘
```

## Технологии

- **Backend**: FastAPI, SQLAlchemy (async), SQLite
- **Telegram**: aiogram 3.x
- **AI**: Claude API (Anthropic)
- **Логирование**: structlog (JSON + console)
- **Package Manager**: uv

## Установка

### 1. Клонирование и зависимости

```bash
# Клонировать репозиторий
cd psycheos-backend

# Установить зависимости через uv
uv sync

# Или через pip
pip install -r requirements.txt
```

### 2. Конфигурация

```bash
# Скопировать пример конфигурации
cp .env.example .env

# Отредактировать .env файл
nano .env
```

Необходимо заполнить:
- `ANTHROPIC_API_KEY` — ключ от Claude API
- `TELEGRAM_PRO_BOT_TOKEN` — токен бота для специалистов
- `TELEGRAM_CLIENT_BOT_TOKEN` — токен бота для клиентов

### 3. Инициализация базы данных

```bash
# Создать базу данных и применить миграции
alembic upgrade head
```

## Запуск

### Режим разработки

Необходимо запустить три процесса:

```bash
# Терминал 1: Backend API
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Терминал 2: Pro Bot (для специалистов)
python bots/pro_bot.py

# Терминал 3: Client Bot (для клиентов)
python bots/client_bot.py
```

### Production

Используйте systemd или Docker для управления процессами.

## Использование

### Для специалиста (Pro Bot)

1. Отправить `/start` боту @PsycheOS_Pro
2. Зарегистрироваться через `/register`
3. Создать сессию через `/new_session`
4. Отправить полученную ссылку клиенту
5. После прохождения скрининга получить результаты

### Для клиента (Client Bot)

1. Перейти по ссылке от терапевта
2. Нажать "Начать скрининг"
3. Отвечать на вопросы (15-20 экранов)
4. По завершении результаты автоматически отправляются терапевту

## API Endpoints

### Specialist

| Метод | Endpoint | Описание |
|-------|----------|----------|
| POST | `/api/v1/specialist/register` | Регистрация |
| GET | `/api/v1/specialist/{telegram_id}` | Профиль |
| GET | `/api/v1/specialist/{telegram_id}/balance` | Баланс токенов |
| POST | `/api/v1/specialist/{telegram_id}/tokens/add` | Добавить токены |
| GET | `/api/v1/specialist/{telegram_id}/transactions` | История операций |

### Session

| Метод | Endpoint | Описание |
|-------|----------|----------|
| POST | `/api/v1/session/create` | Создать сессию |
| POST | `/api/v1/session/{session_id}/start` | Начать сессию |
| GET | `/api/v1/session/{session_id}/next_screen` | Следующий экран |
| POST | `/api/v1/session/{session_id}/response` | Отправить ответ |
| POST | `/api/v1/session/{session_id}/finalize` | Завершить сессию |
| GET | `/api/v1/session/{session_id}/output` | Получить результаты |
| GET | `/api/v1/session/{session_id}/status` | Статус сессии |

### Health

| Метод | Endpoint | Описание |
|-------|----------|----------|
| GET | `/` | Info |
| GET | `/health` | Health check |

## Структура проекта

```
psycheos-backend/
├── app/
│   ├── main.py              # FastAPI приложение
│   ├── config.py            # Конфигурация (pydantic-settings)
│   ├── database.py          # SQLAlchemy setup
│   ├── logging_config.py    # structlog setup
│   ├── exceptions.py        # Кастомные исключения
│   ├── models/              # SQLAlchemy модели
│   │   ├── specialist.py
│   │   ├── session.py
│   │   ├── output.py
│   │   └── transaction.py
│   ├── schemas/             # Pydantic схемы
│   │   └── schemas.py
│   ├── api/                 # API endpoints
│   │   ├── specialist.py
│   │   └── session.py
│   ├── services/            # Бизнес-логика
│   │   ├── session_manager.py
│   │   ├── token_manager.py
│   │   ├── claude_orchestrator.py
│   │   └── screen_bank_loader.py
│   └── data/                # Статические файлы
│       ├── screen_bank.json
│       ├── routing_rules.json
│       └── system_prompt.txt
├── bots/
│   ├── pro_bot.py           # Бот для специалистов
│   ├── client_bot.py        # Бот для клиентов
│   └── keyboards.py         # Inline клавиатуры
├── alembic/                 # Миграции БД
├── logs/                    # Логи (автосоздаётся)
├── alembic.ini
├── pyproject.toml           # uv конфигурация
├── .env.example
└── README.md
```

## База данных

### Таблицы

- **specialists** — специалисты (терапевты)
- **screening_sessions** — сессии скрининга
- **screening_outputs** — результаты скрининга
- **token_transactions** — операции с токенами

## Логирование

Система использует structlog с двумя выводами:
- **JSON** в файл `logs/app.log` — для машинной обработки
- **Readable** в консоль — для разработки

Все исключения логируются с полным traceback.

## Переменные окружения

| Переменная | Описание | По умолчанию |
|------------|----------|--------------|
| `DATABASE_URL` | Строка подключения к SQLite | `sqlite+aiosqlite:///./psycheos.db` |
| `ANTHROPIC_API_KEY` | Ключ Claude API | — |
| `TELEGRAM_PRO_BOT_TOKEN` | Токен бота для специалистов | — |
| `TELEGRAM_CLIENT_BOT_TOKEN` | Токен бота для клиентов | — |
| `DEBUG` | Режим отладки | `True` |
| `LOG_LEVEL` | Уровень логирования | `INFO` |
| `SESSION_EXPIRY_HOURS` | Время жизни сессии (часы) | `48` |
| `FREE_TOKENS_ON_REGISTER` | Бесплатные токены при регистрации | `1` |
| `BACKEND_URL` | URL backend API | `http://localhost:8000` |

## Токены

- При регистрации специалист получает 1 бесплатный токен
- 1 токен = 1 сессия скрининга
- Токен списывается при создании сессии

## Тестирование

```bash
# End-to-end тест
1. Запустить все компоненты
2. В Pro Bot: /register → /new_session
3. Скопировать ссылку и открыть в Client Bot
4. Пройти скрининг
5. Проверить результаты

# Проверить логи
tail -f logs/app.log | jq .
```

## Разработка

### Добавление миграций

```bash
alembic revision --autogenerate -m "Description"
alembic upgrade head
```

### Форматирование кода

```bash
# Используйте ruff или black
ruff format .
ruff check .
```

## Лицензия

MIT
