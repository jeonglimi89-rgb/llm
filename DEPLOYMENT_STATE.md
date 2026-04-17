# vLLM Orchestrator — Deployment State

최종 업데이트: 2026-04-17

이 문서는 "어디까지 검증되었고, 실 프로덕션 전에 무엇이 남았는지"를 정확히 기록합니다.

---

## ✅ 런타임에서 실제 확인된 것

### 코드 + 테스트 (pytest)
- **Regression**: 44/44 PASS (`pytest tests/regression/`) — 0.85s
- **Chaos**: 5/5 PASS (`slow_vllm` / `redis_down` / `burst_load` / `health_always_up` / E2E)
- **Load test (direct)**: cache hit 470 rps, LLM 0.1 rps
- **Load test (HA + TLS)**: 500 req / 50 concurrent → 324 success, P50 1.6s, P99 3.1s

### 정적 분석
- **Ruff lint**: 0 errors (auto-fix 완료)
- **Trivy filesystem scan**: 0 HIGH/CRITICAL
- **Trivy image scan**: 1 HIGH (starlette CVE-2025-62727) → 패치 완료 (>=0.49.1)

### Docker
- **Image build** `vllm-orchestrator:test`: Multi-stage 빌드 성공 (283MB 예상)
- **Container smoke**: gunicorn + 4 workers, /health/ready=200, /metrics=200, /openapi.json 28 paths
- **docker compose (base)**: orchestrator + redis 둘 다 healthy, Redis에 `vllm_orch:rl:*` rate-limit 키 실제 저장 확인

### 전체 스택 (5-service with observability)
- **grafana**: API healthy, database=ok, v10.4.2
- **jaeger**: UI http://localhost:16686 → 200
- **prometheus**: `vllm-orchestrator` target UP, `orchestrator_cache_size` query 성공
- **orchestrator**: Prometheus가 실제로 scraping 중
- **redis**: 6379 healthy

### Nginx TLS 종료 (실작동 검증)
- **Self-signed cert**: 발급 + CN=localhost + 1-day validity
- **HTTPS 8443 handshake**: TLS 1.2/1.3 OK, 인증서 브라우저 수준 경고 외 정상
- **`curl -k https://127.0.0.1:8443/health/ready` → 200 `{"status":"ready"}`**
- **HTTP 8080 → 301 redirect** 확인
- **`/metrics` via HTTPS**: `orchestrator_cache_size 0.0` 반환 (end-to-end TLS 프록시 체인 검증)

### HA 토폴로지 (orch-1 + orch-2 + nginx LB)
- **2 인스턴스 동시 healthy**
- **Failover**: `docker kill llm-orchestrator-1-1` 후 5/5 요청 여전히 HTTP 200 (nginx가 orch-2로 자동 라우팅)
- **Recovery**: `docker start` 후 양쪽 인스턴스 재사용
- **`X-Upstream` header**: `172.18.0.4:8100` 노출 — 실제 라우팅 추적 가능
- **Shared rate limit**: Redis 기반 — 여러 인스턴스 간 바구니 공유 (burst에서 nginx+app 모두 429 정확 반환)

### Kubernetes manifests
- **8 파일 전부 YAML valid**: configmap, secret, redis (StatefulSet+Service+PVC), orchestrator (Deployment+Service+ServiceMonitor), hpa (2-level metrics), pdb (orchestrator + redis), ingress (cert-manager + SSE), networkpolicy (zero-trust)

### Terraform
- **aws-eks/main.tf**: `terraform validate` Success (VPC + EKS + ElastiCache + Secrets Manager + IRSA)
- **single-vm/main.tf**: `terraform validate` Success (DigitalOcean Droplet + cloud-init + firewall + DNS A record)

### CI/CD 파이프라인
- **`.github/workflows/ci.yml`**: lint + regression + Docker build + Trivy + GHCR push
- **`.github/workflows/release.yml`**: semver tag → GHCR multi-tag
- **`.github/workflows/nightly.yml`**: 매일 03:00 UTC regression + smoke load

---

## ❌ 외부 리소스 의존 — 내 환경에서 검증 불가

아래 항목들은 **코드와 설정은 완성됐지만 실제 돌려보지는 못한** 것들입니다. 실 배포 시점에 최종 검증 필요:

