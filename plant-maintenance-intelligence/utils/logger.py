"""
Centralized logging configuration for Plant Maintenance Intelligence
Provides structured logging with console and file handlers, JSON formatting,
and proper error tracking.

Usage:
    from utils.logger import setup_logger
    logger = setup_logger(__name__)
    logger.info("Operation started")
    logger.error("Operation failed", exc_info=True)
"""

import logging
import sys
import json
from pathlib import Path
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import Optional


class JSONFormatter(logging.Formatter):
    """
    Format logs as JSON for structured parsing and log aggregation.
    Includes timestamp, level, logger name, message, and exception info.
    """
    
    def format(self, record: logging.LogRecord) -> str:
        log_obj = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "thread": record.thread,
            "thread_name": record.threadName,
        }
        
        # Add exception info if present
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)
        
        # Add extra fields if present
        if hasattr(record, 'extra_data'):
            log_obj["extra"] = record.extra_data
        
        return json.dumps(log_obj)


class ColoredConsoleFormatter(logging.Formatter):
    """
    Console formatter with color coding for different log levels.
    Makes logs easier to read during development and debugging.
    """
    
    # ANSI color codes
    COLORS = {
        'DEBUG': '\033[36m',      # Cyan
        'INFO': '\033[32m',       # Green
        'WARNING': '\033[33m',    # Yellow
        'ERROR': '\033[31m',      # Red
        'CRITICAL': '\033[35m',   # Magenta
        'RESET': '\033[0m'        # Reset
    }
    
    def format(self, record: logging.LogRecord) -> str:
        # Add color to level name
        levelname = record.levelname
        if levelname in self.COLORS:
            record.levelname = f"{self.COLORS[levelname]}{levelname}{self.COLORS['RESET']}"
        
        # Format the message
        formatted = super().format(record)
        
        # Reset levelname for other handlers
        record.levelname = levelname
        
        return formatted


def setup_logger(
    name: str,
    log_dir: Optional[Path] = None,
    level: int = logging.INFO,
    console_output: bool = True,
    file_output: bool = True,
    json_format: bool = True
) -> logging.Logger:
    """
    Setup logger with console and file handlers.
    
    Args:
        name: Logger name (usually __name__ from calling module)
        log_dir: Directory for log files (default: ./logs relative to project root)
        level: Logging level (default: INFO)
        console_output: Enable console handler (default: True)
        file_output: Enable file handler (default: True)
        json_format: Use JSON format for file logs (default: True)
    
    Returns:
        Configured logger instance
    
    Example:
        >>> from utils.logger import setup_logger
        >>> logger = setup_logger(__name__)
        >>> logger.info("Application started")
        >>> logger.error("Connection failed", exc_info=True)
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    # Avoid duplicate handlers if logger already configured
    if logger.handlers:
        return logger
    
    # Prevent propagation to root logger
    logger.propagate = False
    
    # Console handler (human-readable with colors)
    if console_output:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_format = ColoredConsoleFormatter(
            '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        console_handler.setFormatter(console_format)
        logger.addHandler(console_handler)
    
    # File handler (JSON for parsing and aggregation)
    if file_output:
        if log_dir is None:
            # Default to logs directory in project root
            log_dir = Path(__file__).parent.parent / "logs"
        
        log_dir.mkdir(parents=True, exist_ok=True)
        
        # Create rotating file handler (10MB per file, keep 5 backups)
        log_file = log_dir / f"{name.replace('.', '_')}.log"
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=5,
            encoding='utf-8'
        )
        file_handler.setLevel(level)
        
        if json_format:
            file_handler.setFormatter(JSONFormatter())
        else:
            file_format = logging.Formatter(
                '%(asctime)s | %(levelname)-8s | %(name)s | %(funcName)s:%(lineno)d | %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
            file_handler.setFormatter(file_format)
        
        logger.addHandler(file_handler)
    
    return logger


def get_logger(name: str) -> logging.Logger:
    """
    Get an existing logger or create a new one with default settings.
    
    Args:
        name: Logger name
    
    Returns:
        Logger instance
    """
    logger = logging.getLogger(name)
    if not logger.handlers:
        return setup_logger(name)
    return logger


class LoggerAdapter(logging.LoggerAdapter):
    """
    Logger adapter that adds contextual information to all log messages.
    Useful for adding request IDs, user IDs, or other context.
    
    Example:
        >>> logger = setup_logger(__name__)
        >>> context_logger = LoggerAdapter(logger, {'request_id': '12345'})
        >>> context_logger.info("Processing request")
        # Output includes request_id in extra field
    """
    
    def process(self, msg, kwargs):
        # Add extra context to all log records
        if 'extra' not in kwargs:
            kwargs['extra'] = {}
        kwargs['extra']['extra_data'] = self.extra
        return msg, kwargs


# Module-level logger for this file
_logger = setup_logger(__name__)


def log_function_call(func):
    """
    Decorator to log function entry and exit with timing.
    
    Example:
        >>> @log_function_call
        >>> def process_data(data):
        >>>     return data * 2
    """
    import functools
    import time
    
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        logger = get_logger(func.__module__)
        logger.debug(f"Entering {func.__name__}")
        start_time = time.time()
        
        try:
            result = func(*args, **kwargs)
            duration = time.time() - start_time
            logger.debug(f"Exiting {func.__name__} (duration: {duration:.3f}s)")
            return result
        except Exception as e:
            duration = time.time() - start_time
            logger.error(
                f"Exception in {func.__name__} after {duration:.3f}s: {e}",
                exc_info=True
            )
            raise
    
    return wrapper


# Example usage and testing
if __name__ == "__main__":
    # Test the logger
    test_logger = setup_logger("test_logger")
    
    test_logger.debug("This is a debug message")
    test_logger.info("This is an info message")
    test_logger.warning("This is a warning message")
    test_logger.error("This is an error message")
    test_logger.critical("This is a critical message")
    
    # Test exception logging
    try:
        raise ValueError("Test exception")
    except Exception:
        test_logger.error("Caught an exception", exc_info=True)
    
    print("\nLogger test complete. Check ./logs/test_logger.log for JSON output.")
