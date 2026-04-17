# Kubernetes manifests

Production-oriented deployment: orchestrator HA (2-3 replicas) + Redis + ingress-nginx TLS termination + Prometheus Operator ready.

## Quick deploy (kubectl)

```bash
kubectl create namespace vllm-orchestrator
kubectl -n vllm-orchestrator apply -f configmap.yaml
kubectl -n vllm-orchestrator apply -f secret.yaml          # customize first
kubectl -n vllm-orchestrator apply -f redis.yaml
kubectl -n vllm-orchestrator apply -f orchestrator.yaml
kubectl -n vllm-orchestrator apply -f ingress.yaml
kubectl -n vllm-orchestrator apply -f hpa.yaml
kubectl -n vllm-orchestrator apply -f pdb.yaml
```

## With Helm (preferred)

See `helm/` subdir (not yet provided — use raw manifests).

## Prereqs

- Kubernetes 1.27+
- cert-manager (for TLS via Let's Encrypt)
- ingress-nginx controller
- metrics-server (for HPA)
- Prometheus Operator (optional, for ServiceMonitor)

## Customize

Before applying:
1. `secret.yaml` — replace placeholders with real API keys, Redis password, etc.
2. `ingress.yaml` — set your domain name + cert-manager issuer
3. `orchestrator.yaml` — set image tag + `LLM_BASE_URL` to your vLLM endpoint
