"""secrets.py — Secrets management layer.

모든 민감한 값은 다음 우선순위로 로드:
  1. 실제 env var (process 실행 시 주입됨, container secrets/k8s secrets 가정)
  2. `${SECRET_BACKEND}` — file | vault | aws
     - file: SECRETS_FILE 경로의 암호화 파일 (AES-GCM with SECRETS_KEY env)
     - vault: VAULT_ADDR + VAULT_TOKEN 로 HTTP API 조회 (TODO)
     - aws: AWS Secrets Manager (TODO — boto3 + region)
  3. 코드 기본값 (dev only)

현재 세션에서는 file backend (간단한 암호화) 구현. Vault/AWS는 stub.

파일 포맷 (AES-GCM):
  struct {
    12-byte nonce | ciphertext | 16-byte GCM tag
  }
  ciphertext = JSON {"LLM_API_KEY": "...", "REDIS_PASSWORD": "..."}

생성: `python -m src.app.security.secrets encrypt --input secrets.json --output secrets.enc`
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

_log = logging.getLogger("vllm_orch.secrets")

_cache: dict[str, str] = {}
_loaded = False


def _derive_key(passphrase: str) -> bytes:
    """Passphrase → 32-byte AES key via SHA-256."""
    import hashlib
    return hashlib.sha256(passphrase.encode("utf-8")).digest()


def _encrypt(plaintext: bytes, passphrase: str) -> bytes:
    """AES-GCM encrypt. 반환: nonce(12) || ciphertext || tag(16)."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        raise RuntimeError("cryptography 패키지 필요: pip install cryptography")
    import os as _os
    key = _derive_key(passphrase)
    aesgcm = AESGCM(key)
    nonce = _os.urandom(12)
    ct_with_tag = aesgcm.encrypt(nonce, plaintext, None)  # tag 포함
    return nonce + ct_with_tag


def _decrypt(blob: bytes, passphrase: str) -> bytes:
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        raise RuntimeError("cryptography 패키지 필요")
    if len(blob) < 28:  # 12 nonce + ≥16 tag
        raise ValueError("ciphertext too short")
    key = _derive_key(passphrase)
    aesgcm = AESGCM(key)
    nonce, ct = blob[:12], blob[12:]
    return aesgcm.decrypt(nonce, ct, None)


def _load_from_file() -> dict[str, str]:
    path_str = os.getenv("SECRETS_FILE")
    if not path_str:
        return {}
    path = Path(path_str)
    if not path.exists():
        _log.warning(f"SECRETS_FILE={path_str} not found")
        return {}
    passphrase = os.getenv("SECRETS_KEY")
    if not passphrase:
        _log.warning("SECRETS_FILE set but SECRETS_KEY missing — skipping")
        return {}
    try:
        blob = path.read_bytes()
        plaintext = _decrypt(blob, passphrase)
        data = json.loads(plaintext.decode("utf-8"))
        if not isinstance(data, dict):
            _log.error("secrets file did not decrypt to a dict")
            return {}
        return {str(k): str(v) for k, v in data.items()}
    except Exception as e:
        _log.error(f"secrets file decrypt failed: {e}")
        return {}


def _load_from_vault() -> dict[str, str]:
    """HashiCorp Vault KV v2 — 선택적."""
    addr = os.getenv("VAULT_ADDR")
    token = os.getenv("VAULT_TOKEN")
    path = os.getenv("VAULT_SECRETS_PATH", "secret/data/vllm-orchestrator")
    if not addr or not token:
        return {}
    try:
        import httpx
        url = addr.rstrip("/") + "/v1/" + path.lstrip("/")
        r = httpx.get(url, headers={"X-Vault-Token": token}, timeout=5.0)
        if r.status_code != 200:
            _log.error(f"Vault lookup HTTP {r.status_code}")
            return {}
        doc = r.json() or {}
        # KV v2: data.data
        secrets = ((doc.get("data") or {}).get("data") or {})
        if not isinstance(secrets, dict):
            return {}
        return {str(k): str(v) for k, v in secrets.items()}
    except Exception as e:
        _log.error(f"Vault load failed: {e}")
        return {}


