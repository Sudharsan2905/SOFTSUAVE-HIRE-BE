import logging
import re
import sys

from loguru import logger

_TOKEN_RE = re.compile(r"([?&]token=)[^&\s\"']+")


class _RedactTokenFilter(logging.Filter):
    """Strip ``?token=<JWT>`` from uvicorn access lines (WebSocket auth uses a query param)."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.args = tuple(
            _TOKEN_RE.sub(r"\1[REDACTED]", a) if isinstance(a, str) else a
            for a in (record.args or ())
        )
        if isinstance(record.msg, str):
            record.msg = _TOKEN_RE.sub(r"\1[REDACTED]", record.msg)
        return True


def setup_logging(log_level: str = "INFO") -> None:
    """Configure loguru with a structured console format."""
    for name in ("uvicorn.access", "uvicorn.error", "uvicorn"):
        logging.getLogger(name).addFilter(_RedactTokenFilter())
    logger.remove()
    logger.add(
        sys.stdout,
        level=log_level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<level>{message}</level>"
        ),
        colorize=sys.stdout.isatty(),  # plain text under systemd/journald/cron,
    )


__all__ = ["logger", "setup_logging"]
