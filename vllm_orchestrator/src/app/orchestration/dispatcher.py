"""
dispatcher.py - 태스크 실행 진입점

router → scheduler → queue → llm client → result
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from ..core.contracts import TaskRequest, TaskResult
from ..core.enums import TaskStatus, FallbackMode
from ..core.errors import CircuitOpenError, OverloadError
from ..domain.registry import TaskSpec
from ..execution.queue_manager import QueueManager
from ..execution.scheduler import Scheduler
from ..execution.timeouts import TimeoutPolicy
from ..llm.client import LLMClient
from ..observability.logger import get_logger, log_event
from ..review.task_contracts import evaluate_task_contract

_log = get_logger("dispatch")


# T-tranche-12 (2026-04-09): the set of TaskStatus values that count as a
# failure for fallback explainability purposes. Computed from the enum so
# renames cascade. Used by ``Dispatcher.dispatch()``'s post-hoc wrapper to
# decide whether to enrich a TaskResult that came back from the queue
# (specifically from ``QueueManager.submit``'s exception catch-all) with
# a fallback WaitDecision.
_FAILURE_STATUSES = frozenset({
    TaskStatus.ERROR,
    TaskStatus.SHED,
    TaskStatus.TIMEOUT,
})


def _snapshot_health_probe(llm_client) -> Optional[dict]:
    """Pull the most-recent classified health probe result off the underlying
    adapter (or its CountingAdapter wrapper). Returns ``None`` if the adapter
    doesn't expose ``last_health_probe_result``."""
    inner = getattr(llm_client, "adapter", None)
    while inner is not None:
        result = getattr(inner, "last_health_probe_result", None)
        if result is not None:
            try:
                return result.to_dict()
            except AttributeError:
                return None
        # Unwrap CountingAdapter / similar single-level wrapper.
        wrapped = getattr(inner, "inner", None)
        if wrapped is None or wrapped is inner:
            break
        inner = wrapped
    return None


def _merge_scheduler_wait_into_retry_decision(
    rd_dict: Optional[dict],
    scheduler,
) -> Optional[dict]:
    """Append the scheduler's most-recent ``WaitDecision`` to the
    ``RetryDecision.cooldown_decisions`` array and update the ``total_cooldown_ms``
    aggregate. This is the single place that consolidates LLMClient cooldowns
    and scheduler cooldowns into one explainable list per case (T-tranche 2).
    """
    if rd_dict is None:
        return None
    sched_wait = getattr(scheduler, "last_wait_decision", None)
    if sched_wait is None:
        return rd_dict
    try:
        sched_dict = sched_wait.to_dict()
    except AttributeError:
        return rd_dict
    # Mutate a shallow copy so we don't disturb the original RetryDecision
    # dict (which may be reused by other readers).
    out = dict(rd_dict)
    cd = list(out.get("cooldown_decisions") or [])
    cd.append(sched_dict)
    out["cooldown_decisions"] = cd
    out["total_cooldown_ms"] = int(out.get("total_cooldown_ms", 0) or 0) + int(sched_dict.get("applied_s", 0.0) * 1000)
    return out


def _merge_fallback_wait_into_retry_decision(
    rd_dict: Optional[dict],
    fallback,
) -> Optional[dict]:
    """Append the fallback handler's most-recent ``WaitDecision`` to the
    ``RetryDecision.cooldown_decisions`` array and update the
    ``total_cooldown_ms`` aggregate. Symmetric to
    ``_merge_scheduler_wait_into_retry_decision``.

    T-tranche-10 (2026-04-09): closes the last gap in the "every wait is
    artifact-explainable" invariant. The dispatcher consults the fallback
    handler on its failure exit sites (circuit open / parse fail) to
    record the fallback WaitDecision slot into the retry_decision dict,
    which then flows through CaseTelemetry → RunTelemetry → disk JSON.

    Runtime semantics are unchanged: ``applied_s=0.0, skipped=True``
    because the current ``DegradedModeHandler`` design does not sleep.
    This merge is purely additive explainability.

    If ``fallback`` is None (legacy dispatcher without fallback wiring),
    ``rd_dict`` is returned unchanged.
    """
    if rd_dict is None or fallback is None:
        return rd_dict
    fb_wait = getattr(fallback, "last_wait_decision", None)
    if fb_wait is None:
        return rd_dict
    try:
        fb_dict = fb_wait.to_dict()
    except AttributeError:
        return rd_dict
    out = dict(rd_dict)
    cd = list(out.get("cooldown_decisions") or [])
    cd.append(fb_dict)
    out["cooldown_decisions"] = cd
    out["total_cooldown_ms"] = int(out.get("total_cooldown_ms", 0) or 0) + int(fb_dict.get("applied_s", 0.0) * 1000)
    return out


