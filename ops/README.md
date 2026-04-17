# vLLM Orchestrator — Operations Runbook

## Quick deploy (single host pilot)

```bash
# 1. Clone + prepare env
cp vllm_orchestrator/.env.example vllm_orchestrator/.env
$EDITOR vllm_orchestrator/.env   # Set LLM_BASE_URL, API_KEYS, REDIS_URL etc.

# 2. Generate dev TLS cert (or provide real cert in ops/nginx/certs/)
bash ops/nginx/generate-dev-cert.sh

# 3. Start full stack
docker compose --profile proxy --profile observability up -d

# 4. Verify
curl http://localhost/health/ready
curl http://localhost:9090                    # Prometheus
open http://localhost:3000                    # Grafana (admin / $GRAFANA_ADMIN_PASSWORD)
open http://localhost:16686                   # Jaeger
```

## Profiles

| Profile | Services started |
|---|---|
| (default) | redis + orchestrator |
| `proxy` | + nginx (TLS/LB) |
| `observability` | + prometheus + grafana + jaeger |

## Port mapping

| Port | Service | Exposure |
|---|---|---|
| 80  | nginx HTTP→HTTPS redirect | public |
| 443 | nginx HTTPS API           | public |
| 8100 | orchestrator direct       | 127.0.0.1 only (via nginx LB via internal) |
| 6379 | Redis                      | 127.0.0.1 only |
| 9090 | Prometheus                 | 127.0.0.1 only |
| 3000 | Grafana                    | 127.0.0.1 only |
| 16686 | Jaeger UI                 | 127.0.0.1 only |
| 4318 | OTLP HTTP ingest           | 127.0.0.1 only |

## SLO / Alerting

Defined in `ops/alerts.yml`:

| Alert | Threshold | Severity |
|---|---|---|
| `HighErrorRate` | >5% errors for 2min | critical |
| `HighP99Latency` | P99 >60s for 5min | warning |
| `LLMCircuitOpen` | circuit breaker open 1min | critical |
| `LowCacheHitRate` | <10% for 30min | info |
| `RateLimitBurst` | >10 rejections/s for 2min | warning |

### Hook alerts to PagerDuty/Slack

Add to `ops/prometheus.yml`:

```yaml
alerting:
  alertmanagers:
    - static_configs:
        - targets: ["alertmanager:9093"]
```

Deploy `alertmanager` separately with PagerDuty/Slack receivers.

## Day-2 Operations

### Check health
```bash
curl http://localhost/health/ready                  # public API alive
curl http://localhost/health/detail                 # circuit / queue / LLM status
curl http://localhost:8100/metrics | grep cache_hit_rate
```

### Rotate API keys
```bash
# Inside orchestrator container
docker exec -it <container> python -c "
from app.security.api_keys import get_store
store = get_store()
new_id, plain = store.generate(tier='premium', ttl_days=90)
print(f'key_id={new_id} plain={plain}  # GIVE THIS TO USER, cannot be retrieved again')
"

# Rotate: 기존 키를 grace에 두고 새 키 발급 (24시간 동안 둘 다 유효)
docker exec -it <container> python -c "
from app.security.api_keys import get_store
s = get_store()
new_id, new_plain = s.start_rotation('k-OLD123', grace_hours=24)
print(f'new_key_id={new_id} new_plain={new_plain}')
"

# 24시간 후 old key는 자동 만료. 강제 취소:
docker exec -it <container> python -c "
from app.security.api_keys import get_store
get_store().revoke('k-OLD123')
"
```

### Collect feedback for training
```bash
# 복사본을 S3/GCS로 올린 다음 orchestrator 내부 feedback.jsonl 회전 대기
docker cp <container>:/data/logs/feedback.jsonl ./feedback-$(date +%Y%m%d).jsonl

# 좋은 피드백만 필터 (rating>=4)
jq -c 'select(.rating >= 4)' feedback-*.jsonl > training-candidates.jsonl
```

### Capacity planning

Measured baseline (single orchestrator worker, single 14B vLLM):

| Path | Throughput | P50 | P99 |
|---|---|---|---|
| Pure cache hit | 470 rps | 98 ms | 129 ms |
| LLM scene_graph (variant=3) | 0.1 rps | 56 s | 61 s |
| Rate limit basic (2 rps, 20 burst) | 2 rps sustained | - | - |

Scaling math:
- N orchestrator workers × cache_throughput ≈ N × 470 rps for cached requests
- LLM throughput is vLLM-bound; add more vLLM instances behind nginx LB
- Redis rate limit is per-key; distributed across workers automatically

## Failure modes

### vLLM down
- Circuit breaker opens within `health_fail_threshold=3` consecutive failures
- Requests return fast error (1-2ms)
- `/health/ready` stays 200 (orchestrator itself is alive)
- Alert `LLMCircuitOpen` fires after 1min

Recovery: 
1. Restart vLLM
2. Circuit transitions open → half-open → closed automatically on success
3. No orchestrator restart needed

### Redis down
- Cache layer degrades: in-memory fallback OR "cache miss" for every request
- LLM still works
- Rate limit degrades: per-worker local buckets (not distributed)

