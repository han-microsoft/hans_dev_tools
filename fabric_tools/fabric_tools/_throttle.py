"""Fabric throttle gate — concurrency semaphore + circuit breaker.

Module role:
    All Fabric API calls (GQL + KQL) must acquire/release through the singleton
    FabricThrottleGate. This bounds total concurrent load against the shared
    Fabric capacity (e.g., F8 = 8 CU, but we default to 2 in-flight to leave
    headroom for background processes).

    Composes a generic ``CircuitBreaker`` (from ``app.resilience``) with an
    ``asyncio.Semaphore`` for Fabric-specific concurrency control. The breaker
    owns the Closed → Open → Half-Open → Closed state machine. This module
    owns the semaphore and probe-bypass logic (Fabric needs a probe to bypass
    the semaphore since the semaphore is the capacity gate, not the breaker).

Key collaborators:
    - ``app.resilience``           – generic CircuitBreaker + registry
    - ``_fabric_constants.py``     – FABRIC_MAX_CONCURRENT, FABRIC_CB_THRESHOLD, cooldown values
    - ``graph_explorer/_fabric.py`` – acquires/releases gate for each GQL query
    - ``telemetry/_fabric.py``     – acquires/releases gate for each KQL query

Dependents:
    Singleton accessed via ``get_fabric_gate()``
"""

from __future__ import annotations

import asyncio
import logging

from fabric_tools._resilience import CircuitBreaker, CircuitState, registry
from fabric_tools._constants import (
    FABRIC_CB_COOLDOWN,
    FABRIC_CB_MAX_COOLDOWN,
    FABRIC_CB_THRESHOLD,
    FABRIC_MAX_CONCURRENT,
)

logger = logging.getLogger(__name__)


class FabricThrottleError(Exception):
    """Raised when the circuit breaker is open.

    The message is user-facing (may appear in tool results or SSE streams),
    so it uses non-technical language and avoids internal service names.
    """

    def __init__(self, retry_after: int):
        self.retry_after = retry_after
        # User-friendly message — no 'Fabric', no 'circuit breaker' jargon
        super().__init__(f"Data service temporarily unavailable \u2014 retry in {retry_after}s")


class FabricThrottleGate:
    """Concurrency semaphore + circuit breaker for Fabric API calls.

    Composes:
        - ``CircuitBreaker`` from ``app.resilience`` — state machine (generic)
        - ``asyncio.Semaphore`` — concurrency control (Fabric-specific, F8 CU headroom)

    The semaphore limits in-flight Fabric calls. The breaker tracks
    consecutive failures and rejects calls when the service is down.
    In HALF_OPEN state, one probe request bypasses the semaphore to
    test recovery without consuming a concurrency slot.
    """

    def __init__(self):
        # Concurrency semaphore — Fabric-specific capacity control
        self._semaphore = asyncio.Semaphore(FABRIC_MAX_CONCURRENT)
        # Circuit breaker — created directly for per-instance isolation (tests
        # create multiple gates). The production singleton registers this
        # breaker in the registry via get_fabric_gate() for health endpoint
        # visibility.
        self._breaker = CircuitBreaker(
            "fabric",
            failure_threshold=FABRIC_CB_THRESHOLD,
            cooldown_secs=FABRIC_CB_COOLDOWN,
            max_cooldown_secs=FABRIC_CB_MAX_COOLDOWN,
        )
        # Probe bypass flag — True when one caller should skip the semaphore
        # to test if Fabric has recovered. Fabric-specific concern because
        # the semaphore is the capacity gate (generic breakers don't have one).
        self._half_open_probe_allowed = False

    @property
    def state(self) -> CircuitState:
        """Current circuit breaker state — delegates to generic breaker."""
        return self._breaker.state

    async def acquire(self) -> bool:
        """Acquire permission to make a Fabric API call.

        Returns True if this is a half-open probe (bypassed semaphore).
        Raises FabricThrottleError if circuit is open.

        Side effects:
            - May transition breaker from OPEN → HALF_OPEN (via is_open())
            - Acquires semaphore slot (released by caller via release())
        """
        # Check breaker — is_open() handles OPEN → HALF_OPEN transition
        if self._breaker.is_open():
            s = self._breaker.status()
            raise FabricThrottleError(int(s["seconds_until_probe"] or 1))

        # HALF_OPEN: allow exactly one probe through without acquiring
        # semaphore to test if Fabric has recovered. After the first probe,
        # subsequent HALF_OPEN callers go through the normal semaphore path.
        if self._breaker.state == CircuitState.HALF_OPEN:
            if not self._half_open_probe_allowed:
                # First HALF_OPEN caller — grant probe bypass, set flag so
                # subsequent callers take the normal semaphore path.
                self._half_open_probe_allowed = True
                return True  # Probe bypasses semaphore
            # Subsequent HALF_OPEN calls — fall through to normal semaphore

        # Normal path: acquire semaphore slot
        await self._semaphore.acquire()

        # Re-check after semaphore wait — circuit may have tripped while queued
        if self._breaker.is_open():
            self._semaphore.release()
            s = self._breaker.status()
            raise FabricThrottleError(int(s["seconds_until_probe"] or 1))
        return False

    def release(self, *, _was_probe: bool = False) -> None:
        """Release the semaphore slot after a Fabric API call completes.

        Args:
            _was_probe: If True, the caller bypassed the semaphore (half-open
                        probe) and should NOT release it.
        """
        if not _was_probe:
            self._semaphore.release()

    async def record_success(self) -> None:
        """Record a successful Fabric API response.

        Resets the probe flag so the next HALF_OPEN entry grants a
        fresh probe bypass to the first caller.
        """
        self._breaker.record_success()
        # Reset probe flag — on next OPEN → HALF_OPEN transition, the
        # first caller gets a probe bypass again.
        self._half_open_probe_allowed = False

    async def record_429(self) -> None:
        """Record a 429 response. Delegates to breaker as a failure."""
        self._breaker.record_failure()

    async def record_server_error(self) -> None:
        """Record a 5xx that isn't ColdStartTimeout. Delegates to breaker."""
        self._breaker.record_failure()

    def status(self) -> dict:
        """Return current gate status for health/debug endpoints.

        Extends the generic breaker status with Fabric-specific semaphore info.
        """
        base = self._breaker.status()
        # Add Fabric-specific semaphore availability
        base["semaphore_available"] = self._semaphore._value
        return base


# ── Module-level singleton (thread-safe) ─────────────────────────────────────

import threading as _threading

_gate: FabricThrottleGate | None = None
_gate_lock = asyncio.Lock()


async def get_fabric_gate() -> FabricThrottleGate:
    """Return the singleton FabricThrottleGate (asyncio-safe double-checked locking).

    Registers the gate's breaker in the module-level registry so the
    ``/api/services/health`` endpoint can report its state.
    """
    global _gate
    if _gate is None:
        async with _gate_lock:
            if _gate is None:
                _gate = FabricThrottleGate()
                # Register in the centralized registry for health endpoint visibility
                registry._breakers["fabric"] = _gate._breaker
    return _gate
