import json
import logging
from datetime import datetime, timezone

from app.utils.correlation import get_correlation_id


class StructuredLogger:
    """Structured logger that includes correlation IDs and consistent formatting."""
    
    def __init__(self, name: str):
        self.logger = logging.getLogger(name)
    
    def _format_message(self, level: str, message: str, **kwargs) -> str:
        """Format a log message with structured context."""
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "message": message,
            "logger": self.logger.name,
        }
        
        # Add correlation ID if available
        correlation_id = get_correlation_id()
        if correlation_id:
            log_entry["correlation_id"] = correlation_id
        
        # Add any additional context
        for key, value in kwargs.items():
            if key not in log_entry:
                log_entry[key] = value
        
        return json.dumps(log_entry)
    
    def debug(self, message: str, **kwargs):
        """Log a debug message."""
        self.logger.debug(self._format_message("DEBUG", message, **kwargs))
    
    def info(self, message: str, **kwargs):
        """Log an info message."""
        self.logger.info(self._format_message("INFO", message, **kwargs))
    
    def warning(self, message: str, **kwargs):
        """Log a warning message."""
        self.logger.warning(self._format_message("WARNING", message, **kwargs))
    
    def error(self, message: str, **kwargs):
        """Log an error message."""
        self.logger.error(self._format_message("ERROR", message, **kwargs))
    
    def critical(self, message: str, **kwargs):
        """Log a critical message."""
        self.logger.critical(self._format_message("CRITICAL", message, **kwargs))
    
    def exception(self, message: str, **kwargs):
        """Log an exception with traceback."""
        kwargs["exception"] = True
        self.logger.error(self._format_message("ERROR", message, **kwargs), exc_info=True)


def get_structured_logger(name: str) -> StructuredLogger:
    """Get a structured logger instance."""
    return StructuredLogger(name)


# Create default structured loggers for common modules
api_logger = get_structured_logger("api")
task_logger = get_structured_logger("task")
audit_logger = get_structured_logger("audit")
metrics_logger = get_structured_logger("metrics")
