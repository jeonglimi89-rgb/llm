"""
validators/domain_validators.py - 프로그램별 도메인 검증

원칙: 필드가 존재할 때만 검사. 없는 필드는 skip.
(분해된 태스크는 원래 스키마의 일부 필드만 가짐)
"""

from __future__ import annotations

from typing import Any


class DomainValidatorBase:
    def validate(self, content: dict) -> tuple[bool, list[str]]:
        raise NotImplementedError


class BuilderValidator(DomainValidatorBase):
    def validate(self, content: dict) -> tuple[bool, list[str]]:
        errors = []
        # floors 검사 (있을 때만)
        if "floors" in content and content["floors"] < 1:
            errors.append("floors must be >= 1")
        # spaces 검사 (있을 때만)
        for space in content.get("spaces", []):
            min_a = space.get("min_area_m2")
            pref_a = space.get("preferred_area_m2")
            if min_a is not None and pref_a is not None and pref_a < min_a:
                errors.append(f"preferred area < minimum for {space.get('type', '?')}")
        # patch_intent 검사
        if "operation_type" in content:
            valid_ops = {"resize", "add", "remove", "move", "replace", "merge", "split"}
            if content["operation_type"] not in valid_ops:
                errors.append(f"invalid operation_type: {content['operation_type']}")
        return len(errors) == 0, errors


class MinecraftValidator(DomainValidatorBase):
    def validate(self, content: dict) -> tuple[bool, list[str]]:
        errors = []
        # edit_parse 검사 (target_anchor가 있을 때만)
        if "target_anchor" in content and not content["target_anchor"]:
            errors.append("target_anchor is empty")
        ops = [op.get("type") for op in content.get("operations", [])]
        preserve = set(content.get("preserve", []))
        if "raise" in ops and "keep_height" in preserve:
            errors.append("conflict: raise + keep_height")
        if content.get("scope") == "global" and "preserve" in content and not preserve:
            errors.append("global scope requires at least one preserve entry")
        # style_check 검사
        if "verdict" in content:
            if content["verdict"] not in ("pass", "fail", "warn"):
                errors.append(f"invalid verdict: {content['verdict']}")
        return len(errors) == 0, errors


class AnimationValidator(DomainValidatorBase):
    def validate(self, content: dict) -> tuple[bool, list[str]]:
        errors = []
        # shot_parse 검사 (duration_frames가 있을 때만)
        if "duration_frames" in content and content["duration_frames"] <= 0:
            errors.append("duration_frames must be > 0")
        chars = content.get("characters", [])
        if not chars and content.get("acting"):
            errors.append("acting defined but no characters")
        camera = content.get("camera", {})
        if isinstance(camera, dict):
            if camera.get("framing") == "extreme_close_up" and camera.get("lens_mm", 50) < 30:
                errors.append("extreme close-up with very wide lens is unusual")
        # camera_intent 검사
        if "framing" in content:
            valid_framings = {"wide", "medium", "close_up", "extreme_close_up", "over_shoulder", "pov", "bird_eye"}
            if content["framing"] not in valid_framings:
                errors.append(f"invalid framing: {content['framing']}")
        if "mood" in content:
            valid_moods = {"warm", "cold", "dramatic", "soft", "dark", "bright", "neutral"}
            if content["mood"] not in valid_moods:
                errors.append(f"invalid mood: {content['mood']}")
        return len(errors) == 0, errors


class CADValidator(DomainValidatorBase):
    def validate(self, content: dict) -> tuple[bool, list[str]]:
        errors = []
        if "systems" in content and not content["systems"]:
            errors.append("systems array must not be empty")
        for c in content.get("constraints", []):
            if c.get("severity") == "critical" and not c.get("details"):
                errors.append(f"critical constraint missing details: {c.get('constraint_type', '?')}")
        return len(errors) == 0, errors


class ProductDesignValidator(DomainValidatorBase):
    def validate(self, content: dict) -> tuple[bool, list[str]]:
        errors = []
        # concept 검사 (있을 때만)
        concept = content.get("concept", {})
        if isinstance(concept, dict):
            if "name" in concept and not concept["name"]:
                errors.append("concept.name must not be empty")
            if "category" in concept and not concept["category"]:
                errors.append("concept.category must not be empty")
        # specifications 검사
        specs = content.get("specifications", {})
        if isinstance(specs, dict):
            features = specs.get("features", [])
            if "features" in specs and not features:
                errors.append("specifications.features must not be empty")
        # BOM 검사 (있을 때만)
        bom = content.get("bom", [])
        for item in bom:
            if not item.get("name"):
                errors.append(f"BOM item missing name: {item}")
            if "quantity" in item and (not isinstance(item["quantity"], (int, float)) or item["quantity"] <= 0):
                errors.append(f"BOM item invalid quantity: {item.get('name', '?')}")
        # certification 검사
        cert = content.get("certification", [])
        valid_certs = {"KC", "CE", "FCC", "UL", "RoHS", "IP65", "IP67", "IP68"}
        for c in cert:
            if isinstance(c, str) and c.upper() not in valid_certs:
                errors.append(f"unknown certification: {c}")
        return len(errors) == 0, errors


# 프로그램 → validator 매핑
DOMAIN_VALIDATORS: dict[str, DomainValidatorBase] = {
    "builder": BuilderValidator(),
    "minecraft": MinecraftValidator(),
    "animation": AnimationValidator(),
    "cad": CADValidator(),
    "product_design": ProductDesignValidator(),
}


def get_domain_validator(program: str) -> DomainValidatorBase | None:
    return DOMAIN_VALIDATORS.get(program)
