"""
context/context_assembler.py - 프롬프트 조립

task_type별로 system prompt + context + schema 설명을 조립.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..core.envelope import RequestEnvelope, Message


_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


class ContextAssembler:
    """요청을 vLLM에 보낼 messages 배열로 조립"""

    def build_messages(
        self,
        request: RequestEnvelope,
        schema: dict,
    ) -> list[dict]:
        """RequestEnvelope → [system, user] messages"""
        system_parts = []

        # 1. 공통 시스템 규칙
        system_parts.append(self._load_prompt("common", "system_base"))

        # 2. 프로그램별 시스템 규칙
        prog_system = self._load_prompt(request.program, "system")
        if prog_system:
            system_parts.append(prog_system)

        # 3. 태스크 전용 instruction
        task_prompt = self._load_prompt(request.program, request.task_name)
        if task_prompt:
            system_parts.append(task_prompt)

        # 4. 출력 스키마 설명
        system_parts.append(
            f"You MUST return ONLY valid JSON matching this schema:\n"
            f"```json\n{json.dumps(schema, indent=2, ensure_ascii=False)}\n```\n"
            f"No explanations. No markdown fences. JSON only."
        )

        system_content = "\n\n".join(p for p in system_parts if p)

        # 5. messages 조립
        messages = [{"role": "system", "content": system_content}]

        # 6. 요청의 기존 messages 추가
        for msg in request.messages:
            messages.append({"role": msg.role, "content": msg.content})

        return messages

    def build_repair_messages(
        self,
        original_messages: list[dict],
        raw_text: str,
        errors: list[str],
        schema: dict,
    ) -> list[dict]:
        """repair prompt 조립"""
        repair_base = self._load_prompt("repairs", "repair_base") or ""

        repair_content = (
            f"{repair_base}\n\n"
            f"Previous response failed validation:\n"
            + "\n".join(f"- {e}" for e in errors)
            + f"\n\nPrevious (invalid) response:\n{raw_text[:500]}\n\n"
            f"Schema:\n```json\n{json.dumps(schema, indent=2, ensure_ascii=False)}\n```\n"
            f"Return ONLY valid JSON. No explanations."
        )

        messages = original_messages.copy()
        messages.append({"role": "assistant", "content": raw_text[:500]})
        messages.append({"role": "user", "content": repair_content})
        return messages

    def _load_prompt(self, folder: str, name: str) -> str | None:
        """prompts/{folder}/{name}.txt 로드"""
        path = _PROMPT_DIR / folder / f"{name}.txt"
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
        return None
