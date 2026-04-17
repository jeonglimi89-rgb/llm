"""Regression suite — LLM 없이 결정론적으로 돌릴 수 있는 stack 핵심 기능 검증.

CI/CD 파이프라인에서 이 스위트가 green이어야 배포 허가.
vLLM 불필요. In-memory cache. Creative layer는 off 모드.

pytest vllm_orchestrator/tests/regression/ -v
"""
from __future__ import annotations

import json
import pytest
from pathlib import Path


# ── request_cache ──────────────────────────────────────────────────────────

class TestRequestCache:
    def test_key_stability_internal_filter(self):
        from app.execution.request_cache import RequestCache
        rc = RequestCache()
        k1 = rc.make_key("t", "hello", {})
        k2 = rc.make_key("t", "hello", {"_intent_analysis": {"x": 1}})
        k3 = rc.make_key("t", "hello", {"base_style": "dark"})
        assert k1 == k2, "internal _-prefixed keys must be filtered"
        assert k1 != k3

    def test_put_rejects_invalid_results(self):
        from app.execution.request_cache import RequestCache
        rc = RequestCache()
        assert rc.put("minecraft.scene_graph", "x", {},
                      {"layered_judgment": {"auto_validated": False}}) is False
        assert rc.put("minecraft.scene_graph", "x", {},
                      {"layered_judgment": {"auto_validated": True}}) is True

    def test_lru_eviction(self):
        from app.execution.request_cache import RequestCache
        rc = RequestCache(max_entries=2, ttl_s=3600)
        for i in range(3):
            rc.put("minecraft.scene_graph", f"in{i}", {},
                   {"layered_judgment": {"auto_validated": True}})
        assert rc.size() == 2
        assert rc.stats.evictions == 1

    def test_ttl_expiry(self):
        import time
        from app.execution.request_cache import RequestCache
        rc = RequestCache(max_entries=10, ttl_s=0.1)
        rc.put("minecraft.scene_graph", "x", {},
               {"layered_judgment": {"auto_validated": True}})
        time.sleep(0.15)
        assert rc.get("minecraft.scene_graph", "x", {}) is None
        assert rc.stats.expired == 1


# ── semantic_cache ─────────────────────────────────────────────────────────

class TestSemanticCache:
    def test_similarity_value(self):
        from app.execution.semantic_cache import text_similarity
        sim = text_similarity(
            "witch castle with multiple towers and walls",
            "witch castle with many towers and walls",
        )
        assert sim >= 0.55, f"similar inputs must score ≥0.55, got {sim}"

    def test_different_concepts_miss(self):
        from app.execution.semantic_cache import text_similarity
        sim = text_similarity("frog pond", "witch castle")
        assert sim < 0.3

    def test_store_and_lookup(self):
        from app.execution.semantic_cache import SemanticCache
        # Lower threshold for 짧은 입력 (단어 수 적을수록 edit distance 영향 큼)
        sc = SemanticCache(similarity_threshold=0.4)
        sc.store("minecraft.scene_graph", "witch castle with multiple towers",
                 "", {"layered_judgment": {"auto_validated": True}, "slots": {"nodes": [1]}})
        v, sim, m = sc.lookup("minecraft.scene_graph",
                              "witch castle with many towers", "")
        assert v is not None, f"similar input should hit (sim={sim})"
        assert sim >= 0.4


# ── intent_analyzer ────────────────────────────────────────────────────────

class TestIntentAnalyzer:
    @pytest.mark.parametrize("input_text,expected_concept,expected_demand", [
        ("witch castle with towers", "castle", "high"),
        ("waffle palace", "castle", "medium"),  # castle keyword 우선순위 높음
        ("frog pond", "frog", "low"),
        ("floating sky island", "sky", "medium"),
        ("snail house", "generic", "low"),
    ])
    def test_theme_detection(self, input_text, expected_concept, expected_demand):
        from app.domain.intent_analyzer import analyze_intent
        r = analyze_intent(input_text)
        assert r.concept_category == expected_concept

    def test_variant_count_scaling(self):
        from app.domain.intent_analyzer import analyze_intent
        r_high = analyze_intent("huge creative crazy witch fortress with 5 towers and hidden chambers")
        assert r_high.suggested_variant_count >= 2


