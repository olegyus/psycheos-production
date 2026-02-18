"""
PsycheOS Interpreter Bot - Configuration
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Base paths
BASE_DIR = Path(__file__).parent
SESSIONS_DIR = BASE_DIR / "sessions"
OUTPUTS_DIR = BASE_DIR / "outputs"

# Ensure directories exist
SESSIONS_DIR.mkdir(exist_ok=True)
OUTPUTS_DIR.mkdir(exist_ok=True)

# API Configuration
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "4000"))

# Telegram Configuration
INTERPRETER_BOT_TOKEN = os.getenv("INTERPRETER_BOT_TOKEN")

# Session Configuration
MAX_CLARIFICATION_ITERATIONS = int(os.getenv("MAX_CLARIFICATION_ITERATIONS", "2"))
MAX_REPAIR_ATTEMPTS = int(os.getenv("MAX_REPAIR_ATTEMPTS", "2"))

# Validation
if not ANTHROPIC_API_KEY:
    raise ValueError("ANTHROPIC_API_KEY not found in .env")
if not INTERPRETER_BOT_TOKEN:
    raise ValueError("INTERPRETER_BOT_TOKEN not found in .env")

print(f"✓ Configuration loaded")
print(f"  Model: {ANTHROPIC_MODEL}")
print(f"  Sessions: {SESSIONS_DIR}")
print(f"  Outputs: {OUTPUTS_DIR}")
