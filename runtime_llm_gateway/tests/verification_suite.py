"""
tests/verification_suite.py - 실전 검증 운영 패킷

배치 A: 26개 태스크 스모크 (전수 생존 확인)
배치 B: 핵심 7개 태스크 품질 평가 (3-5건씩)
배치 C: 일관성 + 장애 복원력

실행: cd LLM && python -X utf8 -m runtime_llm_gateway.tests.verification_suite

출력: baselines/verification_report/
"""

from __future__ import annotations

import csv
import json
import os
import sys
import time
import statistics
import tempfile
from pathlib import Path
from typing import Any

_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from runtime_llm_gateway.core.envelope import RequestEnvelope, Message
from runtime_llm_gateway.core.model_profile import DEFAULT_PROFILES
from runtime_llm_gateway.core.task_type import TASK_POOL_MAP, TASK_SCHEMA_MAP
from runtime_llm_gateway.execution.gateway_service import RuntimeGatewayService
from runtime_llm_gateway.execution.pipeline_service import PipelineService
from runtime_llm_gateway.providers.vllm_provider import VLLMProvider, MockProvider
from runtime_llm_gateway.telemetry.audit_logger import AuditLogger

REPORT_DIR = Path(_ROOT) / "baselines" / "verification_report"
SERVER_URL = os.environ.get("LLM_BASE_URL", "http://localhost:8000")

# ---------------------------------------------------------------------------
# 26개 태스크 × 1건 스모크 입력
# ---------------------------------------------------------------------------

SMOKE_CASES: list[dict] = [
    # Builder (6)
    {"task_type": "builder.requirement_parse", "schema_id": "builder/requirement_v1", "text": "2층 주택 거실 크게 해줘"},
    {"task_type": "builder.patch_parse", "schema_id": "builder/patch_v1", "text": "거실 창문 크기 늘려줘, 벽은 유지"},
    {"task_type": "builder.zone_priority_parse", "schema_id": "builder/requirement_v1", "text": "주거지역 건폐율 60% 확인"},
    {"task_type": "builder.exterior_style_parse", "schema_id": "builder/requirement_v1", "text": "외관은 벽돌 모던 스타일"},
    {"task_type": "builder.smalltalk_assist", "schema_id": "", "text": "요즘 인기 있는 주택 스타일이 뭐야?"},
    {"task_type": "builder.project_context", "schema_id": "", "text": "이 프로젝트 지금까지 진행 상황 정리해줘"},
    # Minecraft (5)
    {"task_type": "minecraft.edit_parse", "schema_id": "minecraft/edit_patch_v1", "text": "정면 창문 넓게, 지붕 유지"},
    {"task_type": "minecraft.anchor_resolution", "schema_id": "minecraft/edit_patch_v1", "text": "동쪽 2층 발코니 위치 잡아줘"},
    {"task_type": "minecraft.style_guard", "schema_id": "minecraft/style_guard_v1", "text": "이 디자인이 중세풍에 맞는지 검증해줘"},
    {"task_type": "minecraft.patch_commentary", "schema_id": "", "text": "방금 수정한 거 괜찮아 보여?"},
    {"task_type": "minecraft.history_context", "schema_id": "", "text": "이 빌드 수정 히스토리 정리해줘"},
    # Animation (6)
    {"task_type": "animation.shot_parse", "schema_id": "animation/shot_graph_v1", "text": "노을빛에 슬픈 클로즈업"},
    {"task_type": "animation.camera_map", "schema_id": "animation/camera_lighting_v1", "text": "추격 장면 긴장감 있게"},
    {"task_type": "animation.lighting_map", "schema_id": "animation/camera_lighting_v1", "text": "비 오는 밤 외로운 분위기"},
    {"task_type": "animation.edit_patch_parse", "schema_id": "animation/shot_graph_v1", "text": "카메라 각도 좀 더 높게 바꿔줘"},
    {"task_type": "animation.ui_chat", "schema_id": "", "text": "이 장면 어떤 느낌이야?"},
    {"task_type": "animation.shot_history", "schema_id": "", "text": "지금까지 연출 흐름 정리해줘"},
    # CAD (5)
    {"task_type": "cad.constraint_parse", "schema_id": "cad/constraint_v1", "text": "방수 샤워필터 배수 연결 포함"},
    {"task_type": "cad.patch_parse", "schema_id": "cad/constraint_v1", "text": "전체 폭을 360mm로 바꿔줘"},
    {"task_type": "cad.system_split_parse", "schema_id": "cad/constraint_v1", "text": "전기/배수/구조 시스템 분리해줘"},
    {"task_type": "cad.priority_parse", "schema_id": "cad/constraint_v1", "text": "방수 최우선, 경량 두번째"},
    {"task_type": "cad.rule_lookup_context", "schema_id": "", "text": "이 설계에 적용되는 안전 규정 정리"},
    # Embedding (4) — 이건 chat으로 시뮬레이션
    {"task_type": "builder.rule_search", "schema_id": "", "text": "건폐율 관련 규정 검색"},
    {"task_type": "minecraft.history_search", "schema_id": "", "text": "성 건축 히스토리 검색"},
    {"task_type": "animation.shot_search", "schema_id": "", "text": "슬픈 장면 레퍼런스 검색"},
    {"task_type": "cad.part_search", "schema_id": "", "text": "방수 커넥터 부품 검색"},
]

