# PsycheOS Screen v2 + Pro v2 — План миграции

## Статус: Phase 4 completion (Screen v2 → Pro v2)

---

## 1. Обзор: Что мигрируем и что переделываем

### Screen v2 — ПЕРЕДЕЛКА (не простая миграция)
Legacy Screen использует **старую модель** (4 континуума: economy_exploration, protection_contact, retention_movement, survival_development) и 25 экранов в 5 блоках.

**Screen v2 по спецификации** использует:
- **4 новые ортогональные оси**: A1 (Активация), A2 (Неопределённость), A3 (Импульс), A4 (Временная ориентация)
- **5 слоёв**: L0–L4 (энергетический → когнитивный)
- **3 фазы**: 6 фиксированных экранов → до 3 уточнений → до 5 адаптивных
- **Векторная модель** с weight matrices вместо простого средневзвешенного
- **4 отдельных Claude промпта** вместо одного монолитного

### Pro v2 — РАСШИРЕНИЕ существующего Pro
Текущий Pro уже работает (cases, invites, links). Нужно добавить:
- Создание screening-сессии (assessment)
- Получение результатов скрининга (JSON + DOCX)
- Просмотр статуса скрининга

---

## 2. Архитектурные решения (принять ДО начала кода)

### 2.1. Модель данных Screen v2

**НЕ переносим** из legacy: `Specialist`, `Transaction`, `ScreeningSession`, `ScreeningOutput`.

**ИСПОЛЬЗУЕМ** существующие production модели:
- `User` → вместо `Specialist`
- `Context` → кейс, к которому привязан скрининг
- `BotChatState` → FSM + session payload для Screen бота
- `LinkToken` → ссылка для клиента

**СОЗДАЁМ новую таблицу**: `screening_assessment`
```sql
CREATE TABLE screening_assessment (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    context_id UUID NOT NULL REFERENCES context(id),
    specialist_user_id BIGINT NOT NULL,
    client_chat_id BIGINT,           -- заполняется при старте
    status VARCHAR(20) DEFAULT 'created',  -- created/in_progress/completed/expired
    
    -- Фазовая структура
    phase INTEGER DEFAULT 0,          -- 0/1/2/3
    phase1_completed BOOLEAN DEFAULT FALSE,
    phase2_questions INTEGER DEFAULT 0,
    phase3_questions INTEGER DEFAULT 0,
    
    -- Векторная модель (результаты)
    axis_vector JSONB DEFAULT '{}',   -- {A1: float, A2: float, A3: float, A4: float}
    layer_vector JSONB DEFAULT '{}',  -- {L0: float, L1: float, L2: float, L3: float, L4: float}
    tension_matrix JSONB DEFAULT '{}', -- M[Lk, Aj] = 5x4 matrix
    rigidity JSONB DEFAULT '{}',      -- {polarization, low_variance, strategy_repetition, total}
    confidence FLOAT DEFAULT 0.0,
    ambiguity_zones JSONB DEFAULT '[]',
    dominant_cells JSONB DEFAULT '[]',
    
    -- Полный лог ответов
    response_history JSONB DEFAULT '[]',
    -- Каждый элемент: {screen_id, phase, question_text, selected_options, axis_contributions, layer_contributions, timestamp}
    
    -- Финальный отчёт
    report_json JSONB,               -- полный JSON отчёт
    report_text TEXT,                 -- текстовый отчёт
    
    -- Метаданные
    created_at TIMESTAMPTZ DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    link_token_jti UUID REFERENCES link_token(jti)
);
```

### 2.2. Файловая структура Screen v2

```
app/services/screen/
├── __init__.py
├── engine.py              # Векторный движок: агрегация, нормализация, матрица, ригидность, confidence
├── screen_bank.py         # 6 фиксированных экранов Phase 1 + 20 референс-шаблонов Phase 2/3
├── weight_matrix.py       # Весовые вектора для каждого варианта ответа (из спецификации)
├── prompts.py             # 5 Claude промптов: Router, Constructor, ReportGenerator, SessionBridge, StopDecision
├── orchestrator.py        # Оркестратор фаз: Phase1 (rule-based) → Phase2 (Claude Router) → Phase3 (Claude Constructor)
└── report.py              # Генерация финального отчёта (JSON + TXT + DOCX)

app/webhooks/screen.py     # Webhook handler (заменяет stubs.py)
```

