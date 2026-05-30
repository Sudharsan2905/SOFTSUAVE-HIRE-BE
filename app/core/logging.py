import sys

from loguru import logger


def setup_logging(log_level: str = "INFO") -> None:
    """Configure loguru with a structured console format."""
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
        colorize=True,
    )


__all__ = ["logger", "setup_logging"]
