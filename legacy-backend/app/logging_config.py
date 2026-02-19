"""
Logging configuration using structlog.
Outputs to both JSON file and readable console.
"""

import logging
import sys
from pathlib import Path
from datetime import datetime
import structlog
from structlog.typing import EventDict


# Create logs directory
LOGS_DIR = Path("logs")
LOGS_DIR.mkdir(exist_ok=True)


class JSONFileHandler(logging.FileHandler):
    """File handler that writes JSON logs."""
    
    def __init__(self, filename: str, encoding: str = "utf-8"):
        super().__init__(filename, encoding=encoding)


def add_timestamp(logger: str, method_name: str, event_dict: EventDict) -> EventDict:
    """Add ISO timestamp to log entry."""
    event_dict["timestamp"] = datetime.utcnow().isoformat() + "Z"
    return event_dict


def setup_logging(log_level: str = "INFO", debug: bool = True) -> None:
    """
    Configure structlog with dual output:
    1. JSON format to logs/app.log (machine-readable)
    2. Console format to stdout (human-readable)
    """
    
    # Common processors for all outputs
    shared_processors: list = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        add_timestamp,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]
    
    # Configure structlog
    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    
    # Create formatters
    json_formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(ensure_ascii=False),
        foreign_pre_chain=shared_processors,
    )
    
    console_formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.dev.ConsoleRenderer(colors=True),
        foreign_pre_chain=shared_processors,
    )
    
    # Create handlers
    # JSON file handler
    json_handler = JSONFileHandler(
        filename=str(LOGS_DIR / "app.log"),
        encoding="utf-8",
    )
    json_handler.setFormatter(json_formatter)
    json_handler.setLevel(logging.DEBUG)
    
    # Console handler (readable)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(console_formatter)
    console_handler.setLevel(logging.DEBUG if debug else logging.INFO)
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(json_handler)
    root_logger.addHandler(console_handler)
    root_logger.setLevel(getattr(logging, log_level.upper()))
    
    # Also configure uvicorn and aiogram loggers
    for logger_name in ["uvicorn", "uvicorn.error", "uvicorn.access", "aiogram"]:
        logger = logging.getLogger(logger_name)
        logger.handlers.clear()
        logger.addHandler(json_handler)
        logger.addHandler(console_handler)
        logger.setLevel(getattr(logging, log_level.upper()))


def get_logger(name: str = __name__) -> structlog.stdlib.BoundLogger:
    """Get a structured logger instance."""
    return structlog.get_logger(name)


# Initialize logging on module import
def init_logging():
    """Initialize logging with default settings."""
    from app.config import settings
    setup_logging(log_level=settings.log_level, debug=settings.debug)


# Default logger for the application
logger = structlog.get_logger("psycheos")
