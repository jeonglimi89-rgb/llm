# Comparison Report: CPU Qwen2.5-0.5B vs Current

## Summary

| Metric | Baseline | Current | Delta |
|--------|----------|---------|-------|
| Pass | 35/40 | 35/40 | +0 |
| Rate | 87.5% | 87.5% | +0.0% |
| p50 | 39292ms | 39292ms | +0ms (1.0x) |
| p95 | 143137ms | 143137ms | +0ms |

## Per-Program

| Program | Baseline | Current | Delta | p50 BL | p50 Cur |
|---------|----------|---------|-------|--------|---------|
| builder | 8/10 | 8/10 | +0 | 39986ms | 39986ms |
| cad | 8/10 | 8/10 | +0 | 62714ms | 62714ms |
| minecraft | 10/10 | 10/10 | +0 | 28626ms | 28626ms |
| animation | 9/10 | 9/10 | +0 | 43246ms | 43246ms |

## Verdict: **ROLLBACK**

### Passed
- minecraft: 10/10 >= 10 OK
- animation: 9/10 >= 9 OK
- malformed_output: 1 <= 1 OK

### Warnings
- p50: 39292ms > 5000ms (slow)
- p95: 143137ms > 15000ms (slow)
- schema_failure: 2 > 1

### Blockers
- total_pass: 35/40 < 38
- pass_rate: 87.5% < 95.0%
- builder: 8/10 < 9
- cad: 8/10 < 9
- network_failure: 2 > 0
