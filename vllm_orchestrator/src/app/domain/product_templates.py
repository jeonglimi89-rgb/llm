"""
domain/product_templates.py — Product template DB.

제품 카테고리별 기본 부품/치수/재질 템플릿. LLM 이 "샤워필터" 를 구체적으로
설계할 수 없더라도, 템플릿이 도메인 지식을 주입해서 합리적인 출력 생성.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class ProductTemplate:
    product_id: str
    category: str
    display_name: str
    systems: list[str]
    default_parts: list[dict]
    default_constraints: list[dict]
    default_interfaces: dict
    keywords: list[str]

    def to_slots_enrichment(self) -> dict:
        """template 정보를 generate_part 의 입력 형태로 변환."""
        return {
            "systems": self.systems,
            "constraints": self.default_constraints,
            "design_type": "product",
            "_template": {
                "product_id": self.product_id,
                "default_parts": self.default_parts,
                "default_interfaces": self.default_interfaces,
            },
        }


def load_product_templates(configs_dir: Path) -> dict[str, ProductTemplate]:
    path = configs_dir / "product_templates.json"
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    templates: dict[str, ProductTemplate] = {}
    for pid, data in raw.items():
        templates[pid] = ProductTemplate(
            product_id=pid,
            category=data.get("category", ""),
            display_name=data.get("display_name", ""),
            systems=data.get("systems", []),
            default_parts=data.get("default_parts", []),
            default_constraints=data.get("default_constraints", []),
            default_interfaces=data.get("default_interfaces", {}),
            keywords=data.get("keywords", []),
        )
    return templates


def match_template(
    user_input: str,
    templates: dict[str, ProductTemplate],
) -> Optional[ProductTemplate]:
    """입력 텍스트에서 제품 템플릿을 키워드 매칭으로 찾기."""
    text_lower = user_input.lower()
    best: Optional[ProductTemplate] = None
    best_score = 0
    for template in templates.values():
        score = sum(1 for kw in template.keywords if kw.lower() in text_lower)
        if score > best_score:
            best_score = score
            best = template
    return best if best_score > 0 else None


_TEMPLATES: dict[str, ProductTemplate] = {}


def init_templates(configs_dir: Path) -> dict[str, ProductTemplate]:
    global _TEMPLATES
    _TEMPLATES = load_product_templates(configs_dir)
    return _TEMPLATES


def get_all_templates() -> dict[str, ProductTemplate]:
    return dict(_TEMPLATES)


# ---------------------------------------------------------------------------
# Domain-generic template (MC / Animation / Builder)
# ---------------------------------------------------------------------------

@dataclass
class DomainTemplate:
    """범용 도메인 템플릿. 도메인별 shape 이 달라도 공통 인터페이스로 접근."""
    template_id: str
    domain: str
    display_name: str
    keywords: list[str]
    data: dict  # 도메인별 원본 데이터 전체

    def to_enrichment(self) -> dict:
        """chain engine 에 전달할 enrichment dict."""
        return {"_domain_template": self.data, "_template_id": self.template_id}


_DOMAIN_TEMPLATES: dict[str, list[DomainTemplate]] = {}  # domain → [templates]


def load_domain_templates(configs_dir: Path) -> dict[str, list[DomainTemplate]]:
    """모든 도메인 템플릿을 로드. *_templates.json 파일들을 자동 탐색."""
    result: dict[str, list[DomainTemplate]] = {}
    mapping = {
        "minecraft_templates.json": "minecraft",
        "animation_templates.json": "animation",
        "builder_templates.json": "builder",
        "product_templates.json": "cad",
    }
    for filename, domain in mapping.items():
        path = configs_dir / filename
        if not path.exists():
            continue
        raw = json.loads(path.read_text(encoding="utf-8"))
        templates = []
        for tid, data in raw.items():
            keywords = data.get("keywords", [])
            display = data.get("display_name", tid)
            templates.append(DomainTemplate(
                template_id=tid,
                domain=domain,
                display_name=display,
                keywords=keywords,
                data=data,
            ))
        result[domain] = templates
    return result


def init_domain_templates(configs_dir: Path) -> dict[str, list[DomainTemplate]]:
    global _DOMAIN_TEMPLATES
    _DOMAIN_TEMPLATES = load_domain_templates(configs_dir)
    return _DOMAIN_TEMPLATES


def match_domain_template(
    user_input: str,
    domain: str,
) -> Optional[DomainTemplate]:
    """해당 도메인의 템플릿 중 키워드 매칭으로 최적 찾기."""
    templates = _DOMAIN_TEMPLATES.get(domain, [])
    text_lower = user_input.lower()
    best: Optional[DomainTemplate] = None
    best_score = 0
    for t in templates:
        score = sum(1 for kw in t.keywords if kw.lower() in text_lower)
        if score > best_score:
            best_score = score
            best = t
    return best if best_score > 0 else None
