"""
Centralized logging configuration for the LEAF-Cloud framework.

This module provides a consistent way to configure logging across the entire
application, with support for different log levels, file output, and formatting.
"""

import logging
import sys
from pathlib import Path
from typing import Optional, Dict, List, Union, Type

# Default log format
DEFAULT_LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
DEFAULT_LOG_LEVEL = "INFO"

# Third-party loggers to suppress by default
THIRD_PARTY_LOGGERS = {
    "matplotlib": logging.WARNING,
    "PIL": logging.WARNING,
    "urllib3": logging.WARNING,
    "werkzeug": logging.WARNING,
    "asyncio": logging.WARNING,
    "botocore": logging.WARNING,
    "google": logging.WARNING,
    "boto3": logging.WARNING,
    "fsspec": logging.WARNING,
    "s3fs": logging.WARNING,
    "gcsfs": logging.WARNING,
}


def configure_logging(
    log_level: str = DEFAULT_LOG_LEVEL,
    log_file: Optional[Union[str, Path]] = None,
    log_format: str = DEFAULT_LOG_FORMAT,
    suppress_third_party: bool = True,
) -> None:
    """
    Configure logging for the application.

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Optional path to log file. If None, logs will only go to stderr.
        log_format: Log message format string.
        suppress_third_party: If True, reduce log level for third-party libraries.
    """
    # Convert string log level to numeric value
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    # Create handlers
    handlers: List[logging.Handler] = [
        logging.StreamHandler(sys.stderr)  # Default to stderr
    ]

    # Add file handler if log file is specified
    if log_file:
        try:
            log_path = Path(log_file)
            # Ensure directory exists
            log_path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(
                filename=log_path,
                mode="a",  # Append mode
                encoding="utf-8",
            )
            handlers.append(file_handler)
        except (IOError, OSError) as e:
            logging.error(f"Failed to open log file {log_file}: {e}")

    # Configure root logger
    logging.basicConfig(
        level=numeric_level,
        format=log_format,
        handlers=handlers,
        force=True,  # Override any existing handlers
    )

    # Configure third-party loggers
    if suppress_third_party:
        for logger_name, level in THIRD_PARTY_LOGGERS.items():
            logging.getLogger(logger_name).setLevel(level)

    # Log the configuration
    logger = logging.getLogger(__name__)
    logger.info(f"Logging configured at level {log_level}")
    if log_file:
        logger.info(f"Logging to file: {Path(log_file).resolve()}")


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """
    Get a logger with the specified name.

    Args:
        name: Logger name. If None, returns the root logger.

    Returns:
        Configured logger instance.
    """
    return logging.getLogger(name)


class LoggingContext:
    """Context manager for temporary logging configuration."""

    def __init__(
        self,
        logger: Union[str, logging.Logger],
        level: Optional[int] = None,
        handler: Optional[logging.Handler] = None,
        close: bool = True,
    ):
        self.logger = logger if isinstance(logger, logging.Logger) else logging.getLogger(logger)
        self.level = level
        self.handler = handler
        self.close = close
        self.old_level: Optional[int] = None

    def __enter__(self) -> logging.Logger:
        if self.level is not None:
            self.old_level = self.logger.level
            self.logger.setLevel(self.level)
        if self.handler:
            self.logger.addHandler(self.handler)
        return self.logger

    def __exit__(self, et, ev, tb):
        if self.level is not None and self.old_level is not None:
            self.logger.setLevel(self.old_level)
        if self.handler:
            self.logger.removeHandler(self.handler)
        if self.handler and self.close:
            self.handler.close()
        # implicit return of None => don't suppress exceptions
