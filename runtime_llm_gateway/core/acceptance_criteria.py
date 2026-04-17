"""
core/acceptance_criteria.py - 운영 합격 기준 (CPU / GPU 이중)

CPU 운영 기준: 품질 우선, latency는 허용 범위 넓힘
GPU 전환 기준: 품질 + latency 모두 엄격
"""

# ===================================================================
# CPU 운영 기준 (현재 기본)
# ===================================================================

CPU_CRITERIA = {
    "min_pass": 35,              # 35/40 (87.5%) — CPU 0.5B 현실 기준
    "min_pass_rate": 85.0,
    "max_p50_ms": 60000,         # p50 60초 — CPU에서 현실적
    "max_p95_ms": 180000,        # p95 3분
    "max_network_failure": 2,    # CPU 부하로 간헐적 timeout 허용
    "max_malformed_output": 2,
    "max_schema_failure": 2,
    "program_min_pass": {
        "builder": 8,
        "cad": 8,
        "minecraft": 9,
        "animation": 8,
    },
}

# ===================================================================
# GPU 전환 기준 (GPU 서버 확보 후)
# ===================================================================

GPU_CRITERIA = {
    "min_pass": 38,              # 38/40 (95%)
    "min_pass_rate": 95.0,
    "max_p50_ms": 5000,          # p50 5초
    "max_p95_ms": 15000,         # p95 15초
    "max_network_failure": 0,
    "max_malformed_output": 1,
    "max_schema_failure": 1,
    "program_min_pass": {
        "builder": 9,
        "cad": 9,
        "minecraft": 10,
        "animation": 9,
    },
}

# ===================================================================
# 판정 함수
# ===================================================================

def judge(summary: dict, mode: str = "cpu") -> dict:
    """
    eval summary → 판정 결과

    mode: "cpu" (현재 기본) | "gpu" (GPU 전환 후)

    Returns:
        verdict: CPU_READY / CPU_READY_WITH_WARNINGS / CPU_NOT_READY
                 PROMOTE / PROMOTE_WITH_WARNINGS / HOLD / ROLLBACK
    """
    criteria = CPU_CRITERIA if mode == "cpu" else GPU_CRITERIA

    checks = []
    warnings = []
    blockers = []

    total_pass = summary.get("total_pass", 0)
    total_cases = summary.get("total_cases", 40)
    pass_rate = summary.get("pass_rate", 0)
    p50 = summary.get("p50_ms", 999999)
    p95 = summary.get("p95_ms", 999999)
    by_program = summary.get("by_program", {})

    # 전체 pass
    if total_pass >= criteria["min_pass"]:
        checks.append(f"total_pass: {total_pass}/{total_cases} >= {criteria['min_pass']} OK")
    else:
        blockers.append(f"total_pass: {total_pass}/{total_cases} < {criteria['min_pass']}")

    if pass_rate >= criteria["min_pass_rate"]:
        checks.append(f"pass_rate: {pass_rate}% >= {criteria['min_pass_rate']}% OK")
    else:
        blockers.append(f"pass_rate: {pass_rate}% < {criteria['min_pass_rate']}%")

    # latency
    if p50 <= criteria["max_p50_ms"]:
        checks.append(f"p50: {p50}ms <= {criteria['max_p50_ms']}ms OK")
    else:
        warnings.append(f"p50: {p50}ms > {criteria['max_p50_ms']}ms")

    if p95 <= criteria["max_p95_ms"]:
        checks.append(f"p95: {p95}ms <= {criteria['max_p95_ms']}ms OK")
    else:
        warnings.append(f"p95: {p95}ms > {criteria['max_p95_ms']}ms")

    # 프로그램별
    for prog, min_p in criteria["program_min_pass"].items():
        prog_data = by_program.get(prog, {})
        prog_pass = prog_data.get("pass", 0)
        prog_total = prog_data.get("total", 10)
        if prog_pass >= min_p:
            checks.append(f"{prog}: {prog_pass}/{prog_total} >= {min_p} OK")
        else:
            blockers.append(f"{prog}: {prog_pass}/{prog_total} < {min_p}")

    # 실패 유형
    all_failures = {}
    for prog_data in by_program.values():
        for ftype, count in prog_data.get("failures", {}).items():
            all_failures[ftype] = all_failures.get(ftype, 0) + count

    for ftype, max_val in [
        ("network_failure", criteria["max_network_failure"]),
        ("malformed_output", criteria["max_malformed_output"]),
        ("schema_failure", criteria["max_schema_failure"]),
    ]:
        actual = all_failures.get(ftype, 0)
        if actual <= max_val:
            checks.append(f"{ftype}: {actual} <= {max_val} OK")
        else:
            (blockers if ftype == "network_failure" and mode == "gpu" else warnings).append(
                f"{ftype}: {actual} > {max_val}"
            )

    # 판정
    if mode == "cpu":
        if blockers:
            verdict = "CPU_NOT_READY"
        elif warnings:
            verdict = "CPU_READY_WITH_WARNINGS"
        else:
            verdict = "CPU_READY"
    else:
        if blockers:
            verdict = "ROLLBACK" if len(blockers) >= 3 else "HOLD"
        elif warnings:
            verdict = "PROMOTE_WITH_WARNINGS"
        else:
            verdict = "PROMOTE"

    return {
        "mode": mode,
        "verdict": verdict,
        "checks": checks,
        "warnings": warnings,
        "blockers": blockers,
    }