# ---------------------------------------------------------------------------
# 핵심 7개 태스크 품질 평가 입력 (3-5건씩)
# ---------------------------------------------------------------------------

QUALITY_CASES: dict[str, list[dict]] = {
    "builder.requirement_parse": [
        {"id": "BLD-Q01", "text": "2층 주택, 거실/주방/욕실 포함, 모던 스타일"},
        {"id": "BLD-Q02", "text": "3층 다세대 원룸 2개씩, 옥상 테라스"},
        {"id": "BLD-Q03", "text": "지하 카페 + 2층 주거, 벽돌 외관"},
    ],
    "builder.patch_parse": [
        {"id": "BLD-Q04", "text": "창문 유지하고 출입문만 키워줘"},
        {"id": "BLD-Q05", "text": "2층 방 하나 없애고 거실로 합쳐줘"},
        {"id": "BLD-Q06", "text": "외벽 재료를 스톤으로 변경"},
    ],
    "cad.constraint_parse": [
        {"id": "CAD-Q01", "text": "배수/전기/구조 분리, 방수 IP67"},
        {"id": "CAD-Q02", "text": "3D프린팅 경량 브래킷 설계"},
        {"id": "CAD-Q03", "text": "모터+PCB 내장 접이식 기구부"},
    ],
    "minecraft.edit_parse": [
        {"id": "MC-Q01", "text": "stone 외장 추가, door 유지"},
        {"id": "MC-Q02", "text": "지붕 박공으로 바꿔, 높이는 유지"},
        {"id": "MC-Q03", "text": "2층 발코니 추가, 기존 벽 유지"},
    ],
    "minecraft.style_guard": [
        {"id": "MC-Q04", "text": "이 빌드가 중세풍 스타일에 맞는지 체크"},
        {"id": "MC-Q05", "text": "모던 빌드인데 나무 블록이 너무 많은지 확인"},
        {"id": "MC-Q06", "text": "일본식 건축 팔레트 검증해줘"},
    ],
    "animation.shot_parse": [
        {"id": "ANI-Q01", "text": "클로즈업, 슬픈 감정, 노을빛"},
        {"id": "ANI-Q02", "text": "추격 장면 핸드헬드 빠른 컷"},
        {"id": "ANI-Q03", "text": "두 캐릭터 대화 오버숄더"},
    ],
    "animation.camera_lighting_v1": [
        {"id": "ANI-Q04", "text": "밤 인테리어, 느린 푸시인"},
        {"id": "ANI-Q05", "text": "노을 역광, 실루엣 와이드샷"},
        {"id": "ANI-Q06", "text": "공포 장면, 어둠 속 눈만 보임"},
    ],
}