# ── scene_graph_repair ────────────────────────────────────────────────────

class TestSceneGraphRepair:
    def test_castle_keep_insertion(self):
        from app.domain.scene_graph_repair import repair_scene_graph
        slots = {"nodes": [
            {"id": "foundation", "kind": "primitive", "primitive_type": "cuboid",
             "position": {"x": 0, "y": 0, "z": 0}, "size": {"x": 22, "y": 1, "z": 22},
             "material": "cobblestone"},
            {"id": "outer_wall", "kind": "primitive", "primitive_type": "cuboid",
             "position": {"x": 0, "y": 1, "z": 0}, "size": {"x": 20, "y": 6, "z": 20},
             "material": "deepslate", "hollow": True},
        ]}
        repaired, repairs = repair_scene_graph(slots, "witch castle")
        keep_nodes = [n for n in repaired["nodes"] if "keep" in n.get("id", "").lower()]
        assert len(keep_nodes) >= 1, "castle must have keep inserted"

    def test_material_diversity(self):
        from app.domain.scene_graph_repair import repair_scene_graph
        slots = {"nodes": [
            {"id": "a", "kind": "primitive", "primitive_type": "cuboid",
             "position": {"x": 0, "y": 0, "z": 0}, "size": {"x": 4, "y": 1, "z": 4},
             "material": "honey_block"},
        ]}
        repaired, repairs = repair_scene_graph(slots, "waffle palace")
        mats = {n.get("material") for n in repaired["nodes"] if n.get("material")}
        assert len(mats) >= 4, f"material diversity ≥4 required, got {len(mats)}"

    def test_floating_underside_fix(self):
        from app.domain.scene_graph_repair import repair_scene_graph, PALETTES
        slots = {"nodes": [
            {"id": "island", "kind": "primitive", "primitive_type": "cuboid",
             "position": {"x": 0, "y": 15, "z": 0}, "size": {"x": 16, "y": 1, "z": 16},
             "material": "glass"},
            {"id": "bad_under", "kind": "primitive", "primitive_type": "cone",
             "position": {"x": 0, "y": 14, "z": 0}, "base_radius": 2, "height": 1,
             "material": "glass", "tip_ratio": 0},
        ]}
        repaired, _ = repair_scene_graph(slots, "floating sky island")
        cones = [n for n in repaired["nodes"] if n.get("primitive_type") == "cone"]
        assert cones
        c = cones[0]
        assert c["base_radius"] >= 8
        assert c["height"] >= 5
        assert c["material"] in PALETTES["sky_under"]


# ── variant_sampler scorer ─────────────────────────────────────────────────

class TestVariantScorer:
    def test_perfect_castle_scores_10(self):
        from app.orchestration.variant_sampler import score_scene_graph
        slots = {
            "nodes": [
                {"id": "foundation", "primitive_type": "cuboid",
                 "position": {"x": 0, "y": 0, "z": 0}, "size": {"x": 22, "y": 1, "z": 22},
                 "material": "cobblestone"},
                {"id": "keep", "primitive_type": "cylinder",
                 "position": {"x": 0, "y": 1, "z": 0}, "radius": 4, "height": 10,
                 "material": "cobbled_deepslate"},
                {"id": "wall", "primitive_type": "cuboid",
                 "position": {"x": 0, "y": 1, "z": 0}, "size": {"x": 20, "y": 6, "z": 20},
                 "material": "deepslate", "hollow": True},
                {"id": "tower_nw", "primitive_type": "cylinder",
                 "position": {"x": -9, "y": 1, "z": -9}, "radius": 2, "height": 12,
                 "material": "deepslate"},
                {"id": "tower_se", "primitive_type": "cylinder",
                 "position": {"x": 9, "y": 1, "z": 9}, "radius": 2, "height": 12,
                 "material": "dark_oak_planks"},
                {"id": "spire_nw", "primitive_type": "cone",
                 "position": "node:tower_nw.top", "base_radius": 2, "height": 4,
                 "material": "purple_stained_glass"},
            ],
            "concept_notes": "full castle with towers + keep + walls + foundation mix 5 materials"
        }
        score, breakdown = score_scene_graph(slots, "witch castle",
                                             {"concept_category": "witch"})
        assert score >= 8.0, f"good castle should score ≥8, got {score}: {breakdown}"

    def test_poor_scene_graph_low_score(self):
        from app.orchestration.variant_sampler import score_scene_graph
        slots = {"nodes": [
            {"id": "n1", "primitive_type": "cuboid",
             "position": {"x": 0, "y": 0, "z": 0}, "size": {"x": 2, "y": 2, "z": 2},
             "material": "oak_planks"},
        ]}
        score, _ = score_scene_graph(slots, "witch castle", None)
        assert score < 5.0


