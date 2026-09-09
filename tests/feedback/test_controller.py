from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import inspect

import pytest

import feedback
from feedback import (
    Anomaly,
    ArtifactRef,
    EvidenceRef,
    ExperienceCase,
    ExperienceQuery,
    ExperienceRef,
    FindAnomalyBuilder,
    FindRecoveryController,
    ParameterChange,
    RecoveryAction,
    RecoveryDecision,
    RecoveryExperienceQuery,
    RecoveryProposal,
    RiskLevel,
    RunContext,
    SupervisorState,
    SupervisorStatus,
    ValidationCheck,
    ValidationResult,
    ValidationStatus,
)
from feedback.interfaces import (
    RecoveryAdvisor,
    RecoveryController,
    RecoveryExperienceStore,
)


NOW = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
RUN_ID = "find-controller-001"


class FakeRecoveryExperienceStore:
    def __init__(
        self,
        results: list[ExperienceCase] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.results = [] if results is None else results
        self.error = error
        self.calls = 0
        self.queries: list[RecoveryExperienceQuery] = []

    def search_recovery_cases(
        self,
        query: RecoveryExperienceQuery,
    ) -> list[ExperienceCase]:
        self.calls += 1
        self.queries.append(query)
        if self.error is not None:
            raise self.error
        return self.results


class FakeRecoveryAdvisor:
    def __init__(
        self,
        result: object = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.calls = 0
        self.inputs: list[
            tuple[Anomaly, RunContext, SupervisorState, list[ExperienceCase]]
        ] = []

    def propose(
        self,
        *,
        anomaly: Anomaly,
        run_context: RunContext,
        supervisor_state: SupervisorState,
        matched_experiences: list[ExperienceCase],
    ) -> RecoveryProposal:
        self.calls += 1
        self.inputs.append(
            (anomaly, run_context, supervisor_state, matched_experiences)
        )
        if self.error is not None:
            raise self.error
        return self.result  # type: ignore[return-value]


def _run_context(**overrides: object) -> RunContext:
    values: dict[str, object] = {
        "context_id": "ctx-controller-001",
        "attempt_index": 0,
        "project_id": "project-controller-001",
        "request_source": "web",
        "created_at": NOW,
        "producer": "controller-tests",
        "producer_version": "1.0",
        "research_topic": "Reliable research agents",
        "selection_snapshot_path": "/tmp/selection.json",
        "selection": {"sources": ["arxiv"]},
        "command_redacted": ["python", "modules/finding/main.py"],
        "working_directory": "/workspace",
        "python_executable": "/opt/python",
        "config_snapshot_path": "/tmp/find.config.json",
        "input_snapshot_path": "/tmp/input.json",
        "requested_parameters": {"abstract_scoring_max_workers": 2},
        "effective_parameters": {"abstract_scoring_max_workers": 2},
        "expected_artifacts": [
            ArtifactRef(role="result", path="find_results.json", required=True)
        ],
        "startup_grace_seconds": 30,
        "stall_suspect_seconds": 60,
        "stall_confirm_seconds": 120,
        "recovery_budget": 2,
        "allowed_recovery_actions": [
            RecoveryAction.RETRY_NEW_RUN,
            RecoveryAction.RETRY_WITH_PARAMETER_CHANGE,
            RecoveryAction.SKIP_OPTIONAL_SOURCE,
        ],
        "approval_risk_threshold": RiskLevel.MEDIUM,
        "validation_policy_version": "find.validation.v1",
        "experience_query": ExperienceQuery(
            limit=5,
            project_id="project-controller-001",
            required_context_tags=["source:arxiv"],
        ),
        "external_costs_authorized": True,
        "skippable_sources": ["semantic_scholar", "openalex"],
        "environment_fingerprint": "env-controller-001",
    }
    values.update(overrides)
    return RunContext(**values)  # type: ignore[arg-type]


def _anomaly(**overrides: object) -> Anomaly:
    values: dict[str, object] = {
        "anomaly_id": "anomaly-controller-001",
        "run_id": RUN_ID,
        "created_at": NOW,
        "detected_at": NOW,
        "updated_at": NOW,
        "producer": "controller-tests",
        "producer_version": "1.0",
        "stage": "find",
        "kind": "progress_stalled",
        "blocking": True,
        "confidence": 0.9,
        "detected_by": ["progress_observer"],
        "supervisor_state_revision": 3,
        "symptoms": ["Structured progress signal is stalled"],
        "evidence_refs": [
            EvidenceRef(
                kind="snapshot",
                contract_id="snapshot-controller-001",
                summary="Progress snapshot reports a confirmed stall",
            )
        ],
        "root_cause_status": "suspected",
        "affected_phase": "abstract_scoring",
        "downstream_impact": "Find cannot complete",
        "partial_results_usable": False,
        "recovery_eligible": True,
        "retryable_signal": False,
        "fingerprint": "progress-stalled-abstract-scoring",
        "occurrence_count": 1,
    }
    values.update(overrides)
    return Anomaly(**values)  # type: ignore[arg-type]


def _state(run_context: RunContext, anomaly: Anomaly, **overrides: object) -> SupervisorState:
    values: dict[str, object] = {
        "supervisor_id": "supervisor-controller-001",
        "project_id": run_context.project_id,
        "root_run_id": anomaly.run_id,
        "status": SupervisorStatus.DECIDING,
        "state_revision": 3,
        "created_at": NOW,
        "updated_at": NOW,
        "heartbeat_at": NOW,
        "producer": "controller-tests",
        "producer_version": "1.0",
        "process_alive": False,
        "cancel_requested": False,
        "recovery_attempts": 0,
        "recovery_budget_total": run_context.recovery_budget,
        "recovery_budget_remaining": run_context.recovery_budget,
        "awaiting_approval": False,
        "gate_evaluated": False,
        "allow_read": False,
        "gate_reason": "Choosing a recovery decision",
        "terminal": False,
        "event_sequence": 0,
        "state_path": "/tmp/supervisor_state.json",
        "run_context_id": run_context.context_id,
        "active_run_id": anomaly.run_id,
        "active_anomaly_id": anomaly.anomaly_id,
    }
    values.update(overrides)
    return SupervisorState(**values)  # type: ignore[arg-type]


def _case(**overrides: object) -> ExperienceCase:
    values: dict[str, object] = {
        "case_id": "case-controller-001",
        "case_type": "technical",
        "created_at": NOW,
        "updated_at": NOW,
        "producer": "synthetic-test-only",
        "producer_version": "1.0",
        "verified": True,
        "deprecated": False,
        "context_id": "historical-context",
        "root_run_id": "synthetic-root-run",
        "final_run_id": "synthetic-final-run",
        "root_cause_status": "suspected",
        "evidence_refs": [
            EvidenceRef(kind="validation", summary="Synthetic recovered validation")
        ],
        "attempt_count": 1,
        "outcome": "recovered",
        "validation_after_id": "validation-controller-001",
        "validation_after_status": ValidationStatus.PASS,
        "ready_for_read_after": True,
        "risk_level": RiskLevel.LOW,
        "confidence": 0.9,
        "matched_count": 1,
        "applied_count": 1,
        "successful_application_count": 1,
        "project_id": "project-controller-001",
        "environment_fingerprint": "env-controller-001",
        "context_tags": ["source:arxiv"],
        "anomaly_id": "historical-anomaly",
        "anomaly_kind": "progress_stalled",
        "anomaly_fingerprint": "historical-stall",
        "decision_id": "historical-decision",
        "recovery_action": RecoveryAction.RETRY_WITH_PARAMETER_CHANGE,
        "parameter_changes": [
            ParameterChange(
                name="abstract_scoring_max_workers",
                before=2,
                after=1,
                reason="Synthetic tested reduction",
                source_case_id="case-controller-001",
            )
        ],
    }
    values.update(overrides)
    return ExperienceCase(**values)  # type: ignore[arg-type]


def _proposal(anomaly: Anomaly, run_context: RunContext, **overrides: object) -> RecoveryProposal:
    values: dict[str, object] = {
        "proposal_id": "proposal-controller-001",
        "context_id": run_context.context_id,
        "run_id": anomaly.run_id,
        "anomaly_id": anomaly.anomaly_id,
        "created_at": NOW,
        "proposed_action": RecoveryAction.RETRY_WITH_PARAMETER_CHANGE,
        "reason": "Reduce scoring concurrency",
        "confidence": 0.8,
        "risk_level": RiskLevel.LOW,
        "parameter_changes": {"abstract_scoring_max_workers": 1},
        "evidence_refs": list(anomaly.evidence_refs),
    }
    values.update(overrides)
    return RecoveryProposal(**values)  # type: ignore[arg-type]


def _decide(
    *,
    store: FakeRecoveryExperienceStore | None = None,
    advisor: FakeRecoveryAdvisor | None = None,
    run_context: RunContext | None = None,
    anomaly: Anomaly | None = None,
    state: SupervisorState | None = None,
) -> tuple[RecoveryDecision, FakeRecoveryExperienceStore, Anomaly, RunContext, SupervisorState]:
    context = _run_context() if run_context is None else run_context
    current_anomaly = _anomaly() if anomaly is None else anomaly
    current_state = _state(context, current_anomaly) if state is None else state
    current_store = FakeRecoveryExperienceStore() if store is None else store
    controller = FindRecoveryController(
        experience_store=current_store,
        recovery_advisor=advisor,
    )
    decision = controller.decide(
        anomaly=current_anomaly,
        run_context=context,
        supervisor_state=current_state,
    )
    return decision, current_store, current_anomaly, context, current_state


def _consume_controller(
    controller: RecoveryController,
    anomaly: Anomaly,
    run_context: RunContext,
    state: SupervisorState,
) -> RecoveryDecision:
    return controller.decide(
        anomaly=anomaly,
        run_context=run_context,
        supervisor_state=state,
    )


def test_controller_public_api_and_protocol_signature() -> None:
    signature = inspect.signature(FindRecoveryController.decide)
    assert list(signature.parameters) == [
        "self",
        "anomaly",
        "run_context",
        "supervisor_state",
    ]
    assert all(
        signature.parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
        for name in ("anomaly", "run_context", "supervisor_state")
    )
    assert feedback.FindRecoveryController is FindRecoveryController
    assert "FindRecoveryController" in feedback.__all__

    anomaly = _anomaly(retryable_signal=True)
    context = _run_context()
    state = _state(context, anomaly)
    controller: RecoveryController = FindRecoveryController(
        experience_store=FakeRecoveryExperienceStore()
    )
    assert isinstance(_consume_controller(controller, anomaly, context, state), RecoveryDecision)


@pytest.mark.parametrize("field", ["anomaly", "run_context", "supervisor_state"])
def test_controller_rejects_wrong_input_types_before_dependencies(field: str) -> None:
    context = _run_context()
    anomaly = _anomaly()
    state = _state(context, anomaly)
    inputs: dict[str, object] = {
        "anomaly": anomaly,
        "run_context": context,
        "supervisor_state": state,
    }
    inputs[field] = object()
    store = FakeRecoveryExperienceStore()
    advisor = FakeRecoveryAdvisor()
    controller = FindRecoveryController(experience_store=store, recovery_advisor=advisor)

    with pytest.raises(TypeError):
        controller.decide(**inputs)  # type: ignore[arg-type]

    assert store.calls == 0
    assert advisor.calls == 0


@pytest.mark.parametrize("mismatch", ["context", "project", "run", "anomaly"])
def test_controller_rejects_identity_mismatch_before_dependencies(mismatch: str) -> None:
    context = _run_context()
    anomaly = _anomaly()
    overrides: dict[str, object] = {}
    if mismatch == "context":
        overrides["run_context_id"] = "ctx-other"
    elif mismatch == "project":
        overrides["project_id"] = "project-other"
    elif mismatch == "run":
        overrides["active_run_id"] = "find-other"
    else:
        overrides["active_anomaly_id"] = "anomaly-other"
    state = _state(context, anomaly, **overrides)
    store = FakeRecoveryExperienceStore()
    advisor = FakeRecoveryAdvisor()
    controller = FindRecoveryController(experience_store=store, recovery_advisor=advisor)

    with pytest.raises(ValueError, match="identity"):
        controller.decide(anomaly=anomaly, run_context=context, supervisor_state=state)

    assert store.calls == 0
    assert advisor.calls == 0


@pytest.mark.parametrize(
    ("state_overrides", "anomaly_overrides"),
    [
        ({"cancel_requested": True}, {}),
        (
            {"recovery_attempts": 2, "recovery_budget_remaining": 0},
            {},
        ),
        (
            {"recovery_attempts": 2, "recovery_budget_remaining": 0},
            {"retryable_signal": True},
        ),
        ({"active_recovery_decision_id": "decision-active"}, {}),
        ({}, {"recovery_eligible": False}),
        ({}, {"kind": "run_id_mismatch"}),
        ({}, {"kind": "source_integrity_blocked"}),
        ({}, {"kind": "reading_bridge_rejected"}),
    ],
)
def test_hard_stops_do_not_call_store_or_advisor(
    state_overrides: dict[str, object],
    anomaly_overrides: dict[str, object],
) -> None:
    context = _run_context()
    anomaly = _anomaly(**anomaly_overrides)
    state = _state(context, anomaly, **state_overrides)
    store = FakeRecoveryExperienceStore([_case()])
    advisor = FakeRecoveryAdvisor(_proposal(anomaly, context))

    decision, _, _, _, _ = _decide(
        store=store,
        advisor=advisor,
        run_context=context,
        anomaly=anomaly,
        state=state,
    )

    assert decision.action is RecoveryAction.STOP_AND_REPORT
    assert decision.executable is False
    assert store.calls == 0
    assert advisor.calls == 0


def test_nonblocking_anomaly_returns_no_action_without_dependencies() -> None:
    anomaly = _anomaly(blocking=False)
    context = _run_context()
    state = _state(context, anomaly)
    store = FakeRecoveryExperienceStore([_case()])
    advisor = FakeRecoveryAdvisor(_proposal(anomaly, context))

    decision, _, _, _, _ = _decide(
        store=store,
        advisor=advisor,
        run_context=context,
        anomaly=anomaly,
        state=state,
    )

    assert decision.action is RecoveryAction.NO_ACTION
    assert decision.executable is False
    assert store.calls == 0
    assert advisor.calls == 0


def test_first_safe_experience_is_executable_and_store_query_is_mapped_once() -> None:
    case = _case()
    store = FakeRecoveryExperienceStore([case])
    advisor = FakeRecoveryAdvisor()
    context = _run_context(external_costs_authorized=False)

    decision, _, anomaly, context, _ = _decide(
        store=store,
        advisor=advisor,
        run_context=context,
    )

    assert decision.action is RecoveryAction.RETRY_WITH_PARAMETER_CHANGE
    assert decision.executable is True
    assert decision.new_run_required is True
    assert decision.proposed_new_run_id
    assert decision.requires_approval is False
    assert decision.parameter_changes == case.parameter_changes
    assert decision.matched_experience_refs == [
        ExperienceRef(case_id=case.case_id, verified=True, case_type="technical")
    ]
    assert decision.evidence_of_previous_success == case.evidence_refs
    assert decision.run_id == anomaly.run_id
    assert store.calls == 1
    assert store.queries == [
        RecoveryExperienceQuery(
            anomaly_kind=anomaly.kind,
            project_id=context.project_id,
            environment_fingerprint=context.environment_fingerprint,
            required_context_tags=context.experience_query.required_context_tags,
            limit=10,
        )
    ]
    assert advisor.calls == 0


@pytest.mark.parametrize(
    "case",
    [
        _case(verified=False, outcome="failed", validation_after_status=ValidationStatus.WARNING, ready_for_read_after=False),
        _case(deprecated=True),
        _case(outcome="failed", validation_after_status=ValidationStatus.WARNING, ready_for_read_after=False),
        _case(outcome="success", ready_for_read_after=False),
        _case(matched_count=0, applied_count=0, successful_application_count=0),
        _case(anomaly_kind="result_missing"),
        _case(project_id="project-other"),
        _case(environment_fingerprint="env-other"),
        _case(context_tags=["source:openalex"]),
    ],
    ids=[
        "unverified",
        "deprecated",
        "not-recovered",
        "not-ready",
        "no-success",
        "anomaly-kind",
        "project",
        "environment",
        "context-tags",
    ],
)
def test_rejected_store_cases_fall_through_to_advisor(case: ExperienceCase) -> None:
    store = FakeRecoveryExperienceStore([case])
    anomaly = _anomaly()
    context = _run_context()
    advisor = FakeRecoveryAdvisor(_proposal(anomaly, context))

    decision, _, _, _, _ = _decide(
        store=store,
        advisor=advisor,
        anomaly=anomaly,
        run_context=context,
    )

    assert decision.action is RecoveryAction.RETRY_WITH_PARAMETER_CHANGE
    assert decision.executable is True
    assert store.calls == 1
    assert advisor.calls == 1


@pytest.mark.parametrize(
    "change",
    [
        ParameterChange(name="provider", before=None, after=1, reason="Unsafe"),
        ParameterChange(name="abstract_scoring_max_workers", before=2, after=True, reason="Unsafe"),
        ParameterChange(name="abstract_scoring_max_workers", before=2, after="1", reason="Unsafe"),
        ParameterChange(name="abstract_scoring_max_workers", before=2, after=0, reason="Unsafe"),
        ParameterChange(name="abstract_scoring_max_workers", before=3, after=1, reason="Mismatch"),
        ParameterChange(name="arxiv_timeout_sec", before=None, after=1, reason="Missing"),
    ],
)
def test_unsafe_experience_parameter_changes_stop(change: ParameterChange) -> None:
    decision, _, _, _, _ = _decide(store=FakeRecoveryExperienceStore([_case(parameter_changes=[change])]))
    assert decision.action is RecoveryAction.STOP_AND_REPORT
    assert decision.executable is False


def test_skip_experience_requires_a_currently_skippable_source() -> None:
    allowed = _case(
        recovery_action=RecoveryAction.SKIP_OPTIONAL_SOURCE,
        parameter_changes=[],
        target_sources=["semantic_scholar"],
    )
    blocked = _case(
        recovery_action=RecoveryAction.SKIP_OPTIONAL_SOURCE,
        parameter_changes=[],
        target_sources=["arxiv"],
    )

    allowed_decision, _, _, _, _ = _decide(store=FakeRecoveryExperienceStore([allowed]))
    blocked_decision, _, _, _, _ = _decide(store=FakeRecoveryExperienceStore([blocked]))

    assert allowed_decision.action is RecoveryAction.SKIP_OPTIONAL_SOURCE
    assert allowed_decision.target_sources == ["semantic_scholar"]
    assert allowed_decision.executable is True
    assert allowed_decision.new_run_required is True
    assert allowed_decision.proposed_new_run_id
    assert blocked_decision.action is RecoveryAction.STOP_AND_REPORT


def test_disallowed_experience_action_stops() -> None:
    context = _run_context(allowed_recovery_actions=[RecoveryAction.RETRY_NEW_RUN])
    decision, _, _, _, _ = _decide(
        store=FakeRecoveryExperienceStore([_case()]),
        run_context=context,
    )
    assert decision.action is RecoveryAction.STOP_AND_REPORT


def test_higher_risk_experience_requires_approval_when_within_threshold() -> None:
    case = _case(risk_level=RiskLevel.HIGH)
    context = _run_context(
        approval_risk_threshold=RiskLevel.HIGH,
        external_costs_authorized=False,
    )

    decision, _, _, _, _ = _decide(
        store=FakeRecoveryExperienceStore([case]),
        run_context=context,
    )

    assert decision.action is RecoveryAction.REQUEST_APPROVAL
    assert decision.proposed_action is case.recovery_action
    assert decision.executable is False
    assert decision.requires_approval is True
    assert decision.approval_status == "pending"


def test_store_failure_stops_without_advisor_retry() -> None:
    store = FakeRecoveryExperienceStore(error=RuntimeError("store unavailable"))
    advisor = FakeRecoveryAdvisor()

    decision, _, _, _, _ = _decide(store=store, advisor=advisor)

    assert decision.action is RecoveryAction.STOP_AND_REPORT
    assert store.calls == 1
    assert advisor.calls == 0


def test_same_candidate_action_at_limit_stops_after_store_lookup() -> None:
    anomaly = _anomaly()
    context = _run_context()
    state = _state(
        context,
        anomaly,
        recovery_attempts=1,
        recovery_budget_remaining=1,
        last_recovery_action=RecoveryAction.RETRY_NEW_RUN,
        last_action_attempt_count=1,
    )
    store = FakeRecoveryExperienceStore(
        [
            _case(
                recovery_action=RecoveryAction.RETRY_NEW_RUN,
                parameter_changes=[],
            )
        ]
    )
    advisor = FakeRecoveryAdvisor()

    decision, _, _, _, _ = _decide(
        store=store,
        advisor=advisor,
        run_context=context,
        anomaly=anomaly,
        state=state,
    )

    assert store.calls == 1
    assert advisor.calls == 0
    assert decision.action is RecoveryAction.STOP_AND_REPORT
    assert decision.executable is False
    assert "same action attempt limit" in decision.reason


def test_different_candidate_action_is_not_blocked_by_previous_action_limit() -> None:
    anomaly = _anomaly()
    context = _run_context()
    state = _state(
        context,
        anomaly,
        recovery_attempts=1,
        recovery_budget_remaining=1,
        last_recovery_action=RecoveryAction.RETRY_NEW_RUN,
        last_action_attempt_count=1,
    )
    store = FakeRecoveryExperienceStore([_case()])
    advisor = FakeRecoveryAdvisor()

    decision, _, _, _, _ = _decide(
        store=store,
        advisor=advisor,
        run_context=context,
        anomaly=anomaly,
        state=state,
    )

    assert store.calls == 1
    assert advisor.calls == 0
    assert decision.action is RecoveryAction.RETRY_WITH_PARAMETER_CHANGE
    assert decision.executable is True


def test_first_retryable_signal_uses_advisor_when_no_experience() -> None:
    anomaly = _anomaly(retryable_signal=True)
    context = _run_context(external_costs_authorized=False)
    proposal = _proposal(
        anomaly,
        context,
        proposed_action=RecoveryAction.RETRY_NEW_RUN,
        parameter_changes={},
    )
    advisor = FakeRecoveryAdvisor(proposal)

    decision, store, _, _, state = _decide(
        anomaly=anomaly,
        run_context=context,
        advisor=advisor,
    )

    assert decision.action is RecoveryAction.RETRY_NEW_RUN
    assert decision.executable is True
    assert decision.new_run_required is True
    assert decision.proposed_new_run_id
    assert decision.parameter_changes == []
    assert decision.target_sources == []
    assert decision.budget_before == state.recovery_budget_remaining
    assert decision.budget_after == state.recovery_budget_remaining - 1
    assert store.calls == 1
    assert advisor.calls == 1


def test_retryable_signal_disallowed_advisor_action_stops() -> None:
    anomaly = _anomaly(retryable_signal=True)
    context = _run_context(
        allowed_recovery_actions=[RecoveryAction.RETRY_WITH_PARAMETER_CHANGE]
    )
    proposal = _proposal(
        anomaly,
        context,
        proposed_action=RecoveryAction.RETRY_NEW_RUN,
        parameter_changes={},
    )
    advisor = FakeRecoveryAdvisor(proposal)

    decision, _, _, _, _ = _decide(
        run_context=context,
        anomaly=anomaly,
        advisor=advisor,
    )

    assert decision.action is RecoveryAction.STOP_AND_REPORT
    assert decision.executable is False
    assert advisor.calls == 1


def test_second_retry_does_not_use_automatic_rule() -> None:
    anomaly = _anomaly(retryable_signal=True)
    context = _run_context()
    state = _state(
        context,
        anomaly,
        recovery_attempts=1,
        recovery_budget_remaining=1,
        last_recovery_action=RecoveryAction.RETRY_NEW_RUN,
        last_action_attempt_count=1,
    )

    decision, _, _, _, _ = _decide(run_context=context, anomaly=anomaly, state=state)

    assert decision.action is RecoveryAction.STOP_AND_REPORT
    assert decision.executable is False


def test_second_attempt_can_request_advisor_approval_for_a_different_action() -> None:
    anomaly = _anomaly(retryable_signal=True)
    context = _run_context()
    state = _state(
        context,
        anomaly,
        recovery_attempts=1,
        recovery_budget_remaining=1,
        last_recovery_action=RecoveryAction.RETRY_WITH_PARAMETER_CHANGE,
        last_action_attempt_count=1,
    )
    proposal = _proposal(
        anomaly,
        context,
        proposed_action=RecoveryAction.RETRY_NEW_RUN,
        parameter_changes={},
        risk_level=RiskLevel.MEDIUM,
    )
    advisor = FakeRecoveryAdvisor(proposal)

    decision, _, _, _, _ = _decide(
        advisor=advisor,
        run_context=context,
        anomaly=anomaly,
        state=state,
    )

    assert decision.action is RecoveryAction.REQUEST_APPROVAL
    assert decision.proposed_action is RecoveryAction.RETRY_NEW_RUN
    assert decision.executable is False
    assert advisor.calls == 1


@pytest.mark.parametrize("external_costs_authorized", [False, True])
@pytest.mark.parametrize(
    ("action", "payload"),
    [
        (
            RecoveryAction.RETRY_WITH_PARAMETER_CHANGE,
            {"parameter_changes": {"abstract_scoring_max_workers": 1}},
        ),
        (
            RecoveryAction.SKIP_OPTIONAL_SOURCE,
            {"parameter_changes": {}, "target_sources": ["semantic_scholar"]},
        ),
        (
            RecoveryAction.RETRY_NEW_RUN,
            {"parameter_changes": {}},
        ),
    ],
)
def test_valid_low_risk_advisor_actions_are_executable_after_review(
    external_costs_authorized: bool,
    action: RecoveryAction,
    payload: dict[str, object],
) -> None:
    anomaly = _anomaly()
    context = _run_context(external_costs_authorized=external_costs_authorized)
    proposal = _proposal(anomaly, context, proposed_action=action, **payload)
    proposal_before = proposal.to_dict()
    advisor = FakeRecoveryAdvisor(proposal)

    decision, store, _, _, state = _decide(advisor=advisor, run_context=context, anomaly=anomaly)

    assert decision.action is action
    assert decision.proposal_id is None
    assert decision.proposed_action is None
    assert decision.requires_approval is False
    assert decision.approval_status == "not_required"
    assert decision.executable is True
    assert decision.new_run_required is True
    assert decision.proposed_new_run_id
    assert decision.budget_after == state.recovery_budget_remaining - 1
    assert decision.evidence_of_previous_success == proposal.evidence_refs
    assert proposal.to_dict() == proposal_before
    assert store.calls == 1
    assert advisor.calls == 1
    assert advisor.inputs[0][3] == []


@pytest.mark.parametrize("risk_level", [RiskLevel.MEDIUM, RiskLevel.HIGH])
def test_higher_risk_advisor_action_requires_approval(
    risk_level: RiskLevel,
) -> None:
    anomaly = _anomaly()
    context = _run_context(
        approval_risk_threshold=risk_level,
        external_costs_authorized=False,
    )
    proposal = _proposal(anomaly, context, risk_level=risk_level)
    advisor = FakeRecoveryAdvisor(proposal)

    decision, store, _, _, state = _decide(
        advisor=advisor,
        run_context=context,
        anomaly=anomaly,
    )

    assert decision.action is RecoveryAction.REQUEST_APPROVAL
    assert decision.proposal_id == proposal.proposal_id
    assert decision.proposed_action is proposal.proposed_action
    assert decision.requires_approval is True
    assert decision.approval_status == "pending"
    assert decision.executable is False
    assert decision.proposed_new_run_id is None
    assert decision.budget_after == state.recovery_budget_remaining
    assert store.calls == 1
    assert advisor.calls == 1


@pytest.mark.parametrize(
    "proposal_factory",
    [
        lambda a, c: _proposal(a, c, context_id="ctx-other"),
        lambda a, c: _proposal(a, c, run_id="find-other"),
        lambda a, c: _proposal(a, c, anomaly_id="anomaly-other"),
        lambda a, c: _proposal(
            a,
            c,
            evidence_refs=[EvidenceRef(kind="validation", summary="Unknown")],
        ),
        lambda a, c: _proposal(a, c, parameter_changes={"provider": 1}),
        lambda a, c: _proposal(a, c, parameter_changes={"abstract_scoring_max_workers": True}),
        lambda a, c: _proposal(
            a,
            c,
            proposed_action=RecoveryAction.SKIP_OPTIONAL_SOURCE,
            parameter_changes={},
            target_sources=["arxiv"],
        ),
        lambda a, c: _proposal(a, c, risk_level=RiskLevel.HIGH),
    ],
    ids=[
        "context",
        "run",
        "anomaly",
        "evidence",
        "non-whitelist",
        "invalid-value",
        "core-source",
        "risk",
    ],
)
def test_invalid_or_unauthorized_proposals_stop(
    proposal_factory: object,
) -> None:
    anomaly = _anomaly()
    context = _run_context()
    proposal = proposal_factory(anomaly, context)  # type: ignore[operator]
    advisor = FakeRecoveryAdvisor(proposal)

    decision, _, _, _, _ = _decide(advisor=advisor, run_context=context, anomaly=anomaly)

    assert decision.action is RecoveryAction.STOP_AND_REPORT
    assert decision.executable is False
    assert advisor.calls == 1


@pytest.mark.parametrize(
    "forbidden_name",
    [
        "shell_command",
        "command",
        "source_code",
        "source_patch",
        "api_key",
        "token",
        "credential",
        "long_term_config",
        "project_config_path",
    ],
)
def test_advisor_cannot_request_forbidden_operations(forbidden_name: str) -> None:
    anomaly = _anomaly()
    context = _run_context()
    proposal = _proposal(anomaly, context, parameter_changes={forbidden_name: 1})

    decision, _, _, _, _ = _decide(
        advisor=FakeRecoveryAdvisor(proposal),
        run_context=context,
        anomaly=anomaly,
    )

    assert decision.action is RecoveryAction.STOP_AND_REPORT


def test_disallowed_advisor_action_stops() -> None:
    anomaly = _anomaly()
    context = _run_context(
        allowed_recovery_actions=[RecoveryAction.RETRY_NEW_RUN]
    )
    proposal = _proposal(anomaly, context)

    decision, _, _, _, _ = _decide(
        advisor=FakeRecoveryAdvisor(proposal),
        run_context=context,
        anomaly=anomaly,
    )

    assert decision.action is RecoveryAction.STOP_AND_REPORT


@pytest.mark.parametrize("mode", ["missing", "bad-type", "error"])
def test_missing_or_failed_advisor_stops_without_retry(mode: str) -> None:
    advisor: FakeRecoveryAdvisor | None
    if mode == "missing":
        advisor = None
    elif mode == "bad-type":
        advisor = FakeRecoveryAdvisor(object())
    else:
        advisor = FakeRecoveryAdvisor(error=RuntimeError("advisor unavailable"))

    decision, _, _, _, _ = _decide(advisor=advisor)

    assert decision.action is RecoveryAction.STOP_AND_REPORT
    assert decision.executable is False
    if advisor is not None:
        assert advisor.calls == 1


@pytest.mark.parametrize("action", [RecoveryAction.STOP_AND_REPORT, RecoveryAction.NO_ACTION])
def test_advisor_stop_or_no_action_cannot_suppress_confirmed_anomaly(action: RecoveryAction) -> None:
    anomaly = _anomaly()
    context = _run_context()
    proposal = _proposal(
        anomaly,
        context,
        proposed_action=action,
        parameter_changes={},
    )

    decision, _, _, _, _ = _decide(
        advisor=FakeRecoveryAdvisor(proposal),
        run_context=context,
        anomaly=anomaly,
    )

    assert decision.action is RecoveryAction.STOP_AND_REPORT
    assert decision.executable is False


def test_controller_preserves_all_inputs_and_only_plans_budget() -> None:
    case = _case()
    store_results = [case]
    store = FakeRecoveryExperienceStore(store_results)
    anomaly = _anomaly()
    context = _run_context()
    state = _state(context, anomaly)
    before = deepcopy(
        (
            anomaly.to_dict(),
            context.to_dict(),
            state.to_dict(),
            case.to_dict(),
            store_results,
        )
    )

    decision, _, _, _, _ = _decide(
        store=store,
        run_context=context,
        anomaly=anomaly,
        state=state,
    )

    assert decision.decision_id
    assert decision.created_at.tzinfo is not None
    assert decision.decided_at.tzinfo is not None
    assert decision.proposed_new_run_id
    assert decision.required_post_checks
    assert (
        anomaly.to_dict(),
        context.to_dict(),
        state.to_dict(),
        case.to_dict(),
        store_results,
    ) == before


def test_fake_dependencies_structurally_connect_protocols() -> None:
    store: RecoveryExperienceStore = FakeRecoveryExperienceStore()
    advisor: RecoveryAdvisor = FakeRecoveryAdvisor()
    assert store.search_recovery_cases(RecoveryExperienceQuery(anomaly_kind="progress_stalled", limit=1)) == []
    assert advisor.calls == 0


def test_real_empty_recommendations_anomaly_reaches_advisor_once() -> None:
    validation = ValidationResult(
        validation_id="validation-controller-empty-recommendations",
        run_id=RUN_ID,
        created_at=NOW,
        validated_at=NOW,
        validated_run_dir=f"/runtime/{RUN_ID}",
        producer="find_result_validator",
        producer_version="1.0",
        policy_version="find.validation.minimum.v1",
        duration_ms=4,
        status=ValidationStatus.BLOCK,
        ready_for_read=False,
        summary="Find produced no recommendations",
        checks=[
            ValidationCheck(
                code="strong_recommendations_present",
                status=ValidationStatus.BLOCK,
                required=True,
                message="No strong recommendations are present",
            )
        ],
        passed_check_count=0,
        warning_check_count=0,
        blocked_check_count=1,
        recommendation_target_count=1,
        recommendation_actual_count=0,
        recommendation_shortfall=1,
        strong_recommendation_count=0,
        recommendation_quality_status="empty",
        candidate_ids=[],
        candidate_digest="empty-candidates",
        bridge_probe_status=ValidationStatus.BLOCK,
        input_artifact_refs=[
            ArtifactRef(
                role="result",
                path=f"/runtime/{RUN_ID}/find_results.json",
                required=True,
                exists=True,
                parse_status="valid",
            )
        ],
        bridge_probe_errors=["No recommendations are available"],
        blockers=["No strong recommendations are present"],
        failure_codes=["strong_recommendations_present"],
    )
    validation_before = validation.to_dict()
    anomaly = FindAnomalyBuilder().build(validation_result=validation)
    assert isinstance(anomaly, Anomaly)
    context = _run_context()
    state = _state(context, anomaly)
    proposal = _proposal(
        anomaly,
        context,
        proposed_action=RecoveryAction.RETRY_NEW_RUN,
        parameter_changes={},
    )
    store = FakeRecoveryExperienceStore()
    advisor = FakeRecoveryAdvisor(proposal)
    inputs_before = deepcopy(
        (validation.to_dict(), anomaly.to_dict(), context.to_dict(), state.to_dict())
    )

    decision, _, _, _, _ = _decide(
        store=store,
        advisor=advisor,
        run_context=context,
        anomaly=anomaly,
        state=state,
    )

    assert anomaly.kind == "empty_recommendations"
    assert anomaly.recovery_eligible is True
    assert anomaly.retryable_signal is False
    assert store.calls == 1
    assert store.results == []
    assert advisor.calls == 1
    assert advisor.inputs[0][0] is anomaly
    assert isinstance(decision, RecoveryDecision)
    assert decision.action is RecoveryAction.RETRY_NEW_RUN
    assert decision.executable is True
    assert validation.to_dict() == validation_before
    assert (
        validation.to_dict(),
        anomaly.to_dict(),
        context.to_dict(),
        state.to_dict(),
    ) == inputs_before
