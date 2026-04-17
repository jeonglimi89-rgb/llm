# TLS without owning a domain — sslip.io + Let's Encrypt

도메인 없이 진짜 Let's Encrypt 인증서로 TLS 붙이는 방법. pilot/ephemeral 용도에 적합.

## 원리

**sslip.io** / **nip.io** 는 무료 wildcard DNS 서비스:
- `1.2.3.4.sslip.io` → `1.2.3.4` (A record 자동 생성)
- `orchestrator-42-3-7-195.sslip.io` → `42.3.7.195`

Let's Encrypt는 sslip.io 도메인에 대해서도 인증서를 발급함 (정규 퍼블릭 DNS로 해석되기 때문).

## 요구사항

1. **공개 IPv4 주소가 붙은 서버** (80/443 포트 외부에서 접근 가능)
2. **포트 80 HTTP-01 challenge 통과 가능** (방화벽/NAT 설정)

가정용 NAT 환경은 안 됨. VPS / 클라우드 VM 권장 (DigitalOcean $4 Droplet으로 충분).

## 실행 순서

```bash
# 1. 서버의 공개 IP 확보
PUBLIC_IP=$(curl -s https://api.ipify.org)
echo "Server IP: $PUBLIC_IP"

# 2. sslip.io 도메인 결정
# 하이픈으로 IP 표기 (예: 1-2-3-4.sslip.io) — 점(.) 표기도 가능하지만 와일드카드 인증서 받을 때 안전
DOMAIN="api-${PUBLIC_IP//./-}.sslip.io"
echo "Domain: $DOMAIN"

# 3. DNS 해석 확인
dig +short $DOMAIN
# → $PUBLIC_IP 나와야 함

# 4. .env에 DOMAIN 설정
echo "CERTBOT_DOMAIN=$DOMAIN" >> vllm_orchestrator/.env
echo "CERTBOT_EMAIL=admin@example.com" >> vllm_orchestrator/.env  # 알림용
echo "CORS_ALLOW_ORIGINS=https://$DOMAIN" >> vllm_orchestrator/.env

# 5. nginx 기동 (certbot webroot path 열려있어야 함)
./deploy.sh local-tls   # self-signed로 일단 띄움

# 6. Let's Encrypt 발급 (첫 회만 staging 테스트 권장)
# Staging: rate limit 없음, test cert
docker compose run --rm certbot-init certonly \
    --webroot --webroot-path=/var/www/certbot \
    --email $CERTBOT_EMAIL \
    --agree-tos --no-eff-email \
    --staging \
    -d $DOMAIN \
    --non-interactive

# 7. staging 성공하면 production 전환
docker compose run --rm certbot-init certonly \
    --webroot --webroot-path=/var/www/certbot \
    --email $CERTBOT_EMAIL \
    --agree-tos --no-eff-email \
    -d $DOMAIN \
    --non-interactive

# 8. nginx reload (새 cert 적용)
docker compose exec nginx nginx -s reload

# 9. 검증
curl -I https://$DOMAIN/health/ready
# → HTTP/2 200 + valid Let's Encrypt cert
```

## 자동 갱신

docker-compose.yml의 `certbot-renew` 서비스가 12시간마다 `certbot renew` 실행.
90일 cert이 30일 남을 때 자동 갱신됨.

```bash
./deploy.sh local-tls && docker compose --profile certbot up -d certbot-renew
```

## 프로덕션 전환

pilot이 성공하면 진짜 도메인 구매 + cert-manager (k8s) 또는 certbot으로 전환.
sslip.io 주소는 운영용 SLA 약속 없음 — DNS가 갑자기 내려갈 수 있음.

## 한계

- Rate limit: Let's Encrypt production은 도메인당 50 cert/주 (sslip.io는 여러 사용자 공유하므로 차단 위험)
- 신뢰성: sslip.io 서비스가 unmaintained 되면 DNS 해석 멈춤
- 시각적 문제: URL에 IP가 보여서 신뢰도 떨어짐

→ **pilot만. 프로덕션 도메인 구매 필수.**
