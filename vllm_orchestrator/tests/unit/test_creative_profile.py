"""Unit tests for creative_profile module."""
import pytest
from vllm_orchestrator.src.app.domain.creative_profile import (
    CreativeProfile,
    CreativeMode,
    DiversityTarget,
    get_domain_default,
    resolve_creative_profile,
)


class TestCreativeProfile:
    def test_default_values(self):
        cp = CreativeProfile()
        assert cp.mode == "balanced"
        assert cp.novelty == 0.5
        assert cp.variant_count == 1
        assert not cp.is_multi_variant

    def test_to_dict_roundtrip(self):
        cp = CreativeProfile(mode="expressive", novelty=0.8, variant_count=3)
        d = cp.to_dict()
        cp2 = CreativeProfile.from_dict(d)
        assert cp2.mode == "expressive"
        assert cp2.novelty == 0.8
        assert cp2.variant_count == 3

    def test_is_multi_variant(self):
        assert CreativeProfile(variant_count=3).is_multi_variant
        assert not CreativeProfile(variant_count=1).is_multi_variant

    def test_diversity_weight(self):
        assert CreativeProfile(diversity_target="low").diversity_weight == 0.2
        assert CreativeProfile(diversity_target="medium").diversity_weight == 0.5
        assert CreativeProfile(diversity_target="high").diversity_weight == 0.8

    def test_from_dict_ignores_unknown_keys(self):
        cp = CreativeProfile.from_dict({"mode": "conservative", "unknown_key": 42})
        assert cp.mode == "conservative"


class TestDomainDefaults:
    def test_minecraft_default(self):
        cp = get_domain_default("minecraft")
        assert cp.mode == "balanced"
        assert cp.novelty == 0.72
        assert cp.constraint_strictness == 0.82
        assert cp.style_risk == 0.38
        assert cp.variant_count == 3
        assert cp.diversity_target == "high"

    def test_builder_default(self):
        cp = get_domain_default("builder")
        assert cp.mode == "balanced"
        assert cp.novelty == 0.42
        assert cp.constraint_strictness == 0.95
        assert cp.style_risk == 0.12
        assert cp.variant_count == 2

    def test_animation_default(self):
        cp = get_domain_default("animation")
        assert cp.mode == "balanced"
        assert cp.novelty == 0.58
        assert cp.constraint_strictness == 0.90
        assert cp.style_risk == 0.10
        assert cp.variant_count == 3

    def test_cad_default(self):
        cp = get_domain_default("cad")
        assert cp.mode == "conservative"
        assert cp.novelty == 0.34
        assert cp.constraint_strictness == 0.97
        assert cp.style_risk == 0.06
        assert cp.variant_count == 2
        assert cp.diversity_target == "low"

    def test_unknown_domain_falls_back_to_cad(self):
        cp = get_domain_default("unknown_domain")
        assert cp.mode == "conservative"


class TestResolveCreativeProfile:
    def test_no_context_returns_default(self):
        cp = resolve_creative_profile("minecraft")
        assert cp.novelty == 0.72

    def test_empty_context_returns_default(self):
        cp = resolve_creative_profile("minecraft", {})
        assert cp.novelty == 0.72

    def test_user_override(self):
        cp = resolve_creative_profile("minecraft", {
            "creative_profile": {"novelty": 0.9, "variant_count": 5}
        })
        assert cp.novelty == 0.9
        assert cp.variant_count == 5
        # Other fields stay as minecraft default
        assert cp.mode == "balanced"

    def test_clamping(self):
        cp = resolve_creative_profile("cad", {
            "creative_profile": {"novelty": 1.5, "style_risk": -0.3}
        })
        assert cp.novelty == 1.0
        assert cp.style_risk == 0.0

    def test_invalid_mode_reverts_to_default(self):
        cp = resolve_creative_profile("builder", {
            "creative_profile": {"mode": "INVALID"}
        })
        assert cp.mode == "balanced"  # builder default

    def test_invalid_diversity_target_reverts(self):
        cp = resolve_creative_profile("animation", {
            "creative_profile": {"diversity_target": "EXTREME"}
        })
        assert cp.diversity_target == "medium"  # animation default