### 2.3. FSM Screen бота

```
idle → active → phase1 → [phase2] → [phase3] → report → completed
```

Состояния в `BotChatState.state`:
- `idle` — до старта
- `active` — клиент перешёл по ссылке, показываем приветствие
- `phase1_q{1-6}` — показываем фиксированные экраны
- `phase2_q{1-3}` — уточняющие вопросы (опционально)
- `phase3_q{1-5}` — адаптивные вопросы (опционально)
- `completed` — скрининг завершён

`BotChatState.state_payload["session"]`:
```json
{
    "assessment_id": "uuid",
    "phase": 1,
    "current_screen": {...},
    "screens_shown": 3,
    "selected_options": []
}
```

### 2.4. Поток данных Screen v2

```
1. Pro: specialist создаёт assessment → screening_assessment(status=created)
2. Pro: генерирует link_token(role=client, service=screen) → deep link
3. Client: /start {jti} → verify_link → screening_assessment(status=in_progress)
4. Client: Phase 1 — 6 экранов multi-select → engine.py агрегирует вектор
5. Client: Phase 2 — Claude Router выбирает ambiguity zone → Constructor генерирует вопрос
6. Client: Phase 3 — если confidence < 0.85 после Phase 2
7. System: Report Generator → JSON + TXT → отправка клиенту "спасибо"
8. System: Уведомление Pro → specialist получает отчёт
```

---

## 3. Порядок реализации (шаги для Claude Code)

### Шаг 1: Миграция БД — screening_assessment
- Создать `app/models/screening_assessment.py`
- Создать Alembic миграцию
- Протестировать

### Шаг 2: Векторный движок (engine.py + weight_matrix.py)
- Реализовать агрегацию AxisScore, LayerScore
- Нормализация tanh()
- Матрица напряжения M[k,j]
- Индекс ригидности
- Расчёт confidence
- Определение ambiguity zones
- **Юнит-тесты** с тестовым кейсом из спецификации

### Шаг 3: Банк вопросов (screen_bank.py + weight_matrix.py)
- 6 фиксированных экранов Phase 1 с весами из спецификации
- 20 референс-шаблонов для Phase 2/3
- Формат вопроса: text, options[], weights per option (axis_vector + layer_vector)

### Шаг 4: Claude промпты (prompts.py)
- PHASE2_ROUTER_PROMPT
- PHASE3_CONSTRUCTOR_PROMPT
- REPORT_GENERATOR_PROMPT
- FIRST_SESSION_BRIDGE_PROMPT
- PHASE2_STOP_DECISION_PROMPT

### Шаг 5: Оркестратор фаз (orchestrator.py)
- Phase 1: rule-based, последовательно 6 экранов → engine.aggregate()
- Phase 2: Claude Router → выбор узла → Claude Constructor → engine.update()
- Phase 3: аналогично Phase 2, но с расширенным пулом
- Stopping criteria: |ΔAxis| < 0.1, Confidence ≥ 0.85, max questions
- Финализация → report generation

### Шаг 6: Генератор отчёта (report.py)
- JSON-отчёт (полный machine-readable)
- TXT-отчёт (читаемый)
- DOCX-отчёт (профессиональный для специалиста)
- Структура из спецификации: Axis Profile, Dominant Layers, Top L×A, Rigidity, Confidence, Explanation, How to Read, Interview Protocol

### Шаг 7: Webhook handler (screen.py)
- `/start {jti}` → verify_link → load assessment → show welcome
- Обработка multi-select ответов (callback_query)
- FSM переходы phase1 → phase2 → phase3 → completed
- Отправка отчёта клиенту ("Спасибо")
- Уведомление Pro бота

### Шаг 8: Pro v2 — расширение (webhooks/pro.py)
- Новый callback: `screen_link_{context_id}` → создать assessment + issue_link
- Новый callback: `screen_results_{context_id}` → получить отчёт
- Новый callback: `screen_status_{context_id}` → статус скрининга
- Отправка отчёта как .json + .docx документы

### Шаг 9: Интеграция и тестирование
- set_webhooks.py — убедиться что Screen webhook зарегистрирован
- E2E тест: Pro создаёт → Client проходит → Pro получает отчёт
- Деплой на Railway

