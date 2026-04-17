"""
response_parser.py - LLM 응답 후처리

code fence 제거, JSON repair, enum normalize, malformed 감지.
runtime_llm_gateway/execution/output_stabilizer.py에서 검증된 로직 이식.
"""
from __future__ import annotations

import json
import re
from typing import Any


def extract_json(text: str) -> str:
    """LLM 출력에서 JSON 부분만 추출"""
    if not text or not text.strip():
        raise ValueError("Empty output")

    text = text.strip()

    # markdown 펜스 제거
    if "```" in text:
        match = re.search(r"```(?:json|JSON)?\s*\n?(.*?)```", text, re.DOTALL)
        if match:
            text = match.group(1).strip()

    if text.startswith("{") or text.startswith("["):
        return text

    # balanced brace 찾기
    start = text.find("{")
    if start >= 0:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            c = text[i]
            if esc:
                esc = False
                continue
            if c == "\\":
                esc = True
                continue
            if c == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]

    raise ValueError(f"No JSON found in: {text[:80]}")


def repair_json(text: str) -> str:
    """흔한 JSON 문법 오류 수정"""
    text = re.sub(r",\s*([}\]])", r"\1", text)  # trailing comma
    # truncated output
    open_b = text.count("{") - text.count("}")
    if open_b > 0:
        last_comma = text.rfind(",")
        last_brace = text.rfind("}")
        if last_comma > last_brace:
            text = text[:last_comma]
        text += "}" * open_b
    return text


def parse_llm_output(raw_text: str) -> tuple[dict | None, list[str]]:
    """raw LLM text → (parsed dict, repair log)"""
    repairs = []
    try:
        extracted = extract_json(raw_text)
    except ValueError as e:
        repairs.append(f"extract_failed: {e}")
        return None, repairs

    repaired = repair_json(extracted)
    if repaired != extracted:
        repairs.append("syntax_repaired")

    try:
        parsed = json.loads(repaired)
        return parsed, repairs
    except json.JSONDecodeError as e:
        repairs.append(f"parse_failed: {e}")
        return None, repairs