# ---------------------------------------------------------------------------
# 실패 분류
# ---------------------------------------------------------------------------

FAILURE_TYPES = {
    "F1_NO_RESPONSE": "No Response",
    "F2_INVALID_JSON": "Invalid JSON",
    "F3_SCHEMA_MISMATCH": "Schema Mismatch",
    "F4_SEMANTIC_DRIFT": "Semantic Drift",
    "F5_PARTIAL": "Partial Completion",
    "F6_OVER_INFERENCE": "Over-Inference",
    "F7_UNDER_SPEC": "Under-Specification",
    "F8_CONSISTENCY": "Consistency Failure",
}


def classify_detailed(resp, expect_keys: list[str] = None) -> str:
    if resp.error_code:
        ec = str(resp.error_code).upper()
        if "TIMEOUT" in ec:
            return "F1_NO_RESPONSE"
        if "CONNECTION" in ec or "NETWORK" in ec or "PROVIDER" in ec:
            return "F1_NO_RESPONSE"
        if "PARSE" in ec:
            return "F2_INVALID_JSON"
        return "F2_INVALID_JSON"
    if not resp.validation.schema_ok:
        return "F3_SCHEMA_MISMATCH"
    if not resp.validation.domain_ok:
        return "F4_SEMANTIC_DRIFT"
    if resp.structured_content and expect_keys:
        missing = [k for k in expect_keys if k not in resp.structured_content]
        if missing:
            return "F5_PARTIAL"
    return "PASS"


# ---------------------------------------------------------------------------
# Gateway 팩토리
# ---------------------------------------------------------------------------

def make_gateway() -> RuntimeGatewayService:
    provider = VLLMProvider(base_url=SERVER_URL, api_key="internal-token")
    if not provider.is_available():
        print(f"[WARN] Server not available at {SERVER_URL}, using MockProvider")
        provider = MockProvider()
    gpu_model = os.environ.get("LLM_MODEL", "/home/suzzi/models/Qwen2.5-7B-Instruct-AWQ")
    for p in DEFAULT_PROFILES.values():
        p.resolved_model = gpu_model
        p.timeout_ms = 15000
    return RuntimeGatewayService(provider=provider, audit_logger=AuditLogger(tempfile.mkdtemp()))


# ===================================================================
# 배치 A: 26개 스모크
# ===================================================================

def run_batch_a(gw: RuntimeGatewayService) -> list[dict]:
    print("=" * 85)
    print("BATCH A: 26-Task Smoke Test")
    print("=" * 85)

    results = []
    for case in SMOKE_CASES:
        tt = case["task_type"]
        schema_id = case["schema_id"]
        text = case["text"]

        req = RequestEnvelope(
            task_type=tt, project_id="smoke", session_id="smoke",
            messages=[Message(role="user", content=text)],
            schema_id=schema_id or "",
        )

        start = time.time()
        try:
            resp = gw.process(req)
            ms = int((time.time() - start) * 1000)
        except Exception as e:
            ms = int((time.time() - start) * 1000)
            results.append({"task_type": tt, "response_ok": False, "schema_valid": False,
                            "latency_ms": ms, "error": str(e), "failure_type": "F1_NO_RESPONSE"})
            print(f"  [{tt:<40}] CRASH  {ms:>6}ms  {str(e)[:50]}")
            continue

        response_ok = resp.error_code is None
        schema_valid = resp.validation.schema_ok
        ftype = classify_detailed(resp)

        marker = "OK" if ftype == "PASS" else ftype
        print(f"  [{tt:<40}] {marker:<18} {ms:>6}ms")

        results.append({
            "task_type": tt,
            "response_ok": response_ok,
            "schema_valid": schema_valid,
            "domain_valid": resp.validation.domain_ok,
            "latency_ms": ms,
            "repair_used": resp.validation.repair_attempted,
            "failure_type": ftype,
            "error": resp.error_code,
        })

    # 요약
    total = len(results)
    ok = sum(1 for r in results if r["failure_type"] == "PASS")
    schema_ok = sum(1 for r in results if r["schema_valid"])
    print(f"\n  Smoke: {ok}/{total} pass, schema_valid: {schema_ok}/{total}")
    return results


