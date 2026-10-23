"""Graceful shutdown handler on SIGTERM/SIGINT for issue #31."""

import asyncio
import logging
import signal

logger = logging.getLogger(__name__)

_is_shutting_down = False


def is_shutting_down() -> bool:
    return _is_shutting_down


def install_signal_handlers(grace_seconds: int = 30) -> None:
    """Register SIGTERM/SIGINT handlers for graceful shutdown."""
    global _is_shutting_down

    def _handler(signum, frame):
        global _is_shutting_down
        _is_shutting_down = True
        logger.info("Received signal %d, initiating graceful shutdown.", signum)

    try:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, _handler, sig, None)
    except (NotImplementedError, RuntimeError):
        # Windows fallback / no running loop (e.g., during test import)
        signal.signal(signal.SIGTERM, _handler)
        signal.signal(signal.SIGINT, _handler)

    logger.info("Graceful shutdown handlers installed (grace=%ds).", grace_seconds)
