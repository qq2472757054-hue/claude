"""日志：写入 素材库/日志/YYYY-MM-DD.log。"""
from __future__ import annotations

import datetime as _dt
import logging
from pathlib import Path

_LOGGER_NAME = "coolplay"
_current_dir: Path | None = None


class _DailyFileHandler(logging.Handler):
    def __init__(self, directory: Path):
        super().__init__()
        self.directory = directory

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            day = _dt.date.today().strftime("%Y-%m-%d")
            self.directory.mkdir(parents=True, exist_ok=True)
            with open(self.directory / f"{day}.log", "a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except Exception:  # 日志本身不能让程序崩溃
            self.handleError(record)


def setup(log_dir: Path | None) -> None:
    global _current_dir
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, _DailyFileHandler)
               for h in logger.handlers):
        sh = logging.StreamHandler()
        sh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(sh)
    if log_dir is not None and log_dir != _current_dir:
        for h in list(logger.handlers):
            if isinstance(h, _DailyFileHandler):
                logger.removeHandler(h)
        fh = _DailyFileHandler(log_dir)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
        logger.addHandler(fh)
        _current_dir = log_dir


def get(name: str = "") -> logging.Logger:
    return logging.getLogger(_LOGGER_NAME + ("." + name if name else ""))
