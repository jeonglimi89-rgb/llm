"""
bootstrap.py - 앱 초기화

settings → logger → health → circuit → queue → llm → router → dispatcher → fallback → API

NOTE 2026-04-08 (T-tranche 2): every first-party adapter / scheduler / fallback
construction site here goes through ``UnifiedTimeoutPolicy``. ``health_timeout_s``
is no longer left to the adapter's back-compat default — it is explicitly
threaded from the policy. ``Scheduler`` is constructed with ``policy=`` so its
heavy/light cooldowns and source string come from the same single object.
``DegradedModeHandler`` also receives ``policy=``. The legacy back-compat
defaults are still kept for *external* callers, but no first-party call site
relies on them anymore.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from .settings import AppSettings
from .observability.logger import get_logger
from .observability.health_registry import HealthRegistry
from .execution.circuit_breaker import CircuitBreaker
from .execution.queue_manager import QueueManager
from .execution.scheduler import Scheduler
from .execution.timeouts import TimeoutPolicy, UnifiedTimeoutPolicy
from .llm.client import LLMClient
from .llm.adapters.vllm_http import VLLMHttpAdapter
from .llm.adapters.mock_llm import MockLLMAdapter
from .orchestration.router import Router
from .orchestration.dispatcher import Dispatcher
from .fallback.degraded_modes import DegradedModeHandler
from .tools.registry import create_default_registry
from .domain.profiles import init_profiles
from .orchestration.domain_classifier import DomainClassifier
from .orchestration.requirement_extractor import RequirementExtractor
from .review.domain_evaluator import DomainEvaluator
from .orchestration.orchestrated_pipeline import OrchestratedPipeline
from .orchestration.task_chain import TaskChainEngine, load_chain_definitions
from .orchestration.domain_router import DomainRouter
from .domain.product_templates import init_templates, init_domain_templates
from .domain.schema_enforcer import SchemaEnforcer
from .domain.creative_boundaries import init_creative_boundaries, BoundaryEnforcer
from .domain.heuristics import load_heuristic_packs
from .domain.output_policy import OutputPolicyEnforcer
from .domain.intervention_policy import InterventionPolicy
from .domain.allowed_range import AllowedRangeEnforcer
from .domain.command_graph import CommandGraphBuilder
from .orchestration.variant_planner import VariantPlanner
from .orchestration.post_classification_validator import PostClassificationValidator
from .orchestration.llm_variant_generator import LLMVariantGenerator
from .orchestration.multi_graph_splitter import MultiGraphSplitter
from .domain.heuristic_checks import HeuristicDispatcher
from .domain.input_schemas import InputSchemaValidator
from .domain.param_contract import ParamContractValidator
from .execution.graph_executor import GraphExecutor
from .execution.tool_adapter_normalizer import ToolAdapterNormalizer
from .orchestration.cross_domain_handoff import CrossDomainHandoffManager
from .llm.model_router import ModelRouter
from .llm.adapter_registry import AdapterRegistry
from .llm.adapter_policy import AdapterActivationPolicy
from .llm.routing_calibrator import RoutingCalibrator
from .llm.prompt_budget import PromptBudgetManager
from .llm.runtime_hardening import RuntimeHardening
from .review.creativity_verifier import CreativityVerifier
from .training.data_collector import TrainingDataCollector
from .orchestration.orchestrated_pipeline import set_training_collector


def _build_unified_policy(settings: AppSettings) -> UnifiedTimeoutPolicy:
    """Synthesize a ``UnifiedTimeoutPolicy`` from ``AppSettings``.

    All cooldown values come from ``settings`` (which itself reads env), so
    the policy is the single source-of-truth for *every* first-party site.
    """
    return UnifiedTimeoutPolicy(
        request_timeout_s=float(settings.timeouts.strict_json_s),
        request_timeout_source="settings",
        # Health timeout: settings doesn't carry it explicitly yet, so we
        # default to the policy default and tag the source.
        health_timeout_source="settings",
        max_retries=int(settings.fallback.max_retries),
        # Cooldown sub-section — derived from FallbackSettings.retry_delay_s
        # for transport_retry_cooldown and from default constants for the
        # scheduler heavy/light values. ``cooldown_source`` makes the origin
        # explicit in telemetry.
        transport_retry_cooldown_s=float(settings.fallback.retry_delay_s),
        fallback_retry_delay_s=float(settings.fallback.retry_delay_s),
        cooldown_source="settings",
    )


def _find_llm_url(settings: AppSettings, policy: UnifiedTimeoutPolicy) -> str:
    """LLM 서버 URL 탐색: 설정 → WSL → localhost.

    T-tranche 2: every adapter probe constructs ``VLLMHttpAdapter`` with the
    explicit ``health_timeout_s`` value from ``policy`` instead of leaning on
    the adapter's back-compat default.
    """
    htimeout = float(policy.health_timeout_s)
    # 1. 설정값
    url = settings.llm.base_url
    adapter = VLLMHttpAdapter(
        url, settings.llm.api_key, settings.llm.model,
        health_timeout_s=htimeout,
    )
    if adapter.is_available():
        return url

    # 2. WSL IP 탐지
    try:
        result = subprocess.run(
            ["wsl", "-d", "Ubuntu-24.04", "-u", "root", "-e", "bash", "-c", "hostname -I | awk '{print $1}'"],
            capture_output=True, text=True, timeout=5,
        )
        wsl_ip = result.stdout.strip()
        if wsl_ip:
            wsl_url = f"http://{wsl_ip}:8000"
            if VLLMHttpAdapter(
                wsl_url, settings.llm.api_key, settings.llm.model,
                health_timeout_s=htimeout,
            ).is_available():
                return wsl_url
    except Exception:
        pass

    return url  # 못 찾으면 원래 값 (mock으로 fallback됨)


class Container:
    """의존성 묶음"""

    def __init__(self, settings: AppSettings | None = None, *, _skip_llm_probe: bool = False):
        self.settings = settings or AppSettings.from_env()
        self.log = get_logger("app", self.settings.logs_dir)
        # T-tranche 2: build the unified policy first; everything below
        # threads it explicitly.
        self.policy = _build_unified_policy(self.settings)
        self.health = HealthRegistry(fail_threshold=self.settings.llm.health_fail_threshold)
        self.circuit = CircuitBreaker(fail_threshold=3, reset_timeout_s=60)
        self.queue = QueueManager(
            max_concurrency=self.settings.queue.max_concurrency,
            max_depth=self.settings.queue.max_depth,
            task_timeout_s=self.settings.queue.task_timeout_s,
        )
        self.scheduler = Scheduler(policy=self.policy)
        self.timeouts = TimeoutPolicy(self.settings.timeouts)
        self.tools = create_default_registry()
        self.fallback = DegradedModeHandler(policy=self.policy)

        # LLM
        # _skip_llm_probe: test-only flag that skips _find_llm_url's
        # network probes (5s health timeout + 5s WSL subprocess) and
        # goes straight to the mock fallback. Default False preserves
        # back-compat for all production callers.
        url = self.settings.llm.base_url if _skip_llm_probe else _find_llm_url(self.settings, self.policy)
        adapter = VLLMHttpAdapter(
            url, self.settings.llm.api_key, self.settings.llm.model,
            health_timeout_s=float(self.policy.health_timeout_s),
        )
        if adapter.is_available():
            self.llm_client = LLMClient(
                adapter, self.health, self.circuit,
                self.settings.fallback.max_retries,
                transport_retry_cooldown_s=float(self.policy.transport_retry_cooldown_s),
                transport_retry_cooldown_source=self.policy.cooldown_source,
            )
            self.log.info(f"LLM connected: {url}")
        else:
            mock = MockLLMAdapter()
            self.llm_client = LLMClient(
                mock, self.health, self.circuit,
                self.settings.fallback.max_retries,
                transport_retry_cooldown_s=float(self.policy.transport_retry_cooldown_s),
                transport_retry_cooldown_source=self.policy.cooldown_source,
            )
            self.log.warning(f"LLM not available at {url}, using MockLLM")

        # Domain profiles (data-driven, loaded from configs/)
        self.domain_profiles = init_profiles(self.settings.configs_dir)

        # Orchestration
        self.router = Router()
        # Request-level cache (대규모 운영): Redis 가용 시 분산 캐시,
        # 없으면 in-memory LRU로 fallback. REQUEST_CACHE_DISABLED=1 이면 비활성.
        # REQUEST_CACHE_BACKEND=memory 면 Redis 무시하고 in-memory 강제.
        import os
        if os.getenv("REQUEST_CACHE_DISABLED", "").lower() not in ("1", "true", "yes"):
            from .execution.redis_cache_backend import build_cache
            self.request_cache = build_cache(
                url=os.getenv("REDIS_URL"),
                ttl_s=float(os.getenv("REQUEST_CACHE_TTL_S", "3600")),
                max_entries=int(os.getenv("REQUEST_CACHE_MAX", "1000")),
            )
            stats = self.request_cache.stats_dict()
            backend = stats.get("backend", "memory")
            conn = stats.get("connected", True)
            self.log.info(f"RequestCache enabled: backend={backend} connected={conn} ttl={self.request_cache.ttl_s}s")
            if stats.get("init_error"):
                self.log.info(f"  (redis unavailable: {stats['init_error']} → fell back to in-memory)")
        else:
            self.request_cache = None
            self.log.info("RequestCache disabled via REQUEST_CACHE_DISABLED")

        self.dispatcher = Dispatcher(
            llm_client=self.llm_client,
            queue=self.queue,
            scheduler=self.scheduler,
            timeouts=self.timeouts,
            prompts_dir=self.settings.prompts_dir,
            # T-tranche-10: thread fallback into dispatcher for
            # artifact-level explainability. Does not change runtime
            # semantics — fallback is only consulted to *record* a
            # WaitDecision on failure exit sites, never to override the
            # returned TaskResult.
            fallback=self.fallback,
            request_cache=self.request_cache,
        )

        # Domain-specialized orchestration pipeline
        self.domain_router = DomainRouter(self.domain_profiles)
        self.domain_classifier = DomainClassifier(self.domain_profiles)
        self.requirement_extractor = RequirementExtractor(self.domain_profiles)
        self.domain_evaluator = DomainEvaluator(self.domain_profiles)
        self.schema_enforcer = SchemaEnforcer()
        self.product_templates = init_templates(self.settings.configs_dir)
        self.domain_templates = init_domain_templates(self.settings.configs_dir)
        self.chain_definitions = load_chain_definitions(self.settings.configs_dir)
        self.chain_engine = TaskChainEngine(
            dispatcher=self.dispatcher,
            router=self.router,
            tool_registry=self.tools,
        )
        # Creative layer initialization
        self.creative_boundaries = init_creative_boundaries(self.settings.configs_dir)
        self.boundary_enforcer = BoundaryEnforcer(self.creative_boundaries)
        self.heuristic_packs = load_heuristic_packs(self.settings.configs_dir)
        self.variant_planner = VariantPlanner(
            boundary_enforcer=self.boundary_enforcer,
            evaluator=self.domain_evaluator,
        )
        self.creativity_verifier = CreativityVerifier(
            boundary_enforcer=self.boundary_enforcer,
            heuristic_packs=self.heuristic_packs,
        )
        self.output_policy_enforcer = OutputPolicyEnforcer()

        # Intervention / capability layer initialization
        self.intervention_policy = InterventionPolicy()
        self.allowed_range_enforcer = AllowedRangeEnforcer()
        self.command_graph_builder = CommandGraphBuilder(self.intervention_policy)
        self.post_classification_validator = PostClassificationValidator(self.intervention_policy)
        self.llm_variant_generator = LLMVariantGenerator(self.dispatcher, self.router)
        self.multi_graph_splitter = MultiGraphSplitter()
        self.heuristic_dispatcher = HeuristicDispatcher()
        self.input_schema_validator = InputSchemaValidator()
        self.param_contract_validator = ParamContractValidator()
        self.tool_adapter_normalizer = ToolAdapterNormalizer()
        self.cross_domain_handoff = CrossDomainHandoffManager()
        self.model_router = ModelRouter(
            lora_enabled=self.settings.llm.enable_adapters,
        )
        self.adapter_registry = AdapterRegistry()
        self.adapter_policy = AdapterActivationPolicy(self.adapter_registry)
        self.routing_calibrator = RoutingCalibrator(self.model_router, self.adapter_policy)
        self.prompt_budget = PromptBudgetManager(
            max_context=self.settings.llm.max_context,
            output_reserve=self.settings.llm.max_output_tokens,
        )
        self.runtime_hardening = RuntimeHardening()
        self.graph_executor = GraphExecutor(
            tool_registry=self.tools,
            heuristic_dispatcher=self.heuristic_dispatcher,
            heuristic_packs=self.heuristic_packs,
        )

        self.orchestrated_pipeline = OrchestratedPipeline(
            classifier=self.domain_classifier,
            profiles=self.domain_profiles,
            extractor=self.requirement_extractor,
            evaluator=self.domain_evaluator,
            schema_enforcer=self.schema_enforcer,
            router=self.router,
            dispatcher=self.dispatcher,
            domain_router=self.domain_router,
            chain_engine=self.chain_engine,
            chain_definitions=self.chain_definitions,
            product_templates=self.product_templates,
            variant_planner=self.variant_planner,
            creativity_verifier=self.creativity_verifier,
            output_policy=self.output_policy_enforcer,
            post_classification_validator=self.post_classification_validator,
            allowed_range_enforcer=self.allowed_range_enforcer,
            command_graph_builder=self.command_graph_builder,
            llm_variant_generator=self.llm_variant_generator,
        )

        # Enable training data collection (quality-gated → JSONL files)
        try:
            from .storage.paths import training_data_dir, ensure_dirs
            ensure_dirs()
            self.training_collector = TrainingDataCollector(
                output_dir=training_data_dir(),
            )
        except Exception:
            from pathlib import Path as _P
            self.training_collector = TrainingDataCollector(
                output_dir=_P("./training_data"),
            )
        set_training_collector(self.training_collector)
