# Testing gate boundary

> Introduced T-tranche-6 (2026-04-08). Read this **before** running `pytest tests/` for the first time on a fresh checkout — it tells you why a bare run gives you 359 tests and not 384, and how to pull in the rest on demand.

## Three categories

The test suite has three orthogonal slices, selected via pytest markers:

| Slice | Marker | Scope | Typical runtime | Needs infra? |
|---|---|---|---|---|
| **Deterministic gate** (default) | *(none)* | unit + regression + most integration | ~3 min | no |
| **Infra-dependent suite** | `infra` | requires live LLM server, FastAPI subprocess, or real network | varies | **yes** |
| **Load / soak suite** | `load` | FakeLLM-backed throughput / soak / queue-saturation | ~1 min | no (just slow) |

**The default gate is everything that carries no marker.** It is configured in `pytest.ini` via `addopts = -m "not infra and not load"` — so a plain `pytest tests/` automatically runs only the deterministic slice.

## Commands

```bash
# 1. Default deterministic gate (what CI runs).
pytest tests/

# 2. Infra-dependent suite — only when the live LLM server / network is up.
pytest tests/ -m infra

# 3. Deterministic load suite — when you want to exercise throughput paths.
pytest tests/ -m load

# 4. Absolutely everything (override the default filter).
pytest tests/ -m ""
```

## Why the split exists

Before T-tranche-6, a bare `pytest tests/` would **stall or hang** because:

1. `tests/integration/test_http_e2e.py` spawns a FastAPI subprocess on port 8100 and waits up to ~20s for it to come up. When the subprocess can't bind, the whole file sits there.
2. `tests/load/test_real_orchestrator.py` and `tests/load/test_real_server_full.py` drive a real `VLLMHttpAdapter` against `http://192.168.57.105:8000`. When the LLM server isn't running, every request hits the health timeout (5s each, multiplied by dozens of requests).
3. The FakeLLM-backed load tests under `tests/load/` are deterministic but add ~60s to a clean run.

None of these are bugs or regressions — they're just environment-sensitive. Without markers, a contributor seeing `tests/` hang would have no way to tell whether it's an actual regression or a missing live dependency. **The marker boundary makes that distinction load-bearing in the test infrastructure itself**, not in tribal knowledge.

## Inventory (as of T-tranche-6)

### Infra-dependent (`infra` marker)

File-level `pytestmark = pytest.mark.infra`:
- `tests/integration/test_http_e2e.py` — FastAPI subprocess e2e (6 tests)
- `tests/load/test_real_orchestrator.py` — live LLM server load (5 tests)
- `tests/load/test_real_server_full.py` — live LLM server full-matrix (8 tests)

Function-level `@pytest.mark.infra` on a single test inside an otherwise-deterministic file:
- `tests/integration/test_engine_e2e.py::test_llm_to_compiler_e2e`
- `tests/integration/test_builder_engine_e2e.py::test_llm_to_planner_e2e`

Both function-level tests use `pytest.skip(...)` with an explicit reason when the live server is unreachable, so even `pytest -m infra` on a machine without infra produces a clean `skipped` report, not a hang or a silent pass.

### Load (`load` marker)

All file-level `pytestmark = pytest.mark.load`, all FakeLLM-backed:
- `tests/load/test_overload_rejection.py` (1 test)
- `tests/load/test_serial_40_case.py` (2 tests)
- `tests/load/test_soak_concurrency.py` (2 tests)
- `tests/load/test_timeout_recovery.py` (1 test)

### Deterministic gate

Everything else — `tests/unit/`, `tests/regression/`, and all of `tests/integration/` except the two files above. This is the slice CI runs by default.

## Adding a new test

Picking the right slice:

- **Does your test hit a real network socket or spawn a subprocess?** → `infra`.
- **Does your test use FakeLLM but takes longer than a second per case in a tight loop?** → `load`.
- **Otherwise** → leave it unmarked and it lands in the deterministic gate.

To apply a file-level marker, add near the top of the file:

```python
import pytest

pytestmark = pytest.mark.infra   # or pytest.mark.load
```

For a single test inside an otherwise-deterministic file, decorate the function:

```python
@pytest.mark.infra
def test_my_live_thing():
    ...
    if not adapter.is_available():
        pytest.skip("live LLM server not reachable (infra-dependent test)")
    ...
```

## Artifact explainability coverage (T-tranche-7)

The default gate runs **two coordinated layers** that lock the
explainability contract of `runtime/human_review/export_run_report.json`:

