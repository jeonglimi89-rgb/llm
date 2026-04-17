"""
test_soak_concurrency.py - CPU 운영 검증: soak + concurrency

기본은 짧게 (smoke). 환경변수로 long-run 가능:
  SOAK_DURATION_S=3600  (1시간)
  SOAK_REQUEST_COUNT=120
  SOAK_INTERVAL_S=30
  CONCURRENCY_LEVELS=1,2,4,8,16

load-marked (T-tranche-6, 2026-04-08): deterministic engine-level soak
test (no live LLM), excluded from the default gate for speed (~30s per
test even in smoke mode). Run explicitly with ``pytest -m load``.
"""
import sys, os, time, threading, statistics, json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

pytestmark = pytest.mark.load

from src.app.tools.registry import create_default_registry
from src.app.execution.queue_manager import QueueManager
from src.app.execution.scheduler import Scheduler
from src.app.execution.circuit_breaker import CircuitBreaker
from src.app.observability.health_registry import HealthRegistry


def _make_components():
    return {
        "queue": QueueManager(max_concurrency=1, max_depth=100, task_timeout_s=180),
        "scheduler": Scheduler(cooldown_heavy_s=0.1, cooldown_light_s=0.05),
        "breaker": CircuitBreaker(fail_threshold=5, reset_timeout_s=10),
        "health": HealthRegistry(),
        "registry": create_default_registry(),
    }


# ===================================================================
# SOAK TEST (engine-level, no LLM, focuses on queue/breaker stability)
# ===================================================================

def test_soak_engine_loop():
    duration_s = int(os.getenv("SOAK_DURATION_S", "30"))   # default 30초 (smoke)
    request_count = int(os.getenv("SOAK_REQUEST_COUNT", "60"))
    interval_s = float(os.getenv("SOAK_INTERVAL_S", "0.5"))

    print(f"\n=== SOAK TEST: duration={duration_s}s requests={request_count} interval={interval_s}s ===")
    comp = _make_components()
    reg = comp["registry"]
    queue = comp["queue"]
    breaker = comp["breaker"]
    health = comp["health"]

    metrics = {
        "submitted": 0,
        "full_success": 0,
        "errors": 0,
        "timeouts": 0,
        "breaker_open_events": 0,
        "queue_high_water": 0,
        "stuck_workers": 0,
        "latencies_ms": [],
    }

    test_cases = [
        ("minecraft.compile_archetype", {"target_anchor": {"anchor_type": "facade"}, "operations": [{"type": "add", "delta": {"material": "stone", "count": 5}}], "preserve": []}),
        ("builder.generate_plan", {"floors": 2, "spaces": [{"type": "living_room", "count": 1}, {"type": "bedroom", "count": 2}]}),
        ("cad.generate_part", {"systems": ["mechanical", "electrical"], "constraints": [], "design_type": "product"}),
        ("animation.solve_shot", {"framing": "medium", "mood": "warm", "speed": "moderate"}),
    ]

    start = time.time()
    breaker_was_open = False

    for i in range(request_count):
        if time.time() - start > duration_s:
            break

        tool_name, params = test_cases[i % len(test_cases)]
        req_start = time.time()

        if not breaker.allow():
            metrics["breaker_open_events"] += 1
            time.sleep(interval_s)
            continue

        try:
            result = reg.call(tool_name, params)
            elapsed = int((time.time() - req_start) * 1000)
            metrics["submitted"] += 1
            metrics["latencies_ms"].append(elapsed)
            if "error" not in result and result.get("status") == "executed":
                metrics["full_success"] += 1
                breaker.record_success()
            else:
                metrics["errors"] += 1
                breaker.record_failure()
        except Exception as e:
            metrics["errors"] += 1
            breaker.record_failure()
            if "timeout" in str(e).lower():
                metrics["timeouts"] += 1

        if breaker.state == "open" and not breaker_was_open:
            metrics["breaker_open_events"] += 1
            breaker_was_open = True
        elif breaker.state == "closed":
            breaker_was_open = False

        # Queue 상태 추적
        snap = queue.snapshot()
        if snap["depth"] > metrics["queue_high_water"]:
            metrics["queue_high_water"] = snap["depth"]

        time.sleep(interval_s)

    total_time = time.time() - start
    metrics["duration_s"] = round(total_time, 1)
    metrics["p50_ms"] = int(statistics.median(metrics["latencies_ms"])) if metrics["latencies_ms"] else 0
    if metrics["latencies_ms"]:
        s = sorted(metrics["latencies_ms"])
        metrics["p95_ms"] = s[min(int(len(s) * 0.95), len(s) - 1)]
    else:
        metrics["p95_ms"] = 0

    print(f"  duration:        {metrics['duration_s']}s")
    print(f"  submitted:       {metrics['submitted']}")
    print(f"  full_success:    {metrics['full_success']}")
    print(f"  errors:          {metrics['errors']}")
    print(f"  timeouts:        {metrics['timeouts']}")
    print(f"  breaker_open:    {metrics['breaker_open_events']}")
    print(f"  queue_high_water:{metrics['queue_high_water']}")
    print(f"  p50:             {metrics['p50_ms']}ms")
    print(f"  p95:             {metrics['p95_ms']}ms")

    # Assertions
    assert metrics["submitted"] >= 10, f"Too few submissions: {metrics['submitted']}"
    assert metrics["full_success"] / max(metrics["submitted"], 1) >= 0.95, "Success rate < 95%"
    assert queue.snapshot()["running"] == 0, "Stuck workers detected"
    print("  PASS")
    return metrics


