"""
core/llm_backend.py - Ollama 기반 로컬 LLM 백엔드 (v2)

Ollama HTTP API를 통해 로컬 LLM 추론 + structured output(JSON Schema 강제).

핵심 원칙:
- LLM 출력은 항상 JSON Schema로 강제 (자유 텍스트 금지)
- 최대 토큰 512 이하 (의도 해석/비평/패치는 짧음)
- 외부 API 호출 없음 - 100% 로컬 Ollama
- LLM이 없어도 fallback(규칙 기반)으로 동작

사용법:
    from core.llm_backend import OllamaBackend
    llm = OllamaBackend(model="qwen2.5:7b")
    result = llm.generate_json(prompt, schema)

설치:
    1. Ollama 설치: https://ollama.com
    2. 모델 다운로드: ollama pull qwen2.5:7b
    3. Ollama 서버 실행: ollama serve (자동 시작 되어 있을 수 있음)
"""

from __future__ import annotations

import json
import urllib.request
import urllib.error
from typing import Any, Optional


class OllamaBackend:
    """
    Ollama HTTP API 래퍼.
    structured outputs(format=json_schema)로 JSON Schema 준수 출력 보장.
    외부 패키지 의존 없음 - urllib만 사용.
    """

    def __init__(
        self,
        model: str = "qwen2.5:7b",
        base_url: str = "http://localhost:11434",
        timeout: int = 60,
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # ------------------------------------------------------------------
    # 핵심: JSON Schema 강제 출력
    # ------------------------------------------------------------------

    def generate_json(
        self,
        prompt: str,
        schema: dict,
        max_tokens: int = 512,
        temperature: float = 0.1,
    ) -> dict:
        """
        프롬프트를 보내고 JSON Schema를 준수하는 출력만 반환.
        Ollama의 structured outputs (format 파라미터) 사용.

        Args:
            prompt: 사용자 프롬프트
            schema: 출력이 준수해야 할 JSON Schema
            max_tokens: 최대 생성 토큰 수
            temperature: 생성 온도 (낮을수록 결정적)

        Returns:
            파싱된 JSON dict
        """
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a structured output assistant. Output ONLY valid JSON matching the given schema. No explanations. Respond in Korean where text values are needed.",
                },
                {"role": "user", "content": prompt},
            ],
            "format": schema,  # Ollama structured outputs
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }

        result = self._request("/api/chat", payload)
        content = result["message"]["content"]
        return json.loads(content)

    # ------------------------------------------------------------------
    # 태스크별 프롬프트
    # ------------------------------------------------------------------

    def parse_intent(
        self,
        user_input: str,
        project_type: str,
        context: dict,
        intent_schema: dict,
    ) -> dict:
        """Intent Parser v2: 사용자 입력 -> ParsedIntent JSON"""
        prompt = f"""Parse the user's design request into structured intent.

Project type: {project_type}
Current context: {json.dumps(context, ensure_ascii=False)}
User request: {user_input}

Rules:
- intent_type: one of create_new, modify_existing, explore_variants, refine, undo, compare, select, delete, query
- target_object: the design element being acted on
- constraints: key-value pairs of user-specified constraints
- modification_scope: if modifying, which specific part (null if entire object)
- confidence: 0.0-1.0 how certain the parse is
- ambiguities: list of unclear parts in the request

Output ONLY the JSON."""

        return self.generate_json(prompt, intent_schema)

    def generate_critique(
        self,
        variant_params: dict,
        intent_constraints: dict,
        criteria: list[dict],
        critique_schema: dict,
    ) -> dict:
        """Critique v2: Variant 파라미터 -> 구조화된 비평 JSON"""
        criteria_text = "\n".join(
            f"- {c['name']} ({c.get('label', '')}): {c.get('description', '')}"
            for c in criteria
        )

        prompt = f"""Evaluate this design variant against the criteria.

Variant parameters: {json.dumps(variant_params, ensure_ascii=False)}
User constraints: {json.dumps(intent_constraints, ensure_ascii=False)}

Evaluation criteria:
{criteria_text}

For each criterion, give a score 0.0-1.0.
List specific strengths and weaknesses in Korean.

Output ONLY the JSON."""

        return self.generate_json(prompt, critique_schema)

    def interpret_patch(
        self,
        edit_request: str,
        current_params: dict,
        patch_schema: dict,
    ) -> dict:
        """Delta Patch v2: 수정 요청 -> PatchOperation[] JSON"""
        prompt = f"""Convert the user's edit request into patch operations.

Current parameters (partial): {json.dumps(_truncate_params(current_params), ensure_ascii=False)}
User edit request: {edit_request}

Rules:
- op_type: "set" for absolute values, "adjust" for relative changes, "remove" to delete, "add" to append
- path: JSON pointer format like /dimensions/overall_width_mm
- value: the new value or delta
- relative: true if value is relative to current (e.g., +10mm)

Do NOT regenerate the entire parameter set. Only output the minimal patch operations.

Output ONLY the JSON."""

        return self.generate_json(prompt, patch_schema)

    def generate_variants(
        self,
        intent: dict,
        base_params: dict,
        variant_schema: dict,
        n_variants: int = 3,
    ) -> dict:
        """Variant Generator v2: Intent -> n개 후보안 JSON"""
        prompt = f"""Generate {n_variants} design variant proposals based on the user's intent.

Intent: {json.dumps(intent, ensure_ascii=False)}
Base parameters: {json.dumps(_truncate_params(base_params), ensure_ascii=False)}

Rules:
- Each variant must have different trade-offs (cost vs quality, simple vs complex, etc.)
- Include description in Korean
- Include tags for each variant
- Include diff_from_base showing what changed

Output ONLY the JSON with {n_variants} variants."""

        return self.generate_json(prompt, variant_schema)

    # ------------------------------------------------------------------
    # HTTP 통신
    # ------------------------------------------------------------------

    def _request(self, endpoint: str, payload: dict) -> dict:
        """Ollama HTTP API 호출"""
        url = f"{self.base_url}{endpoint}"
        data = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as e:
            raise ConnectionError(
                f"Ollama 서버에 연결할 수 없습니다 ({self.base_url}).\n"
                f"Ollama가 실행 중인지 확인하세요: ollama serve\n"
                f"Error: {e}"
            )
        except json.JSONDecodeError as e:
            raise ValueError(f"Ollama 응답 JSON 파싱 실패: {e}")

    # ------------------------------------------------------------------
    # 상태 확인
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """Ollama 서버가 실행 중이고 모델이 있는지 확인"""
        try:
            url = f"{self.base_url}/api/tags"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = [m["name"] for m in data.get("models", [])]
                # 모델 이름이 정확히 일치하거나 prefix 일치
                return any(
                    m == self.model or m.startswith(self.model.split(":")[0])
                    for m in models
                )
        except Exception:
            return False

    def list_models(self) -> list[str]:
        """Ollama에 설치된 모델 목록"""
        try:
            url = f"{self.base_url}/api/tags"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return [m["name"] for m in data.get("models", [])]
        except Exception:
            return []

    def pull_model(self, model: Optional[str] = None) -> bool:
        """모델 다운로드 시작 (동기 - 시간 소요)"""
        model = model or self.model
        try:
            payload = {"name": model, "stream": False}
            self._request("/api/pull", payload)
            return True
        except Exception:
            return False


