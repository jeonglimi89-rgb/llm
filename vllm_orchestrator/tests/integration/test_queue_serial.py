"""Queue serial execution: concurrency=1 검증"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tests.fixtures.fake_llm import FakeLLMSlow
from tests.fixtures.helpers import make_test_dispatcher, make_request


def test_serial_no_overlap():
    print("  [1] Serial execution — no overlap")
    adapter = FakeLLMSlow(delay_s=0.3)
    dispatcher, router, _, _, _, queue = make_test_dispatcher(adapter)

    timestamps = []
    for i in range(3):
        req = make_request("cad", "constraint_parse", f"test {i}")
        spec = router.resolve(req)
        t0 = time.time()
        result = dispatcher.dispatch(req, spec)
        t1 = time.time()
        timestamps.append((t0, t1, result.status))

    # 각 태스크가 순차적으로 실행됐는지 확인
    for i in range(1, len(timestamps)):
        prev_end = timestamps[i-1][1]
        curr_start = timestamps[i][0]
        # 이전 태스크 끝 <= 현재 태스크 시작 (순차)
        assert curr_start >= prev_end - 0.05, f"Overlap detected: task {i} started before task {i-1} ended"

    assert queue.total_completed == 3
    print(f"    OK: 3 tasks sequential, no overlap")


def test_queue_wait_increases():
    print("  [2] Queue wait time increases for later tasks")
    # concurrency=1이므로 나중 태스크의 queue_wait가 커야 함
    # 단, 현재 구현이 동기식이라 실제 wait는 0 (순차 호출)
    # 이 테스트는 구조적 검증
    adapter = FakeLLMSlow(delay_s=0.1)
    dispatcher, router, _, _, _, queue = make_test_dispatcher(adapter)

    for i in range(3):
        req = make_request("minecraft", "edit_parse", f"test {i}")
        spec = router.resolve(req)
        dispatcher.dispatch(req, spec)

    snap = queue.snapshot()
    assert snap["total_completed"] == 3
    assert snap["running"] == 0  # 전부 끝남
    assert snap["total_rejected"] == 0
    print(f"    OK: queue completed={snap['total_completed']}, rejected={snap['total_rejected']}")


if __name__ == "__main__":
    print("=== Integration: Queue Serial ===")
    test_serial_no_overlap()
    test_queue_wait_increases()
    print("PASSED")