# ===================================================================
# CONCURRENCY TEST
# ===================================================================

def test_concurrency_levels():
    levels_str = os.getenv("CONCURRENCY_LEVELS", "1,2,4,8,16")
    levels = [int(x) for x in levels_str.split(",")]
    requests_per_level = 20

    print(f"\n=== CONCURRENCY TEST: levels={levels} requests/level={requests_per_level} ===")

    all_results = {}

    for concurrency in levels:
        comp = _make_components()
        reg = comp["registry"]
        breaker = comp["breaker"]

        latencies = []
        errors = 0
        successes = 0
        starvation = 0
        lock = threading.Lock()

        def _worker(i: int):
            nonlocal errors, successes, starvation
            req_start = time.time()
            try:
                result = reg.call("animation.solve_shot", {
                    "framing": "medium",
                    "mood": "neutral",
                    "speed": "fast",
                })
                elapsed = int((time.time() - req_start) * 1000)
                with lock:
                    latencies.append(elapsed)
                    if "error" not in result and result.get("status") == "executed":
                        successes += 1
                    else:
                        errors += 1
            except Exception:
                with lock:
                    errors += 1

        start = time.time()
        with ThreadPoolExecutor(max_workers=concurrency) as ex:
            futures = [ex.submit(_worker, i) for i in range(requests_per_level)]
            for f in as_completed(futures, timeout=60):
                pass
        wall = time.time() - start

        # Starvation: 가장 느린 요청이 평균보다 5배 이상 느리면
        if latencies:
            max_lat = max(latencies)
            avg_lat = statistics.mean(latencies)
            if max_lat > avg_lat * 5:
                starvation = 1

        throughput = successes / wall if wall > 0 else 0
        result = {
            "concurrency": concurrency,
            "throughput_rps": round(throughput, 2),
            "successes": successes,
            "errors": errors,
            "starvation": starvation,
            "p50_ms": int(statistics.median(latencies)) if latencies else 0,
            "p95_ms": sorted(latencies)[min(int(len(latencies)*0.95), len(latencies)-1)] if latencies else 0,
            "wall_s": round(wall, 2),
            "breaker_state": breaker.state,
        }
        all_results[concurrency] = result
        print(f"  c={concurrency}: throughput={throughput:.1f}rps, success={successes}/{requests_per_level}, p50={result['p50_ms']}ms, p95={result['p95_ms']}ms, breaker={breaker.state}")

    # Assertions
    for c, r in all_results.items():
        assert r["successes"] >= requests_per_level * 0.9, f"c={c}: success rate too low ({r['successes']}/{requests_per_level})"
        assert r["breaker_state"] == "closed", f"c={c}: breaker not closed: {r['breaker_state']}"

    print("  PASS")
    return all_results


# ===================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("SOAK + CONCURRENCY VALIDATION")
    print("=" * 60)
    soak = test_soak_engine_loop()
    conc = test_concurrency_levels()

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Soak: {soak['full_success']}/{soak['submitted']} success, p50={soak['p50_ms']}ms")
    print(f"Concurrency:")
    for c, r in conc.items():
        print(f"  c={c}: {r['successes']} success, {r['throughput_rps']}rps, p95={r['p95_ms']}ms")
    print("\nALL VALIDATIONS PASSED")
