# Changelog

All notable changes are documented here. Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Alertmanager config with Slack/PagerDuty/email routing (`ops/alertmanager/alertmanager.yml`)
- certbot auto-renewal docker profile for Let's Encrypt
- `deploy.sh` one-click deployment wrapper (local / local-tls / local-ha / pilot-vm / prod-eks / certbot-init / alerting / status / down)
- LICENSE (MIT), SECURITY.md, CONTRIBUTING.md, CODEOWNERS, Dependabot config
- GitHub issue/PR templates

## [0.1.0] — 2026-04-17

### Added — initial pilot-ready release

**Orchestrator core**
- FastAPI orchestrator with dispatcher + router
- Multi-domain task registry (minecraft / builder / animation / cad)
- 5-gate layered review (schema / language / semantic / domain / contract)
- Circuit breaker + queue + timeout policy

**Creative reasoning pipeline**
- IntentAnalyzer (rule-based, deterministic)
- Multi-variant sampling (3-way parallel, decider scoring)
- LLM self-critique with hallucination guard + self-validation
- Repair loop with regression detection
- Heuristic post-processor (scene_graph_repair)

**Caching**
- Exact-match LRU request cache (in-memory + Redis backends)
- Semantic cache (n-gram Jaccard, threshold 0.55)
- Redis-backed distributed shared cache

**Observability**
- Prometheus metrics endpoint (`/metrics`) with 16+ labels
- OpenTelemetry distributed tracing (OTLP HTTP)
- Structured JSON logs with trace_id correlation
- Grafana dashboard (14 panels)

**Security**
- API key lifecycle (generate / rotate / revoke / grace period)
- PII redaction (email / phone / RRN / CC / IP / IBAN)
- Secrets management (file AES-GCM / Vault / AWS Secrets Manager)
- Rate limiting (Redis distributed token bucket + nginx)
- CORS whitelist

**Feedback**
- POST /tasks/{id}/feedback endpoint
- JSONL store with rotation

**Streaming**
- POST /tasks/submit/stream (SSE with phase events)

**Deployment**
- Multi-stage Dockerfile (non-root, gunicorn 4 workers, healthcheck)
- docker-compose with profiles (base / proxy / observability / alerting / certbot)
- Kubernetes manifests (8 resources: Deployment / Svc / Ingress / HPA / PDB / NetPol / ConfigMap / Secret)
- Terraform (AWS EKS + DigitalOcean single-VM targets)
- nginx TLS + SSE + HA upstream LB

**CI/CD**
- `.github/workflows/ci.yml` — lint + pytest regression + Docker build + Trivy
- `release.yml` — tag-triggered GHCR semver push
- `nightly.yml` — daily regression

**Tests**
- 44 regression tests (deterministic, ~1s)
- 5 chaos scenarios (failover / circuit / rate limit / health)
- Load test harness (asyncio + aiohttp)

### Verified (runtime)
- Docker image build + container smoke (GHCR push from CI)
- docker compose 6-service full stack healthy
- nginx HTTPS handshake + /metrics via TLS
- HA topology failover (orch-1 kill → 5/5 requests via orch-2)
- Regression + chaos green in CI
- Trivy: 0 HIGH/CRITICAL

### Known limitations (v0.1.0)
- Single-GPU vLLM (no multi-GPU HA yet)
- Let's Encrypt cert requires real domain (self-signed fallback for dev)
- Feedback JSONL local only (no auto S3/BQ ship)
