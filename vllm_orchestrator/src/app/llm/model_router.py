"""
llm/model_router.py — Multi-model routing for Qwen2.5-32B canonical baseline.

Canonical model identity:
  core_text_32b  → Qwen/Qwen2.5-32B-Instruct
  core_code_32b  → Qwen/Qwen2.5-Coder-32B-Instruct

All downstream apps see logical model IDs only. Concrete model paths,
quantization, endpoint URLs are resolved at the runtime layer.

Principles:
  - Default text brain = 32B Instruct
  - Code tasks → Coder-32B (separate provider slot)
  - Domain specialization via PEFT adapters, NOT model swaps
  - No silent downgrade; explicit config only
  - Local quantized + remote full parity via runtime_mode
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from typing import Any, Optional
from enum import Enum


# ── Canonical logical model IDs ──

CORE_TEXT_32B = "core_text_32b"
CORE_CODE_32B = "core_code_32b"


class ModelTier(str, Enum):
    TEXT = "text"               # Qwen2.5-32B-Instruct (all text tasks)
    CODE = "code"               # Qwen2.5-Coder-32B-Instruct (code tasks)
    CREATIVE = "creative"       # Same base 32B, higher temperature
    LONG_CONTEXT = "long_context"  # 32B with extended context


class RuntimeMode(str, Enum):
    LOCAL_QUANTIZED = "local_quantized"     # quantized 32B on local GPU
    REMOTE_FULL = "remote_full"             # full precision on remote vLLM
    REMOTE_QUANTIZED = "remote_quantized"   # quantized on remote endpoint


@dataclass
class ProviderMetadata:
    """Metadata for a registered model provider."""
    logical_id: str                     # "core_text_32b" | "core_code_32b"
    canonical_identity: str             # "Qwen/Qwen2.5-32B-Instruct"
    model_family: str = "qwen2.5"
    parameter_scale: str = "32B"
    modality: str = "text"              # "text" | "code" | "vision"
    quantization_mode: str = "none"     # "none"|"awq"|"gptq"|"bnb_4bit"|"bnb_8bit"
    adapter_capable: bool = True
    runtime_mode: str = RuntimeMode.LOCAL_QUANTIZED.value

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ModelEndpoint:
    """Configuration for a single model endpoint."""
    endpoint_id: str
    logical_id: str                     # links to ProviderMetadata
    tier: str                           # ModelTier value
    base_url: str = "http://localhost:8000"
    model_path: str = ""                # deployed model path (may be quantized)
    api_key: str = "internal-token"
    max_output_tokens: int = 512
    temperature: float = 0.1
    top_p: float = 0.95
    timeout_ms: int = 15000
    max_context: int = 32768
    lora_adapter_id: str = ""
    enabled: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ModelRoutingDecision:
    """Result of model routing for a specific request."""
    endpoint_id: str
    logical_id: str
    tier: str
    model_path: str
    base_url: str
    lora_adapter_id: str = ""
    temperature: float = 0.1
    max_output_tokens: int = 512
    timeout_ms: int = 15000
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ── Default provider registry ──

DEFAULT_PROVIDERS: dict[str, ProviderMetadata] = {
    CORE_TEXT_32B: ProviderMetadata(
        logical_id=CORE_TEXT_32B,
        canonical_identity="Qwen/Qwen2.5-32B-Instruct",
        model_family="qwen2.5",
        parameter_scale="32B",
        modality="text",
        adapter_capable=True,
    ),
    CORE_CODE_32B: ProviderMetadata(
        logical_id=CORE_CODE_32B,
        canonical_identity="Qwen/Qwen2.5-Coder-32B-Instruct",
        model_family="qwen2.5",
        parameter_scale="32B",
        modality="code",
        adapter_capable=True,
    ),
}


# ── Default endpoint configurations ──

DEFAULT_ENDPOINTS: dict[str, ModelEndpoint] = {
    "text-32b": ModelEndpoint(
        endpoint_id="text-32b",
        logical_id=CORE_TEXT_32B,
        tier=ModelTier.TEXT.value,
        base_url="http://localhost:8000",
        model_path=os.getenv("LLM_MODEL", "/mnt/d/LLM/models/Qwen2.5-14B-Instruct-AWQ"),
        max_output_tokens=512,
        temperature=0.1,
        timeout_ms=15000,
        max_context=32768,
    ),
    "creative-32b": ModelEndpoint(
        endpoint_id="creative-32b",
        logical_id=CORE_TEXT_32B,
        tier=ModelTier.CREATIVE.value,
        base_url="http://localhost:8000",
        model_path=os.getenv("LLM_MODEL", "/mnt/d/LLM/models/Qwen2.5-14B-Instruct-AWQ"),
        max_output_tokens=512,
        temperature=0.4,
        timeout_ms=20000,
        max_context=32768,
    ),
    "long-ctx-32b": ModelEndpoint(
        endpoint_id="long-ctx-32b",
        logical_id=CORE_TEXT_32B,
        tier=ModelTier.LONG_CONTEXT.value,
        base_url="http://localhost:8000",
        model_path=os.getenv("LLM_MODEL", "/mnt/d/LLM/models/Qwen2.5-14B-Instruct-AWQ"),
        max_output_tokens=512,
        temperature=0.1,
        timeout_ms=30000,
        max_context=131072,
    ),
    "code-32b": ModelEndpoint(
        endpoint_id="code-32b",
        logical_id=CORE_CODE_32B,
        tier=ModelTier.CODE.value,
        base_url="http://localhost:8000",
        model_path="Qwen/Qwen2.5-Coder-32B-Instruct",
        max_output_tokens=1024,
        temperature=0.1,
        timeout_ms=20000,
        max_context=32768,
    ),
}


# ── Domain LoRA adapter mappings ──

DOMAIN_LORA_ADAPTERS: dict[str, str] = {
    "minecraft": "minecraft_style_adapter",
    "builder": "builder_rules_adapter",
    "animation": "animation_direction_adapter",
    "cad": "cad_constraints_adapter",
    "product_design": "cad_constraints_adapter",
}


# ── Pool → tier mapping ──

_POOL_TO_TIER: dict[str, str] = {
    "strict-json-pool": ModelTier.TEXT.value,
    "fast-chat-pool": ModelTier.TEXT.value,
    "long-context-pool": ModelTier.LONG_CONTEXT.value,
    "embedding-pool": ModelTier.TEXT.value,
    "creative-json-pool": ModelTier.CREATIVE.value,
}


# ── Tasks that route to Coder model ──

_CODE_TASKS: set[str] = {
    "builder.floor_plan_generate",
    # code patch, code plan, repo refactor, test repair tasks
}


class ModelRouter:
    """Routes requests to appropriate model endpoints.

    All downstream consumers see only logical model IDs.
    Concrete paths are resolved here.
    """

    def __init__(
        self,
        providers: Optional[dict[str, ProviderMetadata]] = None,
        endpoints: Optional[dict[str, ModelEndpoint]] = None,
        domain_lora: Optional[dict[str, str]] = None,
        lora_enabled: bool = False,
    ):
        self._providers = providers or dict(DEFAULT_PROVIDERS)
        self._endpoints = endpoints or dict(DEFAULT_ENDPOINTS)
        self._domain_lora = domain_lora or dict(DOMAIN_LORA_ADAPTERS)
        self._lora_enabled = lora_enabled

    def route(
        self,
        task_type: str,
        pool_name: str,
        domain: str = "",
        *,
        is_variant: bool = False,
        is_long_context: bool = False,
        is_code_task: bool = False,
    ) -> ModelRoutingDecision:
        """Route a task to the best model endpoint.

        Args:
            task_type: Full task type
            pool_name: Pool name from task router
            domain: Domain for LoRA adapter selection
            is_variant: True if creative variant generation
            is_long_context: True if input exceeds standard context
            is_code_task: True if explicitly a code task
        """
        # 1. Determine tier
        tier = self._resolve_tier(task_type, pool_name, is_variant, is_long_context, is_code_task)

        # 2. Resolve logical model ID
        logical_id = CORE_CODE_32B if tier == ModelTier.CODE.value else CORE_TEXT_32B

        # 3. Find endpoint for tier
        endpoint = self._find_endpoint(tier)

        # 4. Resolve LoRA adapter
        lora = ""
        if self._lora_enabled and domain:
            lora = self._domain_lora.get(domain, "")

        return ModelRoutingDecision(
            endpoint_id=endpoint.endpoint_id,
            logical_id=logical_id,
            tier=tier,
            model_path=endpoint.model_path,
            base_url=endpoint.base_url,
            lora_adapter_id=lora,
            temperature=endpoint.temperature,
            max_output_tokens=endpoint.max_output_tokens,
            timeout_ms=endpoint.timeout_ms,
            reason=self._build_reason(tier, logical_id, lora, task_type),
        )

    def get_provider(self, logical_id: str) -> Optional[ProviderMetadata]:
        return self._providers.get(logical_id)

    def get_endpoint(self, endpoint_id: str) -> Optional[ModelEndpoint]:
        return self._endpoints.get(endpoint_id)

    def list_providers(self) -> list[ProviderMetadata]:
        return list(self._providers.values())

    def list_endpoints(self) -> list[ModelEndpoint]:
        return list(self._endpoints.values())

    def list_enabled_endpoints(self) -> list[ModelEndpoint]:
        return [e for e in self._endpoints.values() if e.enabled]

    def _resolve_tier(self, task_type, pool_name, is_variant, is_long_context, is_code_task):
        # Code tasks → code tier
        if is_code_task or task_type in _CODE_TASKS:
            return ModelTier.CODE.value
        # Variant → creative tier
        if is_variant:
            return ModelTier.CREATIVE.value
        # Long context → long context tier
        if is_long_context:
            return ModelTier.LONG_CONTEXT.value
        # Pool-based default
        return _POOL_TO_TIER.get(pool_name, ModelTier.TEXT.value)

    def _find_endpoint(self, tier: str) -> ModelEndpoint:
        for ep in self._endpoints.values():
            if ep.tier == tier and ep.enabled:
                return ep
        # Fallback to text-32b
        main = self._endpoints.get("text-32b")
        if main:
            return main
        for ep in self._endpoints.values():
            if ep.enabled:
                return ep
        raise RuntimeError("No enabled model endpoints")

    def _build_reason(self, tier, logical_id, lora, task_type):
        parts = [f"tier={tier}", f"model={logical_id}"]
        if lora:
            parts.append(f"lora={lora}")
        parts.append(f"task={task_type}")
        return ", ".join(parts)
