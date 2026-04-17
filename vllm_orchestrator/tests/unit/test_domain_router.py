"""test_domain_router.py — Domain Router classification tests (15 cases)."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.app.domain.profiles import load_domain_profiles
from src.app.orchestration.domain_router import DomainRouter, VALID_DOMAINS

CONFIGS = Path(__file__).resolve().parent.parent.parent / "configs"
_profiles = load_domain_profiles(CONFIGS)
_router = DomainRouter(_profiles)

# CAD x3
def test_cad_shower_filter():
    r = _router.route("충전식 방수 샤워필터 설계, USB-C 충전, IP67")
    assert r.primary_domain == "cad", r.reason

def test_cad_pcb_module():
    r = _router.route("PCB 모터 제어 모듈, 공차 0.1mm, 알루미늄 하우징")
    assert r.primary_domain == "cad", r.reason

def test_cad_dimensions():
    r = _router.route("120x80mm 센서 케이스 부품 설계")
    assert r.primary_domain == "cad", r.reason

# Builder x3
def test_builder_house():
    r = _router.route("2층 주택 거실 크게, 모던 스타일로 지어줘")
    assert r.primary_domain == "builder", r.reason

def test_builder_cafe():
    r = _router.route("지하 카페 + 2층 주거, 벽돌 외관 건축 설계")
    assert r.primary_domain == "builder", r.reason

def test_builder_code():
    r = _router.route("건폐율 60%, 용적률 200% 이내 3층 상가건물")
    assert r.primary_domain == "builder", r.reason

# Minecraft x3
def test_mc_tower():
    r = _router.route("마인크래프트 중세 타워 빌드, 돌벽돌 팔레트")
    assert r.primary_domain == "minecraft", r.reason

def test_mc_facade():
    r = _router.route("정면 벽을 스프루스로 교체하고 창문 유리 넓게")
    assert r.primary_domain == "minecraft", r.reason

def test_mc_harbor():
    r = _router.route("참나무 항구 빌드, 랜턴 배치")
    assert r.primary_domain == "minecraft", r.reason

# Animation x3
def test_anim_closeup():
    r = _router.route("노을빛에 슬픈 클로즈업 연출, 카메라 천천히 푸시")
    assert r.primary_domain == "animation", r.reason

def test_anim_action():
    r = _router.route("추격 씬, 와이드 핸드헬드 카메라, 긴장감 있는 조명")
    assert r.primary_domain == "animation", r.reason

def test_anim_storyboard():
    r = _router.route("스토리보드: 대화 장면 → 감정 리빌 → 액션 전환")
    assert r.primary_domain == "animation", r.reason

# Ambiguous x3
def test_ambiguous_design():
    r = _router.route("이 디자인 수정해줘")
    assert r.primary_domain in VALID_DOMAINS
    # Should be ambiguous (vague input)

def test_ambiguous_style():
    r = _router.route("스타일을 중세풍으로 바꿔줘")
    assert r.primary_domain in VALID_DOMAINS
    # Could be minecraft or builder

def test_ambiguous_never_general():
    r = _router.route("도움이 필요해")
    assert r.primary_domain in VALID_DOMAINS
    assert r.primary_domain not in ("general", "unknown", "")
    assert len(r.candidates) == 5


TESTS = [
    test_cad_shower_filter, test_cad_pcb_module, test_cad_dimensions,
    test_builder_house, test_builder_cafe, test_builder_code,
    test_mc_tower, test_mc_facade, test_mc_harbor,
    test_anim_closeup, test_anim_action, test_anim_storyboard,
    test_ambiguous_design, test_ambiguous_style, test_ambiguous_never_general,
]

if __name__ == "__main__":
    passed = 0
    for fn in TESTS:
        try: fn(); passed += 1; print(f"  OK {fn.__name__}")
        except Exception as e: print(f"  FAIL {fn.__name__}: {e}")
    print(f"\n{passed}/{len(TESTS)} passed")
