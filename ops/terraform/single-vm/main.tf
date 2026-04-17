terraform {
  required_version = ">= 1.5"
  required_providers {
    digitalocean = { source = "digitalocean/digitalocean", version = "~> 2.40" }
  }
}

variable "do_token" {
  type      = string
  sensitive = true
}
variable "domain_name" { type = string }
variable "vllm_endpoint" { type = string }
variable "api_keys" {
  type      = string
  sensitive = true
}
variable "redis_password" {
  type      = string
  sensitive = true
}
variable "grafana_admin_password" {
  type      = string
  sensitive = true
}
variable "droplet_region" { default = "sfo3" }
variable "droplet_size" { default = "s-2vcpu-4gb" }
variable "ssh_public_key_path" {
  type        = string
  description = "Path to SSH public key file (.pub). Use absolute or module-relative path."
  default     = "./ssh_key.pub"
}

provider "digitalocean" {
  token = var.do_token
}

# SSH key
resource "digitalocean_ssh_key" "orchestrator" {
  name       = "orchestrator-deploy"
  public_key = file(var.ssh_public_key_path)
}

# Cloud-init: install docker + git + clone + docker compose up
locals {
  cloud_init = <<-EOT
    #cloud-config
    packages:
      - docker.io
      - docker-compose-v2
      - git
      - ufw
    runcmd:
      - ufw allow 22/tcp
      - ufw allow 80/tcp
      - ufw allow 443/tcp
      - ufw --force enable
      - systemctl enable docker
      - systemctl start docker
      - mkdir -p /opt/vllm-orchestrator
      - cd /opt/vllm-orchestrator && git clone https://github.com/YOUR_ORG/vllm-orchestrator .
      - cd /opt/vllm-orchestrator && cp vllm_orchestrator/.env.example vllm_orchestrator/.env
      - |
        cat > /opt/vllm-orchestrator/vllm_orchestrator/.env <<EOF
        LLM_BASE_URL=${var.vllm_endpoint}
        API_KEYS=${var.api_keys}
        API_KEY_REQUIRED=1
        CORS_ALLOW_ORIGINS=https://${var.domain_name}
        REQUEST_CACHE_BACKEND=redis
        REDIS_URL=redis://redis:6379/0
        RATE_LIMIT_BACKEND=redis
        RATE_LIMIT_RPS=10
        RATE_LIMIT_BURST=50
        UVICORN_WORKERS=4
        LLM_CRITIC_MODE=inline
        OTEL_ENABLED=1
        OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4318
        GRAFANA_ADMIN_PASSWORD=${var.grafana_admin_password}
        EOF
      # Generate TLS via Caddy (auto Let's Encrypt) — replace docker-compose nginx
      - cd /opt/vllm-orchestrator && docker compose --profile observability up -d
  EOT
}

resource "digitalocean_droplet" "orchestrator" {
  image    = "ubuntu-22-04-x64"
  name     = "vllm-orchestrator"
  region   = var.droplet_region
  size     = var.droplet_size
  ssh_keys = [digitalocean_ssh_key.orchestrator.id]
  user_data = local.cloud_init
  tags     = ["vllm-orchestrator", "production"]
}

# DNS A record
data "digitalocean_domain" "main" {
  name = var.domain_name
}

resource "digitalocean_record" "api" {
  domain = data.digitalocean_domain.main.name
  type   = "A"
  name   = "api"
  value  = digitalocean_droplet.orchestrator.ipv4_address
  ttl    = 300
}

# Firewall
resource "digitalocean_firewall" "orchestrator" {
  name = "orchestrator-fw"
  droplet_ids = [digitalocean_droplet.orchestrator.id]

  inbound_rule {
    protocol   = "tcp"
    port_range = "22"
    source_addresses = ["0.0.0.0/0"]    # tighten to admin IPs in prod
  }
  inbound_rule {
    protocol   = "tcp"
    port_range = "80"
    source_addresses = ["0.0.0.0/0"]
  }
  inbound_rule {
    protocol   = "tcp"
    port_range = "443"
    source_addresses = ["0.0.0.0/0"]
  }

  outbound_rule {
    protocol   = "tcp"
    port_range = "all"
    destination_addresses = ["0.0.0.0/0"]
  }
  outbound_rule {
    protocol   = "udp"
    port_range = "all"
    destination_addresses = ["0.0.0.0/0"]
  }
}

output "api_url" {
  value = "https://api.${var.domain_name}"
}

output "droplet_ip" {
  value = digitalocean_droplet.orchestrator.ipv4_address
}
