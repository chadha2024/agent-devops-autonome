"""
Configuration centralisée du logging, même pattern que dans le module
anomaly-detector (Epic 1) : niveaux réels, format horodaté, un seul
handler par logger.
"""

import logging
import os
import sys

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))
    return logger
