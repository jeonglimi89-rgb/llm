#!/usr/bin/env bash
# deploy.sh — One-click deployment wrapper.
#
# Usage:
#   ./deploy.sh local            # docker compose up (dev/pilot)
#   ./deploy.sh local-tls        # + TLS (self-signed) + nginx profile
#   ./deploy.sh local-ha         # + HA topology (2 orch instances)
#   ./deploy.sh pilot-vm         # DigitalOcean Droplet (terraform)
#   ./deploy.sh prod-eks         # AWS EKS (terraform + kubectl)
#   ./deploy.sh certbot-init     # Run Let's Encrypt cert issuance
#   ./deploy.sh status           # Show deployed services
#   ./deploy.sh down             # Stop + clean
#
# Required env (see .env.example):
#   LLM_BASE_URL, API_KEYS, REDIS_URL
#
# For pilot/prod modes, additional env needed (prompted).

set -euo pipefail

MODE="${1:-help}"
cd "$(dirname "$0")"

need_env_file() {
    if [ ! -f vllm_orchestrator/.env ]; then
        echo ">> vllm_orchestrator/.env not found — copying from .env.example"
        cp vllm_orchestrator/.env.example vllm_orchestrator/.env
        echo ">> Edit vllm_orchestrator/.env before continuing (set LLM_BASE_URL, API_KEYS)"
        exit 1
    fi
}