---

## 4. Ключевые решения (НЕ МЕНЯТЬ)

1. **Векторная модель** — 4 оси × 5 слоёв, tanh нормализация, матрица M[k,j]
2. **3-фазная архитектура** — 6 fixed + 3 adaptive + 5 constructor
3. **Claude промпты раздельные** — Router, Constructor, Report, Bridge, Stop
4. **Confidence threshold** = 0.85
5. **Max Phase 2 questions** = 3
6. **Max Phase 3 questions** = 5
7. **Модель Claude** — claude-haiku-4-5 для Router/Stop, claude-sonnet-4-5 для Report/Constructor
8. **Весовой диапазон** — {-0.8, -0.5, -0.3, 0, +0.3, +0.5, +0.8}
9. **Rigidity** = α·Polarization + β·LowVariance + γ·StrategyRepetition (α=0.3, β=0.3, γ=0.4)

---

## 5. Инструкции для Claude Code (копировать по шагам)

### ИНСТРУКЦИЯ 1 (Шаг 1: Модель БД)

```
Создай модель screening_assessment для Screen v2. 

Файл: app/models/screening_assessment.py

Модель SQLAlchemy (async, как bot_chat_state.py):
- id: UUID, PK, server_default=gen_random_uuid()
- context_id: UUID, FK → context.id, NOT NULL
- specialist_user_id: BigInteger, NOT NULL
- client_chat_id: BigInteger, nullable
- status: String(20), default="created" (created/in_progress/completed/expired)
- phase: Integer, default=0
- phase1_completed: Boolean, default=False
- phase2_questions: Integer, default=0
- phase3_questions: Integer, default=0
- axis_vector: JSONB, default={}
- layer_vector: JSONB, default={}
- tension_matrix: JSONB, default={}
- rigidity: JSONB, default={}
- confidence: Float, default=0.0
- ambiguity_zones: JSONB, default=[]
- dominant_cells: JSONB, default=[]
- response_history: JSONB, default=[]
- report_json: JSONB, nullable
- report_text: Text, nullable
- created_at, started_at, completed_at, expires_at: DateTime(timezone=True)
- link_token_jti: UUID, FK → link_token.jti, nullable

Index на context_id и status.

Затем создай Alembic миграцию: alembic/versions/0002_create_screening_assessment.py

Паттерн — как в 0001_create_link_tokens.py.

Обнови CLAUDE.md: добавь screening_assessment в раздел моделей и отметь Phase 4 Screen v2 — Step 1 done.
```

### ИНСТРУКЦИЯ 2 (Шаг 2: Векторный движок)

```
Создай векторный движок Screen v2.

Файл: app/services/screen/__init__.py (пустой)
Файл: app/services/screen/engine.py

Класс ScreeningEngine:

1. aggregate_vectors(responses: list[dict]) -> tuple[dict, dict]
   - Каждый response: {axis_weights: {A1: float, ...}, layer_weights: {L0: float, ...}}
   - AxisScore_j = sum(weights_j) / N
   - LayerScore_k = sum(weights_k) / N
   - Нормализация: tanh(score)
   - Возвращает (axis_vector, layer_vector)

2. compute_tension_matrix(axis_vector, layer_vector) -> dict
   - M[Lk, Aj] = LayerScore_k * AxisScore_j
   - Возвращает {f"L{k}_A{j}": value}

3. compute_rigidity(responses, axis_vector) -> dict
   - polarization: доля |AxisScore_j| > 0.7
   - low_variance: средняя дисперсия внутри каждой оси (< 0.15 = rigid)
   - strategy_repetition: доля повторяющихся паттернов ответов
   - total: 0.3*pol + 0.3*var + 0.4*rep
   - Возвращает {polarization, low_variance, strategy_repetition, total}

4. compute_confidence(responses, axis_vector, ambiguity_count) -> float
   - Базовая: 1 - uncertainty_index
   - uncertainty_index = f(дисперсия, конфликтующие вклады, малая амплитуда)
   - Возвращает 0.0-1.0

5. find_ambiguity_zones(axis_vector, layer_vector, tension_matrix, confidence) -> list[str]
   - Ячейки с |conflict| > threshold или low amplitude
   - Возвращает ["A2_L4", "A1_L0", ...]

6. get_dominant_cells(tension_matrix, top_n=3) -> list[str]
   - Top N ячеек по абсолютному значению
   - Возвращает ["L4_A2", "L2_A4", "L0_A1"]

7. process_response(current_state: dict, new_response: dict) -> dict
   - Добавляет ответ в response_history
   - Пересчитывает все метрики
   - Возвращает обновлённый state

Юнит-тест (тестовый кейс из спецификации):
Входные 14 ответов → ожидаемый результат:
A1 ≈ -0.62, A2 ≈ -0.68, A3 ≈ +0.41, A4 ≈ -0.73
L0 ≈ +0.72, L1 ≈ -0.18, L2 ≈ +0.65, L3 ≈ -0.44, L4 ≈ +0.81
Confidence ≈ 0.66

Обнови CLAUDE.md: Step 2 done.
```

