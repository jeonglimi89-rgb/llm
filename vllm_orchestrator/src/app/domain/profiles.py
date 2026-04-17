"""
domain/profiles.py — Domain Profile Registry

각 도메인(CAD, Builder, Minecraft, Animation, Product Design)의 전문 프로필을 정의.
프로필은 configs/domain_profiles.json 에서 로드되며, 분류/추출/평가/프롬프트
구성의 single source of truth.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class DomainProfile:
    """단일 도메인의 전문 프로필."""
    domain: str

    # 분류용 어휘 (term → weight)
    vocabulary: dict[str, float] = field(default_factory=dict)
    # task 분류 신호 (task_name → trigger keywords)
    task_signals: dict[str, list[str]] = field(default_factory=dict)

    # LLM reasoning template (도메인별 사고 프레임)
    reasoning_template: str = ""

    # 제약/선호 스키마
    constraint_fields: list[str] = field(default_factory=list)
    soft_preference_fields: list[str] = field(default_factory=list)

    # 출력 스키마
    output_schema_ref: str = ""
    required_output_keys: set[str] = field(default_factory=set)

    # 도메인별 추가 추출 슬롯
    domain_slots: dict[str, str] = field(default_factory=dict)

    # 검증
    validation_checklist: list[str] = field(default_factory=list)
    fail_modes: list[str] = field(default_factory=list)

    # Fallback / escalation
    fallback_task_name: str = ""
    escalation_threshold: float = 0.3

    # Chain / tool policy
    chain_name: str = ""               # default chain to use (e.g. "cad_full_design")
    allowed_tools: list[str] = field(default_factory=list)
    repair_policy: str = "retry_once"  # "retry_once" | "fail_loud" | "skip"

    # Extended schemas (reasoning / constraint / output)
    reasoning_schema: dict = field(default_factory=dict)
    constraint_schema: dict = field(default_factory=dict)
    output_schema: dict = field(default_factory=dict)

    # Model routing (32B canonical baseline)
    llm_default: str = "core_text_32b"          # default text model logical ID
    llm_code_default: str = "core_code_32b"     # code task model logical ID
    llm_review_default: str = "core_text_32b"   # review/validation model logical ID
    lora_adapter_id: str = ""                   # domain PEFT adapter name


def load_domain_profiles(configs_dir: Path) -> dict[str, DomainProfile]:
    """configs/domain_profiles.json 에서 4개 도메인 프로필을 로드."""
    path = configs_dir / "domain_profiles.json"
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    profiles: dict[str, DomainProfile] = {}
    for domain, data in raw.items():
        profiles[domain] = DomainProfile(
            domain=domain,
            vocabulary=data.get("vocabulary", {}),
            task_signals=data.get("task_signals", {}),
            reasoning_template=data.get("reasoning_template", ""),
            constraint_fields=data.get("constraint_fields", []),
            soft_preference_fields=data.get("soft_preference_fields", []),
            output_schema_ref=data.get("output_schema_ref", ""),
            required_output_keys=set(data.get("required_output_keys", [])),
            domain_slots=data.get("domain_slots", {}),
            validation_checklist=data.get("validation_checklist", []),
            fail_modes=data.get("fail_modes", []),
            fallback_task_name=data.get("fallback_task_name", ""),
            escalation_threshold=data.get("escalation_threshold", 0.3),
            chain_name=data.get("chain_name", ""),
            allowed_tools=data.get("allowed_tools", []),
            repair_policy=data.get("repair_policy", "retry_once"),
            reasoning_schema=data.get("reasoning_schema", {}),
            constraint_schema=data.get("constraint_schema", {}),
            output_schema=data.get("output_schema", {}),
            llm_default=data.get("llm_default", "core_text_32b"),
            llm_code_default=data.get("llm_code_default", "core_code_32b"),
            llm_review_default=data.get("llm_review_default", "core_text_32b"),
            lora_adapter_id=data.get("lora_adapter_id", ""),
        )
    return profiles


_PROFILES: dict[str, DomainProfile] = {}


def init_profiles(configs_dir: Path) -> dict[str, DomainProfile]:
    """모듈 캐시 초기화. Bootstrap 에서 호출."""
    global _PROFILES
    _PROFILES = load_domain_profiles(configs_dir)
    return _PROFILES


def get_domain_profile(domain: str) -> Optional[DomainProfile]:
    return _PROFILES.get(domain)


def get_all_profiles() -> dict[str, DomainProfile]:
    return dict(_PROFILES)
