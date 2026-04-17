"""
peft_manager.py — 공통 PEFT/LoRA 어댑터 관리 계층

Qwen2.5-Instruct (텍스트) + Qwen2.5-VL (비전) 모두에서 사용 가능.
safetensors 기반 어댑터 로드/스왑/등록.

사용:
  manager = PeftManager()
  manager.register("minecraft_v1", "/path/to/adapter/safetensors")
  model = manager.apply(base_model, "minecraft_v1")
"""
from __future__ import annotations

import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Optional

log = logging.getLogger("peft_manager")


@dataclass
class AdapterInfo:
    name: str
    path: str
    loaded: bool = False
    metadata: dict = field(default_factory=dict)


class PeftManager:
    """PEFT LoRA 어댑터 레지스트리 + 핫 스왑 매니저."""

    def __init__(self):
        self._adapters: dict[str, AdapterInfo] = {}
        self._active: dict[int, str] = {}  # model_id → active adapter name

    def register(self, name: str, path: str, metadata: dict | None = None) -> AdapterInfo:
        """어댑터 등록 (safetensors 디렉토리 또는 HF hub ID)."""
        p = Path(path)
        if p.exists():
            # 로컬 경로 — safetensors 파일 확인
            safetensor_files = list(p.glob("*.safetensors"))
            if not safetensor_files:
                log.warning(f"No .safetensors files in {path}")
        info = AdapterInfo(name=name, path=path, metadata=metadata or {})
        self._adapters[name] = info
        log.info(f"Adapter registered: {name} → {path}")
        return info

    def list_adapters(self) -> list[AdapterInfo]:
        return list(self._adapters.values())

    def get_active(self, model: Any) -> Optional[str]:
        return self._active.get(id(model))

    def apply(self, model: Any, adapter_name: str) -> Any:
        """모델에 PEFT LoRA 어댑터 적용.

        이미 같은 어댑터가 적용되어 있으면 no-op.
        다른 어댑터가 적용되어 있으면 스왑.
        처음이면 PeftModel 래핑.

        Returns:
            PeftModel 래핑된 모델 (또는 원래 모델, peft 미설치 시)
        """
        if adapter_name not in self._adapters:
            raise ValueError(f"Unknown adapter: {adapter_name}. Registered: {list(self._adapters.keys())}")

        current = self._active.get(id(model))
        if current == adapter_name:
            return model  # 이미 활성

        adapter = self._adapters[adapter_name]

        try:
            from peft import PeftModel

            if hasattr(model, 'load_adapter'):
                # 이미 PeftModel — 어댑터 스왑
                if not adapter.loaded:
                    model.load_adapter(adapter.path, adapter_name=adapter_name)
                    adapter.loaded = True
                model.set_adapter(adapter_name)
            else:
                # 최초 PEFT 래핑
                model = PeftModel.from_pretrained(model, adapter.path, adapter_name=adapter_name)
                model.eval()
                adapter.loaded = True

            self._active[id(model)] = adapter_name
            log.info(f"Adapter '{adapter_name}' applied to model")
            return model

        except ImportError:
            log.warning("peft 패키지 미설치 — 어댑터 적용 건너뜀")
            return model

    def detach(self, model: Any) -> Any:
        """현재 활성 어댑터 비활성화."""
        if hasattr(model, 'disable_adapter'):
            model.disable_adapter()
        self._active.pop(id(model), None)
        return model

    def scan_directory(self, base_dir: str) -> int:
        """디렉토리 내 모든 어댑터 자동 등록.

        구조: base_dir/adapter_name/adapter_config.json + *.safetensors
        """
        base = Path(base_dir)
        if not base.exists():
            return 0

        count = 0
        for subdir in base.iterdir():
            if not subdir.is_dir():
                continue
            config = subdir / "adapter_config.json"
            safetensors = list(subdir.glob("*.safetensors"))
            if config.exists() or safetensors:
                self.register(subdir.name, str(subdir))
                count += 1

        log.info(f"Scanned {base_dir}: {count} adapters found")
        return count