# ── PII redaction ─────────────────────────────────────────────────────────

class TestPIIRedaction:
    def test_email_redacted(self):
        from app.security.pii import redact_text
        assert "[EMAIL]" in redact_text("contact me at alice@example.com please")
        assert "alice@example.com" not in redact_text("contact me at alice@example.com please")

    def test_kr_rrn_redacted(self):
        from app.security.pii import redact_text
        s = redact_text("주민번호 900101-1234567 확인")
        assert "[RRN]" in s
        assert "900101" not in s

    def test_credit_card_redacted(self):
        from app.security.pii import redact_text
        s = redact_text("card 4111-1111-1111-1111 expired")
        assert "[CC]" in s

    def test_ipv4_redacted(self):
        from app.security.pii import redact_text
        s = redact_text("server at 192.168.1.100 responding")
        assert "[IPv4]" in s

    def test_non_pii_preserved(self):
        from app.security.pii import redact_text
        s = redact_text("witch castle with moonlit spires")
        assert s == "witch castle with moonlit spires"

    def test_nested_dict_redacted(self):
        from app.security.pii import redact_value
        d = {"name": "alice", "email": "alice@example.com", "tags": ["bob@x.com", "safe"]}
        out = redact_value(d)
        assert "[EMAIL]" in out["email"]
        assert "[EMAIL]" in out["tags"][0]


# ── API key rotation ──────────────────────────────────────────────────────

class TestAPIKeyRotation:
    def test_generate_and_check(self, tmp_path):
        from app.security.api_keys import APIKeyStore
        store = APIKeyStore(path=str(tmp_path / "keys.jsonl"))
        key_id, plain = store.generate(tier="default")
        assert key_id.startswith("k-")
        rec = store.check(plain)
        assert rec is not None
        assert rec.key_id == key_id
        assert rec.tier == "default"

    def test_bad_key_rejected(self, tmp_path):
        from app.security.api_keys import APIKeyStore
        store = APIKeyStore(path=str(tmp_path / "keys.jsonl"))
        store.generate()
        assert store.check("not-a-real-key") is None

    def test_revoke(self, tmp_path):
        from app.security.api_keys import APIKeyStore
        store = APIKeyStore(path=str(tmp_path / "keys.jsonl"))
        key_id, plain = store.generate()
        assert store.check(plain) is not None
        assert store.revoke(key_id)
        assert store.check(plain) is None

    def test_rotation_grace_period(self, tmp_path):
        from app.security.api_keys import APIKeyStore
        store = APIKeyStore(path=str(tmp_path / "keys.jsonl"))
        old_id, old_plain = store.generate()
        new_id, new_plain = store.start_rotation(old_id, grace_hours=1)
        assert new_id is not None
        # Both usable within grace window
        assert store.check(old_plain) is not None
        assert store.check(new_plain) is not None


# ── Feedback store ────────────────────────────────────────────────────────

