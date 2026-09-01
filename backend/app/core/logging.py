import logging
import sys

from app.core.config import get_settings

_CONFIGURED = False


class _CompactFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts = self.formatTime(record, "%H:%M:%S")
        return f"{ts} {record.levelname:<7} {record.name} {record.getMessage()}"


def configure_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    settings = get_settings()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_CompactFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level.upper())
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    _CONFIGURED = True