Recovery: restart Redis. State is ephemeral (LRU cache), no data loss concern beyond cache warming.

### Orchestrator OOM
- Uvicorn workers restart independently (gunicorn master keeps running)
- Lost state: in-memory cache only (Redis backend persists)

### TLS cert expired
- nginx serves 5xx after cert expires
- Alert should fire on monitoring side (uptime check)

Recovery: renew cert (certbot for Let's Encrypt), `docker compose exec nginx nginx -s reload`

## Chaos testing

```bash
# Run all scenarios
python vllm_orchestrator/tests/chaos/chaos_test.py --scenario all

# Specific scenarios
python vllm_orchestrator/tests/chaos/chaos_test.py --scenario vllm_down
python vllm_orchestrator/tests/chaos/chaos_test.py --scenario burst_load
```

Run monthly; all scenarios should pass.

## Load testing

```bash
# Pure cache baseline
python vllm_orchestrator/tests/load_test.py --scenario cache_hit \
    --concurrent 50 --total 500 --timeout 5

# Realistic mixed (70% repeat, 30% novel)
python vllm_orchestrator/tests/load_test.py --scenario mixed \
    --concurrent 10 --total 100 --timeout 120
```

Run before every major release. Baseline numbers above; regression if drops below 70% of baseline.

## Secrets management

Three modes, pick one:

### 1. File-based (development)
```bash
# Encrypt secrets
echo '{"LLM_API_KEY":"xxx","REDIS_PASSWORD":"yyy"}' > /tmp/secrets.json
python -m src.app.security.secrets encrypt \
    --input /tmp/secrets.json \
    --output /data/secrets.enc \
    --passphrase "$SECRETS_KEY"
rm /tmp/secrets.json

# Run container
docker run -e SECRETS_BACKEND=file \
    -e SECRETS_FILE=/data/secrets.enc \
    -e SECRETS_KEY=$SECRETS_KEY \
    vllm-orchestrator
```

### 2. HashiCorp Vault
```bash
docker run -e SECRETS_BACKEND=vault \
    -e VAULT_ADDR=https://vault.example.com \
    -e VAULT_TOKEN=$VAULT_TOKEN \
    -e VAULT_SECRETS_PATH=secret/data/vllm-orchestrator \
    vllm-orchestrator
```

### 3. AWS Secrets Manager
```bash
docker run -e SECRETS_BACKEND=aws \
    -e AWS_SECRET_ID=vllm-orchestrator/prod \
    -e AWS_REGION=ap-northeast-2 \
    vllm-orchestrator
```

## Observability

### Logs
- stdout/stderr → Docker logs (use `docker logs -f orchestrator`)
- Rotating files at `/data/logs/*.log` (50MB × 5 archives default)
- JSON structured events with `trace_id` field for Jaeger correlation

### Metrics
- Prometheus endpoint: `http://orchestrator:8100/metrics`
- 16+ metric labels (see `observability/metrics.py`)
- Grafana dashboard: `ops/grafana/dashboards/orchestrator-overview.json`

### Traces
- OpenTelemetry → OTLP HTTP → Jaeger
- Enable: `OTEL_ENABLED=1 OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4318`
- Sampling: `OTEL_TRACES_SAMPLER_ARG=1.0` (100%, reduce in prod)

## CI/CD

GitHub Actions at `.github/workflows/`:
- `ci.yml` — lint + regression + Docker build + Trivy security scan (every PR/push)
- `release.yml` — tagged release → GHCR push

To deploy new version:
```bash
git tag v1.2.3
git push origin v1.2.3
# → automatic GHCR publish
# → pull on prod: docker compose pull && docker compose up -d
```

## Known limitations (still NOT production-ready for enterprise)

1. **Single GPU** — no multi-GPU scaling yet. Add more vLLM instances behind nginx for HA.
2. **No blue/green deploy** — Docker compose does not support zero-downtime rolling update natively. Use K8s for true rolling deploy.
3. **Feedback JSONL local only** — no shipping to S3/BQ. Add a cron job.
4. **No TLS on internal services** — Redis, Prometheus, Grafana are localhost-only but unencrypted in transit. Use docker network isolation.
5. **Secrets backends** — file backend is dev-only. Use Vault or cloud provider for prod.
6. **Model files** — not shipped with image. Mount `/models` volume separately (see Dockerfile).

## Support matrix

| Component | Tested | Status |
|---|---|---|
| Orchestrator | ✓ | 44 regression tests passing |
| Redis backend | ✓ | graceful fallback verified |
| nginx config | syntax-validated | runtime test requires Docker |
| Grafana dashboards | JSON valid | live render requires running stack |
| Prometheus alerts | syntax-validated | firing test requires running stack |
| CI pipelines | | requires GitHub deployment |
| Jaeger tracing | OTel SDK initialized | traces require running Jaeger |
| Docker image | Dockerfile static-checked | requires `docker build` environment |
| Load testing | ✓ | measured baselines captured |
| Chaos testing | ✓ (3/5 scenarios) | vllm_down / slow_vllm need vLLM control |