class Dispatcher:
    """태스크를 queue → llm 으로 실행"""

    def __init__(
        self,
        llm_client: LLMClient,
        queue: QueueManager,
        scheduler: Scheduler,
        timeouts: TimeoutPolicy,
        prompts_dir: Path | str = "",
        *,
        fallback=None,
        request_cache=None,
    ):
        self.llm = llm_client
        self.queue = queue
        self.scheduler = scheduler
        self.timeouts = timeouts
        self.prompts_dir = Path(prompts_dir) if prompts_dir else None
        # T-tranche-10: optional DegradedModeHandler used *only* for
        # explainability (record + merge WaitDecision into retry_decision
        # on failure exit sites). Does NOT alter runtime semantics — the
        # dispatcher still returns TaskStatus.ERROR on failure, not
        # DEGRADED. Default None preserves back-compat for external
        # callers that never pass a fallback.
        self.fallback = fallback
        # Optional RequestCache (execution/request_cache.py). None 이면 캐싱 비활성.
        # 대규모 운영에서 반복 요청을 LLM 호출 없이 재사용하기 위한 레이어.
        self.request_cache = request_cache

    def dispatch(self, request: TaskRequest, spec: TaskSpec, *, system_prompt_override: str | None = None) -> TaskResult:
        """queue를 통해 실행 (cache hit 시 queue 건너뜀)"""
        # OpenTelemetry span wraps the entire dispatch (noop if OTEL disabled)
        try:
            from ..observability.tracing import span as _otel_span, record_event as _otel_event
        except Exception:
            from contextlib import nullcontext as _otel_span
            def _otel_event(*a, **k): pass

        with _otel_span(
            "dispatch",
            task_type=request.task_type,
            task_id=request.task_id,
            request_id=request.request_id,
        ):
            return self._dispatch_inner(request, spec, system_prompt_override=system_prompt_override, _otel_event=_otel_event)

    def _dispatch_inner(self, request: TaskRequest, spec: TaskSpec, *, system_prompt_override: str | None = None, _otel_event=None) -> TaskResult:
        if _otel_event is None:
            def _otel_event(*a, **k): pass
        # Cache lookup (creative/expensive task 대상). system_prompt_override 있으면 캐시 skip
        # — override는 runtime별 동적 prompt라 같은 input이라도 다른 결과 나옴.
        if self.request_cache is not None and not system_prompt_override:
            cached = self.request_cache.get(request.task_type, request.user_input, request.context)
            if cached is not None:
                _otel_event("cache_hit", {"layer": "exact"})
                log_event(
                    _log, "cache_hit",
                    task_type=request.task_type,
                    task_id=request.task_id,
                    cache_stats=self.request_cache.stats_dict(),
                )
                cached_copy = dict(cached)
                cached_copy["request_id"] = request.request_id
                cached_copy["task_id"] = request.task_id
                cached_copy["cache_hit"] = True
                return TaskResult.from_dict(cached_copy) if hasattr(TaskResult, "from_dict") else _build_task_result_from_dict(cached_copy)

        # Semantic cache (near-duplicate hit) — exact miss 후 시도.
        if not system_prompt_override:
            try:
                from ..execution.semantic_cache import get_semantic_cache
                sem = get_semantic_cache()
                if sem is not None and sem.is_cacheable(request.task_type):
                    # context key: exact_cache와 동일한 정규화 사용
                    _ctx_key = ""
                    try:
                        from ..execution.request_cache import _stable_context_key
                        _ctx_key = _stable_context_key(request.context)
                    except Exception:
                        pass
                    sem_value, sim, matched = sem.lookup(request.task_type, request.user_input, _ctx_key)
                    log_event(
                        _log, "semantic_cache_lookup",
                        task_type=request.task_type,
                        task_id=request.task_id,
                        hit=sem_value is not None,
                        similarity=sim,
                        threshold=sem.threshold,
                        input_preview=request.user_input[:60],
                        ctx_key=_ctx_key[:40],
                        size=sem.size(),
                    )
                    if sem_value is not None:
                        log_event(
                            _log, "semantic_cache_hit",
                            task_type=request.task_type,
                            task_id=request.task_id,
                            similarity=sim,
                            matched_input=(matched or "")[:80],
                        )
                        try:
                            from ..observability.metrics import observe_cache
                            observe_cache(request.task_type, "semantic_hit")
                        except Exception:
                            pass
                        cached_copy = dict(sem_value)
                        cached_copy["request_id"] = request.request_id
                        cached_copy["task_id"] = request.task_id
                        cached_copy["cache_hit"] = True
                        cached_copy["semantic_cache_match"] = {"similarity": sim, "matched_input": matched}
                        return TaskResult.from_dict(cached_copy) if hasattr(TaskResult, "from_dict") else _build_task_result_from_dict(cached_copy)
            except Exception as _e:
                log_event(_log, "semantic_cache_error", error=str(_e))

        def handler(req: TaskRequest) -> TaskResult:
            return self._execute(req, spec, system_prompt_override=system_prompt_override)

        try:
            result = self.queue.submit(request, handler)
        except OverloadError:
            # Queue is full — OverloadError was raised by QueueManager.submit's
            # depth-check branch (see queue_manager.py line 45). See the shed
            # branch below for the fallback merge that was wired in T-tranche-11.
            return self._build_shed_task_result(request)

        # T-tranche-12 (2026-04-09) post-hoc fallback merge wrapper.
        #
        # ``QueueManager.submit`` has an exception catch-all branch
        # (queue_manager.py line 61-70) that intercepts *any* exception
        # escaping from ``handler`` (i.e. ``Dispatcher._execute``) and
        # returns its own ``TaskResult(status=TaskStatus.ERROR, ...)``
        # with ``retry_decision=None`` (dataclass default). That path
        # bypasses ``_execute``'s own failure exit sites, so the
        # fallback record+merge wired into T-tranche-10 never runs.
        #
        # In practice ``_execute`` today does not let exceptions escape,
        # so this wrapper is defense-in-depth for any future refactor.
        # When it *does* fire, it preserves runtime semantics — the
        # TaskResult status stays ERROR, no degrade switch — and only
        # enriches ``retry_decision`` with a fallback WaitDecision slot
        # so artifact explainability is uniform across all failure
        # surfaces of the dispatcher.
        if (
            self.fallback is not None
            and result.status in _FAILURE_STATUSES
            and result.retry_decision is None
        ):
            self.fallback.record_wait_decision()
            synthesized = {"cooldown_decisions": [], "total_cooldown_ms": 0}
            result.retry_decision = _merge_fallback_wait_into_retry_decision(
                synthesized, self.fallback,
            )
        return result

    def _build_shed_task_result(self, request: TaskRequest) -> TaskResult:
        """Shed TaskResult with optional fallback explainability.

        Extracted from the inline ``dispatch()`` body so the T-tranche-11
        AST drift layer still sees exactly one ``return TaskResult(
        status=TaskStatus.SHED, ...)`` site inside the Dispatcher class
        after T-tranche-12's ``dispatch()`` refactor for the post-hoc
        queue wrapper.

        T-tranche-11: like the two ``_execute()`` failure exits, the shed
        branch gets fallback explainability — runtime semantics stay
        ``SHED``, the artifact just gains a ``fallback_retry`` entry in
        ``retry_decision.cooldown_decisions``. The ``rd_dict`` is
        synthesized from scratch here because the LLMClient never even
        ran on this branch, so there's no ``last_retry_decision`` to
        seed from.

        T-tranche-13 (2026-04-09): explicitly notify the scheduler that
        this request was shed via ``scheduler.note_shed(request)``. The
        method is a documented no-op that pins the "shed is a non-event"
        invariant (``_last_finish`` / ``_last_was_heavy`` /
        ``last_wait_decision`` all stay unchanged). Calling it from here
        makes the contract visible in the dispatcher source instead of
        buried as "scheduler.pre_execute is just never called on this
        path" implicit behavior. Any future change that starts mutating
        scheduler state from ``note_shed`` will be caught by
        ``tests/unit/test_scheduler_shed_semantics.py``.
        """
        # T-tranche-13: explicit shed signal — no state mutation by contract.
        try:
            self.scheduler.note_shed(request)
        except AttributeError:
            # Back-compat: Scheduler implementations from before T-tranche-13
            # don't have note_shed(). Silently ignored — the contract the
            # method pins is still satisfied (the state machine wasn't
            # touched), just implicitly.
            pass

        rd_dict: Optional[dict] = None
        if self.fallback is not None:
            self.fallback.record_wait_decision()
            rd_dict = {"cooldown_decisions": [], "total_cooldown_ms": 0}
            rd_dict = _merge_fallback_wait_into_retry_decision(rd_dict, self.fallback)
        return TaskResult(
            request_id=request.request_id,
            task_id=request.task_id,
            task_type=request.task_type,
            status=TaskStatus.SHED,
            errors=["Queue full — request shed"],
            retry_decision=rd_dict,
        )

    def _execute(self, request: TaskRequest, spec: TaskSpec, *, system_prompt_override: str | None = None) -> TaskResult:
        """단일 태스크 실행"""
        start = time.time()

        # Intent analysis (결정론적, ~1ms). Creative task만 실행.
        # 결과는 request.context에 _intent_analysis key로 병합되어 downstream (variant/critic)에서 참조.
        try:
            from ..domain.intent_analyzer import analyze_intent, is_creative_task
            if is_creative_task(request.task_type):
                intent_report = analyze_intent(request.user_input)
                if not isinstance(request.context, dict):
                    request.context = {}
                request.context.setdefault("_intent_analysis", intent_report.to_dict())
                log_event(
                    _log, "intent_analyzed",
                    task_type=request.task_type,
                    task_id=request.task_id,
                    concept=intent_report.concept_category,
                    creative_demand=intent_report.creative_demand,
                    complexity=intent_report.complexity,
                    variant_count=intent_report.suggested_variant_count,
                    modifiers=intent_report.modifiers,
                )
        except Exception as e:
            log_event(_log, "intent_analysis_failed", task_type=request.task_type, error=str(e))

        # 프롬프트 조립 (override 가 있으면 외부 enriched prompt 사용)
        system_prompt = system_prompt_override if system_prompt_override else self._load_prompt(spec)
        timeout_s = self.timeouts.get_timeout(spec.timeout_class)

        # Optional unified-policy total deadline (T-tranche). The Dispatcher
        # itself doesn't own the policy directly; the LLMClient or its caller
        # may have set ``self.llm.total_deadline_s_default`` (currently None).
        total_deadline_s = getattr(self.llm, "total_deadline_s_default", None)

        # 스케줄러 쿨다운 — T-tranche 2: pass deadline + headroom so the
        # scheduler can clamp / skip cooldowns that would push us over budget.
        # Older Scheduler implementations don't accept these kwargs, so call
        # the new signature opportunistically.
        try:
            self.scheduler.pre_execute(
                request,
                total_deadline_s=total_deadline_s,
                request_headroom_s=float(timeout_s),
            )
        except TypeError:
            # back-compat with a Scheduler that doesn't yet accept the kwargs
            self.scheduler.pre_execute(request)

        # Multi-variant sampling decision (scene_graph only):
        # intent/context 기반으로 variant_count 1~3 결정. 1이면 single-shot 동등.
        variant_report_dict = None
        variant_count = 1
        if not system_prompt_override:
            try:
                from .variant_sampler import should_run_variants
                _ctx = request.context if isinstance(request.context, dict) else {}
                _intent_ctx = _ctx.get("_intent_analysis") if isinstance(_ctx, dict) else None
                variant_count = should_run_variants(request.task_type, _ctx, _intent_ctx)
            except Exception:
                variant_count = 1

        try:
            if variant_count > 1:
                from .variant_sampler import sample_variants
                _ctx2 = request.context if isinstance(request.context, dict) else {}
                _intent_ctx2 = _ctx2.get("_intent_analysis") if isinstance(_ctx2, dict) else None
                report = sample_variants(
                    self.llm,
                    spec=spec,
                    system_prompt=system_prompt,
                    user_input=request.user_input,
                    variant_count=variant_count,
                    timeout_s=timeout_s,
                    total_deadline_s=total_deadline_s,
                    intent_report=_intent_ctx2,
                )
                variant_report_dict = report.to_dict()
                sel = report.selected
                if sel and sel.slots:
                    parsed = sel.slots
                    raw_text = sel.raw_text or ""
                    llm_ms = sel.latency_ms
                    log_event(
                        _log, "variant_sampling_done",
                        task_type=request.task_type,
                        task_id=request.task_id,
                        variant_count=report.variant_count,
                        selected=sel.family,
                        selected_score=sel.score,
                        total_wall_ms=report.total_wall_ms,
                        scores={v.family: v.score for v in report.variants},
                    )
                else:
                    log_event(_log, "variant_sampling_all_failed", task_type=request.task_type)
                    parsed, raw_text, llm_ms = self.llm.extract_slots(
                        system_prompt=system_prompt,
                        user_input=request.user_input,
                        pool_type=spec.pool_type,
                        timeout_s=timeout_s,
                        total_deadline_s=total_deadline_s,
                    )
            else:
                parsed, raw_text, llm_ms = self.llm.extract_slots(
                    system_prompt=system_prompt,
                    user_input=request.user_input,
                    pool_type=spec.pool_type,
                    timeout_s=timeout_s,
                    total_deadline_s=total_deadline_s,
                )
        except CircuitOpenError:
            rd_dict = (
                self.llm.last_retry_decision.to_dict()
                if getattr(self.llm, "last_retry_decision", None) is not None
                else None
            )
            rd_dict = _merge_scheduler_wait_into_retry_decision(rd_dict, self.scheduler)
            # T-tranche-10: record fallback wait for explainability.
            # Runtime semantics stay ERROR; only the artifact gains a
            # new fallback_retry entry in cooldown_decisions[].
            if self.fallback is not None:
                self.fallback.record_wait_decision()
                rd_dict = _merge_fallback_wait_into_retry_decision(rd_dict, self.fallback)
            # T-tranche-14 (2026-04-09): explicit signal that the case
            # aborted on circuit-open AFTER scheduler.pre_execute had
            # already run. The method is a documented no-op that pins
            # the "baseline non-shift" invariant: _last_finish and
            # _last_was_heavy must NOT advance here because no real
            # execution happened, and last_wait_decision must be
            # preserved (it was populated by the pre_execute sleep that
            # did happen). Calling this AFTER the scheduler-wait merge
            # above ensures the wait decision is captured into rd_dict
            # before the signal — the signal itself is pure explainability.
            try:
                self.scheduler.note_circuit_open(request)
            except AttributeError:
                # Back-compat: Scheduler implementations from before
                # T-tranche-14 don't have note_circuit_open(). The
                # contract the method pins is still satisfied
                # (the state machine wasn't touched), just implicitly.
                pass
            return TaskResult(
                request_id=request.request_id,
                task_id=request.task_id,
                task_type=request.task_type,
                status=TaskStatus.ERROR,
                errors=["Circuit breaker open"],
                latency_ms=int((time.time() - start) * 1000),
                retry_decision=rd_dict,
                health_probe_result=_snapshot_health_probe(self.llm),
            )

        self.scheduler.post_execute(request)
        total_ms = int((time.time() - start) * 1000)

        # Capture per-call decisions even on success (telemetry needs them).
        rd_dict = (
            self.llm.last_retry_decision.to_dict()
            if getattr(self.llm, "last_retry_decision", None) is not None
            else None
        )
        rd_dict = _merge_scheduler_wait_into_retry_decision(rd_dict, self.scheduler)
        health_dict = _snapshot_health_probe(self.llm)

        if parsed is None:
            # schema gate hard-fail. layered judgment 도 포함해 일관 표시.
            judgment = evaluate_task_contract(
                task_type=request.task_type,
                user_input=request.user_input,
                payload=None,
                schema_validated=False,
                artifact_id=request.task_id,
            )
            # T-tranche-10: record fallback wait for explainability on the
            # parse-fail path too. Same additive rule as circuit-open.
            if self.fallback is not None:
                self.fallback.record_wait_decision()
                rd_dict = _merge_fallback_wait_into_retry_decision(rd_dict, self.fallback)
            return TaskResult(
                request_id=request.request_id,
                task_id=request.task_id,
                task_type=request.task_type,
                status=TaskStatus.ERROR,
                raw_text=raw_text,
                validated=False,
                layered_judgment=judgment.to_dict(),
                errors=["LLM output parse failed"],
                latency_ms=total_ms,
                retry_decision=rd_dict,
                health_probe_result=health_dict,
            )

        # Heuristic post-processor (task-specific, deterministic).
        # LLM output의 프롬프트 규칙 위반을 결정론적 코드로 보정해서 품질 floor를 올림.
        # 현재는 minecraft.scene_graph만 대상. 불변: 유효한 output은 추가만 하고
        # 기존 노드의 필드를 바꾸지 않음 (spatial jitter는 예외 — minor 이동만).
        repair_applied: list[str] = []
        # Variant sampling이 이미 variant 내부에서 heuristic repair를 적용했다면
        # 중복 실행 방지 — 단, selected variant의 repair 리스트를 가져와 로그 일관성 유지.
        _variant_already_repaired = bool(variant_report_dict) and variant_count > 1
        if _variant_already_repaired:
            try:
                sel_dict = next(
                    (v for v in (variant_report_dict.get("variants") or [])
                     if v.get("family") == variant_report_dict.get("selected_family")),
                    None,
                )
                if sel_dict:
                    repair_applied = list(sel_dict.get("heuristic_repairs") or [])
            except Exception:
                pass
        if not _variant_already_repaired and request.task_type == "minecraft.scene_graph" and parsed:
            try:
                from ..domain.scene_graph_repair import repair_scene_graph
                parsed, repair_applied = repair_scene_graph(parsed, request.user_input)
                if repair_applied:
                    log_event(
                        _log, "scene_graph_repair_applied",
                        task_type=request.task_type,
                        task_id=request.task_id,
                        repairs=repair_applied,
                    )
            except Exception as e:
                log_event(
                    _log, "scene_graph_repair_failed",
                    task_type=request.task_type,
                    error=str(e),
                )

        # Self-critique + repair loop.
        # LLM_CRITIC_MODE 선택:
        #   inline (default) — critic 결과 나올 때까지 blocking, repair 루프 가능
        #   async            — critic을 background thread로 fire-and-forget, 즉시 응답 반환 (repair 스킵)
        #   off (또는 LLM_CRITIC_DISABLED=1) — critic 완전 비활성
        critic_report_dict = None
        repair_round_applied = False
        try:
            import os as _os
            critic_mode = _os.getenv("LLM_CRITIC_MODE", "inline").lower()
            if _os.getenv("LLM_CRITIC_DISABLED", "").lower() in ("1", "true", "yes"):
                critic_mode = "off"
            critic_disabled = critic_mode == "off"

            if critic_mode == "async" and parsed:
                # Async: critic 실행을 detached thread로 던지고 즉시 진행 (latency 절약).
                # 결과는 self._async_critic_results 에 task_id → CritiqueReport 로 남김 (optional).
                from ..review.llm_critic import critique, critic_enabled_for
                if critic_enabled_for(request.task_type, request.user_input):
                    import threading
                    def _run_async_critic(_llm, _tt, _ui, _slots, _to, _dl, _tid):
                        try:
                            rep = critique(_llm, task_type=_tt, user_input=_ui,
                                           slots=_slots, timeout_s=_to, total_deadline_s=_dl)
                            log_event(
                                _log, "llm_critic_async_done",
                                task_type=_tt, task_id=_tid,
                                overall_quality=rep.overall_quality,
                                repair_needed=rep.repair_needed,
                                issue_count=len(rep.issues),
                                critic_latency_ms=rep.critic_latency_ms,
                            )
                            try:
                                from ..observability.metrics import observe_critic
                                observe_critic(_tt, rep.repair_needed, rep.overall_quality,
                                               rep.critic_latency_ms / 1000.0)
                            except Exception:
                                pass
                        except Exception as _e:
                            log_event(_log, "llm_critic_async_failed", task_type=_tt, error=str(_e))
                    t = threading.Thread(
                        target=_run_async_critic,
                        args=(self.llm, request.task_type, request.user_input,
                              parsed, min(30.0, float(timeout_s)), total_deadline_s, request.task_id),
                        daemon=True,
                    )
                    t.start()
                    # 응답에 "async critic dispatched" 플래그
                    critic_report_dict = {"mode": "async", "dispatched": True}

            if not critic_disabled and critic_mode != "async" and parsed:
                from ..review.llm_critic import critique, critic_enabled_for
                if critic_enabled_for(request.task_type, request.user_input):
                    report = critique(
                        self.llm,
                        task_type=request.task_type,
                        user_input=request.user_input,
                        slots=parsed,
                        timeout_s=min(30.0, float(timeout_s)),
                        total_deadline_s=total_deadline_s,
                    )
                    critic_report_dict = report.to_dict()
                    log_event(
                        _log, "llm_critic_done",
                        task_type=request.task_type,
                        task_id=request.task_id,
                        overall_quality=report.overall_quality,
                        repair_needed=report.repair_needed,
                        issue_count=len(report.issues),
                        critic_latency_ms=report.critic_latency_ms,
                    )
                    try:
                        from ..observability.metrics import observe_critic
                        observe_critic(
                            request.task_type, report.repair_needed,
                            report.overall_quality, report.critic_latency_ms / 1000.0,
                        )
                    except Exception:
                        pass
                    # Repair round (max 1): critical/major issue → 프롬프트에 critique 주입 후 재생성
                    if report.is_usable() and report.repair_needed and report.repair_hint:
                        repair_system = (
                            system_prompt
                            + "\n\n## Previous attempt had issues — fix these:\n"
                            + report.repair_hint
                            + "\n"
                            + "\n".join(
                                f"- [{i.severity}/{i.aspect}] {i.description} → {i.suggestion}"
                                for i in report.issues[:5]
                            )
                            + "\n\nGenerate a CORRECTED version. Output ONE JSON only."
                        )
                        try:
                            repaired_parsed, repaired_raw, _ = self.llm.extract_slots(
                                system_prompt=repair_system,
                                user_input=request.user_input,
                                pool_type=spec.pool_type,
                                timeout_s=timeout_s,
                                total_deadline_s=total_deadline_s,
                            )
                            if repaired_parsed is not None:
                                if request.task_type == "minecraft.scene_graph":
                                    try:
                                        from ..domain.scene_graph_repair import repair_scene_graph
                                        repaired_parsed, _ = repair_scene_graph(
                                            repaired_parsed, request.user_input
                                        )
                                    except Exception:
                                        pass
                                # Regression detection: 새 결과가 원본보다 나빠지면 원본 유지
                                # scene_graph: score_scene_graph() 사용
                                # 기타 task: parsed 유효성 + 길이만 비교 (간단)
                                accept_repair = True
                                prev_score = None
                                new_score = None
                                try:
                                    if request.task_type == "minecraft.scene_graph":
                                        from .variant_sampler import score_scene_graph
                                        _ctx_for_score = request.context if isinstance(request.context, dict) else {}
                                        _intent_for_score = _ctx_for_score.get("_intent_analysis") if isinstance(_ctx_for_score, dict) else None
                                        prev_score, _ = score_scene_graph(parsed, request.user_input, _intent_for_score)
                                        new_score, _ = score_scene_graph(repaired_parsed, request.user_input, _intent_for_score)
                                        # 새 점수가 원본보다 0.5점 이상 낮아지면 regression → reject
                                        if new_score < prev_score - 0.5:
                                            accept_repair = False
                                    else:
                                        # 일반 task: 노드/키 개수 비교 (heuristic)
                                        def _count_keys(d):
                                            return len(d) if isinstance(d, dict) else 0
                                        prev_score = float(_count_keys(parsed))
                                        new_score = float(_count_keys(repaired_parsed))
                                        if new_score < prev_score * 0.5:
                                            accept_repair = False
                                except Exception:
                                    pass  # score 실패 시 기본 accept

                                if accept_repair:
                                    parsed = repaired_parsed
                                    raw_text = repaired_raw
                                    repair_round_applied = True
                                    log_event(
                                        _log, "critic_repair_applied",
                                        task_type=request.task_type,
                                        task_id=request.task_id,
                                        repair_hint=report.repair_hint[:120],
                                        prev_score=prev_score,
                                        new_score=new_score,
                                    )
                                    try:
                                        from ..observability.metrics import observe_repair
                                        observe_repair(request.task_type, "critic_driven")
                                    except Exception:
                                        pass
                                else:
                                    log_event(
                                        _log, "critic_repair_rejected_regression",
                                        task_type=request.task_type,
                                        task_id=request.task_id,
                                        prev_score=prev_score,
                                        new_score=new_score,
                                        reason="new score dropped vs original",
                                    )
                                    try:
                                        from ..observability.metrics import repair_events
                                        repair_events.labels(
                                            task_type=request.task_type,
                                            kind="regression_rejected",
                                        ).inc()
                                    except Exception:
                                        pass
                        except Exception as re_e:
                            log_event(_log, "critic_repair_failed", task_type=request.task_type, error=str(re_e))
        except Exception as e:
            log_event(_log, "llm_critic_pipeline_error", task_type=request.task_type, error=str(e))

        # 5-gate layered review (review/task_contracts.py).
        # validated 는 더 이상 "JSON 파싱 성공" 이 아니라 5게이트 모두 통과한 경우다.
        judgment = evaluate_task_contract(
            task_type=request.task_type,
            user_input=request.user_input,
            payload=parsed,
            schema_validated=True,
            artifact_id=request.task_id,
        )

        if not judgment.auto_validated:
            # 디버깅을 위해 rationale + raw_text 일부 함께 로그
            gate_rationales = []
            for gate in judgment.gates:
                if gate and not gate.passed:
                    gate_rationales.append(f"{gate.name}: {gate.rationale}")
            log_event(
                _log, "layered_review_failed",
                task_type=request.task_type,
                final_judgment=judgment.final_judgment,
                severity=judgment.severity,
                failure_categories=judgment.failure_categories,
                rationales=gate_rationales,
                raw_text_preview=(raw_text or "")[:300],
            )

        result = TaskResult(
            request_id=request.request_id,
            task_id=request.task_id,
            task_type=request.task_type,
            status=TaskStatus.DONE,
            slots=parsed,
            raw_text=raw_text,
            validated=judgment.auto_validated,
            layered_judgment=judgment.to_dict(),
            latency_ms=total_ms,
            retry_decision=rd_dict,
            health_probe_result=health_dict,
            repair_applied=bool(repair_applied) or repair_round_applied,
            critic_report=critic_report_dict,
            variant_report=variant_report_dict,
        )

        # Cache store: auto_validated=True 인 결과만 (RequestCache 내부에서 재검증).
        result_dict = result.to_dict()
        if self.request_cache is not None and not system_prompt_override:
            try:
                stored = self.request_cache.put(
                    request.task_type, request.user_input, request.context, result_dict
                )
                if stored:
                    log_event(
                        _log, "cache_store",
                        task_type=request.task_type,
                        task_id=request.task_id,
                        cache_stats=self.request_cache.stats_dict(),
                    )
            except Exception as e:
                log_event(_log, "cache_store_failed", task_type=request.task_type, error=str(e))

        # Semantic cache store (같은 결과를 similarity-layer에도 보관)
        if not system_prompt_override:
            try:
                from ..execution.semantic_cache import get_semantic_cache
                sem = get_semantic_cache()
                if sem is not None and sem.is_cacheable(request.task_type):
                    from ..execution.request_cache import _stable_context_key
                    _ctx_key = _stable_context_key(request.context)
                    ok = sem.store(request.task_type, request.user_input, _ctx_key, result_dict)
                    if ok:
                        log_event(
                            _log, "semantic_cache_store",
                            task_type=request.task_type,
                            task_id=request.task_id,
                            user_input_preview=request.user_input[:60],
                            stats=sem.stats_dict(),
                        )
            except Exception as _e:
                log_event(_log, "semantic_cache_store_failed", task_type=request.task_type, error=str(_e))

        return result

    def _load_prompt(self, spec: TaskSpec) -> str:
        """프롬프트 파일 로드.

        파일 안에 ``### {task_name}`` 섹션이 있으면 그 섹션 + ``## Rules``
        섹션만 추출해서 LLM에게 보낸다. 섹션이 없으면 파일 전체를 반환.
        이렇게 하면 하나의 도메인 프롬프트 파일에 여러 task가 정의되어
        있어도 LLM이 해당 task만 보게 된다.
        """
        if self.prompts_dir:
            path = self.prompts_dir / spec.prompt_file
            if path.is_file():
                full = path.read_text(encoding="utf-8").strip()
                return self._extract_task_section(full, spec.task_name)

        return f"Output ONLY valid JSON for {spec.task_name}. No explanations. Korean for text values."

    @staticmethod
    def _extract_task_section(full_prompt: str, task_name: str) -> str:
        """프롬프트 파일에서 특정 task 섹션 + Rules 섹션만 추출.

        구조 예시::

            # Domain Slot Extraction        ← 헤더 (항상 포함)
            ## Tasks
            ### requirement_parse           ← task_name 매칭
            Extract ...
            ### patch_intent_parse           ← 다른 task (제외)
            ...
            ## Rules                         ← 항상 포함
            1. Output ONLY valid JSON ...

        ``### {task_name}`` 부터 다음 ``###`` 또는 ``##`` 까지가 task 섹션.
        ``## Rules`` 부터 파일 끝까지가 rules 섹션. 둘을 합쳐 반환.
        매칭 실패하면 파일 전체를 반환 (back-compat).
        """
        lines = full_prompt.split("\n")
        header_lines: list[str] = []
        task_lines: list[str] = []
        rules_lines: list[str] = []

        section = "header"  # header | tasks | target | other_task | rules
        for line in lines:
            stripped = line.strip()

            # Detect section boundaries
            if stripped.startswith("### "):
                section_name = stripped[4:].strip().lower()
                if section_name == task_name.lower():
                    section = "target"
                    task_lines.append(line)
                    continue
                else:
                    if section == "target":
                        section = "other_task"
                    elif section != "rules":
                        section = "other_task"
                    continue
            elif stripped.startswith("## Rules") or stripped.startswith("## 규칙"):
                section = "rules"
                rules_lines.append(line)
                continue
            elif stripped.startswith("## "):
                if section == "target":
                    section = "other_task"
                elif section == "header":
                    section = "tasks"

            # Collect lines based on current section
            if section == "header":
                header_lines.append(line)
            elif section == "target":
                task_lines.append(line)
            elif section == "rules":
                rules_lines.append(line)

        # If we found the target section, compose focused prompt
        if task_lines:
            parts = []
            if header_lines:
                parts.append("\n".join(header_lines).strip())
            parts.append("\n".join(task_lines).strip())
            if rules_lines:
                parts.append("\n".join(rules_lines).strip())
            return "\n\n".join(parts)

        # Fallback: no matching section found → return full prompt
        return full_prompt
