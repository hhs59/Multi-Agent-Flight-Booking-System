import logging
import os
import sys

from pythonjsonlogger.jsonlogger import JsonFormatter


def setup_logging() -> None:
    root_logger = logging.getLogger()
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()

    try:
        level = logging.getLevelNamesMapping()[level_name]
    except KeyError:
        level = logging.INFO
        root_logger.warning(
            "Unrecognised log level %r, falling back to INFO",
            level_name,
        )

    root_logger.setLevel(level)

    handler = logging.StreamHandler(sys.stdout)
    formatter = JsonFormatter(
        fmt="%(timestamp)s %(name)s %(levelname)s %(message)s",
        timestamp=True,
    )
    handler.setFormatter(formatter)

    root_logger.handlers.clear()
    root_logger.addHandler(handler)
