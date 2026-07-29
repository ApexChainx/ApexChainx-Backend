"""Centralized logging configuration via dictConfig (#21)."""

import logging
import logging.config
import os


def configure_logging() -> None:
    level = os.getenv("LOG_LEVEL", "INFO")
    log_format = os.getenv("LOG_FORMAT", "plaintext")

    handlers: dict = {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json" if log_format == "json" else "plain",
            "stream": "ext://sys.stdout",
        }
    }

    formatters: dict = {
        "plain": {
            "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        },
        "json": {
            "()": "app.core.logging_config._JsonFormatter",
        },
    }

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": formatters,
            "handlers": handlers,
            "root": {"level": level, "handlers": ["console"]},
        }
    )


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        import json as _json

        return _json.dumps(
            {
                "timestamp": self.formatTime(record),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
        )
