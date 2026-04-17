# core/ — 공용 로컬 LLM 코어
# 모든 프로젝트(Product Design AI, Drawing AI, ...)가 공유하는 핵심 모듈
#
# 구조:
#   core/
#   ├── schemas/                  스키마 정의 (JSON Schema + Python dataclass)
#   │   ├── _common.json          공통 intent/critique/patch 스키마
#   │   └── projects/             프로젝트별 engine_params 스키마
#   │       ├── product_design.json
#   │       └── drawing_ai.json
#   ├── schema_registry.py        스키마 로딩/검증/매핑
#   ├── intent_parser.py          자연어 → ParsedIntent (v1: 규칙, v2: LLM)
#   ├── variant_generator.py      ParsedIntent → Variant[] 파라미터 조합
#   ├── critique_ranker.py        Variant[] → Critique[] 평가/랭킹
#   ├── delta_patch.py            수정 요청 → PatchOperation[] (전체 재생성 금지)
#   ├── memory_log.py             세션 기록 파이프라인
#   └── models.py                 공용 데이터 모델 (ParsedIntent, Variant, etc.)
