"""
tests/run_full_eval.py - 40케이스 전체 eval + 벤치마크 + 실패 분류

실행: cd LLM && python -X utf8 -m runtime_llm_gateway.tests.run_full_eval

출력:
  - 프로그램별 pass/fail 집계
  - latency p50/p95
  - 실패 분류 (network/timeout/malformed/semantic)
  - 결과 JSON 저장
"""

from __future__ import annotations

import json
import os
import sys
import time
import statistics
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from runtime_llm_gateway.core.config_loader import build_gateway
from runtime_llm_gateway.core.envelope import RequestEnvelope, Message


DATASET_PATH = Path(__file__).parent / "eval_dataset.json"
RESULTS_DIR = Path(_ROOT) / "baselines" / "full_eval"


# ---------------------------------------------------------------------------
# 실패 분류
# ---------------------------------------------------------------------------

class FailureType:
    PASS = "pass"
    NETWORK = "network_failure"
    TIMEOUT = "timeout"
    MALFORMED = "malformed_output"
    SCHEMA_FAIL = "schema_failure"
    DOMAIN_FAIL = "domain_failure"
    SEMANTIC = "semantic_regression"


def classify_failure(resp, expect_keys: list[str]) -> str:
    """응답을 분류"""
    if resp.error_code:
        ec = resp.error_code.upper()
        if "CONNECTION" in ec or "PROVIDER" in ec:
            return FailureType.NETWORK
        if "TIMEOUT" in ec:
            return FailureType.TIMEOUT
        if "PARSE" in ec:
            return FailureType.MALFORMED
        return FailureType.MALFORMED

    if not resp.validation.schema_ok:
        return FailureType.SCHEMA_FAIL

    if not resp.validation.domain_ok:
        return FailureType.DOMAIN_FAIL

    # Semantic: expect_keys 확인
    if resp.structured_content and expect_keys:
        for key in expect_keys:
            if key not in resp.structured_content:
                return FailureType.SEMANTIC

    return FailureType.PASS


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------

def run_eval():
    dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    gw = build_gateway()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    all_results = []
    program_stats: dict[str, dict] = {}

    programs = ["builder", "cad", "minecraft", "animation"]

    for program in programs:
        cases = dataset.get(program, [])
        if not cases:
            continue

        latencies = []
        pass_count = 0
        fail_counts: dict[str, int] = {}
        program_results = []

        print(f"\n=== {program.upper()} ({len(cases)} cases) ===")

        for case in cases:
            cid = case["id"]
            text = case["text"]
            schema_id = case["schema_id"]
            expect_keys = case.get("expect_keys", [])
            task_type = f"{program}.{'requirement_parse' if program == 'builder' else 'constraint_parse' if program == 'cad' else 'edit_parse' if program == 'minecraft' else 'shot_parse'}"

            req = RequestEnvelope(
                task_type=task_type,
                project_id="eval",
                session_id=f"eval_{program}",
                messages=[Message(role="user", content=text)],
                schema_id=schema_id,
            )

            start = time.time()
            try:
                resp = gw.process(req)
                latency_ms = int((time.time() - start) * 1000)
            except Exception as e:
                latency_ms = int((time.time() - start) * 1000)
                print(f"  [{cid}] CRASH: {e}")
                program_results.append({"id": cid, "status": FailureType.NETWORK, "latency_ms": latency_ms})
                fail_counts[FailureType.NETWORK] = fail_counts.get(FailureType.NETWORK, 0) + 1
                continue

            status = classify_failure(resp, expect_keys)
            latencies.append(latency_ms)

            if status == FailureType.PASS:
                pass_count += 1
                marker = "OK"
            else:
                fail_counts[status] = fail_counts.get(status, 0) + 1
                marker = status.upper()

            print(f"  [{cid}] {marker:20s} {latency_ms:>6}ms  \"{text[:40]}\"")

            result_entry = {
                "id": cid,
                "text": text,
                "status": status,
                "latency_ms": latency_ms,
                "schema_ok": resp.validation.schema_ok,
                "domain_ok": resp.validation.domain_ok,
                "repair": resp.validation.repair_attempted,
                "error": resp.error_code,
            }
            if resp.structured_content:
                result_entry["output_keys"] = list(resp.structured_content.keys())
            program_results.append(result_entry)

        # 프로그램 집계
        p50 = int(statistics.median(latencies)) if latencies else 0
        p95 = int(sorted(latencies)[int(len(latencies) * 0.95)] if latencies else 0)

        stats = {
            "total": len(cases),
            "pass": pass_count,
            "fail": len(cases) - pass_count,
            "pass_rate": round(pass_count / len(cases) * 100, 1),
            "p50_ms": p50,
            "p95_ms": p95,
            "failures": fail_counts,
        }
        program_stats[program] = stats

        print(f"  --- {program}: {pass_count}/{len(cases)} pass ({stats['pass_rate']}%) | p50={p50}ms p95={p95}ms")
        if fail_counts:
            print(f"      failures: {fail_counts}")

        # 저장
        with open(RESULTS_DIR / f"{program}_results.json", "w", encoding="utf-8") as f:
            json.dump(program_results, f, ensure_ascii=False, indent=2)

        all_results.extend(program_results)

    # 전체 요약
    total = sum(s["total"] for s in program_stats.values())
    total_pass = sum(s["pass"] for s in program_stats.values())
    all_latencies = [r["latency_ms"] for r in all_results if r.get("latency_ms")]

    summary = {
        "date": time.strftime("%Y-%m-%d"),
        "server": "current (from server_config.json)",
        "total_cases": total,
        "total_pass": total_pass,
        "total_fail": total - total_pass,
        "pass_rate": round(total_pass / total * 100, 1) if total else 0,
        "p50_ms": int(statistics.median(all_latencies)) if all_latencies else 0,
        "p95_ms": int(sorted(all_latencies)[int(len(all_latencies) * 0.95)]) if all_latencies else 0,
        "by_program": program_stats,
    }

    with open(RESULTS_DIR / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    # 최종 출력
    print("\n" + "=" * 85)
    print("FULL EVAL SUMMARY")
    print("=" * 85)
    print(f"  Total: {total_pass}/{total} pass ({summary['pass_rate']}%)")
    print(f"  Latency: p50={summary['p50_ms']}ms, p95={summary['p95_ms']}ms")
    print()
    print(f"  {'Program':<15} {'Pass':>6} {'Fail':>6} {'Rate':>8} {'p50':>8} {'p95':>8}")
    print(f"  {'-'*55}")
    for prog, s in program_stats.items():
        print(f"  {prog:<15} {s['pass']:>4}/{s['total']:<2} {s['fail']:>6} {s['pass_rate']:>7}% {s['p50_ms']:>6}ms {s['p95_ms']:>6}ms")
    print()
    if any(s["failures"] for s in program_stats.values()):
        print("  Failure breakdown:")
        for prog, s in program_stats.items():
            if s["failures"]:
                print(f"    {prog}: {s['failures']}")
    print("=" * 85)
    print(f"  Results saved to: {RESULTS_DIR}")


if __name__ == "__main__":
    run_eval()