case "$MODE" in
  local)
    need_env_file
    echo ">> Launching local stack (orchestrator + redis + observability)"
    docker compose --profile observability up -d
    echo
    echo "Orchestrator:  http://localhost:8100"
    echo "Grafana:       http://localhost:3000 (admin / ${GRAFANA_ADMIN_PASSWORD:-changeme})"
    echo "Prometheus:    http://localhost:9090"
    echo "Jaeger:        http://localhost:16686"
    ;;

  local-tls)
    need_env_file
    echo ">> Generating self-signed cert (dev only)"
    bash ops/nginx/generate-dev-cert.sh
    echo ">> Launching stack + nginx TLS + observability"
    docker compose --profile proxy --profile observability up -d
    echo
    echo "HTTPS API:     https://localhost:8443  (self-signed cert, -k flag needed)"
    echo "Grafana:       http://localhost:3000"
    ;;

  local-ha)
    need_env_file
    bash ops/nginx/generate-dev-cert.sh
    echo ">> Launching HA topology (2 orchestrator instances + nginx LB)"
    docker compose \
      -f docker-compose.yml \
      -f docker-compose.ha.yml \
      -f docker-compose.ha-override.yml \
      --profile proxy --profile observability \
      up -d redis orchestrator-1 orchestrator-2 nginx prometheus grafana jaeger
    echo
    echo "HA HTTPS API:  https://localhost:8443  (via nginx LB)"
    echo "Test failover: docker kill llm-orchestrator-1-1 && curl -sk https://localhost:8443/health/ready"
    ;;

  certbot-init)
    : "${CERTBOT_DOMAIN:?set CERTBOT_DOMAIN to your api domain}"
    : "${CERTBOT_EMAIL:?set CERTBOT_EMAIL to contact address}"
    echo ">> Requesting Let's Encrypt cert for $CERTBOT_DOMAIN"
    docker compose --profile certbot-init run --rm certbot-init
    echo ">> Restart nginx to pick up new cert"
    docker compose --profile proxy restart nginx
    ;;

  alerting)
    : "${SLACK_WEBHOOK_URL:?set SLACK_WEBHOOK_URL (or PAGERDUTY_ROUTING_KEY)}"
    need_env_file
    echo ">> Launching full stack with alerting (Alertmanager active)"
    docker compose --profile observability --profile alerting up -d
    echo "Alertmanager: http://localhost:9093"
    ;;

  pilot-vm)
    : "${DIGITALOCEAN_TOKEN:?set DIGITALOCEAN_TOKEN}"
    : "${DOMAIN_NAME:?set DOMAIN_NAME}"
    : "${VLLM_ENDPOINT:?set VLLM_ENDPOINT (e.g. http://your-gpu:8000)}"
    : "${API_KEYS:?set API_KEYS (comma-separated)}"
    : "${SSH_KEY:?set SSH_KEY to path of your ssh public key (.pub)}"
    cp "$SSH_KEY" ops/terraform/single-vm/ssh_key.pub
    cd ops/terraform/single-vm
    terraform init
    terraform apply \
      -var="do_token=$DIGITALOCEAN_TOKEN" \
      -var="domain_name=$DOMAIN_NAME" \
      -var="vllm_endpoint=$VLLM_ENDPOINT" \
      -var="api_keys=$API_KEYS" \
      -var="redis_password=${REDIS_PASSWORD:-$(openssl rand -hex 16)}" \
      -var="grafana_admin_password=${GRAFANA_ADMIN_PASSWORD:-$(openssl rand -hex 16)}"
    echo ">> Droplet created. DNS propagation takes 1-5 min."
    echo ">> API will be at: https://api.$DOMAIN_NAME"
    ;;

  prod-eks)
    : "${AWS_REGION:?set AWS_REGION (e.g. ap-northeast-2)}"
    : "${CLUSTER_NAME:?set CLUSTER_NAME}"
    : "${VLLM_ENDPOINT:?set VLLM_ENDPOINT}"
    : "${API_KEYS:?set API_KEYS}"
    : "${DOMAIN_NAME:?set DOMAIN_NAME}"
    cd ops/terraform/aws-eks
    terraform init
    terraform apply \
      -var="aws_region=$AWS_REGION" \
      -var="cluster_name=$CLUSTER_NAME" \
      -var="vllm_endpoint=$VLLM_ENDPOINT" \
      -var="api_keys=$API_KEYS" \
      -var="domain_name=$DOMAIN_NAME"
    echo ">> EKS cluster provisioned. Configure kubectl:"
    echo "   aws eks update-kubeconfig --region $AWS_REGION --name $CLUSTER_NAME"
    echo ">> Then apply manifests:"
    echo "   kubectl create ns vllm-orchestrator"
    echo "   kubectl -n vllm-orchestrator apply -f ../../k8s/"
    ;;

  status)
    echo ">> docker compose services:"
    docker compose ps 2>/dev/null || echo "  (no compose project running)"
    echo
    echo ">> Running containers with 'orchestrator' in name:"
    docker ps --filter "name=orch" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
    ;;

  down)
    echo ">> Stopping all profiles"
    docker compose \
      --profile proxy --profile observability --profile alerting \
      --profile certbot --profile certbot-init \
      down
    # HA stack
    docker compose \
      -f docker-compose.yml \
      -f docker-compose.ha.yml \
      -f docker-compose.ha-override.yml \
      --profile proxy --profile observability \
      down 2>/dev/null || true
    ;;

  help|*)
    cat <<EOF
Usage: $0 <mode>

Modes:
  local           Docker compose (dev/pilot)
  local-tls       + nginx TLS (self-signed)
  local-ha        + HA topology (2 orch instances)
  certbot-init    Issue Let's Encrypt cert (requires CERTBOT_DOMAIN + CERTBOT_EMAIL)
  alerting        Launch with Alertmanager active (SLACK_WEBHOOK_URL needed)
  pilot-vm        DigitalOcean Droplet via terraform
  prod-eks        AWS EKS via terraform
  status          Show running services
  down            Stop + clean

Env examples:
  LLM_BASE_URL=http://gpu-host:8000
  API_KEYS=dev_key,ci_key
  CORS_ALLOW_ORIGINS=https://app.example.com
  DIGITALOCEAN_TOKEN=dop_v1_...
  AWS_REGION=ap-northeast-2

Docs: ops/README.md
EOF
    ;;
esac
