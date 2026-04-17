"""Unit tests for heuristics module."""
import pytest

from vllm_orchestrator.src.app.domain.heuristics import (
    Heuristic,
    HeuristicPack,
    load_heuristic_packs,
    _matches_condition,
)
from vllm_orchestrator.src.app.domain.creative_profile import CreativeProfile


class TestMatchesCondition:
    def test_always(self):
        assert _matches_condition("always", None)
        assert _matches_condition("always", CreativeProfile())

    def test_mode_equals(self):
        cp = CreativeProfile(mode="expressive")
        assert _matches_condition("mode==expressive", cp)
        assert not _matches_condition("mode==conservative", cp)

    def test_novelty_greater(self):
        cp = CreativeProfile(novelty=0.72)
        assert _matches_condition("novelty>0.5", cp)
        assert not _matches_condition("novelty>0.8", cp)

    def test_novelty_less(self):
        cp = CreativeProfile(novelty=0.3)
        assert _matches_condition("novelty<0.5", cp)
        assert not _matches_condition("novelty<0.2", cp)

    def test_no_profile_only_always(self):
        assert not _matches_condition("mode==expressive", None)
        assert not _matches_condition("novelty>0.5", None)


class TestHeuristicPack:
    def test_all_applicable_always(self):
        pack = HeuristicPack(
            domain="test",
            safety_heuristics=[
                Heuristic("h1", "test", "safety", "always", 0, "check1", "fix1"),
            ],
            creativity_heuristics=[
                Heuristic("h2", "test", "creativity", "mode==expressive", 1, "check2", "fix2"),
            ],
        )
        # With no profile, only "always" heuristics
        applicable = pack.all_applicable(None)
        assert len(applicable) == 1
        assert applicable[0].heuristic_id == "h1"

    def test_all_applicable_with_profile(self):
        pack = HeuristicPack(
            domain="test",
            safety_heuristics=[
                Heuristic("h1", "test", "safety", "always", 0, "check1", "fix1"),
            ],
            creativity_heuristics=[
                Heuristic("h2", "test", "creativity", "mode==expressive", 1, "check2", "fix2"),
            ],
        )
        cp = CreativeProfile(mode="expressive")
        applicable = pack.all_applicable(cp)
        assert len(applicable) == 2

    def test_priority_ordering(self):
        pack = HeuristicPack(
            domain="test",
            safety_heuristics=[
                Heuristic("h_low", "test", "safety", "always", 10, "c", "f"),
                Heuristic("h_high", "test", "safety", "always", 0, "c", "f"),
            ],
        )
        applicable = pack.all_applicable(None)
        assert applicable[0].heuristic_id == "h_high"
        assert applicable[1].heuristic_id == "h_low"

    def test_to_dict(self):
        pack = HeuristicPack(
            domain="minecraft",
            safety_heuristics=[
                Heuristic("h1", "minecraft", "safety", "always", 0, "check", "fix"),
            ],
        )
        d = pack.to_dict()
        assert d["domain"] == "minecraft"
        assert len(d["safety"]) == 1


class TestLoadHeuristicPacks:
    def test_default_packs_loaded(self):
        packs = load_heuristic_packs()
        assert "minecraft" in packs
        assert "builder" in packs
        assert "animation" in packs
        assert "cad" in packs
        assert "product_design" in packs

    def test_minecraft_has_all_categories(self):
        packs = load_heuristic_packs()
        mc = packs["minecraft"]
        assert len(mc.safety_heuristics) > 0
        assert len(mc.quality_heuristics) > 0
        assert len(mc.creativity_heuristics) > 0

    def test_safety_heuristics_are_always(self):
        packs = load_heuristic_packs()
        for domain, pack in packs.items():
            for h in pack.safety_heuristics:
                assert h.applies_when == "always", (
                    f"{domain}.{h.heuristic_id} safety heuristic must apply 'always'"
                )

    def test_creativity_heuristics_are_conditional(self):
        packs = load_heuristic_packs()
        for domain, pack in packs.items():
            for h in pack.creativity_heuristics:
                assert h.applies_when != "always", (
                    f"{domain}.{h.heuristic_id} creativity heuristic should be conditional"
                )
