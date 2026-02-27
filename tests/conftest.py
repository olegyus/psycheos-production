"""
Shared pytest configuration.

IMPORTANT: env vars must be set before *any* app module is imported because
`app/config.py` calls `Settings()` at module level (pydantic-settings reads
env once at instantiation time).  conftest.py is always loaded first, so this
file is the correct place to inject test values.
"""
import os

_TEST_ENV = {
    # Database — fake URL; engine is lazy so no real connection is attempted.
    "DATABASE_URL_POOLER": "postgresql+psycopg://test:test@localhost:5432/test_db",
    "DATABASE_URL_DIRECT": "postgresql+psycopg://test:test@localhost:5432/test_db",
    # Claude — dummy key; all Claude calls are mocked in smoke tests.
    "ANTHROPIC_API_KEY": "sk-ant-test-00000000",
    # Telegram tokens — syntactically valid but fake.
    "TG_TOKEN_PRO":              "1111111111:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA1",
    "TG_TOKEN_SCREEN":           "2222222222:BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB2",
    "TG_TOKEN_INTERPRETATOR":    "3333333333:CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC3",
    "TG_TOKEN_CONCEPTUALIZATOR": "4444444444:DDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD4",
    "TG_TOKEN_SIMULATOR":        "5555555555:EEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE5",
    # Webhook secrets
    "TG_WEBHOOK_SECRET_PRO":              "secret-pro",
    "TG_WEBHOOK_SECRET_SCREEN":           "secret-screen",
    "TG_WEBHOOK_SECRET_INTERPRETATOR":    "secret-interp",
    "TG_WEBHOOK_SECRET_CONCEPTUALIZATOR": "secret-concept",
    "TG_WEBHOOK_SECRET_SIMULATOR":        "secret-sim",
    # Usernames (needed for deep-link helpers in some handlers)
    "TG_USERNAME_SCREEN":           "psycheos_screen_test_bot",
    "TG_USERNAME_INTERPRETATOR":    "psycheos_interp_test_bot",
    "TG_USERNAME_CONCEPTUALIZATOR": "psycheos_concept_test_bot",
    "TG_USERNAME_SIMULATOR":        "psycheos_sim_test_bot",
}

for _key, _val in _TEST_ENV.items():
    os.environ.setdefault(_key, _val)
