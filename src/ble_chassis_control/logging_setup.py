"""应用文件日志:滚动写入本地日志目录。"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOGGER_NAME = "ble_chassis_control"
_configured = False
_log_path: Path | None = None


def log_dir() -> Path:
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / "ble-chassis-control" / "logs"
    return Path.home() / ".ble-chassis-control" / "logs"


def setup_file_logging() -> Path | None:
    """初始化文件日志;失败时返回 None,不影响应用启动。"""
    global _configured, _log_path
    if _configured:
        return _log_path
    _configured = True

    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    target = log_dir() / "app.log"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            target,
            maxBytes=2 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        logger.addHandler(handler)
        _log_path = target
    except Exception:
        _log_path = None
    return _log_path


def get_log_path() -> Path | None:
    return _log_path
