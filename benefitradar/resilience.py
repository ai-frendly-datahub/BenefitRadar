from __future__ import annotations

import threading
from typing import Any, Callable, Dict, Optional

import structlog
from pybreaker import CircuitBreaker

logger = structlog.get_logger(__name__)


class SourceCircuitBreakerListener:
    """Listener for circuit breaker state changes with structlog integration."""

    def state_change(
        self,
        cb: CircuitBreaker,
        before: str,
        after: str,
    ) -> None:
        """Log state transitions."""
        logger.info(
            "circuit_breaker_state_change",
            source=cb.name,
            before=before,
            after=after,
        )

    def failure(
        self,
        cb: CircuitBreaker,
        exc: Exception,
    ) -> None:
        """Log failures."""
        logger.warning(
            "circuit_breaker_failure",
            source=cb.name,
            exception=type(exc).__name__,
            message=str(exc),
        )

    def success(self, cb: CircuitBreaker) -> None:
        """Log successes."""
        logger.debug(
            "circuit_breaker_success",
            source=cb.name,
        )


class SourceCircuitBreakerManager:
    """Thread-safe registry of per-source circuit breakers."""

    def __init__(self) -> None:
        """Initialize the manager with empty registry and lock."""
        self._instances: Dict[str, CircuitBreaker] = {}
        self._lock = threading.RLock()
        self._listener = SourceCircuitBreakerListener()

    def get_breaker(self, source_name: str) -> CircuitBreaker:
        """Get or create a circuit breaker for a source.

        Args:
            source_name: Name of the source (e.g., 'BBC News', 'Reuters')

        Returns:
            CircuitBreaker instance for the source
        """
        if source_name in self._instances:
            return self._instances[source_name]

        with self._lock:
            # Double-check pattern
            if source_name in self._instances:
                return self._instances[source_name]

            breaker = CircuitBreaker(
                fail_max=5,
                reset_timeout=60,
                success_threshold=2,
                listeners=[self._listener],
                name=source_name,
                exclude=[ValueError, KeyError, AttributeError],
            )
            self._instances[source_name] = breaker
            return breaker

    def reset_breaker(self, source_name: str) -> None:
        """Reset a specific source's circuit breaker.

        Args:
            source_name: Name of the source to reset
        """
        with self._lock:
            if source_name in self._instances:
                self._instances[source_name].reset()
                logger.info("circuit_breaker_reset", source=source_name)

    def reset_all(self) -> None:
        """Reset all circuit breakers."""
        with self._lock:
            for breaker in self._instances.values():
                breaker.reset()
            logger.info("circuit_breaker_reset_all", count=len(self._instances))

    def get_status(self) -> Dict[str, str]:
        """Get status of all circuit breakers.

        Returns:
            Dict mapping source names to their current state
        """
        with self._lock:
            return {name: breaker.state for name, breaker in self._instances.items()}


# Global singleton instance
_manager: Optional[SourceCircuitBreakerManager] = None
_manager_lock = threading.Lock()


def get_circuit_breaker_manager() -> SourceCircuitBreakerManager:
    """Get or create the global circuit breaker manager.

    Returns:
        The global SourceCircuitBreakerManager instance
    """
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                _manager = SourceCircuitBreakerManager()
    return _manager