# ---------------------------------------------------------------------------
# 유틸
# ---------------------------------------------------------------------------

def _truncate_params(params: dict, max_keys: int = 20) -> dict:
    """프롬프트 길이 제한을 위해 params 축약"""
    result = {}
    count = 0
    for key, value in params.items():
        if count >= max_keys:
            result["_truncated"] = True
            break
        if isinstance(value, list) and len(value) > 3:
            result[key] = value[:3] + [f"... ({len(value)} items)"]
        elif isinstance(value, dict):
            result[key] = {k: v for i, (k, v) in enumerate(value.items()) if i < 5}
        else:
            result[key] = value
        count += 1
    return result


# ---------------------------------------------------------------------------
# 모델 가이드
# ---------------------------------------------------------------------------

class OllamaModelGuide:
    """
    Ollama 모델 선택 가이드.

    | 모델                | 크기   | 한국어 | JSON 안정성 | VRAM    |
    |---------------------|--------|--------|-------------|---------|
    | qwen2.5:7b          | 4.4GB  | 우수   | 우수        | 5-6GB   |
    | qwen2.5:3b          | 1.9GB  | 양호   | 양호        | 2-3GB   |
    | llama3.1:8b         | 4.7GB  | 양호   | 양호        | 5-6GB   |
    | phi3.5:latest       | 2.2GB  | 보통   | 우수        | 3-4GB   |
    | gemma2:9b           | 5.4GB  | 양호   | 양호        | 6-7GB   |

    추천: qwen2.5:7b (한국어 + JSON 안정성 최고)
    GPU 없으면: qwen2.5:3b (CPU에서도 동작)
    """

    RECOMMENDED = {
        "default": "qwen2.5:7b",
        "lightweight": "qwen2.5:3b",
        "cpu_only": "qwen2.5:3b",
        "best_korean": "qwen2.5:7b",
    }

    @classmethod
    def get_setup_instructions(cls, model: str = "qwen2.5:7b") -> str:
        return f"""
=== 로컬 LLM 설치 가이드 (v2 - Ollama) ===

1. Ollama 설치:
   https://ollama.com 에서 다운로드 후 설치

2. 모델 다운로드:
   ollama pull {model}

3. 서버 확인 (보통 자동 실행됨):
   ollama serve

4. 사용:
   from core.llm_backend import OllamaBackend
   llm = OllamaBackend(model="{model}")

   # 연결 확인
   print(llm.is_available())
   print(llm.list_models())

   # Intent Parser에 연결
   from core.intent_parser import IntentParserModule
   parser = IntentParserModule(registry, "product_design")
   parser.llm_backend = llm
"""
