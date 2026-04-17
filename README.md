# vLLM Orchestrator Platform

자연어 → 구조화 JSON 파이프라인을 4개 도메인 (Minecraft / Builder(CAD) / Product Design / Animation)에 제공하는 LLM 오케스트레이션 플랫폼.

```
User Input → Domain Classifier → Task Router → LLM (vLLM 14B-AWQ) → Schema Validation → Response
                                                    ↓
                                        Cache / Variants / Critic / Repair
```

## Stack

- **Orchestrator** (`vllm_orchestrator/`) — FastAPI, 44 regression tests, 5 chaos scenarios
- **Redis** — distributed cache + rate limit (Upstash / ElastiCache / self-hosted)
- **vLLM** — 14B-Instruct-AWQ on RTX 5070 (external GPU service)
- **Nginx** — TLS termination + SSE + upstream HA
- **Prometheus + Grafana + Jaeger** — observability
- **Docker Compose / Kubernetes / Terraform** — 3-tier deployment options

## Quick Start

```bash
# 1. Clone + env
cp vllm_orchestrator/.env.example vllm_orchestrator/.env
$EDITOR vllm_orchestrator/.env    # set LLM_BASE_URL, API_KEYS

# 2. Launch full observability stack
docker compose --profile observability up -d

# 3. Verify
curl http://localhost:8100/health/ready
curl http://localhost:8100/metrics | head
open http://localhost:3000         # Grafana (admin / changeme)
```

## Deployment options

| Scale | Target | How |
|---|---|---|
| Dev / PoC | Local Docker | `docker compose up` |
| Pilot (1-10 users) | Single VM | `ops/terraform/single-vm` (DigitalOcean) |
| Production (HA) | Kubernetes | `ops/k8s/*.yaml` + `ops/terraform/aws-eks` |
| Self-hosted | Linux box + nginx | `ops/README.md` runbook |

## Documentation

- [`DEPLOYMENT_STATE.md`](DEPLOYMENT_STATE.md) — current verified state + remaining gaps
- [`ops/README.md`](ops/README.md) — day-2 operations runbook (key rotation, chaos, load, secrets, recovery)
- [`.github/workflows/ci.yml`](.github/workflows/ci.yml) — CI pipeline
- [`ops/grafana/dashboards/`](ops/grafana/dashboards/) — 14-panel monitoring dashboard

## Tests

```bash
# Regression (fast, no external deps)
cd vllm_orchestrator && python -m pytest tests/regression/ -v

# Chaos (requires running stack)
python tests/chaos/chaos_test.py --scenario all

# Load
python tests/load_test.py --scenario mixed --concurrent 10 --total 100
```

## Contributing

Push to main triggers CI (lint + regression + Docker build + Trivy scan).
Tag `v*.*.*` triggers GHCR image release.

## License

Private / internal.
