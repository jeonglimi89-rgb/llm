"""Unit tests for allowed_range module."""
import pytest
from vllm_orchestrator.src.app.domain.allowed_range import (
    AllowedRangeEnforcer, DomainAllowedRange, AllowedRange,
    RangeEnforcementResult, get_domain_allowed_range,
)
from vllm_orchestrator.src.app.domain.creative_profile import CreativeProfile


@pytest.fixture
def enforcer():
    return AllowedRangeEnforcer()


class TestAllowedRangeEnforcer:
    # ── B. Allowed range tests ──

    def test_minecraft_novelty_095_normalize(self, enforcer):
        """Minecraft novelty 0.95 override -> normalize to max 0.85."""
        cp = CreativeProfile(mode="balanced", novelty=0.95, constraint_strictness=0.82,
                             style_risk=0.38, variant_count=3, diversity_target="high")
        adjusted, result = enforcer.enforce("minecraft", cp)
        assert result.passed  # normalize mode
        assert result.adjusted
        assert adjusted.novelty == 0.85  # clamped to max
        assert any("novelty" in v for v in result.violations)

    def test_builder_style_risk_030_normalize(self, enforcer):
        """Builder style_risk 0.30 override -> normalize to max 0.18."""
        cp = CreativeProfile(mode="balanced", novelty=0.42, constraint_strictness=0.95,
                             style_risk=0.30, variant_count=2, diversity_target="medium")
        adjusted, result = enforcer.enforce("builder", cp)
        assert result.adjusted
        assert adjusted.style_risk == 0.18

    def test_animation_style_risk_025_normalize(self, enforcer):
        """Animation style_risk 0.25 override -> normalize to max 0.15."""
        cp = CreativeProfile(mode="balanced", novelty=0.58, constraint_strictness=0.90,
                             style_risk=0.25, variant_count=3, diversity_target="medium")
        adjusted, result = enforcer.enforce("animation", cp)
        assert result.adjusted
        assert adjusted.style_risk == 0.15

    def test_cad_novelty_060_strict_fail(self, enforcer):
        """CAD novelty 0.60 override -> strict mode fail."""
        cp = CreativeProfile(mode="conservative", novelty=0.60, constraint_strictness=0.97,
                             style_risk=0.06, variant_count=2, diversity_target="low")
        _, result = enforcer.enforce("cad", cp, strict_override=True)
        assert not result.passed
        assert result.enforcement_mode == "strict"
        assert any("novelty" in v for v in result.violations)

    def test_valid_profile_passes(self, enforcer):
        """Profile within range passes without adjustment."""
        cp = CreativeProfile(mode="balanced", novelty=0.72, constraint_strictness=0.82,
                             style_risk=0.38, variant_count=3, diversity_target="high")
        adjusted, result = enforcer.enforce("minecraft", cp)
        assert result.passed
        assert not result.adjusted
        assert adjusted.novelty == 0.72  # unchanged

    def test_invalid_mode_normalized(self, enforcer):
        """Mode not in allowed_modes gets normalized."""
        cp = CreativeProfile(mode="expressive", novelty=0.42, constraint_strictness=0.95,
                             style_risk=0.12, variant_count=2, diversity_target="medium")
        adjusted, result = enforcer.enforce("builder", cp)
        # builder allows: conservative, balanced — not expressive
        assert result.adjusted
        assert adjusted.mode == "conservative"

    def test_variant_count_clamped(self, enforcer):
        """variant_count exceeding max gets clamped."""
        cp = CreativeProfile(variant_count=10, mode="balanced", novelty=0.72,
                             constraint_strictness=0.82, style_risk=0.38, diversity_target="high")
        adjusted, result = enforcer.enforce("minecraft", cp)
        assert adjusted.variant_count == 4  # minecraft max

    def test_result_to_dict(self, enforcer):
        cp = CreativeProfile(novelty=0.95, mode="balanced", constraint_strictness=0.82,
                             style_risk=0.38, variant_count=3, diversity_target="high")
        _, result = enforcer.enforce("minecraft", cp)
        d = result.to_dict()
        assert "violations" in d
        assert "adjustments" in d


class TestGetDomainAllowedRange:
    def test_all_domains_have_ranges(self):
        for domain in ["minecraft", "builder", "animation", "cad", "product_design"]:
            dar = get_domain_allowed_range(domain)
            assert dar.domain == domain

    def test_unknown_domain_defaults_to_cad(self):
        dar = get_domain_allowed_range("unknown")
        assert dar.domain == "cad"

    def test_minecraft_ranges(self):
        dar = get_domain_allowed_range("minecraft")
        assert dar.novelty.min_val == 0.45
        assert dar.novelty.max_val == 0.85
        assert dar.enforcement_mode == "normalize"

    def test_cad_ranges(self):
        dar = get_domain_allowed_range("cad")
        assert dar.novelty.min_val == 0.15
        assert dar.novelty.max_val == 0.45
        assert dar.style_risk.max_val == 0.08