### ИНСТРУКЦИЯ 3 (Шаг 3: Банк вопросов)

```
Создай банк вопросов Screen v2.

Файл: app/services/screen/screen_bank.py
Файл: app/services/screen/weight_matrix.py

В weight_matrix.py — весовые вектора для КАЖДОГО варианта ответа:

PHASE1_WEIGHTS — 6 экранов, каждый экран содержит варианты с весами.

Формат:
PHASE1_SCREENS = [
    {
        "screen_id": "P1_01",
        "question": "Когда вы устали, но при этом есть важное дело, что из этого чаще всего с вами происходит?",
        "type": "multi_select",
        "options": [
            {
                "text": "Я всё равно начинаю делать, даже через силу",
                "axis_weights": {"A1": 0.5, "A3": 0.3, "A4": 0.4},
                "layer_weights": {"L0": 0.3, "L1": 0.6, "L2": 0.2}
            },
            ...все варианты из спецификации для всех 6 экранов...
        ]
    },
    ...
]

PHASE2_TEMPLATES — 20 узлов (A1×L0 ... A4×L4), каждый:
{
    "node": "A1_L0",
    "diagnostic_split": "первичная усталость vs устойчивый сниженный тонус",
    "reference_question": "Когда у вас появляется возможность нормально восстановиться...",
    "options": [...с weights и split_logic...],
    "split_logic": {"H1_supported_by": [...], "H2_supported_by": [...]}
}

Все 20 узлов со всеми вариантами ответов и весами — из спецификации.

Все тексты НА РУССКОМ ЯЗЫКЕ.

В screen_bank.py:
- get_phase1_screen(index: int) -> dict
- get_phase2_template(node: str) -> dict
- get_all_phase2_nodes() -> list[str]

Обнови CLAUDE.md: Step 3 done.
```

### ИНСТРУКЦИЯ 4 (Шаг 4: Claude промпты)

```
Создай Claude промпты для Screen v2.

Файл: app/services/screen/prompts.py

5 промптов (все на АНГЛИЙСКОМ для Claude, но с инструкцией отвечать по-русски где нужно):

1. PHASE2_ROUTER_PROMPT — выбор ambiguity-зоны
   Input: AxisVector, LayerVector, RigidityIndex, AmbiguityZones, Confidence
   Output: JSON {selected_node, reason}
   
2. PHASE3_CONSTRUCTOR_PROMPT — генерация уточняющего вопроса
   Input: diagnostic node, split goal, available templates
   Output: JSON {question, options, split_logic}
   Вопрос и варианты ДОЛЖНЫ быть на русском.
   
3. REPORT_GENERATOR_PROMPT — финальный отчёт
   Input: все вектора, матрица, ригидность, confidence
   Output: структурированный текст НА РУССКОМ
   Без диагнозов, без советов, только архитектура регуляции.
   
4. FIRST_SESSION_BRIDGE_PROMPT — протокол интервью
   Input: structural profile
   Output: JSON {axis_verification, layer_exploration, functional_context} — НА РУССКОМ
   
5. PHASE2_STOP_DECISION_PROMPT — решение об остановке
   Input: previous/updated vectors, conflict, confidence, questions_asked
   Output: JSON {stop_phase2: bool, reason}

Функция assemble_prompt(prompt_type: str, context: dict) -> str

Промпты из спецификации (раздел "Архитектура ИИ"). Добавь temperature/top_p рекомендации в комментариях.

Обнови CLAUDE.md: Step 4 done.
```

