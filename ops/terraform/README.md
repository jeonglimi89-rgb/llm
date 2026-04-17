# Terraform templates

Two deployment targets provided:

## 1. AWS EKS + managed services
`aws-eks/` — EKS cluster + Managed Redis (ElastiCache) + ALB + Secrets Manager

```bash
cd aws-eks
terraform init
terraform plan -var-file=prod.tfvars
terraform apply
```

## 2. Single-VM (Linode/DigitalOcean/Hetzner)
`single-vm/` — One Ubuntu 22.04 VM + docker compose + Caddy for TLS

```bash
cd single-vm
terraform init
terraform plan
terraform apply
# cloud-init 자동 실행: docker install + compose up
```

## Variables to set before apply

- `vllm_endpoint` — URL of your GPU-backed vLLM
- `api_keys` — comma-separated initial keys (rotate via CLI later)
- `domain_name` — for Let's Encrypt
- `aws_region` / `droplet_region` etc.
