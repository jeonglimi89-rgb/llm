"""
llm/adapter_registry.py — PEFT Adapter Registry.

Manages domain-specific LoRA adapters for the 32B base model.
Adapters specialize the base model for each domain without model swaps.

Adapter attach/detach is request-scoped: each request specifies which
adapter (if any) to apply. The vLLM server loads adapters via --lora-modules.

When an adapter is not yet trained/available, graceful fallback to the
base 32B model with a warning telemetry event.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional
from enum import Enum

from ..observability.logger import get_logger

_log = get_logger("adapter_registry")


class AdapterStatus(str, Enum):
    AVAILABLE = "available"         # trained and deployed
    RESERVED = "reserved"           # interface defined, weights not yet trained
    DISABLED = "disabled"           # explicitly turned off
    ERROR = "error"                 # failed to load


@dataclass
class AdapterSpec:
    """Specification for a single LoRA adapter."""
    adapter_id: str                     # "builder_rules_adapter"
    domain: str                         # "builder"
    base_model_id: str = "core_text_32b"
    adapter_path: str = ""              # path to adapter weights (empty = not trained)
    rank: int = 16                      # LoRA rank
    alpha: float = 32.0                 # LoRA alpha
    target_modules: list[str] = field(default_factory=lambda: ["q_proj", "v_proj"])
    status: AdapterStatus = AdapterStatus.RESERVED
    description: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value if isinstance(self.status, AdapterStatus) else self.status
        return d

    @property
    def is_available(self) -> bool:
        return self.status == AdapterStatus.AVAILABLE and bool(self.adapter_path)


@dataclass
class AdapterAttachResult:
    """Result of attempting to attach an adapter to a request."""
    adapter_id: str
    attached: bool = False
    fallback_to_base: bool = False
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ── Pre-registered adapters (all reserved until trained) ──

_ADAPTER_REGISTRY: dict[str, AdapterSpec] = {
    "builder_rules_adapter": AdapterSpec(
        adapter_id="builder_rules_adapter",
        domain="builder",
        description="Korean building codes, zoning rules, spatial planning conventions",
    ),
    "cad_constraints_adapter": AdapterSpec(
        adapter_id="cad_constraints_adapter",
        domain="cad",
        description="Mechanical engineering constraints, manufacturing tolerances, IP ratings",
    ),
    "minecraft_style_adapter": AdapterSpec(
        adapter_id="minecraft_style_adapter",
        domain="minecraft",
        description="Minecraft block palettes, architectural styles, build conventions",
    ),
    "animation_direction_adapter": AdapterSpec(
        adapter_id="animation_direction_adapter",
        domain="animation",
        description="Cinematic direction, camera grammar, emotion-to-shot mappings",
    ),
}


class AdapterRegistry:
    """Manages PEFT adapter lifecycle and request-scoped attachment."""

    def __init__(self, adapters: Optional[dict[str, AdapterSpec]] = None):
        import copy
        self._adapters = copy.deepcopy(adapters) if adapters else copy.deepcopy(_ADAPTER_REGISTRY)

    def get(self, adapter_id: str) -> Optional[AdapterSpec]:
        return self._adapters.get(adapter_id)

    def get_for_domain(self, domain: str) -> Optional[AdapterSpec]:
        """Find the adapter registered for a domain."""
        for spec in self._adapters.values():
            if spec.domain == domain:
                return spec
        return None

    def list_all(self) -> list[AdapterSpec]:
        return list(self._adapters.values())

    def list_available(self) -> list[AdapterSpec]:
        return [a for a in self._adapters.values() if a.is_available]

    def list_reserved(self) -> list[AdapterSpec]:
        return [a for a in self._adapters.values() if a.status == AdapterStatus.RESERVED]

    def attach(self, adapter_id: str) -> AdapterAttachResult:
        """Attempt to attach an adapter for the current request.

        If the adapter is not yet available (reserved/disabled), falls back
        to the base model with a warning.
        """
        spec = self._adapters.get(adapter_id)
        if spec is None:
            return AdapterAttachResult(
                adapter_id=adapter_id,
                attached=False,
                fallback_to_base=True,
                reason=f"Unknown adapter: {adapter_id}",
            )

        if spec.is_available:
            return AdapterAttachResult(
                adapter_id=adapter_id,
                attached=True,
                reason="adapter loaded",
            )

        # Graceful fallback
        _log.warning(
            f"Adapter '{adapter_id}' status={spec.status.value}, "
            f"falling back to base model"
        )
        return AdapterAttachResult(
            adapter_id=adapter_id,
            attached=False,
            fallback_to_base=True,
            reason=f"adapter status={spec.status.value}, using base model",
        )

    def attach_for_domain(self, domain: str) -> AdapterAttachResult:
        """Attach the domain's registered adapter, if any."""
        spec = self.get_for_domain(domain)
        if spec is None:
            return AdapterAttachResult(
                adapter_id="",
                attached=False,
                fallback_to_base=True,
                reason=f"No adapter registered for domain '{domain}'",
            )
        return self.attach(spec.adapter_id)

    def register(self, spec: AdapterSpec) -> None:
        """Register or update an adapter spec."""
        self._adapters[spec.adapter_id] = spec

    def set_status(self, adapter_id: str, status: AdapterStatus, path: str = "") -> bool:
        """Update adapter status (e.g., after training completes)."""
        spec = self._adapters.get(adapter_id)
        if not spec:
            return False
        spec.status = status
        if path:
            spec.adapter_path = path
        return True