| 항목 | 필요한 것 | 검증 방법 |
|---|---|---|
| GitHub Actions 실주행 | GitHub 레포 push 권한 | `git push origin main` 후 Actions 탭에서 녹색 확인 |
| Let's Encrypt 실인증서 | 실 도메인 + 80/443 외부 노출 | `certbot --nginx -d api.example.com` 또는 cert-manager + Route53 |
| AWS EKS 배포 | AWS 계정 + 자격증명 | `cd ops/terraform/aws-eks && terraform apply` |
| DO Droplet 배포 | DigitalOcean API 토큰 | `cd ops/terraform/single-vm && terraform apply` |
| 2+ vLLM HA | 2대 이상 GPU 서버 | `ops/nginx/nginx-ha.conf` upstream에 서버 추가 |
| HashiCorp Vault | Vault 서버 | `SECRETS_BACKEND=vault VAULT_ADDR=... VAULT_TOKEN=...` |
| AWS Secrets Manager | AWS 계정 | `SECRETS_BACKEND=aws AWS_SECRET_ID=...` |
| PagerDuty/Slack alerts | 알림 계정 | Alertmanager config 추가 (`ops/alerts.yml`에 연동) |

---

## 📊 실측된 baseline 숫자

| 지표 | 값 | 측정 조건 |
|---|---|---|
| 순수 cache hit throughput | **470 rps** | 단일 worker, 500 req, 50 concurrent |
| HTTPS+HA cache hit | 324 success / 500 req | nginx rate limit 50 r/s 걸림 |
| LLM scene_graph latency | 17-36s | variant_count=3, ctx=2048 |
| cache hit latency | 16 ms | exact match |
| semantic cache hit latency | 5 ms | threshold=0.55 |
| TLS handshake overhead | +30ms | self-signed |
| Container startup | 15-20s | gunicorn 4 workers |
| vLLM 14B 기동 | 60-90s | AWQ 9.4GB → KV cache 0.5 GB |

---

## 📦 전체 파일 구조

```
D:/LLM/
├── vllm_orchestrator/              # orchestrator app
│   ├── src/app/                    # ~60 modules
│   │   ├── api/                    # routes/ + auth_middleware
│   │   ├── core/                   # contracts
│   │   ├── domain/                 # intent_analyzer, scene_graph_repair, registry
│   │   ├── execution/              # request_cache, redis_cache_backend, semantic_cache
│   │   ├── llm/                    # client + adapters
│   │   ├── observability/          # metrics (Prometheus) + tracing (OTel) + logger
│   │   ├── orchestration/          # dispatcher, variant_sampler, ...
│   │   ├── review/                 # llm_critic, task_contracts, ...
│   │   ├── security/               # secrets, pii, api_keys  ← 이 tranche 신규
│   │   ├── storage/                # paths, feedback_store
│   │   └── main.py
│   ├── tests/
│   │   ├── regression/             # 44 tests — CI pipeline
│   │   ├── chaos/                  # 5 fault injection scenarios
│   │   ├── load_test.py            # asyncio-based
│   │   └── integration/ + unit/    # 기존 테스트
│   ├── Dockerfile                  # multi-stage, non-root
│   ├── .dockerignore
│   ├── requirements.txt            # pinned deps (starlette CVE 패치 포함)
│   ├── .env.example                # 35+ env vars
│   ├── configs/
│   │   ├── app_config.yaml         # 중앙 설정
│   │   └── presets/
│   ├── prompts/, schemas/
│   └── pytest.ini
│
├── docker-compose.yml              # base: redis + orch
├── docker-compose.ha.yml           # overlay: 2 orch instances
├── docker-compose.ha-override.yml  # HA nginx config 교체
│
├── ops/                            # deployment + ops artifacts
│   ├── README.md                   # full runbook
│   ├── prometheus.yml              # scrape config
│   ├── alerts.yml                  # 5 SLO rules
│   ├── nginx/
│   │   ├── nginx.conf              # standard TLS + SSE
│   │   ├── nginx-ha.conf           # HA variant with failover
│   │   ├── generate-dev-cert.sh
│   │   └── certs/                  # self-signed (gitignored)
│   ├── grafana/
│   │   ├── dashboards/orchestrator-overview.json    # 14 panels
│   │   └── provisioning/
│   ├── k8s/
│   │   ├── configmap.yaml
│   │   ├── secret.yaml
│   │   ├── redis.yaml              # StatefulSet + headless Svc
│   │   ├── orchestrator.yaml       # Deployment + Svc + ServiceMonitor
│   │   ├── hpa.yaml                # CPU 70% + memory 75%
│   │   ├── pdb.yaml                # minAvailable=1
│   │   ├── ingress.yaml            # cert-manager + SSE annotations
│   │   └── networkpolicy.yaml      # zero-trust
│   └── terraform/
│       ├── aws-eks/main.tf         # VPC + EKS + ElastiCache + Secrets + IRSA
│       └── single-vm/main.tf       # DO Droplet + cloud-init
│
└── .github/workflows/
    ├── ci.yml                      # every PR/push
    ├── release.yml                 # tag-triggered GHCR push
    └── nightly.yml                 # 03:00 UTC
```

---

## 🎯 배포 결정 매트릭스

