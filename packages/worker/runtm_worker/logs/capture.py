"""Log capture utilities for build/deploy pipeline."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from runtm_shared.types import LogType

logger = logging.getLogger(__name__)


def redact_secrets(message: str, secrets: set[str]) -> str:
    """Redact secret values from a log message.

    Replaces any occurrence of secret values with [REDACTED].
    Uses word boundaries to avoid partial matches.

    Args:
        message: Log message to redact
        secrets: Set of secret values to redact

    Returns:
        Message with secrets replaced by [REDACTED]
    """
    if not secrets:
        return message

    result = message
    for secret in secrets:
        if secret and len(secret) >= 3:  # Only redact non-trivial values
            # Use re.escape to handle special characters in secrets
            # Replace the secret value with [REDACTED]
            result = result.replace(secret, "[REDACTED]")

    return result


class LogCapture:
    """Captures logs during build/deploy and persists to database.

    Usage:
        with LogCapture(db, deployment_id, LogType.BUILD) as log:
            log.write("Starting build...")
            # do work
            log.write("Build complete!")
        # Logs are automatically flushed to DB on exit

    With secret redaction:
        with LogCapture(db, deployment_id, LogType.DEPLOY, redact_values={"secret123"}) as log:
            log.write("Connecting with secret123...")  # Logs: "Connecting with [REDACTED]..."
    """

    def __init__(
        self,
        db: Session,
        deployment_id: str,
        log_type: LogType,
        redact_values: set[str] | None = None,
    ):
        """Initialize log capture.

        Args:
            db: Database session
            deployment_id: Deployment ID (internal UUID as string)
            log_type: Type of log (build or deploy)
            redact_values: Set of secret values to redact from logs
        """
        self.db = db
        self.deployment_id = deployment_id
        self.log_type = log_type
        self.buffer: list[str] = []
        self.start_time = datetime.now(timezone.utc)
        self.redact_values: set[str] = redact_values or set()

    def add_redact_value(self, value: str) -> None:
        """Add a value to be redacted from logs.

        Args:
            value: Secret value to redact
        """
        if value and len(value) >= 3:
            self.redact_values.add(value)

    def add_redact_values(self, values: dict) -> None:
        """Add multiple values to be redacted from logs.

        Args:
            values: Dict of secret values (only values are used)
        """
        for value in values.values():
            if value:
                self.add_redact_value(str(value))

    def write(self, message: str) -> None:
        """Write a log message.

        Secret values are automatically redacted before storing.

        Args:
            message: Message to log
        """
        # Redact secrets before storing
        safe_message = redact_secrets(message, self.redact_values)
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        self.buffer.append(f"[{timestamp}] {safe_message}")
        print(f"[{self.log_type.value}] {self.buffer[-1]}", flush=True)
        logger.info("[%s] %s", self.log_type.value, safe_message)

    def write_lines(self, lines: list[str]) -> None:
        """Write multiple log lines.

        Args:
            lines: Lines to log
        """
        for line in lines:
            self.write(line)

    def get_content(self) -> str:
        """Get all captured log content.

        Returns:
            Log content as single string
        """
        return "\n".join(self.buffer)

    def flush(self) -> None:
        """Flush logs to database.

        Creates a BuildLog record with current content.
        """
        if not self.buffer:
            return

        # Import here to avoid circular imports
        import uuid

        from runtm_api.db.models import BuildLog

        log_record = BuildLog(
            deployment_id=uuid.UUID(self.deployment_id),
            log_type=self.log_type,
            content=self.get_content(),
        )
        self.db.add(log_record)
        self.db.commit()

    def __enter__(self) -> LogCapture:
        """Context manager entry."""
        self.write(f"Starting {self.log_type.value} phase")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit - flush logs."""
        if exc_type:
            self.write(f"Error: {exc_val}")
        else:
            duration = datetime.now(timezone.utc) - self.start_time
            self.write(f"Completed {self.log_type.value} phase in {duration.total_seconds():.1f}s")
        self.flush()


class SimpleLogBuffer:
    """Simple in-memory log buffer for non-DB logging.

    Useful for capturing logs before database is available.
    """

    def __init__(self) -> None:
        self.lines: list[str] = []

    def write(self, message: str) -> None:
        """Write a log message."""
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        self.lines.append(f"[{timestamp}] {message}")

    def get_content(self) -> str:
        """Get all log content."""
        return "\n".join(self.lines)

    def clear(self) -> None:
        """Clear the buffer."""
        self.lines = []
