# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.1.x   | ✅ Current |
| < 0.1   | ❌ Pre-release |

## Reporting a Vulnerability

**Do NOT open a public GitHub issue for security vulnerabilities.**

Instead, email: `security@example.com` (replace with real contact) with:

1. Description of the vulnerability
2. Steps to reproduce
3. Affected version(s)
4. Your GitHub username (for credit, optional)

### Response SLA

| Severity | First response | Patch target |
|---|---|---|
| Critical (RCE, auth bypass) | 24 hours | 7 days |
| High (info disclosure, DoS) | 72 hours | 30 days |
| Medium (limited impact) | 1 week | 90 days |
| Low | 1 month | Next regular release |

## Security Practices

This project includes:

- **Dependency scanning**: Trivy runs on every PR + nightly (`/.github/workflows/ci.yml`)
- **Secrets management**: env var > Vault > AWS Secrets Manager (`src/app/security/secrets.py`)
- **PII redaction**: Feedback/logs strip email, phone, RRN, credit card, IP (`src/app/security/pii.py`)
- **API key rotation**: Grace period support for zero-downtime key rotation
- **Rate limiting**: Redis-backed distributed token bucket
- **TLS**: nginx + Let's Encrypt (`ops/nginx/nginx.conf`)
- **Network policies**: Zero-trust k8s NetworkPolicy (`ops/k8s/networkpolicy.yaml`)
- **Non-root containers**: Dockerfile sets UID 1000

## Known Limitations

- Self-signed certs in dev mode (`ops/nginx/generate-dev-cert.sh`) — replace with real cert in production
- In-memory rate limit fallback if Redis unavailable — less strict, per-worker buckets
- API key `env API_KEYS` fallback is plaintext — recommend APIKeyStore for production

## Cryptographic Commitments

- Secrets file backend uses AES-GCM (`cryptography` lib)
- API key storage: SHA-256 hash only; plain keys never persisted
- Request signing: not yet implemented (future)

## Third-party dependencies

Direct production deps with known CVEs (as of v0.1.0):

| Package | Version | Known CVEs |
|---|---|---|
| fastapi | >=0.118 | None (CVE-2025-62727 fixed via starlette pin) |
| redis | >=5.0 | None |
| prometheus-client | >=0.20 | None |

Run `trivy image ghcr.io/jeonglimi89-rgb/vllm-orchestrator:latest` to see current state.