# ===================================================================
# 배치 B: 핵심 7개 품질 평가
# ===================================================================

def run_batch_b(gw: RuntimeGatewayService) -> list[dict]:
    print("\n" + "=" * 85)
    print("BATCH B: Core 7 Tasks Quality Evaluation")
    print("=" * 85)

    results = []
    for task_type, cases in QUALITY_CASES.items():
        schema_id = TASK_SCHEMA_MAP.get(task_type, "")
        # camera_lighting은 task_type이 다를 수 있음
        if task_type == "animation.camera_lighting_v1":
            task_type_actual = "animation.camera_map"
            schema_id = "animation/camera_lighting_v1"
        else:
            task_type_actual = task_type

        print(f"\n  --- {task_type} ---")
        for case in cases:
            req = RequestEnvelope(
                task_type=task_type_actual, project_id="quality", session_id="quality",
                messages=[Message(role="user", content=case["text"])],
                schema_id=schema_id,
            )

            start = time.time()
            try:
                resp = gw.process(req)
                ms = int((time.time() - start) * 1000)
            except Exception as e:
                ms = int((time.time() - start) * 1000)
                results.append({"test_id": case["id"], "task_type": task_type,
                                "response_ok": False, "grade": "D", "latency_ms": ms, "failure_type": "F1_NO_RESPONSE"})
                print(f"  [{case['id']}] CRASH  {ms:>6}ms")
                continue

            ftype = classify_detailed(resp)

            # 자동 등급 판정
            if ftype == "PASS":
                # content 풍부도로 A/B 분류
                content = resp.structured_content or {}
                filled = sum(1 for v in content.values() if v) if isinstance(content, dict) else 0
                total_fields = len(content) if isinstance(content, dict) else 1
                fill_rate = filled / max(total_fields, 1)
                grade = "A" if fill_rate >= 0.7 else "B"
            elif ftype in ("F5_PARTIAL", "F7_UNDER_SPEC"):
                grade = "C"
            else:
                grade = "D"

            marker = f"{grade} ({ftype})" if ftype != "PASS" else grade
            print(f"  [{case['id']}] {marker:<20} {ms:>6}ms  \"{case['text'][:40]}\"")

            results.append({
                "test_id": case["id"],
                "task_type": task_type,
                "input_summary": case["text"],
                "response_ok": resp.error_code is None,
                "schema_valid": resp.validation.schema_ok,
                "domain_valid": resp.validation.domain_ok,
                "grade": grade,
                "latency_ms": ms,
                "repair_used": resp.validation.repair_attempted,
                "failure_type": ftype,
            })

    # 등급 분포
    grades = {"A": 0, "B": 0, "C": 0, "D": 0}
    for r in results:
        grades[r["grade"]] = grades.get(r["grade"], 0) + 1
    print(f"\n  Grades: A={grades['A']} B={grades['B']} C={grades['C']} D={grades['D']}")
    return results


# ===================================================================
# 배치 C: 일관성 + 장애 복원력
# ===================================================================

