"""Centralized logging configuration using loguru."""

import sys
from pathlib import Path
from loguru import logger


def setup_logger(
    level: str = "INFO",
    log_file: str = "./logs/avrpe.log",
    rotation: str = "1 day",
    retention: str = "30 days",
    colorize: bool = True,
) -> None:
    """Configure loguru logger with file and console sinks."""
    logger.remove()

    log_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{line}</cyan> | "
        "<level>{message}</level>"
    )

    logger.add(
        sys.stdout,
        level=level,
        format=log_format,
        colorize=colorize,
        backtrace=True,
        diagnose=True,
    )

    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    logger.add(
        log_file,
        level=level,
        format=log_format,
        rotation=rotation,
        retention=retention,
        backtrace=True,
        diagnose=True,
        compression="gz",
    )


setup_logger()

__all__ = ["logger", "setup_logger"]
