# Domain-Specialized LLM Orchestration Layer

## Overview

VLLM 은 범용 LLM 서빙이 아니라 **4개 상위 AI (CAD / Builder / Minecraft / Animation) 전용 도메인 오케스트레이션 레이어**. 자연어 입력을 도메인별 전문 컨텍스트로 해석하고, 도메인에 맞는 구조화된 출력을 안정적으로 생성.

## Architecture

```
POST /tasks/orchestrate {"user_input": "충전식 샤워필터 설계, 방수 IP67"}
  │
  ▼
[A] DomainClassifier (rule-based, LLM 0회)
  → domain=cad, confidence=0.72, task=constraint_parse
  │
  ▼
[B] DomainProfile 로드 (configs/domain_profiles.json)
  → reasoning_template, vocabulary, constraint_fields, required_output_keys
  │
  ▼
[C] RequirementExtractor (rule-based, LLM 0회)
  → hard_constraints=["방수", "IP67"], domain_specific={dimensions: "...", material: "..."}
  │
  ▼
[D] OrchestratedPipeline._build_enriched_prompt()
  → reasoning_template + 제약 요약 + 기존 도메인 프롬프트 결합
  │
  ▼
  기존 Dispatcher.dispatch(system_prompt_override=enriched)
  → 기존 5-gate review (무변경)
  │
  ▼
[E] DomainEvaluator (rule-based, LLM 0회)
  → constraint_coverage, terminology_accuracy, actionability, ...
  → needs_repair=True 이면 repair pass (LLM 1회 추가)
  │
  ▼
[F] DomainTelemetry 기록
  → OrchestrationResult 반환
```

## Components

| Component | File | Role | LLM 호출 |
|---|---|---|---|
| DomainClassifier | `src/app/orchestration/domain_classifier.py` | 가중 키워드 기반 도메인 분류 | 0회 |
| DomainProfile | `src/app/domain/profiles.py` + `configs/domain_profiles.json` | 도메인별 어휘/추론/스키마/검증 프로필 | N/A |
| RequirementExtractor | `src/app/orchestration/requirement_extractor.py` | regex/키워드 기반 하드/소프트 제약 추출 | 0회 |
| OrchestratedPipeline | `src/app/orchestration/orchestrated_pipeline.py` | 전체 파이프라인 조율 | 1-2회 |
| DomainEvaluator | `src/app/review/domain_evaluator.py` | 6번째 평가 레이어 (기존 5-gate 이후) | 0회 |
| DomainTelemetry | `src/app/observability/domain_telemetry.py` | 관측 기록 | N/A |

## Endpoints

| Endpoint | 역할 | 필수 입력 |
|---|---|---|
| `POST /tasks/submit` (기존) | domain+task_name 직접 지정 | `{domain, task_name, user_input}` |
| `POST /tasks/orchestrate` (신규) | 자연어만 입력, 자동 분류 | `{user_input, context?}` |

## Domain Profiles

`configs/domain_profiles.json` 에 4개 도메인이 선언적으로 정의:

- **CAD**: geometry/topology/constraint/connectivity/manufacturability/electrical/drainage
- **Builder**: rule search/zoning/plan interpretation/structural/MEP/constructability
- **Minecraft**: build composition/silhouette/palette/block grammar/gameplay-context
- **Animation**: storyboard/shot planning/acting/expression/continuity/redraw-conditioning

각 프로필은 vocabulary (가중 키워드), task_signals, reasoning_template, constraint_fields, required_output_keys, validation_checklist, fail_modes 를 포함.

## Evaluation Scores

DomainEvaluator 가 반환하는 6개 점수:

| Score | Weight | 의미 |
|---|---|---|
| constraint_coverage | 0.30 | 추출된 하드 제약이 출력에 반영된 비율 |
| terminology_accuracy | 0.20 | 도메인 전문 용어 사용 정확도 |
| output_schema_compliance | 0.20 | 필수 출력 키 존재 여부 |
| actionability | 0.15 | placeholder/generic 아닌 실제 값 비율 |
| hallucination_risk | 0.10 | 입력에 없는 숫자 생성 비율 (역전) |
| domain_match | 0.05 | 분류 도메인과 실행 도메인 일치 여부 |

overall_score < 0.5 이면 repair pass 실행 (LLM 1회 추가, 누락 제약 보완).

## 기존 시스템과의 관계

- **기존 5-gate review** (schema/language/semantic/domain_guard/contract): **무변경**. DomainEvaluator 는 이 5-gate **이후** 추가 실행되는 6번째 레이어.
- **기존 Router/Dispatcher**: **무변경**. `system_prompt_override` optional param 만 추가 (None 이면 기존 동작).
- **기존 telemetry** (CaseTelemetry/RunTelemetry): **무변경**. DomainTelemetry 는 별도 레코드.
- **기존 gate tests (498개)**: 전부 green 유지.