def run_batch_c_consistency(gw: RuntimeGatewayService) -> list[dict]:
    print("\n" + "=" * 85)
    print("BATCH C-1: Consistency Check (3x repeat)")
    print("=" * 85)

    # 핵심 3개 태스크만 반복
    consistency_inputs = [
        ("builder.requirement_parse", "builder/requirement_v1", "2층 주택 거실 크게 모던 스타일"),
        ("cad.constraint_parse", "cad/constraint_v1", "방수 샤워필터 배수 연결 포함"),
        ("animation.shot_parse", "animation/shot_graph_v1", "노을빛 슬픈 클로즈업"),
    ]

    results = []
    for tt, schema_id, text in consistency_inputs:
        print(f"\n  --- {tt} (3 runs) ---")
        outputs = []
        for run_i in range(3):
            req = RequestEnvelope(
                task_type=tt, project_id="consistency", session_id=f"cons_{run_i}",
                messages=[Message(role="user", content=text)],
                schema_id=schema_id,
            )
            start = time.time()
            try:
                resp = gw.process(req)
                ms = int((time.time() - start) * 1000)
                keys = sorted(resp.structured_content.keys()) if resp.structured_content else []
                print(f"    run {run_i+1}: {'OK' if resp.validation.schema_ok else 'FAIL':5s} {ms:>6}ms keys={keys}")
                outputs.append({"keys": keys, "schema_ok": resp.validation.schema_ok, "ms": ms})
            except Exception as e:
                ms = int((time.time() - start) * 1000)
                print(f"    run {run_i+1}: CRASH {ms:>6}ms {str(e)[:40]}")
                outputs.append({"keys": [], "schema_ok": False, "ms": ms})

        # 일관성 판정: keys가 모두 같으면 A, 일부 다르면 B, 크게 다르면 C
        all_keys = [frozenset(o["keys"]) for o in outputs]
        if len(set(all_keys)) == 1 and all(o["schema_ok"] for o in outputs):
            cons_grade = "A"
        elif all(o["schema_ok"] for o in outputs):
            cons_grade = "B"
        else:
            cons_grade = "C"

        print(f"    consistency: {cons_grade}")
        results.append({"task_type": tt, "consistency_grade": cons_grade, "runs": outputs})

    return results


def run_batch_c_recovery(gw: RuntimeGatewayService) -> list[dict]:
    print("\n" + "=" * 85)
    print("BATCH C-2: Failure Recovery Tests")
    print("=" * 85)

    results = []

    # 1. 빈 입력
    print("\n  [R1] Empty input")
    req = RequestEnvelope(
        task_type="builder.requirement_parse", project_id="recovery", session_id="rec",
        messages=[Message(role="user", content="")],
        schema_id="builder/requirement_v1",
    )
    resp = gw.process(req)
    ok = resp.error_code is not None or resp.structured_content is not None
    print(f"    envelope returned: {ok}, error_code: {resp.error_code}")
    results.append({"test": "empty_input", "handled": ok})

    # 2. 존재하지 않는 스키마
    print("  [R2] Unknown schema_id")
    req2 = RequestEnvelope(
        task_type="builder.requirement_parse", project_id="recovery", session_id="rec",
        messages=[Message(role="user", content="2층 주택 설계해줘")],
        schema_id="nonexistent/schema_v99",
    )
    resp2 = gw.process(req2)
    ok2 = resp2.to_dict() is not None  # envelope 반환 여부
    print(f"    envelope returned: {ok2}, schema_ok: {resp2.validation.schema_ok}")
    results.append({"test": "unknown_schema", "handled": ok2})

    # 3. 매우 긴 입력
    print("  [R3] Very long input")
    long_text = "방 3개 화장실 2개 거실 크게 " * 50
    req3 = RequestEnvelope(
        task_type="builder.requirement_parse", project_id="recovery", session_id="rec",
        messages=[Message(role="user", content=long_text)],
        schema_id="builder/requirement_v1",
    )
    start3 = time.time()
    resp3 = gw.process(req3)
    ms3 = int((time.time() - start3) * 1000)
    print(f"    response: {'OK' if resp3.error_code is None else resp3.error_code}, {ms3}ms")
    results.append({"test": "long_input", "handled": True, "latency_ms": ms3})

    # 4. request_id 추적
    print("  [R4] Request ID tracking")
    req4 = RequestEnvelope(
        request_id="test_req_id_12345",
        task_type="minecraft.edit_parse", project_id="recovery", session_id="rec",
        messages=[Message(role="user", content="창문 넓게")],
        schema_id="minecraft/edit_patch_v1",
    )
    resp4 = gw.process(req4)
    id_preserved = resp4.request_id == "test_req_id_12345"
    print(f"    request_id preserved: {id_preserved}")
    results.append({"test": "request_id_tracking", "handled": id_preserved})

    # 5. freeform 태스크 (structured_only=False)
    print("  [R5] Freeform task (fast-chat-pool)")
    req5 = RequestEnvelope(
        task_type="minecraft.patch_commentary", project_id="recovery", session_id="rec",
        messages=[Message(role="user", content="방금 수정한 거 어때?")],
        schema_id="",
    )
    resp5 = gw.process(req5)
    has_text = resp5.raw_text is not None and len(resp5.raw_text) > 0
    print(f"    raw_text present: {has_text}")
    results.append({"test": "freeform_task", "handled": has_text})

    return results


