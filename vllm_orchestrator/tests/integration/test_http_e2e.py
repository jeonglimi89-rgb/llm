"""
test_http_e2e.py — 실제 FastAPI 서버 HTTP 경유 E2E

서버 프로세스 기동 → HTTP 호출 → 결과 검증 → 서버 종료

infra-dependent (T-tranche-6, 2026-04-08):
    Every test in this file spawns a FastAPI subprocess on port 8100 and
    exercises it over HTTP. It is NOT part of the default deterministic
    gate. Run explicitly with ``pytest -m infra`` when the infra slot is
    available. See pytest.ini and docs/testing_gate.md.
"""
import sys, json, time, subprocess, urllib.request, urllib.error, signal, os
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

pytestmark = pytest.mark.infra

SERVER_PORT = 8100
SERVER_URL = f"http://127.0.0.1:{SERVER_PORT}"


def start_server():
    """FastAPI 서버 백그라운드 시작"""
    env = os.environ.copy()
    env["PORT"] = str(SERVER_PORT)
    proc = subprocess.Popen(
        [sys.executable, "-m", "src.app.main"],
        cwd=str(Path(__file__).resolve().parent.parent.parent),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # 서버 기동 대기
    for _ in range(20):
        time.sleep(1)
        try:
            urllib.request.urlopen(f"{SERVER_URL}/health/live", timeout=2)
            return proc
        except Exception:
            pass
    proc.kill()
    raise RuntimeError("Server failed to start")


def stop_server(proc):
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def http_get(path: str) -> dict:
    r = urllib.request.urlopen(f"{SERVER_URL}{path}", timeout=10)
    return json.loads(r.read().decode())


def http_post(path: str, body: dict) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(f"{SERVER_URL}{path}", data=data,
                                 headers={"Content-Type": "application/json"}, method="POST")
    r = urllib.request.urlopen(req, timeout=180)
    return json.loads(r.read().decode())


# ===================================================================

@pytest.fixture(scope="module")
def proc():
    """Start/stop the FastAPI server for the test module."""
    p = start_server()
    yield p
    stop_server(p)


def test_health_live(proc):
    print("  [1] GET /health/live")
    result = http_get("/health/live")
    assert result["status"] == "alive"
    print(f"    OK: {result}")


def test_health_ready(proc):
    print("  [2] GET /health/ready")
    result = http_get("/health/ready")
    assert result["status"] == "ready", f"Not ready: {result}"
    print(f"    OK: {result}")


def test_health_detail(proc):
    print("  [3] GET /health/detail")
    result = http_get("/health/detail")
    assert "health" in result
    assert "queue" in result
    assert "circuit" in result
    print(f"    OK: keys={list(result.keys())}")


def test_submit_task(proc):
    print("  [4] POST /tasks/submit (mock LLM)")
    result = http_post("/tasks/submit", {
        "domain": "minecraft",
        "task_name": "edit_parse",
        "user_input": "정면 창문 넓게",
        "priority": "normal",
    })
    assert "request_id" in result, f"No request_id: {result}"
    assert "task_id" in result
    assert result["status"] in ("done", "error", "degraded"), f"Unexpected status: {result['status']}"
    print(f"    OK: status={result['status']}, task_id={result['task_id']}")
    if result.get("slots"):
        print(f"    slots: {json.dumps(result['slots'], ensure_ascii=False)[:80]}")


def test_submit_builder(proc):
    print("  [5] POST /tasks/submit builder.requirement_parse")
    result = http_post("/tasks/submit", {
        "domain": "builder",
        "task_name": "requirement_parse",
        "user_input": "2층 주택 거실 크게",
    })
    assert result.get("status") in ("done", "error", "degraded")
    print(f"    OK: status={result['status']}")


def test_submit_invalid_task(proc):
    print("  [6] POST /tasks/submit invalid task_type → 400")
    try:
        http_post("/tasks/submit", {
            "domain": "nonexistent",
            "task_name": "fake_task",
            "user_input": "test",
        })
        print("    FAIL: expected 400")
    except urllib.error.HTTPError as e:
        assert e.code == 400, f"Expected 400, got {e.code}"
        print(f"    OK: 400 returned")


# ===================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("HTTP E2E Tests (FastAPI server)")
    print("=" * 60)

    print("  Starting server...")
    proc = start_server()
    print(f"  Server PID: {proc.pid}")

    tests = [
        test_health_live,
        test_health_ready,
        test_health_detail,
        test_submit_task,
        test_submit_builder,
        test_submit_invalid_task,
    ]

    passed = 0
    try:
        for fn in tests:
            try:
                fn(proc)
                passed += 1
            except Exception as e:
                print(f"  [FAIL] {fn.__name__}: {e}")
    finally:
        print(f"\n  Stopping server...")
        stop_server(proc)

    print(f"\nResults: {passed}/{len(tests)} passed")
    if passed == len(tests):
        print("ALL HTTP E2E TESTS PASSED!")
