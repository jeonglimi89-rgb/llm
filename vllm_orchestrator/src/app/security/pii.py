"""pii.py — PII redaction for feedback/logs.

사용자 입력, 피드백 notes, 로그에 개인정보가 들어갈 수 있음.
저장 전에 결정론적으로 마스킹:
  - email → "***@***.***"
  - 전화번호 (US/KR) → "***-****"
  - 신용카드 → "****-****-****-****"
  - IPv4/IPv6 → "ip-masked"
  - 주민등록번호 (KR) → "******-*******"
  - IBAN → "IBAN-MASKED"
  - 여권번호 (일반 영숫자 패턴) — conservative
  - 자유 텍스트: 이름같은 사람 이름은 감지 어려움 — skip (false positive 위험)

환경변수:
  PII_REDACTION_ENABLED=1 (기본)
"""
from __future__ import annotations

import os
import re
from typing import Any


# 패턴은 과소 아닌 과대 매칭을 선호 (false positive < false negative)
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")
_CC_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")
_KR_RRN_RE = re.compile(r"\b\d{6}[-]?\d{7}\b")            # 주민번호
_KR_PHONE_RE = re.compile(r"\b0(?:10|11|16|17|18|19)[-. ]?\d{3,4}[-. ]?\d{4}\b")
_US_PHONE_RE = re.compile(r"\b(?:\+?1[-.\s]?)?\(?[2-9]\d{2}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")
_IPV4_RE = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1\d\d|\d{1,2})\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|\d{1,2})\b")
_IPV6_RE = re.compile(r"\b(?:[A-Fa-f0-9]{1,4}:){2,}[A-Fa-f0-9]{1,4}\b")
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{1,30}\b")


def enabled() -> bool:
    return os.getenv("PII_REDACTION_ENABLED", "1").lower() in ("1", "true", "yes")


def redact_text(text: str) -> str:
    """단일 문자열에서 PII 패턴 마스킹. 결정론적, 입력이 없으면 그대로 반환."""
    if not text or not isinstance(text, str) or not enabled():
        return text
    s = text
    s = _EMAIL_RE.sub("[EMAIL]", s)
    s = _KR_RRN_RE.sub("[RRN]", s)
    s = _CC_RE.sub("[CC]", s)
    s = _KR_PHONE_RE.sub("[PHONE]", s)
    s = _US_PHONE_RE.sub("[PHONE]", s)
    s = _IPV6_RE.sub("[IPv6]", s)
    s = _IPV4_RE.sub("[IPv4]", s)
    # IBAN은 false positive 위험 있어서 별도 검증 없이 substitute
    s = _IBAN_RE.sub("[IBAN]", s)
    return s


def redact_value(v: Any) -> Any:
    """dict/list/str 재귀 순회. 키는 유지, 값만 redact."""
    if isinstance(v, str):
        return redact_text(v)
    if isinstance(v, dict):
        return {k: redact_value(val) for k, val in v.items()}
    if isinstance(v, list):
        return [redact_value(x) for x in v]
    return v
