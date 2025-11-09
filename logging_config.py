# =====================================================================
# logging_config.py
# =====================================================================
import logging
from logging.handlers import RotatingFileHandler
import os

LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

def setup_logger(name="bot", level=logging.INFO):
    """Configure a rotating file logger with masking for sensitive info."""
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # 🔁 Rotate logs every 5 MB, keep 5 old files
    handler = RotatingFileHandler(
        os.path.join(LOG_DIR, f"{name}.log"), maxBytes=5_000_000, backupCount=5
    )
    formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - %(name)s - %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return logger
