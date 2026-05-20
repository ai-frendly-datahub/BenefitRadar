from __future__ import annotations

from types import SimpleNamespace

import pytest

from benefitradar import resilience
from benefitradar.resilience import (
    SourceCircuitBreakerListener,
    SourceCircuitBreakerManager,
    get_circuit_breaker_manager,
)

pytestmark = pytest.mark.unit


def test_listener_methods_log_state_failure_and_success(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, dict[str, object]]] = []

    class FakeLogger:
        def info(self, event: str, **kwargs: object) -> None:
            events.append((event, kwargs))

        def warning(self, event: str, **kwargs: object) -> None:
            events.append((event, kwargs))

        def debug(self, event: str, **kwargs: object) -> None:
            events.append((event, kwargs))

    monkeypatch.setattr(resilience, "logger", FakeLogger())
    listener = SourceCircuitBreakerListener()
    breaker = SimpleNamespace(name="보조금24")

    listener.before_call(breaker, object())
    listener.state_change(
        breaker,
        SimpleNamespace(name="closed"),
        SimpleNamespace(name="open"),
    )
    listener.state_change(breaker, None, SimpleNamespace(name="closed"))
    listener.failure(breaker, RuntimeError("boom"))
    listener.success(breaker)

    assert events == [
        (
            "circuit_breaker_state_change",
            {"source": "보조금24", "before": "closed", "after": "open"},
        ),
        (
            "circuit_breaker_state_change",
            {"source": "보조금24", "before": None, "after": "closed"},
        ),
        (
            "circuit_breaker_failure",
            {"source": "보조금24", "exception": "RuntimeError", "message": "boom"},
        ),
        ("circuit_breaker_success", {"source": "보조금24"}),
    ]


def test_circuit_breaker_manager_reuses_resets_and_reports_status() -> None:
    manager = SourceCircuitBreakerManager()

    first = manager.get_breaker("source-a")
    second = manager.get_breaker("source-a")
    other = manager.get_breaker("source-b")

    assert first is second
    assert other is not first
    assert first.name == "source-a"
    assert first.fail_max == 5
    assert first.reset_timeout == 60
    assert manager.get_status() == {"source-a": "closed", "source-b": "closed"}

    manager.reset_breaker("source-a")
    manager.reset_breaker("missing")
    manager.reset_all()

    assert manager.get_status() == {"source-a": "closed", "source-b": "closed"}


def test_global_circuit_breaker_manager_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(resilience, "_manager", None)

    first = get_circuit_breaker_manager()
    second = get_circuit_breaker_manager()

    assert first is second
