"""구조화 로깅. 자동화는 사후 추적이 전부라 JSON 라인으로 남긴다."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path


class JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "ctx", None)
        if extra:
            payload["ctx"] = extra
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class HumanFormatter(logging.Formatter):
    COLORS = {"WARNING": "\033[33m", "ERROR": "\033[31m", "CRITICAL": "\033[31m"}
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, "")
        ctx = getattr(record, "ctx", None)
        tail = f"  {json.dumps(ctx, ensure_ascii=False)}" if ctx else ""
        return f"{color}[{record.levelname:<7}]{self.RESET} {record.getMessage()}{tail}"


def setup(level: str = "INFO", log_file: str | Path | None = None) -> None:
    root = logging.getLogger("autocommerce")
    root.setLevel(level.upper())
    root.handlers.clear()

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(HumanFormatter())
    root.addHandler(console)

    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(JsonLineFormatter())
        root.addHandler(fh)


def get(name: str) -> logging.Logger:
    return logging.getLogger(f"autocommerce.{name}")


def log(logger: logging.Logger, level: int, msg: str, **ctx) -> None:
    """logger.info('...', ctx={...}) 축약."""
    logger.log(level, msg, extra={"ctx": ctx} if ctx else {})
