from __future__ import annotations

import pytest

from benefitradar.logger import configure_logging


@pytest.fixture(scope="session", autouse=True)
def configure_test_logging() -> None:
    """Configure logging for tests (session-scoped)."""
    configure_logging(log_level="INFO", use_json=False)


@pytest.fixture(autouse=True)
def reconfigure_logging_per_test() -> None:
    """Reconfigure logging for each test to ensure logger is fresh."""
    configure_logging(log_level="INFO", use_json=False)