def _load_from_aws() -> dict[str, str]:
    """AWS Secrets Manager — 선택적."""
    secret_id = os.getenv("AWS_SECRET_ID")
    if not secret_id:
        return {}
    try:
        import boto3
        client = boto3.client("secretsmanager", region_name=os.getenv("AWS_REGION"))
        resp = client.get_secret_value(SecretId=secret_id)
        s = resp.get("SecretString")
        if not s:
            return {}
        data = json.loads(s)
        return {str(k): str(v) for k, v in data.items()}
    except Exception as e:
        _log.error(f"AWS secrets load failed: {e}")
        return {}


def _load_all() -> None:
    """모든 backend에서 secrets 수집. 우선순위: file → vault → aws → env.
    env이 가장 후순위로 보인 이유: file/vault/aws에 있으면 env에 값 있어도 그게 최신으로 간주.
    하지만 env가 ultimate override (container ENV가 배포 당시 값)로 동작하도록 os.getenv 최우선."""
    global _cache, _loaded
    if _loaded:
        return
    _loaded = True
    backend = os.getenv("SECRETS_BACKEND", "file").lower()
    merged: dict[str, str] = {}
    if backend in ("file", "all"):
        merged.update(_load_from_file())
    if backend in ("vault", "all"):
        merged.update(_load_from_vault())
    if backend in ("aws", "all"):
        merged.update(_load_from_aws())
    _cache = merged
    if merged:
        _log.info(f"Secrets loaded: {len(merged)} keys from backend={backend}")


def get(key: str, default: Optional[str] = None) -> Optional[str]:
    """Env var 우선, 없으면 secrets backend. 둘 다 없으면 default."""
    v = os.getenv(key)
    if v:
        return v
    _load_all()
    return _cache.get(key, default)


def require(key: str) -> str:
    """필수 secret. 없으면 RuntimeError."""
    v = get(key)
    if v is None or v == "":
        raise RuntimeError(f"Required secret '{key}' not configured (env var or secrets backend)")
    return v


# ── CLI: encrypt / decrypt helpers ──────────────────────────────────────────

def _cli_encrypt(input_path: str, output_path: str, passphrase: str) -> None:
    data = Path(input_path).read_bytes()
    # Validate JSON
    try:
        json.loads(data.decode("utf-8"))
    except Exception:
        print("input must be valid JSON", file=sys.stderr)
        sys.exit(1)
    blob = _encrypt(data, passphrase)
    Path(output_path).write_bytes(blob)
    print(f"wrote encrypted secrets → {output_path} ({len(blob)} bytes)")


def _cli_decrypt(input_path: str, passphrase: str) -> None:
    blob = Path(input_path).read_bytes()
    plaintext = _decrypt(blob, passphrase)
    sys.stdout.write(plaintext.decode("utf-8"))


def main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="secrets")
    sub = ap.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("encrypt")
    e.add_argument("--input", required=True, help="plaintext JSON file")
    e.add_argument("--output", required=True, help="encrypted output file")
    e.add_argument("--passphrase", help="passphrase (or env SECRETS_KEY)")

    d = sub.add_parser("decrypt")
    d.add_argument("--input", required=True)
    d.add_argument("--passphrase", help="passphrase (or env SECRETS_KEY)")

    g = sub.add_parser("get")
    g.add_argument("key")

    args = ap.parse_args(argv)
    if args.cmd == "encrypt":
        pw = args.passphrase or os.getenv("SECRETS_KEY")
        if not pw:
            print("passphrase required (--passphrase or SECRETS_KEY env)", file=sys.stderr)
            return 1
        _cli_encrypt(args.input, args.output, pw)
    elif args.cmd == "decrypt":
        pw = args.passphrase or os.getenv("SECRETS_KEY")
        if not pw:
            print("passphrase required", file=sys.stderr)
            return 1
        _cli_decrypt(args.input, pw)
    elif args.cmd == "get":
        v = get(args.key)
        print(v or "", end="")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