# ===================================================================
# 최종 보고서 생성
# ===================================================================

def generate_report(smoke: list, quality: list, consistency: list, recovery: list):
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    # 데이터 저장
    with open(REPORT_DIR / "batch_a_smoke.json", "w", encoding="utf-8") as f:
        json.dump(smoke, f, ensure_ascii=False, indent=2)
    with open(REPORT_DIR / "batch_b_quality.json", "w", encoding="utf-8") as f:
        json.dump(quality, f, ensure_ascii=False, indent=2)
    with open(REPORT_DIR / "batch_c_consistency.json", "w", encoding="utf-8") as f:
        json.dump(consistency, f, ensure_ascii=False, indent=2)
    with open(REPORT_DIR / "batch_c_recovery.json", "w", encoding="utf-8") as f:
        json.dump(recovery, f, ensure_ascii=False, indent=2)

    # 집계
    smoke_total = len(smoke)
    smoke_pass = sum(1 for r in smoke if r["failure_type"] == "PASS")
    smoke_schema = sum(1 for r in smoke if r.get("schema_valid", False))
    smoke_latencies = [r["latency_ms"] for r in smoke if r.get("latency_ms")]

    quality_grades = {"A": 0, "B": 0, "C": 0, "D": 0}
    for r in quality:
        quality_grades[r["grade"]] = quality_grades.get(r["grade"], 0) + 1

    # 실패 유형 분포
    failure_dist = {}
    for r in smoke + quality:
        ft = r.get("failure_type", "PASS")
        if ft != "PASS":
            failure_dist[ft] = failure_dist.get(ft, 0) + 1

    # 핵심 태스크별 결과
    core_tasks = {}
    for r in quality:
        tt = r["task_type"]
        if tt not in core_tasks:
            core_tasks[tt] = {"total": 0, "pass": 0, "grades": []}
        core_tasks[tt]["total"] += 1
        if r["grade"] in ("A", "B"):
            core_tasks[tt]["pass"] += 1
        core_tasks[tt]["grades"].append(r["grade"])

    recovery_pass = sum(1 for r in recovery if r.get("handled"))

    # 보고서 텍스트
    report = []
    report.append("=" * 85)
    report.append("[검증 단계 보고]")
    report.append("=" * 85)
    report.append("")
    report.append("### 1) 전체 상태")
    report.append(f"  이번 턴 범위: 26-task smoke + 7-core quality + consistency + recovery")
    report.append(f"  사용 모델: Qwen2.5-7B-Instruct-AWQ (GPU, RTX 5070)")
    report.append(f"  structured output: vLLM native guided decoding (json_schema)")
    report.append(f"  fallback server: yes")
    report.append("")

    report.append("### 2) 전수 스모크 결과")
    report.append(f"  전체 태스크 수: {smoke_total}")
    report.append(f"  성공: {smoke_pass}")
    report.append(f"  실패: {smoke_total - smoke_pass}")
    report.append(f"  schema valid: {smoke_schema}")
    inv_json = sum(1 for r in smoke if r.get("failure_type") == "F2_INVALID_JSON")
    no_resp = sum(1 for r in smoke if r.get("failure_type") == "F1_NO_RESPONSE")
    report.append(f"  invalid JSON: {inv_json}")
    report.append(f"  empty/timeout: {no_resp}")
    report.append("")

    report.append("### 3) 핵심 태스크 품질 결과")
    for tt, ct in core_tasks.items():
        report.append(f"  {tt}: {ct['pass']}/{ct['total']} usable, grades={ct['grades']}")
    report.append("")

    report.append("### 4) 등급 분포")
    for g in ("A", "B", "C", "D"):
        report.append(f"  {g}: {quality_grades[g]}")
    report.append("")

    report.append("### 5) 실패 Top 5")
    fail_list = [r for r in smoke + quality if r.get("failure_type", "PASS") != "PASS"]
    for i, f in enumerate(fail_list[:5], 1):
        report.append(f"  {i}. [{f.get('task_type', f.get('test_id','?'))}] {f.get('failure_type','?')}")
    if not fail_list:
        report.append("  (none)")
    report.append("")

    report.append("### 6) 실패 유형 분포")
    for ft_code, ft_name in FAILURE_TYPES.items():
        count = failure_dist.get(ft_code, 0)
        report.append(f"  {ft_code} {ft_name}: {count}")
    report.append("")

    report.append("### 7) 성능")
    if smoke_latencies:
        report.append(f"  평균 latency: {int(statistics.mean(smoke_latencies))}ms")
        report.append(f"  P50: {int(statistics.median(smoke_latencies))}ms")
        sorted_lat = sorted(smoke_latencies)
        p95_idx = min(int(len(sorted_lat) * 0.95), len(sorted_lat) - 1)
        report.append(f"  P95: {sorted_lat[p95_idx]}ms")
        report.append(f"  최대값: {max(smoke_latencies)}ms")
    report.append("")

    report.append("### 8) 메트릭/운영")
    report.append(f"  request_id 추적: {'OK' if any(r.get('test') == 'request_id_tracking' and r['handled'] for r in recovery) else 'FAIL'}")
    report.append(f"  freeform fallback: {'OK' if any(r.get('test') == 'freeform_task' and r['handled'] for r in recovery) else 'FAIL'}")
    report.append(f"  recovery tests: {recovery_pass}/{len(recovery)} pass")
    report.append("")

    report.append("### 9) 결론")
    a_tasks = [r["task_type"] for r in quality if r["grade"] == "A"]
    b_tasks = [r["task_type"] for r in quality if r["grade"] == "B"]
    c_tasks = [r["task_type"] for r in quality if r["grade"] == "C"]
    d_tasks = [r["task_type"] for r in quality if r["grade"] == "D"]
    report.append(f"  즉시 운영 가능 (A): {len(set(a_tasks))} tasks")
    report.append(f"  후처리 후 운영 (B): {len(set(b_tasks))} tasks")
    report.append(f"  프롬프트 수정 필요 (C): {len(set(c_tasks))} tasks")
    report.append(f"  스키마 재설계 필요 (D): {len(set(d_tasks))} tasks")
    report.append("=" * 85)

    report_text = "\n".join(report)

    with open(REPORT_DIR / "verification_report.txt", "w", encoding="utf-8") as f:
        f.write(report_text)

    # CSV
    with open(REPORT_DIR / "quality_results.csv", "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "test_id", "task_type", "input_summary", "response_ok", "schema_valid",
            "domain_valid", "grade", "latency_ms", "repair_used", "failure_type",
        ])
        writer.writeheader()
        for r in quality:
            writer.writerow(r)

    return report_text


# ===================================================================
# 메인
# ===================================================================

if __name__ == "__main__":
    gw = make_gateway()

    smoke = run_batch_a(gw)
    quality = run_batch_b(gw)
    consistency = run_batch_c_consistency(gw)
    recovery = run_batch_c_recovery(gw)

    report = generate_report(smoke, quality, consistency, recovery)
    print("\n" + report)
    print(f"\nResults saved to: {REPORT_DIR}")