**어디에 배포해야 하는가?**

| 시나리오 | 추천 | 이유 |
|---|---|---|
| PoC / 내부 사용 | `docker compose up` (local VM) | 30분 내 시작 |
| 소규모 pilot (1-10 user) | `terraform/single-vm` (DO Droplet) | 월 $20-40 |
| 중규모 (10-100 user) | `docker compose --profile proxy` (single VM + nginx HA) | |
| 대규모 (100+ user, HA) | `terraform/aws-eks` (k8s + multi-AZ) | 월 $200+ |
| Enterprise (SLA) | k8s + 외부 Redis Sentinel/Cluster + multi-region | 별도 설계 |

---

## 🚦 배포 전 체크리스트 (실제 사용자에 노출 전)

Pilot 배포 권장 순서:

### 1. 필수 변경사항
- [ ] `vllm_orchestrator/.env` 에 실제 값 입력 (특히 `API_KEYS`, `LLM_BASE_URL`)
- [ ] `API_KEY_REQUIRED=1` 설정
- [ ] `CORS_ALLOW_ORIGINS` 에 실제 도메인만 (wildcard 금지)
- [ ] Real TLS cert (Let's Encrypt / 회사 CA)
- [ ] Docker image를 GHCR 또는 private registry로 푸시

### 2. 모니터링 설정
- [ ] Prometheus + Alertmanager 프로덕션 인스턴스
- [ ] `ops/alerts.yml` 을 PagerDuty/Slack으로 연결
- [ ] Grafana 대시보드에 접속 권한 부여

### 3. 백업
- [ ] Redis AOF 파일 또는 RDB 스냅샷 → S3/GCS 일일 백업
- [ ] `feedback.jsonl` 회전 파일 → 동일 백업

### 4. 보안
- [ ] API keys는 store에 생성 (`python -m src.app.security.api_keys ...`)
- [ ] Secrets는 Vault/AWS Secrets Manager (file backend는 dev only)
- [ ] Network policy / firewall 적용
- [ ] TLS 인증서 자동 갱신 (cert-manager 또는 certbot.timer)

### 5. 운영 절차
- [ ] On-call rotation 결정
- [ ] `ops/README.md`의 day-2 runbook 팀에 공유
- [ ] Chaos test 월 1회 실행 권장
- [ ] Load baseline 재측정 (프로덕션 환경)

### 6. 최종 smoke test (배포 직후)
- [ ] `curl https://api.your-domain/health/ready` → 200
- [ ] `curl -X POST https://api.your-domain/tasks/submit -H 'X-API-Key: $KEY' ...` → 200
- [ ] Grafana에서 트래픽 대시보드 확인
- [ ] Jaeger에서 첫 trace 표시 확인

---

## 📉 알려진 한계

1. **Multi-GPU vLLM HA**: 현재 nginx upstream config에 주석 예시만. 실 배포시 GPU 서버 IP 추가 필요.
2. **WebSocket/SSE through nginx**: SSE는 검증됨. WebSocket 필요 시 `proxy_set_header Upgrade/Connection` 추가.
3. **API key migration**: env `API_KEYS` → `APIKeyStore` 마이그레이션 스크립트 미제공 (수동 `generate` 후 구키 revoke).
4. **Feedback → 학습**: JSONL 수집은 됨. 실제 LoRA/DPO 학습 파이프라인은 별도 프로젝트 (`training/adapter_trainer.py` stub 존재).
5. **Cost observability**: vLLM 호출량 기반 비용 metric은 없음 (토큰 카운터는 있음).
6. **Multi-tenancy**: tier-별 격리는 API key tier로만. 실제 resource 격리 (per-tier queue) 미구현.

---

## 🔑 한 줄 총평

**지금 상태**: Pilot 배포 가능한 코드/설정/테스트/문서가 전부 완성됨. 내 환경(Windows + WSL + 1 GPU)에서 가능한 모든 런타임 검증 완료. 8 종류 external 의존(GitHub push, AWS 계정, 실 도메인 등)은 실 배포 시 최종 확인 필요.

**"완성"이라 말하지 않는 이유**: 위 8개 중 최소 3개 (GitHub CI 첫 주행, 실 도메인 TLS, 실 클라우드 배포)가 녹색 확인되어야 "프로덕션 배포 완료"라고 부를 수 있음.

**다음 한 번의 명령**으로 완성 가능 (사용자 쪽에서):
```bash
# GitHub push → CI 실행
git push origin main

# 또는 DigitalOcean single-VM 배포
cd ops/terraform/single-vm && terraform apply \
  -var="do_token=$DO_TOKEN" \
  -var="domain_name=example.com" \
  -var="vllm_endpoint=http://your-gpu-server:8000" \
  -var="api_keys=initial_key_1,initial_key_2"
```
