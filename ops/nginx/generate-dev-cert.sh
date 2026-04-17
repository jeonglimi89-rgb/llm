#!/usr/bin/env bash
# Development-only self-signed cert for local TLS testing.
# Production: use Let's Encrypt via certbot or cloud provider.

set -euo pipefail
cd "$(dirname "$0")"
mkdir -p certs

openssl req -x509 -nodes -newkey rsa:2048 \
    -keyout certs/privkey.pem \
    -out certs/fullchain.pem \
    -days 365 \
    -subj "/C=KR/ST=Seoul/L=Seoul/O=Dev/CN=localhost" \
    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"

chmod 600 certs/privkey.pem
echo "Self-signed cert generated at ops/nginx/certs/"
echo "NOTE: Browsers will show warning. For production use Let's Encrypt."