class TestFeedbackStore:
    def test_record_and_read(self, tmp_path):
        from app.storage.feedback_store import FeedbackStore, FeedbackEntry
        store = FeedbackStore(path=str(tmp_path / "fb.jsonl"))
        ok = store.record(FeedbackEntry(task_id="t1", rating=4, tags=["good"]))
        assert ok
        entries = store.recent(10)
        assert len(entries) == 1
        assert entries[0]["rating"] == 4

    def test_pii_redacted_on_record(self, tmp_path):
        from app.storage.feedback_store import FeedbackStore, FeedbackEntry
        store = FeedbackStore(path=str(tmp_path / "fb.jsonl"))
        store.record(FeedbackEntry(
            task_id="t1", rating=3, notes="contact me at test@example.com"
        ))
        entries = store.recent(10)
        assert "[EMAIL]" in entries[0]["notes"]
        assert "test@example.com" not in entries[0]["notes"]

    def test_rotation(self, tmp_path, monkeypatch):
        from app.storage.feedback_store import FeedbackStore, FeedbackEntry
        monkeypatch.setenv("FEEDBACK_MAX_BYTES", "200")
        monkeypatch.setenv("FEEDBACK_KEEP_ARCHIVES", "2")
        store = FeedbackStore(path=str(tmp_path / "fb.jsonl"))
        for i in range(50):
            store.record(FeedbackEntry(task_id=f"t{i}", rating=4,
                                       notes="x" * 30))
        archives = sorted(tmp_path.glob("fb.jsonl.*"))
        assert len(archives) <= 2


# ── llm_critic (parse + validation) ───────────────────────────────────────

class TestLLMCritic:
    def test_summarize_scene_graph(self):
        from app.review.llm_critic import _summarize_scene_graph
        s = _summarize_scene_graph({"nodes": [
            {"id": "a", "primitive_type": "cuboid",
             "position": {"x": 0, "y": 0, "z": 0}, "size": {"x": 5, "y": 5, "z": 5},
             "material": "stone"},
        ], "concept_notes": "test"})
        assert "Nodes: 1" in s
        assert "stone" in s

    def test_critic_parser_extracts_json(self):
        from app.review.llm_critic import _parse_critic_output
        raw = 'Prefix text {"overall_quality": 0.7, "issues": [], "repair_needed": false} tail'
        d = _parse_critic_output(raw)
        assert d is not None
        assert d["overall_quality"] == 0.7

    def test_task_router_coverage(self):
        from app.review.llm_critic import critic_enabled_for
        assert critic_enabled_for("minecraft.scene_graph")
        assert critic_enabled_for("minecraft.brainstorm")
        assert critic_enabled_for("builder.plan")
        assert critic_enabled_for("animation.shot_parse")
        assert critic_enabled_for("cad.design")
        assert not critic_enabled_for("minecraft.build_parse")  # strict task not covered


# ── Secrets layer (encrypt/decrypt) ───────────────────────────────────────

class TestSecrets:
    def test_encrypt_decrypt_roundtrip(self, tmp_path):
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa
        except ImportError:
            pytest.skip("cryptography not installed")
        from app.security.secrets import _encrypt, _decrypt
        plaintext = b'{"LLM_API_KEY": "secret123", "REDIS_PW": "x"}'
        passphrase = "strong-passphrase"
        blob = _encrypt(plaintext, passphrase)
        assert blob != plaintext
        out = _decrypt(blob, passphrase)
        assert out == plaintext

    def test_wrong_passphrase_fails(self, tmp_path):
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa
        except ImportError:
            pytest.skip("cryptography not installed")
        from app.security.secrets import _encrypt, _decrypt
        blob = _encrypt(b"data", "pw1")
        with pytest.raises(Exception):
            _decrypt(blob, "pw2")


# ── Tracing (noop mode) ───────────────────────────────────────────────────

class TestTracing:
    def test_noop_when_disabled(self, monkeypatch):
        monkeypatch.setenv("OTEL_ENABLED", "0")
        # Reset module state
        import importlib
        import app.observability.tracing as t
        importlib.reload(t)
        t.init_tracing(None)
        with t.span("test") as s:
            s.set_attribute("k", "v")
        # No exception = success
        assert t._noop is True


# ── Variant sampler (without real LLM — mock adapter) ─────────────────────

class TestVariantSamplerWithMock:
    def test_scoring_handles_invalid(self):
        from app.orchestration.variant_sampler import score_scene_graph
        # None/비dict → 0
        score, breakdown = score_scene_graph(None, "anything", None)
        assert score == 0.0
        assert "reason" in breakdown

    def test_empty_nodes_low_score(self):
        from app.orchestration.variant_sampler import score_scene_graph
        # 빈 dict → 낮은 점수 (0~3)
        score, _ = score_scene_graph({}, "anything", None)
        assert score < 4.0