### ИНСТРУКЦИЯ 5 (Шаг 5: Оркестратор)

```
Создай оркестратор фаз Screen v2.

Файл: app/services/screen/orchestrator.py

Класс ScreenOrchestrator:

Зависимости: ScreeningEngine, screen_bank, prompts, anthropic.AsyncAnthropic

1. async start_assessment(assessment_id) -> dict
   - Возвращает первый экран Phase 1
   
2. async process_phase1_response(assessment_id, screen_index, selected_options) -> dict
   - Добавляет ответ с весами через engine.process_response()
   - Если screen_index < 6 → возвращает следующий экран
   - Если screen_index == 6 → вычисляет вектор, проверяет confidence
     - Если confidence ≥ 0.85 → переход к отчёту
     - Иначе → переход к Phase 2

3. async process_phase2_response(assessment_id, selected_options) -> dict
   - Обновляет вектор
   - Вызывает STOP_DECISION через Claude
   - Если stop → переход к отчёту
   - Если continue → вызывает ROUTER → выбирает узел → CONSTRUCTOR → генерирует вопрос
   - Если phase2_questions >= 3 и confidence < 0.85 → Phase 3

4. async process_phase3_response(assessment_id, selected_options) -> dict
   - Аналогично Phase 2, но max 5 вопросов
   - Всегда заканчивается отчётом

5. async generate_report(assessment_id) -> dict
   - REPORT_GENERATOR через Claude
   - FIRST_SESSION_BRIDGE через Claude
   - Сохраняет в screening_assessment.report_json и report_text
   - Возвращает {report_json, report_text}

Все методы работают с БД через AsyncSession.
Все Claude вызовы через anthropic.AsyncAnthropic с settings.ANTHROPIC_API_KEY.
Router/Stop → claude-haiku-4-5, Constructor/Report/Bridge → claude-sonnet-4-5.

Обнови CLAUDE.md: Step 5 done.
```

### ИНСТРУКЦИЯ 6 (Шаг 6: Отчёт)

```
Создай генератор отчётов Screen v2.

Файл: app/services/screen/report.py

1. format_report_json(assessment) -> dict
   Полный JSON со всеми полями из спецификации:
   assessment_id, timestamp, axis_vector, layer_vector, dominant_cells,
   rigidity, confidence, phases, report_text

2. format_report_txt(assessment, report_data) -> str
   Читаемый текстовый отчёт на РУССКОМ:
   - Краткий профиль осей
   - Доминирующие слои
   - Топ L×A сочетания
   - Индекс гибкости
   - Уверенность
   - Пояснение (из Claude)
   - Как читать профиль (шаблон)
   - Ориентиры для первой сессии (из Claude)

3. async generate_report_docx(report_data) -> bytes
   Профессиональный DOCX-документ (используя python-docx):
   - Заголовок: "PsycheOS Screening v2 — Структурный профиль"
   - Дата, Assessment ID
   - Таблица осей (4 строки)
   - Таблица слоёв (5 строк)
   - Таблица топ-сочетаний
   - Индекс гибкости
   - Пояснительный текст
   - Протокол первой сессии
   Шрифт: Arial, заголовки жирные, таблицы с рамками.

Обнови CLAUDE.md: Step 6 done.
```

### ИНСТРУКЦИЯ 7 (Шаг 7: Webhook Screen)