- `tests/integration/test_export_hardening.py` — broad **field-existence** and
  schema-presence coverage of every additive T-tranche field. Drives
  `run_export(adapter_override=fake)` end-to-end and validates that the
  serialized JSON has the right shape.
- `tests/integration/test_artifact_explainability.py` — focused
  **semantic-value** coverage. The file has two layers:

  Core invariants (T-tranche-7, 5 tests):
  1. Cooldown applied path: `cooldown_decisions[i]` for `transport_retry`
     contains `applied_s > 0` with `clamped=False, skipped=False` when
     the deadline allows it.
  2. Count-based retry exhaustion: `retry_decision_reason ==
     "transport_retry_exhausted"` and `attempts_used == 1 + max_retries`
     when retries exhaust by count rather than budget.
  3. JSON-roundtrip shape: every `cooldown_decisions[i]` has the canonical
     7-key `WaitDecision` shape, normalized `kind` enum value, numeric
     types, non-empty `source`.
  4. Health failure classification (connection_refused branch): staged
     `HealthProbeResult` flows into per-case and run-level
     `health_failure_reason`.
  5. Operator-set values survive end-to-end: every operator-supplied
     timeout / cooldown / source label appears in the JSON at exactly the
     value the operator set, and `policy_source_summary` lists all 4
     source labels with non-default values.

  Full-surface sweeps (T-tranche-8, 10 tests):
  6. **Normalized health reason sweep** — 7-row `pytest.mark.parametrize`
     table driving every `HEALTH_REASON_*` constant
     (`ok / timeout / connection_refused / dns_failure / http_error /
     malformed_response / unexpected_error`) through a full
     `run_export` JSON roundtrip. Per-case and run-level
     `health_failure_reason` must equal the exact normalized string for
     failure branches; for the `ok` branch both values must be `None`
     (per the `export_runtime` rule that populates the field only when
     `available=False`). An accompanying drift-prevention test compares
     the parametrize table against `dir(vllm_http)` to catch renamed /
     added / removed constants before the sweep silently loses coverage.
  7. **Scheduler heavy wait applied path** — two `builder.requirement_parse`
     (HEAVY) cases in sequence force case 2 to carry a `scheduler_heavy`
     entry in its `cooldown_decisions` with `applied_s > 0`,
     `skipped=False`, `clamped=False`, `source="test"`, and the
     internal consistency `applied_s ≤ configured_s`. Run-level
     `total_cooldown_ms` aggregate must reflect the applied wait.
  8. **Scheduler light wait applied path** — same shape, using two
     `builder.patch_intent_parse` (LIGHT) cases to flip the scheduler's
     `_last_was_heavy` to False and force the `scheduler_light` kind
     on case 2. All semantic-value assertions mirror the heavy path.

  Fallback wait + settings-source survival (T-tranche-9, 4 tests):
  9. **Fallback WaitDecision JSON shape** — `DegradedModeHandler.record_wait_decision()`
     (extracted in T-tranche-10 from the first block of `handle_failure`)
     populates `last_wait_decision` with the canonical 7-key WaitDecision
     dict; after `to_dict()` + JSON roundtrip the dict carries
     `kind="fallback_retry"`, the operator-supplied `configured_s`,
     `applied_s=0.0`, `skipped=True`, `clamped=False`,
     `skip_reason="zero_configured"`, and the policy `source` label.
     This is the **pure serialization contract** layer; the T-tranche-10
     test below is the **artifact E2E** layer.
     Note: the current handler intentionally does **not** sleep
     (silent-fallback risk avoidance), so `applied_s == 0.0` and
     `skipped is True` are documented invariants, not bugs.
  10. **Fallback legacy constructor source = "default"** — drift-prevention
     check that the legacy `DegradedModeHandler()` (no `policy=`) emits a
     decision with `source="default"`, locking the asymmetry vs the
     policy-driven path.
  11. **Settings-driven `cooldown_source="settings"` survives end-to-end**
     — builds a `UnifiedTimeoutPolicy` via
     `bootstrap._build_unified_policy(settings)` (the same path the live
     `Container` takes), drives `run_export(adapter_override=...)` with
     all four source labels = `"settings"`, and verifies the on-disk
     JSON carries `cooldown_source="settings"`,
     `configured_{transport_retry,fallback_retry}` = the settings value,
     a 4-key `policy_source_summary` with every slot = `"settings"`, and
     the per-case `transport_retry_cooldown_source = "settings"`.
  12. **Container ↔ artifact cross-check** — builds a `Container(settings=...)`
     with the same `settings.fallback.retry_delay_s` and asserts
     `policy + scheduler + fallback + llm_client` all read `source="settings"`.
     This is the explicit cross-link between the live wiring (locked at
     memory level by hardening test #47) and the artifact-level test
     #11 above.

  Dispatcher failure exit coverage audit (T-tranche-11, 6 tests):
  F1. **Static failure-exit inventory drift** (`tests/unit/test_dispatcher_failure_exit_inventory.py`).
      AST-parses `src/app/orchestration/dispatcher.py`, enumerates every
      `return TaskResult(...)` site inside the `Dispatcher` class, and
      pins the per-status counts (`ERROR=2, SHED=1, DONE=1`). Any new
      `return TaskResult` added or an existing one removed fails the
      test immediately before any runtime test runs.
  F2. **Failure-exit fallback-merge uniformity** (same file). For every
      failure return site (`status ∈ {ERROR, SHED, TIMEOUT}` per the
      `TaskStatus` enum), the enclosing function body must reference
      **both** `record_wait_decision` and
      `_merge_fallback_wait_into_retry_decision`. Adding a new failure
      branch without wiring the fallback merge fails the test loud,
      even if no runtime test exists for that specific branch yet.
  F3. **SHED branch runtime closure** — T-tranche-11 patched
      `Dispatcher.dispatch()`'s `OverloadError` queue-shed branch to
      synthesize an empty `rd_dict` on-the-fly and merge the fallback
      `WaitDecision` into it before returning. Runtime semantics stay
      `TaskStatus.SHED` (no DEGRADED switch). The test installs a fake
      `QueueManager` whose `submit` raises `OverloadError`, drives
      `dispatch()`, and asserts the returned `TaskResult.retry_decision`
      carries exactly one `fallback_retry` entry with the canonical
      7-key shape + operator-supplied `configured_s` + `applied_s=0.0`
      + `skipped=True`.
  F4. **SHED branch back-compat** — external callers that build
      `Dispatcher` without `fallback=` still hit a clean shed: the test
      exercises the same fake queue without a fallback handler and
      asserts `status=SHED` and `retry_decision is None` (no synthesis
      when no fallback was passed).

  The F1/F2 pair is the **static inventory protection** layer; F3/F4
  plus the 2 T-tranche-10 tests below are the **runtime explainability
  protection** layer.

  QueueManager coverage + post-hoc wrapper (T-tranche-12, 5 tests):
  F5. **QueueManager failure exit inventory**
      (`tests/unit/test_queue_manager_failure_exit_inventory.py`, 3
      tests). AST-scans `src/app/execution/queue_manager.py` and pins
      its single `return TaskResult(...)` site (the
      `except Exception` catch-all in `submit()`, status=ERROR). Any
      new queue-level failure return fires the inventory drift. This
      file deliberately does NOT require the queue layer to reference
      `record_wait_decision` or `_merge_fallback_wait_into_retry_decision`
      — queue_manager is a lower-level execution primitive and should
      stay decoupled from fallback semantics. The file's third test
      is a negative assertion that enforces that decoupling.
  F6. **Post-hoc dispatcher wrapper for queue-returned failure results**
      (runtime, in `test_artifact_explainability.py`). T-tranche-12
      patched `Dispatcher.dispatch()` so that any `TaskResult` returned
      from `queue.submit()` with `status in {ERROR, SHED, TIMEOUT}` AND
      `retry_decision is None` is post-hoc enriched with a fallback
      `WaitDecision` via `_merge_fallback_wait_into_retry_decision`.
      The test monkey-patches the dispatcher's `_execute` to raise an
      exception, which is caught by `QueueManager.submit`'s catch-all
      branch and returned as an ERROR `TaskResult`; the wrapper then
      enriches it in-place. Semantic assertions: canonical 7-key shape,
      `configured_s == 0.21`, `applied_s == 0.0`, `skipped=True`,
      `skip_reason="zero_configured"`, `source="test"`.
  F7. **Idempotency / no-double-merge** (runtime). Pre-stages a
      dispatcher whose `_execute` returns a `TaskResult(ERROR, ...)`
      that **already** carries a `retry_decision` with one
      `fallback_retry` entry (simulating T-tranche-10's in-`_execute`
      merge). Asserts the post-hoc wrapper leaves it untouched — the
      final `cooldown_decisions` still has exactly one `fallback_retry`
      entry. Locks the `retry_decision is None` guard.

  With F1-F7 in place, every failure surface of the dispatcher
  (`_execute` circuit-open, `_execute` parse-fail, `dispatch` shed,
  `dispatch`'s post-hoc catch of queue-returned failures) is covered
  by both a static AST drift guard AND a runtime explainability test
  that reads the fallback entry semantic values. Queue-level failure
  returns are inventory-pinned and architecturally decoupled.

  Scheduler shed-path semantics (T-tranche-13, 10 tests in
  `tests/unit/test_scheduler_shed_semantics.py`):
  F8. **"Shed is a non-event" invariant** — the Scheduler tracks CPU
      cooldown between *successful executions*, so a shed case
      (`OverloadError` in `queue.submit`) must not advance
      `_last_finish`, flip `_last_was_heavy`, or overwrite
      `last_wait_decision`. T-tranche-13 makes this explicit with a
      `Scheduler.note_shed(request)` method that is intentionally a
      documented no-op. 5 unit tests pin the no-op contract (one per
      mutable field + one for return value + one for arbitrary task
      types).
  F9. **State-machine correctness across success → shed → success** —
      3 tests prove the invariant at the sequence level: a heavy case
      → shed(s) → next heavy case must measure cooldown against the
      *original* successful finish, never the shed moment. Covers the
      "already elapsed" and "still firing" branches, plus 3 consecutive
      sheds.
  F10. **Dispatcher wiring** — 2 tests assert that
      `Dispatcher._build_shed_task_result()` calls
      `self.scheduler.note_shed(request)` exactly once (proven via a
      recording Scheduler subclass) AND that the dispatcher stays
      `AttributeError`-safe when given a legacy Scheduler without
      `note_shed` (back-compat preservation).

  Scheduler circuit-open aborted-execution semantics (T-tranche-14, 12
  tests in `tests/unit/test_scheduler_circuit_open_semantics.py`):
  F11. **"Circuit-open is an aborted execution, not a full non-event"
      invariant**. Unlike the shed path, the circuit-open path has
      `pre_execute` already running before `extract_slots` raises
      `CircuitOpenError`, so the scheduler has ALREADY slept a real
      cooldown and ALREADY populated `last_wait_decision` with a new
      WaitDecision. T-tranche-14 makes the invariant explicit with
      `Scheduler.note_circuit_open(request)` — a documented no-op
      that leaves `_last_finish` / `_last_was_heavy` unchanged AND
      explicitly **preserves** the pre_execute-populated
      `last_wait_decision` (critical difference from `note_shed`).
      5 block-A tests pin the non-mutation contract on the method.
  F12. **State-machine correctness across success → circuit-open →
      success sequences**. 5 block-B tests prove that:
      - heavy → circuit-open → heavy: case3 cooldown is anchored to
        case1's finish (not case2's circuit-open moment), and
        already_elapsed fires at the correct wall-clock boundary.
      - heavy → light-request circuit-open → *: `_last_was_heavy`
        survives because the flag belongs to the prior success.
      - light → heavy-request circuit-open → *: converse light-flag
        preservation.
      - 3 consecutive circuit-opens: baseline drift = 0.
      - **Shed vs circuit-open differ in `last_wait_decision`**: after
        `note_shed`, `last_wait_decision` is the prior real event;
        after `note_circuit_open`, it's the circuit-open case's own
        pre_execute decision. This test is the explicit documentation
        of the two tranches' subtle difference.
  F13. **Dispatcher wiring + legacy back-compat**. 2 block-C tests:
      a recording Scheduler subclass proves `_execute`'s CircuitOpen
      branch calls `scheduler.note_circuit_open(request)` exactly once
      with the original request; a legacy `_LegacyScheduler` stub
      without `note_circuit_open` proves the dispatcher's
      `try/except AttributeError` guard preserves back-compat.

  **With F8-F13 in place, the audit of "non-success exit paths never
  shift the scheduler cooldown baseline" is complete.** Both
  non-success paths (shed, circuit-open) have explicit source signals
  + invariant tests + sequence tests + dispatcher wiring tests, and
  the semantic distinction between them (shed = full non-event,
  circuit-open = aborted execution with legitimate pre_execute wait)
  is documented by an explicit comparison test (block B5).

  Real CircuitBreaker integration proof (T-tranche-15, 4 tests in
  `tests/integration/test_real_breaker_circuit_open.py`):
  F14. **Natural breaker trip via 3 failed dispatches**. Unlike F11-F13
      which use a synthetic `_CircuitOpenLLM` stub that raises
      `CircuitOpenError` on every `extract_slots` call, F14 uses a
      real `CircuitBreaker(fail_threshold=3)` + `AlwaysFailAdapter` +
      `max_retries=0`. Three failing dispatches cause the breaker to
      transition `closed → open` **by its own `record_failure` path**.
      The 4th dispatch then enters the dispatcher's `CircuitOpenError`
      branch — proven by `adapter.calls == 3` after case 4 (the
      breaker short-circuited inside `LLMClient.extract_slots` before
      the adapter was ever touched), plus `"Circuit breaker open"` in
      `result.errors` and `retry_decision_reason == "circuit_open"`.
  F15. **`note_circuit_open` fires exactly once on the real-trip case**.
      A `_RecordingScheduler` subclass records `note_circuit_open`,
      `note_shed`, and `post_execute` calls. After 4 dispatches:
      `circuit_open_calls == 1` (case 4 only), `post_execute_calls == 3`
      (cases 1-3 only, via parse-fail branch — circuit-open path skips
      post_execute), `shed_calls == 0`.
  F16. **Scheduler baseline anchored across real breaker transition**.
      Captures `_last_finish` after case 3's parse-fail `post_execute`,
      runs case 4 (real circuit-open), asserts `_last_finish == t3`
      and `_last_was_heavy is True`. Combined with F11-F13's synthetic
      invariants, the baseline-non-shift contract is now proven at
      BOTH the unit level (synthetic) AND the integration level
      (real breaker transition).
  F17. **Artifact E2E: circuit-open case scheduler wait + reason survive
      to `export_run_report.json`**. Drives `run_export` with 4 HR
      cases against `AlwaysFailAdapter` + `max_retries=0` so the
      default `CircuitBreaker` inside `run_export` trips naturally on
      case 3. Reads the on-disk JSON and asserts case 4 has
      `retry_decision_reason == "circuit_open"`, cases 1-3 are
      `transport_retry_exhausted`, and case 4's `cooldown_decisions`
      carries both a `scheduler_heavy` entry (from pre_execute) AND a
      `fallback_retry` entry (from the dispatcher's T-tranche-10
      wiring on the CircuitOpenError branch) with canonical 7-key
      shape and operator-supplied `source="test"`.

  **With F14-F17 in place, the non-success exit path audit includes
  the final integration proof layer.** Synthetic `_CircuitOpenLLM`
  tests (F11-F13) prove the dispatcher wiring and Scheduler invariants
  at the method level; real `CircuitBreaker` tests (F14-F17) prove
  the full production chain — breaker → LLMClient → Dispatcher →
  Scheduler → fallback → CaseTelemetry → RunTelemetry → disk — works
  when an actual `record_failure`-driven state transition fires the
  `CircuitOpenError` path.

  CircuitBreaker half_open recovery audit (T-tranche-16, 10 tests in
  `tests/integration/test_circuit_breaker_half_open_recovery.py`):
  F18. **State property lazy transition** (block A, 4 tests). The
      `CircuitBreaker.state` property flips `open → half_open`
      **lazily** only when `time.time() - _last_failure_time >
      _reset_timeout_s` on a property read. Tests pin: (A1) the flip
      fires on first eligible read, (A2) state stays `open` during
      the reset window, (A3) `half_open → closed` is NOT a time-based
      lazy transition — it requires an explicit `record_success()`,
      (A4) repeated reads after the flip are idempotent (no drift
      back to `open`, no failure count mutation).
  F19. **allow() / record_success() / record_failure() cycle** (block
      B, 3 tests). (B1) `allow()` returns True on `half_open` so the
      trial request proceeds. (B2) `record_success()` after
      `half_open` restores `closed` and resets failures to 0.
      (B3) `record_failure()` on `half_open` reverts to `open`,
      increments failures (NOT resets), and updates
      `_last_failure_time` so the reset cooldown restarts from the
      failure moment — otherwise the breaker would immediately
      re-flip to half_open on the next read.
  F20. **Dispatcher full recovery sequence** (block C, 2 tests). C1:
      3 dispatches against `_FlakyAdapter(fail_count=3)` naturally
      trip the breaker → `time.sleep(reset_timeout + ε)` →
      4th dispatch enters half_open trial → adapter returns valid
      JSON → `record_success()` → breaker closed, failures=0 →
      dispatcher DONE path → `scheduler._last_finish` advances
      (recovery IS a normal post_execute path, not a special
      exception). C2: 3 dispatches fail → sleep → 4th dispatch is
      the half_open trial but `_FlakyAdapter(fail_count=5)` still
      fails → parse-fail branch (not circuit-open — `allow()`
      returned True) → breaker reverts to `open`, failures=4 →
      5th dispatch hits the real circuit-open path with
      `retry_decision_reason == "circuit_open"`.
  F21. **Multi-cycle stability** (block D, 1 test). A `_WavyAdapter`
      cycles fail×3 → success → fail×3 → success. Two full
      `trip → recover → trip → recover` cycles must leave the
      breaker in exactly the same state after each recovery:
      `_state="closed"`, `_failures=0`, `allow()` returns True.
      Proves no hidden state accumulates across cycles.

  All 10 tests use `reset_timeout_s=0.05` and `time.sleep(0.07)` to
  keep total runtime under 1.3s while exercising real time-based
  state transitions. The audit layer now covers the full breaker
  state machine: `closed ↔ open ↔ half_open`.

  Recovery artifact E2E (T-tranche-17, 5 tests in
  `tests/integration/test_recovery_artifact_e2e.py`):
  F22. **Minimal `circuit_override` hook in `run_export`**. A single
      optional kwarg `circuit_override: CircuitBreaker = None` added
      to `scripts/export_human_review.py::run_export`. When omitted
      (the default for every existing caller), `run_export` still
      constructs its own fresh `CircuitBreaker()` exactly as before
      — zero behavior change. When provided, tests can inject a
      shared breaker to carry tripped state across phase-based
      `run_export` calls. Test E (`circuit_override_default_none_...`)
      pins the back-compat contract.
  F23. **Phase 1 artifact**: 3 failing cases + shared breaker →
      all 3 cases land as `transport_retry_exhausted`, none as
      `circuit_open`. Mid-trip breaker state is `open` with
      `_failures=3`. Locks that the breaker-trip threshold is
      reached *after* case 3 (not before), so case 3 itself is
      still a parse-fail exhaustion — the first true circuit-open
      would be case 4 if phase 1 continued.
  F24. **Phase 2 recovery case artifact shape**. After a short
      `time.sleep(0.07)`, the shared breaker's next state read
      lazy-flips to `half_open`. Case 4 is the half_open trial that
      succeeds via `_FlakyAdapter` (returns valid JSON on call 4).
      `LLMClient.record_success()` flips the breaker back to
      `closed` + `_failures=0`. The recovery case must appear in
      `export_run_report.json` as a **normal DONE success**:
      `final_status == "success"`, `retry_decision_reason ==
      "initial_success"`, `attempts_used == 1`,
      `transport_retry_count == 0`, `budget_exhausted is False`,
      `health_failure_reason is None`, and the `review_data.json`
      entry carries `auto_status == "done"` and the parsed slots
      from the adapter. Recovery success is **structurally
      identical** to any ordinary first success — no special
      marker, no residue from the prior trip.
  F25. **Steady-state case 5 uses recovery-case baseline**. With
      `scheduler_cooldown_heavy_s=0.1`, case 5 (the case immediately
      after the recovery) must carry a `scheduler_heavy`
      `cooldown_decisions[i]` entry. Its per-wait `configured_s`
      is the scheduler's `wait_needed = policy_value -
      elapsed_since_last` (slightly less than 0.1 because case 5
      arrived ~microseconds after case 4's post_execute); the
      run-level `configured_scheduler_cooldown_heavy_s` carries the
      raw policy value `0.1`. Both fields are asserted. Non-skipped,
      non-clamped, `applied_s > 0` → real sleep happened →
      scheduler baseline anchored to the recovery case's finish.
  F26. **Negative distinction**: recovery success must NOT carry any
      circuit-open artifact markers — no
      `retry_decision_reason == "circuit_open"`, no `fallback_retry`
      entry in `cooldown_decisions` (the T-tranche-10 fallback merge
      only fires on the dispatcher's failure exit sites, never on
      the DONE path). Both phase 2 cases are checked. If a future
      refactor ever starts marking recovery cases specially (e.g.,
      by copying the fallback merge into the DONE path), F26 fails.

  **The full non-success exit path audit + recovery artifact proof is
  now complete.** F1-F7 cover dispatcher failure exit inventory + fallback
  merge coverage. F8-F10 cover shed path state invariance. F11-F13 cover
  circuit-open aborted-execution semantics at the method level. F14-F17
  cover real-breaker `closed → open` transition + circuit-open artifact
  E2E. F18-F21 cover `open ↔ half_open ↔ closed` state-machine
  correctness at the object level. F22-F26 cover the **artifact-level
  distinction** between circuit-open aborted execution, half_open
  recovery success, and ordinary steady-state success.

  Recovery + strict gate E2E (T-tranche-18, 5 tests in
  `tests/integration/test_recovery_strict_gate_e2e.py`):
  F27. **Recovery + strict gate PASS**. Drives phase 1 (3 failures
      → breaker trip) + sleep + phase 2 (1 recovery case via
      `_FlakyPassAdapter` returning `{"intent": "recovered"}`).
      Reads BOTH `export_run_report.json` (dispatcher-level
      telemetry) AND `review_data.json` (strict-gate telemetry).
      Asserts: `case.final_status == "success"`,
      `retry_decision_reason == "initial_success"`,
      `attempts_used == 1`, AND
      `entry.auto_validated == True`,
      `entry.layered_judgment.final_judgment == "pass"`,
      `failure_categories == []`,
      `entry.layered_judgment.severity == "info"` (the lowest
      Severity enum value on pass). Recovery + pass payload is
      structurally identical to an ordinary first-attempt success
      from the strict-gate layer's perspective.
  F28. **Recovery + strict gate FAIL**. Same phase-based harness but
      with `_FlakyFailAdapter` returning Chinese-key payload
      `{"楼层": "2층", ...}` (HR-001 family). The layered review
      must REJECT the case while the dispatcher STILL returns DONE —
      proving that dispatcher-level success and layered-review verdict
      are **orthogonal layers**. Asserts:
      `case.final_status == "success"`,
      `retry_decision_reason == "initial_success"` (NOT circuit_open),
      AND
      `entry.auto_validated == False`,
      `entry.layered_judgment.final_judgment == "fail"`,
      `"wrong_key_locale" in entry.failure_categories`,
      `entry.layered_judgment.severity in {warn, high, critical}`.
  F29. **Ordinary success baseline byte-equivalence**. For BOTH the
      PASS payload and the FAIL payload, runs a parallel
      "ordinary success" baseline via `_CleanPassAdapter` /
      `_CleanFailAdapter` (no breaker trip, no phase split, fresh
      default CircuitBreaker) and asserts field-by-field equality of
      the strict-gate verdict between the recovery case and the
      baseline case: `auto_validated`, `final_judgment`,
      `failure_categories`, `parsed_slots`, `auto_status`,
      `severity`, and `layered_judgment.{auto_validated,
      final_judgment, severity}`. Recovery must not leak any hidden
      metadata into the gate layer that would differentiate it from
      an ordinary success.
  F30. **Ordering proof**: dispatcher-success semantics ⊥ layered
      verdict. A parametrized test runs both pass and fail payload
      variants and asserts the recovery case's
      `retry_decision_reason == "initial_success"` in both cases,
      while `auto_validated` varies with the payload only. This
      locks the invariant "dispatcher success means the LLMClient
      returned non-None parsed slots; it has no knowledge of the
      layered gate, which runs AFTER dispatcher DONE".
  F31. **Negative distinction across both layers**: for both pass and
      fail payload variants, recovery cases must have:
      - `case.retry_decision_reason != "circuit_open"`
      - 0 `fallback_retry` entries in `case.cooldown_decisions`
        (the dispatcher's failure-exit fallback merge only fires on
        ERROR/SHED paths, never on DONE)
      - `entry.auto_status == "done"` (not ERROR)
      - NO `failure_categories` containing "circuit" (the layered
        review's categories are semantic, not execution-level; if
        "circuit" ever appears, the two layers have bled into each
        other and the audit is broken)

  **With F27-F31 in place, recovery success artifact proof is complete
  at ALL three layers**: dispatcher-level (T-tranche-17), scheduler
  baseline (T-tranche-17 case 5), and strict-gate / layered judgment
  (T-tranche-18). Recovery is structurally indistinguishable from
  ordinary success across every layer, and dispatcher success is
  orthogonal to layered verdict.

  Fallback production closure (T-tranche-10, 2 tests):
  13. **Fallback wait reaches artifact `cooldown_decisions[]` on failure**
      — the production closure of the "every wait is artifact-explainable"
      invariant. Runtime changes (all additive, no semantic drift):
      - `DegradedModeHandler.record_wait_decision()` extracted from the
        first block of `handle_failure` (pure emit; does NOT touch cache
        / mock / reject).
      - New `_merge_fallback_wait_into_retry_decision(rd_dict, fallback)`
        helper in `dispatcher.py`, symmetric to the scheduler-wait merger.
      - `Dispatcher.__init__` now accepts `fallback=` (default `None` for
        back-compat); on the two failure exit sites (circuit-open and
        parse-fail) the dispatcher calls `self.fallback.record_wait_decision()`
        and merges via the helper. On the success path nothing changes.
      - `bootstrap.Container` passes `self.fallback` into `Dispatcher(...)`;
        `scripts/export_human_review.py::run_export` also constructs a
        `DegradedModeHandler(policy=unified)` and threads it through.
      The test drives an `_AlwaysFailAdapter` through `run_export` with
      `max_retries=0`, reads the on-disk JSON, and asserts the per-case
      `cooldown_decisions` contains **exactly one** `kind="fallback_retry"`
      entry with the canonical 7-key shape, `configured_s` equal to the
      operator-supplied value, `applied_s=0.0`, `skipped=True`,
      `skip_reason="zero_configured"`, `source="test"`, and that
      `total_cooldown_ms` stays at `0` (no wall-clock consumed).
  14. **Dispatcher fallback-merge helper additive + None-safe** —
      drift-prevention for `_merge_fallback_wait_into_retry_decision`.
      Verifies the helper is a pure no-op when `fallback is None`
      (returns the same object), a no-op when `fallback.last_wait_decision
      is None`, and otherwise appends a fresh `fallback_retry` entry to
      a shallow-copied output dict without mutating the input. Locks
      the non-mutation contract that the scheduler merger already has.

Both files are unmarked → part of the default gate. Together they catch
field deletion, serialization gaps, value-loss between memory and disk,
"key exists but semantically empty" drift, per-branch regressions in
the health classifier / scheduler wait merger, label-source loss
between settings and artifact, and now the fallback wait end-to-end
closure from production runtime to serialized artifact.

## Bootstrap Container coverage

`tests/unit/test_bootstrap_container.py` (13 tests) locks the full
dependency injection chain of `bootstrap.Container`:

- **Policy mapping** (2 tests): `_build_unified_policy(settings)` maps
  `settings.timeouts.strict_json_s` → `policy.request_timeout_s`,
  `settings.fallback.retry_delay_s` → `policy.transport_retry_cooldown_s`
  + `policy.fallback_retry_delay_s`, etc. with `cooldown_source="settings"`.
- **Attribute completeness** (1 test): every public attribute
  (settings, policy, health, circuit, queue, scheduler, timeouts, tools,
  fallback, llm_client, router, dispatcher) exists with the correct type.
- **Policy threading** (4 tests): scheduler, fallback, llm_client, and
  dispatcher all receive their config values from the unified policy,
  not from constructor defaults.
- **Mock adapter fallback** (1 test): when the LLM URL is unreachable,
  Container falls back to `MockLLMAdapter` (proven via
  `adapter.provider_name == "mock"`).
- **Settings propagation** (5 tests): queue concurrency/depth/timeout,
  health fail threshold, circuit breaker defaults, timeout policy values,
  and LLM client max_retries are all wired from the correct source.

**Known gap discovered**: the mock-adapter fallback path
(bootstrap.py line 132-137) does NOT propagate
`settings.fallback.max_retries` to `LLMClient` — it defaults to 1
instead of the settings value. The live-adapter path (line 124-128)
does propagate it. Test E5 pins this actual behavior so the gap is
tracked.

**Gate time impact**: Container tests add ~100s to the gate because
`_find_llm_url` probes an unreachable URL (5s health timeout) +
WSL subprocess (5s timeout) per Container construction. A module-level
shared Container instance is reused for inspection-only tests to
minimize the overhead.

## Drift prevention

`tests/unit/test_gate_boundary.py` is a small drift-prevention layer that fails the default gate loudly if:

- A file in `EXPECTED_INFRA_FILES` or `EXPECTED_LOAD_FILES` loses its file-level `pytestmark`.
- A new file under `tests/load/` lands without either marker (so it can't silently leak into the default gate).
- `EXPECTED_MIXED_INFRA_FILES` lose their function-level `@pytest.mark.infra` decorator.
- The `pytest.ini` marker registration or default `addopts` filter drifts.
- `docs/testing_gate.md` (this file) disappears or stops mentioning the two markers.

When you add a new `infra` / `load` file, **update the inventory sets at the top of `test_gate_boundary.py` in the same commit**. The drift check will otherwise fail immediately.
