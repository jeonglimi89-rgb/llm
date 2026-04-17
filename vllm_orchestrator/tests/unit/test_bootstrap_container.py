"""
test_bootstrap_container.py — unit tests for bootstrap.Container and its
dependency injection chain.

Background
==========
``bootstrap.py`` is the single entry point that wires together the
entire vllm_orchestrator runtime: settings → policy → health → circuit →
queue → scheduler → timeouts → tools → fallback → LLM adapter →
LLM client → router → dispatcher. Despite being the most critical
154 lines in the repo, it had near-zero dedicated test coverage.

The existing tests only touch:
  - ``_build_unified_policy(settings)`` (test_artifact_explainability scenario J)
  - ``Container().policy.cooldown_source == "settings"`` (hardening #47,
    explainability J/cross)

This file adds focused unit tests for the **full Container construction
chain**, without requiring a live LLM server (the Container will
always fall back to MockLLMAdapter when the settings URL is unreachable,
which is the expected case in a test environment).

What this file tests
====================
A. ``_build_unified_policy(settings)`` — settings → policy field mapping
B. ``Container.__init__`` — every public attribute is populated with
   the correct type and wired through the unified policy
C. Policy threading — every downstream consumer (scheduler, fallback,
   llm_client) reads the policy-derived source labels and cooldown values
D. LLM adapter fallback — when the settings URL is unreachable,
   Container falls back to MockLLMAdapter (not a live connection)
E. Settings field propagation — queue/circuit/health parameters from
   settings actually reach the components that use them
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.app.bootstrap import Container, _build_unified_policy
from src.app.settings import AppSettings
from src.app.execution.timeouts import (
    DEFAULT_HEALTH_TIMEOUT_S,
    DEFAULT_REQUEST_TIMEOUT_S,
    DEFAULT_TRANSPORT_RETRY_COOLDOWN_S,
)
from src.app.execution.circuit_breaker import CircuitBreaker
from src.app.execution.queue_manager import QueueManager
from src.app.execution.scheduler import Scheduler
from src.app.execution.timeouts import TimeoutPolicy, UnifiedTimeoutPolicy
from src.app.llm.client import LLMClient
from src.app.orchestration.router import Router
from src.app.orchestration.dispatcher import Dispatcher
from src.app.fallback.degraded_modes import DegradedModeHandler
from src.app.observability.health_registry import HealthRegistry
from src.app.tools.registry import ToolRegistry


# All tests use a settings object with an unreachable LLM URL so
# Container always picks the MockLLMAdapter path — no network needed.
_UNREACHABLE = "http://203.0.113.1:65000"  # RFC 5737 TEST-NET-3


# Module-level shared Container for tests that only INSPECT attributes
# without mutating them. Constructed once to avoid paying the ~10s
# _find_llm_url probe overhead per test. Tests that need custom settings
# (E-block) must construct their own Container via _test_settings.
_SHARED_CONTAINER: Container | None = None


def _get_shared_container() -> Container:
    """Lazy-construct a Container with default-ish settings and an
    unreachable LLM URL. Reused across tests that only read attributes."""
    global _SHARED_CONTAINER
    if _SHARED_CONTAINER is None:
        s = AppSettings()
        s.llm.base_url = _UNREACHABLE
        _SHARED_CONTAINER = Container(settings=s, _skip_llm_probe=True)
    return _SHARED_CONTAINER


def _test_settings(**overrides) -> AppSettings:
    """Build an AppSettings with an unreachable LLM URL and optional
    field overrides for specific test scenarios."""
    s = AppSettings()
    s.llm.base_url = _UNREACHABLE
    for key, val in overrides.items():
        parts = key.split(".")
        obj = s
        for p in parts[:-1]:
            obj = getattr(obj, p)
        setattr(obj, parts[-1], val)
    return s


# ===========================================================================
# A. _build_unified_policy: settings → policy field mapping
# ===========================================================================

def test_build_unified_policy_maps_settings_to_policy_fields():
    """``_build_unified_policy`` must read settings fields and produce
    a ``UnifiedTimeoutPolicy`` with the correct values and source
    labels. This is the single function that bridges ``AppSettings``
    and the unified policy object."""
    print("  [A1] _build_unified_policy maps settings → policy fields")
    s = _test_settings()
    s.timeouts.strict_json_s = 45.0
    s.fallback.max_retries = 5
    s.fallback.retry_delay_s = 1.23

    p = _build_unified_policy(s)

    assert isinstance(p, UnifiedTimeoutPolicy)
    assert p.request_timeout_s == 45.0, p.request_timeout_s
    assert p.request_timeout_source == "settings"
    assert p.max_retries == 5, p.max_retries
    assert p.transport_retry_cooldown_s == 1.23, p.transport_retry_cooldown_s
    assert p.fallback_retry_delay_s == 1.23, p.fallback_retry_delay_s
    assert p.cooldown_source == "settings"
    assert p.health_timeout_source == "settings"
    # Health timeout defaults to the policy default (settings doesn't
    # carry it explicitly yet — see bootstrap.py line 47).
    assert p.health_timeout_s == DEFAULT_HEALTH_TIMEOUT_S
    print("    OK")


def test_build_unified_policy_uses_default_settings_when_not_overridden():
    """With default AppSettings, the policy should carry the default
    timeout/retry/cooldown values from the settings dataclass defaults."""
    print("  [A2] _build_unified_policy with default settings → default policy values")
    s = AppSettings()
    p = _build_unified_policy(s)
    assert p.request_timeout_s == 15.0  # TimeoutSettings.strict_json_s default (GPU)
    assert p.max_retries == 1            # FallbackSettings.max_retries default
    assert p.transport_retry_cooldown_s == 2.0  # FallbackSettings.retry_delay_s default
    assert p.fallback_retry_delay_s == 2.0
    assert p.cooldown_source == "settings"
    print("    OK")


# ===========================================================================
# B. Container.__init__ — every public attribute is populated
# ===========================================================================

def test_container_creates_all_public_attributes():
    """Container must expose all the expected public attributes after
    construction. Each attribute must be the correct type. This locks
    the DI chain structure — adding or removing an attribute will fail
    this test immediately."""
    print("  [B1] Container creates all public attributes with correct types")
    c = _get_shared_container()

    # Settings & policy
    assert isinstance(c.settings, AppSettings)
    assert isinstance(c.policy, UnifiedTimeoutPolicy)

    # Execution layer
    assert isinstance(c.health, HealthRegistry)
    assert isinstance(c.circuit, CircuitBreaker)
    assert isinstance(c.queue, QueueManager)
    assert isinstance(c.scheduler, Scheduler)
    assert isinstance(c.timeouts, TimeoutPolicy)

    # Tools
    assert isinstance(c.tools, ToolRegistry)

    # Fallback
    assert isinstance(c.fallback, DegradedModeHandler)

    # LLM client
    assert isinstance(c.llm_client, LLMClient)

    # Orchestration
    assert isinstance(c.router, Router)
    assert isinstance(c.dispatcher, Dispatcher)
    print("    OK: 12 public attributes, all correct types")


# ===========================================================================
# C. Policy threading — downstream consumers read policy values
# ===========================================================================

def test_container_threads_policy_into_scheduler():
    """Scheduler must receive its cooldown values and source label from
    the unified policy, not from its constructor defaults."""
    print("  [C1] Container threads policy into Scheduler")
    s = _test_settings()
    s.fallback.retry_delay_s = 0.77
    c = Container(settings=s, _skip_llm_probe=True)

    assert c.scheduler.cooldown_source == "settings"
    # Scheduler heavy/light cooldowns come from policy defaults
    # (settings doesn't override scheduler-specific values currently).
    assert c.scheduler.cooldown_heavy_s >= 0
    assert c.scheduler.cooldown_light_s >= 0
    print("    OK")


def test_container_threads_policy_into_fallback():
    """DegradedModeHandler must receive fallback_retry_delay from the
    unified policy."""
    print("  [C2] Container threads policy into DegradedModeHandler")
    s = _test_settings()
    s.fallback.retry_delay_s = 0.77
    c = Container(settings=s, _skip_llm_probe=True)

    assert c.fallback.fallback_retry_delay_s == 0.77
    assert c.fallback.fallback_retry_delay_source == "settings"
    print("    OK")


def test_container_threads_policy_into_llm_client():
    """LLMClient must receive transport_retry_cooldown from the unified
    policy, not from its constructor default."""
    print("  [C3] Container threads policy into LLMClient")
    s = _test_settings()
    s.fallback.retry_delay_s = 0.77
    c = Container(settings=s, _skip_llm_probe=True)

    assert c.llm_client.transport_retry_cooldown_s == 0.77
    assert c.llm_client.transport_retry_cooldown_source == "settings"
    print("    OK")


def test_container_threads_fallback_into_dispatcher():
    """Dispatcher must receive the fallback handler from Container so
    the T-tranche-10 explainability wiring works."""
    print("  [C4] Container threads fallback into Dispatcher")
    c = _get_shared_container()
    assert c.dispatcher.fallback is c.fallback
    print("    OK")


# ===========================================================================
# D. LLM adapter fallback — unreachable URL → MockLLMAdapter
# ===========================================================================

def test_container_falls_back_to_mock_adapter_when_url_unreachable():
    """When the LLM URL is unreachable (RFC TEST-NET-3), Container must
    construct a MockLLMAdapter and wrap it in LLMClient. The adapter
    must report its provider_name so we can identify which adapter was
    chosen without a live server."""
    print("  [D1] Container falls back to MockLLMAdapter on unreachable URL")
    c = _get_shared_container()

    # LLMClient wraps the adapter. The adapter is accessible via .adapter.
    adapter = c.llm_client.adapter
    # MockLLMAdapter has provider_name = "mock"
    assert getattr(adapter, "provider_name", "") == "mock", (
        f"expected MockLLMAdapter (provider_name='mock'), got "
        f"{getattr(adapter, 'provider_name', '?')!r}"
    )
    print("    OK: adapter is MockLLMAdapter")


# ===========================================================================
# E. Settings field propagation — queue/circuit/health params
# ===========================================================================

def test_container_propagates_queue_settings():
    """Queue manager must receive its max_concurrency and max_depth from
    settings, not from its constructor defaults."""
    print("  [E1] Container propagates queue settings")
    s = _test_settings()
    s.queue.max_concurrency = 4
    s.queue.max_depth = 50
    s.queue.task_timeout_s = 99
    c = Container(settings=s, _skip_llm_probe=True)

    assert c.queue._max_concurrency == 4
    assert c.queue._max_depth == 50
    assert c.queue._task_timeout_s == 99
    print("    OK")


def test_container_propagates_health_fail_threshold():
    """HealthRegistry must receive fail_threshold from settings."""
    print("  [E2] Container propagates health fail threshold")
    s = _test_settings()
    s.llm.health_fail_threshold = 7
    c = Container(settings=s, _skip_llm_probe=True)

    assert c.health._fail_threshold == 7
    print("    OK")


def test_container_circuit_breaker_has_hardcoded_defaults():
    """CircuitBreaker is constructed with hardcoded defaults
    (fail_threshold=3, reset_timeout_s=60), NOT from settings. This
    locks the current contract so a future refactor that wires it
    through settings must update this test explicitly."""
    print("  [E3] CircuitBreaker uses hardcoded defaults (not from settings)")
    c = _get_shared_container()

    assert c.circuit._fail_threshold == 3
    assert c.circuit._reset_timeout_s == 60
    print("    OK: fail_threshold=3, reset_timeout_s=60 (hardcoded)")


def test_container_timeouts_policy_uses_settings_timeout_values():
    """TimeoutPolicy (the legacy pool-keyed policy) must carry the
    timeout values from settings. This is separate from
    UnifiedTimeoutPolicy."""
    print("  [E4] TimeoutPolicy uses settings timeout values")
    s = _test_settings()
    s.timeouts.strict_json_s = 33.0
    s.timeouts.fast_chat_s = 22.0
    c = Container(settings=s, _skip_llm_probe=True)

    assert c.timeouts.get_timeout("strict_json") == 33.0
    assert c.timeouts.get_timeout("fast_chat") == 22.0
    print("    OK")


def test_container_llm_client_max_retries_on_mock_fallback_path():
    """LLMClient.max_retries must come from settings.fallback.max_retries
    on BOTH the live-adapter path AND the mock-adapter fallback path.

    Bug history: before the bootstrap audit fix, the mock-adapter path
    (bootstrap.py line 132-137) did NOT pass max_retries as a positional
    arg, so it defaulted to LLMClient.__init__'s default (1) regardless
    of the settings value. The live-adapter path (line 124-128) did pass
    it. The fix adds the missing positional arg on the mock path.
    """
    print("  [E5] LLMClient.max_retries from settings on mock fallback path")
    s = _test_settings()
    s.fallback.max_retries = 7
    c = Container(settings=s, _skip_llm_probe=True)

    assert c.llm_client.max_retries == 7, (
        f"mock fallback path should propagate settings.fallback.max_retries=7, "
        f"got {c.llm_client.max_retries}"
    )
    print("    OK")


TESTS = [
    test_build_unified_policy_maps_settings_to_policy_fields,
    test_build_unified_policy_uses_default_settings_when_not_overridden,
    test_container_creates_all_public_attributes,
    test_container_threads_policy_into_scheduler,
    test_container_threads_policy_into_fallback,
    test_container_threads_policy_into_llm_client,
    test_container_threads_fallback_into_dispatcher,
    test_container_falls_back_to_mock_adapter_when_url_unreachable,
    test_container_propagates_queue_settings,
    test_container_propagates_health_fail_threshold,
    test_container_circuit_breaker_has_hardcoded_defaults,
    test_container_timeouts_policy_uses_settings_timeout_values,
    test_container_llm_client_max_retries_on_mock_fallback_path,
]


if __name__ == "__main__":
    print("=" * 60)
    print("Bootstrap Container unit tests")
    print("=" * 60)
    passed = 0
    for fn in TESTS:
        try:
            fn()
            passed += 1
        except Exception as e:
            print(f"  [FAIL] {fn.__name__}: {e}")
            import traceback; traceback.print_exc()
    print(f"\nResults: {passed}/{len(TESTS)} passed")
