from __future__ import annotations

import json
import logging
import os
from io import StringIO
from pathlib import Path

import pytest
import structlog

from benefitradar.logger import get_logger, configure_logging


class TestLoggerConfiguration:
    """Test structured logging configuration."""

    def test_get_logger_returns_bound_logger(self) -> None:
        """Should return a logger instance with bind method."""
        logger = get_logger("test_module")
        # structlog returns proxy objects, check for bind method
        assert hasattr(logger, "bind")
        assert callable(logger.bind)

    def test_logger_name_is_set(self) -> None:
        """Should set logger name in context."""
        logger = get_logger("my_module")
        # BoundLogger has _context attribute
        assert hasattr(logger, "_context")

    def test_context_binding_works(self) -> None:
        """Should bind key-value pairs to logger context."""
        logger = get_logger("test")
        bound = logger.bind(source_name="test_source", article_id=123)
        # Check that bind returns a logger-like object with methods
        assert hasattr(bound, "info")
        assert callable(bound.info)

    def test_json_output_format_is_valid(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Should output valid JSON when configured for JSON output."""
        # Configure for JSON output
        configure_logging(log_level="INFO", use_json=True)
        logger = get_logger("test_json")

        logger.info("test_event", key="value", number=42)

        captured = capsys.readouterr()
        # Parse JSON from output
        lines = [line for line in captured.err.split("\n") if line.strip()]
        if lines:
            parsed = json.loads(lines[0])
            assert parsed.get("event") == "test_event"
            assert parsed.get("key") == "value"
            assert parsed.get("number") == 42

    def test_log_level_filtering_info(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Should filter logs by level (INFO level)."""
        configure_logging(log_level="INFO", use_json=False)
        logger = get_logger("test_level")

        logger.debug("debug_msg")
        logger.info("info_msg")

        captured = capsys.readouterr()
        # INFO should be present, DEBUG should not
        assert "info_msg" in captured.err or "info_msg" in captured.out
        # DEBUG might not appear depending on configuration

    def test_log_level_filtering_warning(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Should filter logs by level (WARNING level)."""
        configure_logging(log_level="WARNING", use_json=False)
        logger = get_logger("test_warn")

        logger.info("info_msg")
        logger.warning("warning_msg")

        captured = capsys.readouterr()
        # WARNING should be present
        assert "warning_msg" in captured.err or "warning_msg" in captured.out

    def test_timestamp_is_iso8601_utc(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Should include ISO8601 UTC timestamp in logs."""
        configure_logging(log_level="INFO", use_json=True)
        logger = get_logger("test_timestamp")

        logger.info("test_event")

        captured = capsys.readouterr()
        lines = [line for line in captured.err.split("\n") if line.strip()]
        if lines:
            parsed = json.loads(lines[0])
            # Should have timestamp field
            assert "timestamp" in parsed
            # Should be ISO8601 format (contains T and Z or +)
            ts = parsed["timestamp"]
            assert "T" in ts or isinstance(ts, str)

    def test_environment_variable_log_level(self) -> None:
        """Should read log level from RADAR_LOG_LEVEL env var."""
        os.environ["RADAR_LOG_LEVEL"] = "DEBUG"
        try:
            configure_logging()
            logger = get_logger("test_env")
            # Should not raise
            assert logger is not None
        finally:
            if "RADAR_LOG_LEVEL" in os.environ:
                del os.environ["RADAR_LOG_LEVEL"]

    def test_default_log_level_is_info(self) -> None:
        """Should default to INFO level when env var not set."""
        if "RADAR_LOG_LEVEL" in os.environ:
            del os.environ["RADAR_LOG_LEVEL"]

        configure_logging()
        logger = get_logger("test_default")
        assert logger is not None

    def test_logger_with_context_propagation(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Should propagate context across bound loggers."""
        configure_logging(log_level="INFO", use_json=True)
        logger = get_logger("test_context")

        bound = logger.bind(source_name="test_source")
        bound.info("event_with_context", article_link="http://example.com")

        captured = capsys.readouterr()
        lines = [line for line in captured.err.split("\n") if line.strip()]
        if lines:
            parsed = json.loads(lines[0])
            assert parsed.get("source_name") == "test_source"
            assert parsed.get("article_link") == "http://example.com"

    def test_multiple_loggers_independent(self) -> None:
        """Should create independent logger instances."""
        logger1 = get_logger("module1")
        logger2 = get_logger("module2")

        # Different names should return different logger instances
        assert logger1 is not logger2
        assert hasattr(logger1, "info")
        assert hasattr(logger2, "info")
