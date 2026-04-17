"""Unit tests for multi_graph_splitter module."""
import pytest
from vllm_orchestrator.src.app.orchestration.multi_graph_splitter import (
    MultiGraphSplitter, MultiGraphSplitResult, DomainSubRequest,
)


@pytest.fixture
def splitter():
    return MultiGraphSplitter()


class TestMultiGraphSplitter:
    def test_single_domain_no_split(self, splitter):
        result = splitter.analyze("중세풍 타워 만들어줘")
        assert not result.is_multi_domain

    def test_multi_domain_detected(self, splitter):
        result = splitter.analyze(
            "마인크래프트 성 만들고 카메라 워킹도 짜줘"
        )
        assert result.is_multi_domain

    def test_multi_domain_with_split_marker(self, splitter):
        result = splitter.analyze(
            "마인크래프트 블록으로 타워 만들어줘 그리고 카메라 클로즈업 연출도 해줘"
        )
        assert result.is_multi_domain
        if result.can_split:
            assert len(result.sub_requests) >= 2
            domains = {s.domain for s in result.sub_requests}
            assert "minecraft" in domains
            assert "animation" in domains

    def test_builder_and_cad_mixed(self, splitter):
        result = splitter.analyze(
            "건물 외관 도면 만들고 PCB 배선 설계도도 그려줘"
        )
        assert result.is_multi_domain

    def test_pure_minecraft(self, splitter):
        result = splitter.analyze("블록으로 다리 만들어줘")
        assert not result.is_multi_domain or not result.needs_clarification

    def test_empty_input(self, splitter):
        result = splitter.analyze("")
        assert not result.is_multi_domain

    def test_result_to_dict(self, splitter):
        result = splitter.analyze("마인크래프트 빌드 만들어줘")
        d = result.to_dict()
        assert "is_multi_domain" in d
        assert "sub_requests" in d

    def test_sub_request_has_confidence(self, splitter):
        result = splitter.analyze(
            "마인크래프트 성 만들고 건물 도면도 그려줘"
        )
        if result.sub_requests:
            for sr in result.sub_requests:
                assert 0.0 <= sr.confidence <= 1.0
