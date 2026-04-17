#!/usr/bin/env bash
# preflight.sh — 배포 전 환경 검사.
#
# 사용자가 실 배포하기 직전에 실행. 도구 설치 + 크레덴셜 + 네트워크 + 설정을
# 종합 검증하고, 무엇이 부족한지 명확히 알려줌.
#
# Usage:
#   ./preflight.sh local          # docker compose 로컬 배포 준비 검사
#   ./preflight.sh pilot-vm       # DO Droplet 배포 준비 검사
#   ./preflight.sh prod-eks       # AWS EKS 배포 준비 검사
#   ./preflight.sh tls            # Let's Encrypt 준비 검사

set +e
MODE="${1:-local}"
PASS=0
FAIL=0
WARN=0

check() {
    local name="$1"
    local cmd="$2"
    if eval "$cmd" >/dev/null 2>&1; then
        echo "  [PASS] $name"
        PASS=$((PASS+1))
    else
        echo "  [FAIL] $name"
        FAIL=$((FAIL+1))
    fi
}

warn() {
    local name="$1"
    local cmd="$2"
    if eval "$cmd" >/dev/null 2>&1; then
        echo "  [ OK ] $name"
        PASS=$((PASS+1))
    else
        echo "  [WARN] $name (optional)"
        WARN=$((WARN+1))
    fi
}

has_env() {
    local var="$1"
    [ -n "${!var}" ]
}

echo "========================================================"
echo "Preflight checks — mode=$MODE"
echo "========================================================"
echo

echo "[1/4] Local tools"
check "docker CLI"         "which docker"
check "docker daemon"      "docker info"
check "docker compose v2"  "docker compose version 2>&1 | grep -qE 'version v?2'"
case "$MODE" in
    prod-eks) check "kubectl"    "which kubectl";;&
    prod-eks) check "helm"       "which helm";;&
    prod-eks) check "aws CLI"    "which aws";;&
    prod-eks) check "terraform"  "which terraform";;
    pilot-vm) check "terraform"  "which terraform";;&
    pilot-vm) check "openssl"    "which openssl";;
    tls)      check "openssl"    "which openssl";;&
esac
echo

echo "[2/4] Repo + config"
check "vllm_orchestrator/.env exists" "test -f vllm_orchestrator/.env"
check "Dockerfile valid"              "docker build --pull=false --dry-run -f vllm_orchestrator/Dockerfile vllm_orchestrator 2>&1 | grep -q 'FROM\|Successfully' || docker build -f vllm_orchestrator/Dockerfile vllm_orchestrator/ -t preflight-test 2>&1 | tail -1 | grep -qiE 'build|writing|tag'"
check "docker-compose.yml valid"      "docker compose -f docker-compose.yml config --quiet"
case "$MODE" in
    prod-eks) check "k8s manifests valid"  "for f in ops/k8s/*.yaml; do python3 -c \"import yaml; list(yaml.safe_load_all(open('\$f')))\" || exit 1; done";;
    pilot-vm) check "terraform single-vm"  "cd ops/terraform/single-vm && terraform validate 2>&1 | grep -q Success";;
esac
echo

echo "[3/4] Env vars"
case "$MODE" in
    local|tls)
        check "LLM_BASE_URL set"   "grep -q '^LLM_BASE_URL=' vllm_orchestrator/.env"
        warn "API_KEYS set"         "grep -qE '^API_KEYS=.+' vllm_orchestrator/.env"
        ;;
    pilot-vm)
        check "DIGITALOCEAN_TOKEN"  "has_env DIGITALOCEAN_TOKEN"
        check "DOMAIN_NAME"         "has_env DOMAIN_NAME"
        check "VLLM_ENDPOINT"       "has_env VLLM_ENDPOINT"
        check "API_KEYS"            "has_env API_KEYS"
        check "SSH_KEY file"        "test -f \"\$SSH_KEY\""
        ;;
    prod-eks)
        check "AWS_REGION"          "has_env AWS_REGION"
        check "AWS credentials"     "aws sts get-caller-identity"
        check "CLUSTER_NAME"        "has_env CLUSTER_NAME"
        check "VLLM_ENDPOINT"       "has_env VLLM_ENDPOINT"
        check "API_KEYS"            "has_env API_KEYS"
        check "DOMAIN_NAME"         "has_env DOMAIN_NAME"
        ;;
    tls)
        check "CERTBOT_DOMAIN"      "has_env CERTBOT_DOMAIN"
        check "CERTBOT_EMAIL"       "has_env CERTBOT_EMAIL"
        check "DNS A record resolves" "getent hosts \"\$CERTBOT_DOMAIN\""
        check "Port 80 reachable"   "timeout 3 bash -c '</dev/tcp/\$CERTBOT_DOMAIN/80'"
        ;;
esac
echo

echo "[4/4] Downstream reachability"
case "$MODE" in
    local|tls)
        if grep -q '^LLM_BASE_URL=' vllm_orchestrator/.env; then
            URL=$(grep '^LLM_BASE_URL=' vllm_orchestrator/.env | cut -d= -f2-)
            warn "vLLM reachable ($URL/health)" "curl -sSf -m 5 '\$URL/health'"
        fi
        ;;
    pilot-vm|prod-eks)
        [ -n "$VLLM_ENDPOINT" ] && warn "vLLM at $VLLM_ENDPOINT" "curl -sSf -m 5 '$VLLM_ENDPOINT/health'"
        ;;
esac
warn "GitHub actions recent status" "gh run list --limit 1 2>&1 | grep -qE 'completed|success'"
echo

echo "========================================================"
echo "Summary: $PASS passed, $FAIL failed, $WARN warnings"
echo "========================================================"

if [ $FAIL -gt 0 ]; then
    echo
    echo "[X] $FAIL checks failed. Fix before deploying."
    exit 1
fi
if [ $WARN -gt 0 ]; then
    echo
    echo "[!] $WARN warnings — proceed with caution."
    exit 0
fi
echo
echo "[OK] All checks passed. Proceed with:"
case "$MODE" in
    local)    echo "    ./deploy.sh local";;
    tls)      echo "    ./deploy.sh local-tls && ./deploy.sh certbot-init";;
    pilot-vm) echo "    ./deploy.sh pilot-vm";;
    prod-eks) echo "    ./deploy.sh prod-eks";;
esac
