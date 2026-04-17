"""load_test.py — asyncio + aiohttp 기반 부하 테스트.

기존 uvicorn single-worker 인스턴스의 실제 capacity를 측정.
원격 분산 부하 도구(locust/k6) 없이도 단일 머신에서 바로 돌림.

사용법:
  python vllm_orchestrator/tests/load_test.py \\
      --url http://127.0.0.1:8100 \\
      --concurrent 10 \\
      --total 50 \\
      --scenario scene_graph

시나리오:
  scene_graph  — 다양한 witch/waffle/frog 입력 (캐시 miss 강제)
  cache_hit    — 동일 입력 반복 (cache layer 측정)
  mixed        — 70% repeat + 30% novel (현실적)

출력:
  - requests/s (throughput)
  - P50 / P95 / P99 latency
  - error rate
  - cache hit rate (응답의 cache_hit 필드 확인)
  - timeout / 429 / 500 counts
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Optional

try:
    import aiohttp
except ImportError:
    print("pip install aiohttp 필요")
    raise


# ── Test scenarios ──────────────────────────────────────────────────────────

_NOVEL_INPUTS = [
    "witch castle with multiple towers and walls",
    "waffle palace with honey grids",
    "floating sky island magic school",
    "frog amusement park with lily pads",
    "gothic lighthouse on a stormy coast",
    "crystal cave dwelling",
    "steampunk clockwork library tower",
    "medieval fortress with 4 corner towers",
    "honeycomb bee palace",
    "sunken pirate fortress ruins",
    "cloud kingdom with rainbow bridges",
    "dragon lair with treasure vault",
    "pumpkin patch village",
    "mushroom forest temple",
    "arctic ice cathedral",
]

_REPEAT_INPUTS = [
    "witch castle with multiple towers and walls",
    "waffle palace with honey grids",
    "floating sky island magic school",
]


@dataclass
class Result:
    status: int = 0
    latency_s: float = 0.0
    cache_hit: bool = False
    error: Optional[str] = None
    response_size: int = 0


@dataclass
class Stats:
    results: list[Result] = field(default_factory=list)
    start_time: float = 0.0
    end_time: float = 0.0

    def record(self, r: Result) -> None:
        self.results.append(r)

    def summary(self) -> dict:
        if not self.results:
            return {"error": "no results"}
        successes = [r for r in self.results if r.status == 200 and not r.error]
        errors = [r for r in self.results if r.status != 200 or r.error]
        latencies = sorted(r.latency_s for r in successes)
        total_time = max(0.001, self.end_time - self.start_time)
        cache_hits = sum(1 for r in successes if r.cache_hit)
        status_dist: dict[int, int] = {}
        for r in self.results:
            status_dist[r.status] = status_dist.get(r.status, 0) + 1

        def pct(p: float) -> float:
            if not latencies:
                return 0.0
            idx = int(len(latencies) * p)
            idx = min(idx, len(latencies) - 1)
            return round(latencies[idx], 3)

        return {
            "total_requests": len(self.results),
            "successful": len(successes),
            "errors": len(errors),
            "error_rate": round(len(errors) / len(self.results), 4),
            "throughput_rps": round(len(self.results) / total_time, 2),
            "success_rps": round(len(successes) / total_time, 2),
            "wall_time_s": round(total_time, 2),
            "latency": {
                "p50_s": pct(0.50),
                "p95_s": pct(0.95),
                "p99_s": pct(0.99),
                "mean_s": round(statistics.mean(latencies), 3) if latencies else 0.0,
                "min_s": round(min(latencies), 3) if latencies else 0.0,
                "max_s": round(max(latencies), 3) if latencies else 0.0,
            },
            "cache_hits": cache_hits,
            "cache_hit_rate": round(cache_hits / len(successes), 4) if successes else 0.0,
            "status_distribution": status_dist,
            "error_samples": [r.error for r in errors[:5]],
        }


# ── Request generator ──────────────────────────────────────────────────────

def _build_input(scenario: str, idx: int, rng: random.Random) -> str:
    if scenario == "scene_graph":
        return _NOVEL_INPUTS[idx % len(_NOVEL_INPUTS)]
    if scenario == "cache_hit":
        return _REPEAT_INPUTS[idx % len(_REPEAT_INPUTS)]
    if scenario == "mixed":
        # 70% from repeat pool, 30% novel
        if rng.random() < 0.7:
            return _REPEAT_INPUTS[rng.randrange(len(_REPEAT_INPUTS))]
        return _NOVEL_INPUTS[rng.randrange(len(_NOVEL_INPUTS))]
    raise ValueError(f"unknown scenario: {scenario}")


async def _one_request(
    session: aiohttp.ClientSession,
    url: str,
    user_input: str,
    api_key: Optional[str],
    timeout_s: float,
) -> Result:
    body = {
        "domain": "minecraft",
        "task_name": "scene_graph",
        "user_input": user_input,
    }
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if api_key:
        headers["X-API-Key"] = api_key
    t0 = time.time()
    # self-signed cert allowance
    import ssl as _ssl
    _insecure = _ssl.create_default_context()
    _insecure.check_hostname = False
    _insecure.verify_mode = _ssl.CERT_NONE
    try:
        async with session.post(
            f"{url}/tasks/submit",
            json=body,
            headers=headers,
            ssl=_insecure,
            timeout=aiohttp.ClientTimeout(total=timeout_s),
        ) as resp:
            status = resp.status
            text = await resp.text()
            latency = time.time() - t0
            size = len(text)
            cache_hit = False
            if status == 200:
                try:
                    d = json.loads(text)
                    cache_hit = bool(d.get("cache_hit", False))
                except Exception:
                    pass
            return Result(status=status, latency_s=latency,
                          cache_hit=cache_hit, response_size=size,
                          error=None if status == 200 else f"HTTP {status}: {text[:80]}")
    except asyncio.TimeoutError:
        return Result(status=0, latency_s=time.time() - t0, error="timeout")
    except Exception as e:
        return Result(status=0, latency_s=time.time() - t0, error=f"{type(e).__name__}: {e}"[:120])


async def run_load(
    url: str,
    total: int,
    concurrent: int,
    scenario: str,
    api_key: Optional[str],
    timeout_s: float,
    seed: int = 42,
) -> Stats:
    rng = random.Random(seed)
    stats = Stats(start_time=time.time())

    sem = asyncio.Semaphore(concurrent)

    async with aiohttp.ClientSession() as session:
        async def runner(i: int):
            async with sem:
                inp = _build_input(scenario, i, rng)
                r = await _one_request(session, url, inp, api_key, timeout_s)
                stats.record(r)

        await asyncio.gather(*(runner(i) for i in range(total)))

    stats.end_time = time.time()
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description="Orchestrator load test")
    ap.add_argument("--url", default="http://127.0.0.1:8100")
    ap.add_argument("--concurrent", type=int, default=10, help="concurrent request workers")
    ap.add_argument("--total", type=int, default=50, help="total requests to send")
    ap.add_argument("--scenario", default="mixed", choices=["scene_graph", "cache_hit", "mixed"])
    ap.add_argument("--api-key", default=None, help="X-API-Key header (if API_KEY_REQUIRED=1)")
    ap.add_argument("--timeout", type=float, default=180.0)
    args = ap.parse_args()

    print(f"Load test → {args.url}  scenario={args.scenario}  "
          f"concurrent={args.concurrent}  total={args.total}")
    print("-" * 70)

    stats = asyncio.run(run_load(
        url=args.url,
        total=args.total,
        concurrent=args.concurrent,
        scenario=args.scenario,
        api_key=args.api_key,
        timeout_s=args.timeout,
    ))

    summary = stats.summary()
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