```
Создай webhook handler для Screen v2 бота.

Файл: app/webhooks/screen.py (заменяет логику из stubs.py для screen)

Функция: async handle_screen(update, bot, db, state, chat_id, user_id)

Паттерн — как в interpretator.py, но с multi-select логикой.

FSM:
- state=None или "idle": 
  /start {jti} → verify_link → load screening_assessment → 
  upsert_chat_state(state="active", payload={assessment_id}) → 
  показать приветствие + кнопку "Начать"

- state="active":
  callback "start_screening" → 
  upsert_chat_state(state="phase1_q1") → 
  показать первый экран (multi-select из screen_bank)

- state="phase1_q{N}":
  callback "multi:{screen_id}:{idx}" → toggle чекбокс в payload
  callback "multi_done:{screen_id}" → 
    orchestrator.process_phase1_response() →
    если есть next_screen → upsert_chat_state(state=f"phase1_q{N+1}") → показать экран
    если Phase 2 → upsert_chat_state(state="phase2_q1") → показать вопрос
    если отчёт → generate_report → upsert_chat_state(state="completed")

- state="phase2_q{N}" и "phase3_q{N}":
  аналогично phase1, но вопросы от Claude

- state="completed":
  показать "Спасибо, результаты отправлены специалисту"
  отправить уведомление Pro боту

Клавиатура multi-select: InlineKeyboardMarkup с toggle-чекбоксами [✓]/[ ] + кнопка "Готово ✓"

Уведомление Pro: через python-telegram-bot Bot(token=settings.TG_TOKEN_PRO).send_message()

Обнови CLAUDE.md: Step 7 done.
```

### ИНСТРУКЦИЯ 8 (Шаг 8: Pro v2)

```
Расширь Pro бот для Screen v2.

Файл: app/webhooks/pro.py — добавить в существующий код

Новые callbacks:

1. screen_link_{context_id}:
   - Создать screening_assessment(context_id, specialist_user_id, expires_at=+48h)
   - issue_link(role="client", service="screen", subject_id=0)
   - Отправить deep link клиенту
   
2. screen_status_{context_id}:
   - SELECT screening_assessment WHERE context_id
   - Показать: status, phase, confidence, created_at
   
3. screen_results_{context_id}:
   - Если assessment.status == "completed":
     - Отправить report.json как документ
     - Отправить report.docx как документ
     - Показать краткий текстовый профиль в сообщении
   - Иначе: "Скрининг ещё не завершён"

4. Обработка уведомления от Screen бота:
   - Когда скрининг завершён → Pro получает inline-кнопку "Посмотреть результаты"

Кнопки в меню кейса (case_{context_id}):
- Добавить: "📊 Скрининг" → screen_menu_{context_id}
- screen_menu: "Создать ссылку" / "Статус" / "Результаты" / "Назад"

Обнови CLAUDE.md: Step 8 done, Phase 4 complete.
```

### ИНСТРУКЦИЯ 9 (Шаг 9: Интеграция)

```
Финальная интеграция Screen v2 + Pro v2.

1. app/main.py — убедись что screen webhook router зарегистрирован 
   (он уже должен быть через create_webhook_router в цикле по bot_config)

2. scripts/set_webhooks.py — убедись screen бот включён

3. requirements.txt — проверь что python-docx есть (уже есть: python-docx==1.1.2)

4. Проверь все импорты и зависимости

5. Создай тест: scripts/test_screen_flow.py
   - Создаёт assessment через прямой вызов в БД
   - Симулирует 6 ответов Phase 1
   - Проверяет вычисление вектора
   - Проверяет генерацию отчёта
   
6. Обнови CLAUDE.md:
   - Phase 4: COMPLETE (5/5 ботов мигрированы)
   - Добавь описание Screen v2 архитектуры
   - Обнови структуру файлов
   - Следующий шаг: Phase 5 (AI Integration polish)
```

---

## 6. Риски и митигация

| Риск | Митигация |
|------|-----------|
| Claude не возвращает валидный JSON | Fallback: повтор с temperature=0, затем default response |
| Confidence не достигает 0.85 | Phase 3 гарантирует завершение после max вопросов |
| Большой payload в state_payload | JSONB в Supabase выдержит, но response_history ограничить 50 записями |
| DOCX генерация на Railway | python-docx работает без GUI, проблем не будет |
| Multi-select callback flood | Debounce через toggle в payload, отправка только по "Готово" |

---

## 7. Оценка времени

| Шаг | Оценка |
|-----|--------|
| 1. Модель БД | 15 мин |
| 2. Движок | 45 мин |
| 3. Банк вопросов | 30 мин |
| 4. Промпты | 30 мин |
| 5. Оркестратор | 60 мин |
| 6. Отчёт | 45 мин |
| 7. Webhook Screen | 60 мин |
| 8. Pro v2 | 45 мин |
| 9. Интеграция | 30 мин |
| **Итого** | **~6 часов** |
