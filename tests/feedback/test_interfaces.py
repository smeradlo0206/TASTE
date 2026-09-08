from __future__ import annotations

from datetime import datetime, timezone
import inspect
from typing import Protocol, get_type_hints

import pytest

import feedback
import feedback.interfaces as interfaces
from feedback import (
    Anomaly,
    AnomalyBuilder,
    ArtifactRef,
    EvidenceRef,
    ExecutionHandle,
    Executor,
    ExperienceCase,
    ExperienceQuery,
    ExperienceStore,
    FeedbackAdapter,
    FindStageRequest,
    ProgressSnapshot,
    ProgressStatus,
    RecoveryAction,
    RecoveryAdvisor,
    RecoveryController,
    RecoveryDecision,
    RecoveryExperienceQuery,
    RecoveryExperienceStore,
    RecoveryProposal,
    ResultValidator,
    RiskLevel,
    RunContext,
    SupervisorState,
    SupervisorStatus,
    ValidationCheck,
    ValidationResult,
    ValidationStatus,
)


OBSERVED_AT = datetime(2026, 9, 1, 14, 0, tzinfo=timezone.utc)


def _make_run_context() -> RunContext:
    return RunContext(
        context_id="ctx-executor-interface",
        attempt_index=0,
        project_id="project-001",
        request_source="web",
        created_at=OBSERVED_AT,
        producer="interface-tests",
        producer_version="1.0",
        research_topic="Reliable research agents",
        selection_snapshot_path="/snapshots/selection.json",
        selection={"venues": ["ICLR"]},
        command_redacted=["python", "modules/finding/main.py"],
        working_directory="/workspace",
        python_executable="/opt/miniforge3/envs/taste/bin/python",
        config_snapshot_path="/snapshots/find-redacted.json",
        input_snapshot_path="/snapshots/input.json",
        requested_parameters={"minimum_recommendations": 5},
        effective_parameters={"minimum_recommendations": 5},
        expected_artifacts=[
            ArtifactRef(role="result", path="final/find_results.json", required=True)
        ],
        startup_grace_seconds=30,
        stall_suspect_seconds=60,
        stall_confirm_seconds=120,
        recovery_budget=1,
        allowed_recovery_actions=[RecoveryAction.RETRY_NEW_RUN],
        approval_risk_threshold=RiskLevel.MEDIUM,
        validation_policy_version="find.validation.v1",
        experience_query=ExperienceQuery(limit=10),
    )


def _make_execution_handle(*, context_id: str) -> ExecutionHandle:
    return ExecutionHandle(
        context_id=context_id,
        pid=6102,
        started_at=OBSERVED_AT,
        process_alive=True,
        stdout_path="/runtime/logs/find.stdout.log",
        stderr_path="/runtime/logs/find.stderr.log",
    )


def _make_progress_snapshot(*, cancel_requested: bool = False) -> ProgressSnapshot:
    return ProgressSnapshot(
        snapshot_id="snapshot-observer-interface",
        run_id="find_20260901_140000_000001",
        created_at=OBSERVED_AT,
        observed_at=OBSERVED_AT,
        sequence=0,
        producer="interface-tests",
        producer_version="1.0",
        status=ProgressStatus.RUNNING,
        phase="finding",
        counts={"candidates": 0},
        elapsed_seconds=0.0,
        seconds_without_progress=0.0,
        process_alive=True,
        cancel_requested=cancel_requested,
        artifact_observations=[],
        progress_parse_ok=True,
        result_exists=False,
        source_status_exists=False,
        source_total=0,
        source_ready=0,
        source_limited=0,
        source_failed=0,
        status_reason="Find is running",
    )


def _make_validation_result() -> ValidationResult:
    return ValidationResult(
        validation_id="validation-interface",
        run_id="find_20260901_140000_000001",
        created_at=OBSERVED_AT,
        validated_at=OBSERVED_AT,
        validated_run_dir="/runtime/runs/find_20260901_140000_000001",
        producer="interface-tests",
        producer_version="1.0",
        policy_version="find.validation.v1",
        duration_ms=1,
        status=ValidationStatus.PASS,
        ready_for_read=True,
        summary="Find output passed validation",
        checks=[
            ValidationCheck(
                code="run_dir_exists",
                status=ValidationStatus.PASS,
                required=True,
                message="Run directory exists",
            )
        ],
        passed_check_count=1,
        warning_check_count=0,
        blocked_check_count=0,
        recommendation_target_count=1,
        recommendation_actual_count=1,
        recommendation_shortfall=0,
        strong_recommendation_count=1,
        recommendation_quality_status="ok",
        candidate_ids=["paper-001"],
        candidate_digest="candidate-digest",
        bridge_probe_status=ValidationStatus.PASS,
        input_artifact_refs=[],
    )


def _make_anomaly() -> Anomaly:
    return Anomaly(
        anomaly_id="anomaly-interface",
        run_id="find_20260901_140000_000001",
        created_at=OBSERVED_AT,
        detected_at=OBSERVED_AT,
        updated_at=OBSERVED_AT,
        producer="interface-tests",
        producer_version="1.0",
        stage="find",
        kind="progress_stalled",
        blocking=True,
        confidence=0.8,
        detected_by=["progress_observer"],
        supervisor_state_revision=0,
        symptoms=["No progress"],
        evidence_refs=[
            EvidenceRef(kind="snapshot", summary="Progress stopped")
        ],
        root_cause_status="suspected",
        affected_phase="finding",
        downstream_impact="Read cannot start",
        partial_results_usable=False,
        recovery_eligible=True,
        retryable_signal=True,
        fingerprint="progress-stalled-interface",
        occurrence_count=1,
    )


def _make_supervisor_state(
    run_context: RunContext,
    anomaly: Anomaly,
) -> SupervisorState:
    return SupervisorState(
        supervisor_id="supervisor-interface",
        project_id=run_context.project_id,
        root_run_id=anomaly.run_id,
        status=SupervisorStatus.DECIDING,
        state_revision=1,
        created_at=OBSERVED_AT,
        updated_at=OBSERVED_AT,
        heartbeat_at=OBSERVED_AT,
        producer="interface-tests",
        producer_version="1.0",
        process_alive=False,
        cancel_requested=False,
        recovery_attempts=0,
        recovery_budget_total=1,
        recovery_budget_remaining=1,
        awaiting_approval=False,
        gate_evaluated=False,
        allow_read=False,
        gate_reason="Recovery decision is pending",
        terminal=False,
        event_sequence=0,
        state_path="/runtime/feedback/supervisor-state.json",
        run_context_id=run_context.context_id,
        active_run_id=anomaly.run_id,
        active_anomaly_id=anomaly.anomaly_id,
    )


class FakeExecutor:
    def execute(self, run_context: RunContext) -> ExecutionHandle:
        return _make_execution_handle(context_id=run_context.context_id)


class FailingExecutor:
    def execute(self, run_context: RunContext) -> ExecutionHandle:
        raise RuntimeError("Find process could not be started")


class FakeExperienceStore:
    def search_cases(self, query: ExperienceQuery) -> list[ExperienceCase]:
        return []


class FakeRecoveryExperienceStore:
    def __init__(self, cases: list[ExperienceCase]) -> None:
        self.cases = cases
        self.queries: list[RecoveryExperienceQuery] = []

    def search_recovery_cases(
        self,
        query: RecoveryExperienceQuery,
    ) -> list[ExperienceCase]:
        self.queries.append(query)
        return list(self.cases)


class FakeFeedbackAdapter:
    def adapt(
        self,
        request: FindStageRequest,
        experience_query: ExperienceQuery,
        experiences: list[ExperienceCase],
    ) -> RunContext:
        return _make_run_context()


class FakeObserver:
    def observe(
        self,
        run_context: RunContext,
        execution_handle: ExecutionHandle,
        previous_snapshot: ProgressSnapshot | None = None,
        *,
        cancel_requested: bool = False,
    ) -> ProgressSnapshot:
        return _make_progress_snapshot(cancel_requested=cancel_requested)


class FakeResultValidator:
    def __init__(self, result: ValidationResult) -> None:
        self.result = result

    def validate(
        self,
        execution_handle: ExecutionHandle,
    ) -> ValidationResult:
        return self.result


class FakeAnomalyBuilder:
    def __init__(self, result: Anomaly | None) -> None:
        self.result = result
        self.calls: list[
            tuple[ProgressSnapshot | None, ValidationResult | None]
        ] = []

    def build(
        self,
        *,
        progress_snapshot: ProgressSnapshot | None = None,
        validation_result: ValidationResult | None = None,
    ) -> Anomaly | None:
        self.calls.append((progress_snapshot, validation_result))
        return self.result


class FakeRecoveryController:
    def __init__(self) -> None:
        self.calls: list[tuple[Anomaly, RunContext, SupervisorState]] = []

    def decide(
        self,
        *,
        anomaly: Anomaly,
        run_context: RunContext,
        supervisor_state: SupervisorState,
    ) -> RecoveryDecision:
        self.calls.append((anomaly, run_context, supervisor_state))
        budget = supervisor_state.recovery_budget_remaining
        return RecoveryDecision(
            decision_id="decision-interface-no-action",
            run_id=anomaly.run_id,
            anomaly_id=anomaly.anomaly_id,
            created_at=OBSERVED_AT,
            decided_at=OBSERVED_AT,
            producer="interface-tests",
            producer_version="1.0",
            action=RecoveryAction.NO_ACTION,
            reason="Protocol substitution test does not choose a recovery action",
            risk_level=RiskLevel.LOW,
            executable=False,
            new_run_required=False,
            exploratory=False,
            requires_approval=False,
            approval_status="not_required",
            attempt_index=1,
            budget_before=budget,
            budget_cost=0,
            budget_after=budget,
            max_same_action_attempts=1,
            verification_policy=run_context.validation_policy_version,
            required_post_checks=[],
            success_definition="No recovery action is executed",
            stop_if_failed=True,
        )


class FakeRecoveryApprovalGate:
    def resolve(
        self,
        *,
        decision: RecoveryDecision,
        approved: bool | None = None,
        approved_by: str | None = None,
        reason: str | None = None,
    ) -> RecoveryDecision:
        return RecoveryDecision.from_json(decision.to_json())


class FakeRecoveryAdvisor:
    def __init__(self) -> None:
        self.calls: list[
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
        self.calls.append(
            (anomaly, run_context, supervisor_state, matched_experiences)
        )
        return RecoveryProposal(
            proposal_id="proposal-interface",
            context_id=run_context.context_id,
            run_id=anomaly.run_id,
            anomaly_id=anomaly.anomaly_id,
            created_at=OBSERVED_AT,
            proposed_action=RecoveryAction.NO_ACTION,
            reason="No candidate recovery change is proposed by the fake advisor",
            confidence=0.5,
            risk_level=RiskLevel.LOW,
        )


def _observe_once(
    observer: interfaces.Observer,
    run_context: RunContext,
    execution_handle: ExecutionHandle,
) -> ProgressSnapshot:
    return observer.observe(
        run_context,
        execution_handle,
        previous_snapshot=None,
        cancel_requested=True,
    )


def _run_validation(
    validator: ResultValidator,
    execution_handle: ExecutionHandle,
) -> ValidationResult:
    return validator.validate(execution_handle)


def _build_anomaly(
    builder: AnomalyBuilder,
    *,
    progress_snapshot: ProgressSnapshot | None = None,
    validation_result: ValidationResult | None = None,
) -> Anomaly | None:
    return builder.build(
        progress_snapshot=progress_snapshot,
        validation_result=validation_result,
    )


def _decide_recovery(
    controller: RecoveryController,
    *,
    anomaly: Anomaly,
    run_context: RunContext,
    supervisor_state: SupervisorState,
) -> RecoveryDecision:
    return controller.decide(
        anomaly=anomaly,
        run_context=run_context,
        supervisor_state=supervisor_state,
    )


def _resolve_recovery_approval(
    gate: interfaces.RecoveryApprovalGate,
    *,
    decision: RecoveryDecision,
    approved: bool | None = None,
    approved_by: str | None = None,
    reason: str | None = None,
) -> RecoveryDecision:
    return gate.resolve(
        decision=decision,
        approved=approved,
        approved_by=approved_by,
        reason=reason,
    )


def _search_recovery_experience(
    store: RecoveryExperienceStore,
    query: RecoveryExperienceQuery,
) -> list[ExperienceCase]:
    return store.search_recovery_cases(query)


def _propose_recovery(
    advisor: RecoveryAdvisor,
    *,
    anomaly: Anomaly,
    run_context: RunContext,
    supervisor_state: SupervisorState,
    matched_experiences: list[ExperienceCase],
) -> RecoveryProposal:
    return advisor.propose(
        anomaly=anomaly,
        run_context=run_context,
        supervisor_state=supervisor_state,
        matched_experiences=matched_experiences,
    )


def test_recovery_advisor_has_the_minimal_propose_signature() -> None:
    advisor = interfaces.RecoveryAdvisor
    hints = get_type_hints(advisor.propose)
    signature = inspect.signature(advisor.propose)
    public_members = {
        name for name in advisor.__dict__ if not name.startswith("_")
    }

    assert Protocol in advisor.__mro__
    assert getattr(advisor, "_is_runtime_protocol", False) is False
    assert list(signature.parameters) == [
        "self",
        "anomaly",
        "run_context",
        "supervisor_state",
        "matched_experiences",
    ]
    assert hints["anomaly"] is Anomaly
    assert hints["run_context"] is RunContext
    assert hints["supervisor_state"] is SupervisorState
    assert hints["matched_experiences"] == list[ExperienceCase]
    assert hints["return"] is RecoveryProposal
    for name in (
        "anomaly",
        "run_context",
        "supervisor_state",
        "matched_experiences",
    ):
        parameter = signature.parameters[name]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is inspect.Parameter.empty
    assert public_members == {"propose"}


def test_fake_recovery_advisor_connects_inputs_without_deciding_or_mutating() -> None:
    anomaly = _make_anomaly()
    run_context = _make_run_context()
    supervisor_state = _make_supervisor_state(run_context, anomaly)
    matched_experiences: list[ExperienceCase] = []
    before = (
        anomaly.to_json(),
        run_context.to_json(),
        supervisor_state.to_json(),
        list(matched_experiences),
    )
    advisor: RecoveryAdvisor = FakeRecoveryAdvisor()

    proposal = _propose_recovery(
        advisor,
        anomaly=anomaly,
        run_context=run_context,
        supervisor_state=supervisor_state,
        matched_experiences=matched_experiences,
    )

    assert isinstance(proposal, RecoveryProposal)
    assert not isinstance(proposal, RecoveryDecision)
    assert proposal.context_id == run_context.context_id
    assert proposal.run_id == anomaly.run_id
    assert proposal.anomaly_id == anomaly.anomaly_id
    assert advisor.calls == [
        (anomaly, run_context, supervisor_state, matched_experiences)
    ]
    assert advisor.calls[0][3] is matched_experiences
    assert (
        anomaly.to_json(),
        run_context.to_json(),
        supervisor_state.to_json(),
        list(matched_experiences),
    ) == before


def test_recovery_advisor_has_stable_module_and_public_imports() -> None:
    from feedback import RecoveryAdvisor as PublicRecoveryAdvisor
    from feedback.interfaces import RecoveryAdvisor as ModuleRecoveryAdvisor

    assert PublicRecoveryAdvisor is interfaces.RecoveryAdvisor
    assert ModuleRecoveryAdvisor is interfaces.RecoveryAdvisor
    assert RecoveryAdvisor is interfaces.RecoveryAdvisor
    assert "RecoveryAdvisor" in feedback.__all__


def test_recovery_experience_store_has_the_minimal_search_signature() -> None:
    store = interfaces.RecoveryExperienceStore
    hints = get_type_hints(store.search_recovery_cases)
    signature = inspect.signature(store.search_recovery_cases)
    public_members = {
        name for name in store.__dict__ if not name.startswith("_")
    }

    assert Protocol in store.__mro__
    assert getattr(store, "_is_runtime_protocol", False) is False
    assert list(signature.parameters) == ["self", "query"]
    assert hints["query"] is RecoveryExperienceQuery
    assert hints["return"] == list[ExperienceCase]
    assert signature.parameters["query"].default is inspect.Parameter.empty
    assert public_members == {"search_recovery_cases"}


def test_fake_recovery_experience_store_connects_existing_contracts() -> None:
    case = ExperienceCase(
        case_id="case-recovery-interface",
        case_type="technical",
        created_at=OBSERVED_AT,
        updated_at=OBSERVED_AT,
        producer="interface-tests",
        producer_version="1.0",
        verified=True,
        deprecated=False,
        context_id="ctx-recovery-interface",
        root_run_id="find-root-interface",
        final_run_id="find-final-interface",
        root_cause_status="unknown",
        evidence_refs=[EvidenceRef(kind="validation", summary="Recovered")],
        attempt_count=1,
        outcome="recovered",
        validation_after_id="validation-recovery-interface",
        validation_after_status=ValidationStatus.PASS,
        ready_for_read_after=True,
        risk_level=RiskLevel.LOW,
        confidence=0.9,
        matched_count=1,
        applied_count=1,
        successful_application_count=1,
        project_id="project-001",
        anomaly_id="anomaly-recovery-interface",
        anomaly_kind="progress_stalled",
        anomaly_fingerprint="progress-stalled-interface",
        recovery_action=RecoveryAction.RETRY_NEW_RUN,
    )
    query = RecoveryExperienceQuery(
        anomaly_kind="progress_stalled",
        limit=10,
        project_id="project-001",
    )
    store: RecoveryExperienceStore = FakeRecoveryExperienceStore([case])

    result = _search_recovery_experience(store, query)

    assert result == [case]
    assert store.queries == [query]


def test_recovery_experience_store_has_stable_module_and_public_imports() -> None:
    from feedback import RecoveryExperienceStore as PublicRecoveryExperienceStore
    from feedback.interfaces import (
        RecoveryExperienceStore as ModuleRecoveryExperienceStore,
    )

    assert PublicRecoveryExperienceStore is interfaces.RecoveryExperienceStore
    assert ModuleRecoveryExperienceStore is interfaces.RecoveryExperienceStore
    assert RecoveryExperienceStore is interfaces.RecoveryExperienceStore
    assert "RecoveryExperienceStore" in feedback.__all__


def test_recovery_controller_has_the_minimal_decide_signature() -> None:
    controller = interfaces.RecoveryController
    hints = get_type_hints(controller.decide)
    signature = inspect.signature(controller.decide)
    public_members = {
        name for name in controller.__dict__ if not name.startswith("_")
    }

    assert Protocol in controller.__mro__
    assert getattr(controller, "_is_runtime_protocol", False) is False
    assert list(signature.parameters) == [
        "self",
        "anomaly",
        "run_context",
        "supervisor_state",
    ]
    assert hints["anomaly"] is Anomaly
    assert hints["run_context"] is RunContext
    assert hints["supervisor_state"] is SupervisorState
    assert hints["return"] is RecoveryDecision
    for name in ("anomaly", "run_context", "supervisor_state"):
        parameter = signature.parameters[name]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is inspect.Parameter.empty
    assert public_members == {"decide"}


def test_fake_recovery_controller_connects_existing_contracts_without_execution() -> None:
    anomaly = _make_anomaly()
    run_context = _make_run_context()
    supervisor_state = _make_supervisor_state(run_context, anomaly)
    before = (
        anomaly.to_json(),
        run_context.to_json(),
        supervisor_state.to_json(),
    )
    controller: RecoveryController = FakeRecoveryController()

    decision = _decide_recovery(
        controller,
        anomaly=anomaly,
        run_context=run_context,
        supervisor_state=supervisor_state,
    )

    assert isinstance(decision, RecoveryDecision)
    assert decision.action is RecoveryAction.NO_ACTION
    assert decision.run_id == anomaly.run_id
    assert decision.anomaly_id == anomaly.anomaly_id
    assert decision.executable is False
    assert controller.calls == [(anomaly, run_context, supervisor_state)]
    assert (
        anomaly.to_json(),
        run_context.to_json(),
        supervisor_state.to_json(),
    ) == before


def test_recovery_controller_has_stable_module_and_public_imports() -> None:
    from feedback import RecoveryController as PublicRecoveryController
    from feedback.interfaces import RecoveryController as ModuleRecoveryController

    assert PublicRecoveryController is interfaces.RecoveryController
    assert ModuleRecoveryController is interfaces.RecoveryController
    assert RecoveryController is interfaces.RecoveryController
    assert "RecoveryController" in feedback.__all__


def test_recovery_approval_gate_has_the_minimal_resolve_signature() -> None:
    gate = interfaces.RecoveryApprovalGate
    hints = get_type_hints(gate.resolve)
    signature = inspect.signature(gate.resolve)
    public_members = {
        name for name in gate.__dict__ if not name.startswith("_")
    }

    assert Protocol in gate.__mro__
    assert getattr(gate, "_is_runtime_protocol", False) is False
    assert list(signature.parameters) == [
        "self",
        "decision",
        "approved",
        "approved_by",
        "reason",
    ]
    assert hints["decision"] is RecoveryDecision
    assert hints["approved"] == bool | None
    assert hints["approved_by"] == str | None
    assert hints["reason"] == str | None
    assert hints["return"] is RecoveryDecision
    assert signature.parameters["decision"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["decision"].default is inspect.Parameter.empty
    for name in ("approved", "approved_by", "reason"):
        parameter = signature.parameters[name]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is None
    assert public_members == {"resolve"}


def test_fake_recovery_approval_gate_returns_an_isolated_contract_copy() -> None:
    anomaly = _make_anomaly()
    run_context = _make_run_context()
    supervisor_state = _make_supervisor_state(run_context, anomaly)
    decision = FakeRecoveryController().decide(
        anomaly=anomaly,
        run_context=run_context,
        supervisor_state=supervisor_state,
    )
    before = decision.to_json()
    gate: interfaces.RecoveryApprovalGate = FakeRecoveryApprovalGate()

    resolved = _resolve_recovery_approval(gate, decision=decision)

    assert resolved == decision
    assert resolved is not decision
    assert decision.to_json() == before


def test_recovery_approval_gate_has_stable_module_and_public_imports() -> None:
    from feedback import RecoveryApprovalGate as PublicRecoveryApprovalGate
    from feedback.interfaces import (
        RecoveryApprovalGate as ModuleRecoveryApprovalGate,
    )

    assert PublicRecoveryApprovalGate is interfaces.RecoveryApprovalGate
    assert ModuleRecoveryApprovalGate is interfaces.RecoveryApprovalGate
    assert "RecoveryApprovalGate" in feedback.__all__


def test_anomaly_builder_has_the_minimal_build_signature() -> None:
    builder = interfaces.AnomalyBuilder
    hints = get_type_hints(builder.build)
    signature = inspect.signature(builder.build)
    progress_snapshot = signature.parameters["progress_snapshot"]
    validation_result = signature.parameters["validation_result"]
    public_members = {
        name for name in builder.__dict__ if not name.startswith("_")
    }

    assert Protocol in builder.__mro__
    assert list(signature.parameters) == [
        "self",
        "progress_snapshot",
        "validation_result",
    ]
    assert hints["progress_snapshot"] == ProgressSnapshot | None
    assert hints["validation_result"] == ValidationResult | None
    assert hints["return"] == Anomaly | None
    assert progress_snapshot.kind is inspect.Parameter.KEYWORD_ONLY
    assert progress_snapshot.default is None
    assert validation_result.kind is inspect.Parameter.KEYWORD_ONLY
    assert validation_result.default is None
    assert public_members == {"build"}


def test_fake_anomaly_builder_connects_all_supported_evidence_modes() -> None:
    anomaly = _make_anomaly()
    builder: AnomalyBuilder = FakeAnomalyBuilder(anomaly)
    progress_snapshot = _make_progress_snapshot()
    validation_result = _make_validation_result()

    assert _build_anomaly(builder, progress_snapshot=progress_snapshot) is anomaly
    assert _build_anomaly(builder, validation_result=validation_result) is anomaly
    assert (
        _build_anomaly(
            builder,
            progress_snapshot=progress_snapshot,
            validation_result=validation_result,
        )
        is anomaly
    )
    assert builder.calls == [
        (progress_snapshot, None),
        (None, validation_result),
        (progress_snapshot, validation_result),
    ]


def test_fake_anomaly_builder_can_report_no_anomaly() -> None:
    builder: AnomalyBuilder = FakeAnomalyBuilder(None)

    assert _build_anomaly(builder, progress_snapshot=_make_progress_snapshot()) is None


def test_anomaly_builder_has_stable_module_and_public_imports() -> None:
    from feedback import AnomalyBuilder as PublicAnomalyBuilder
    from feedback.interfaces import AnomalyBuilder as ModuleAnomalyBuilder

    assert PublicAnomalyBuilder is interfaces.AnomalyBuilder
    assert ModuleAnomalyBuilder is interfaces.AnomalyBuilder
    assert AnomalyBuilder is interfaces.AnomalyBuilder
    assert "AnomalyBuilder" in feedback.__all__


def test_result_validator_has_the_minimal_validate_signature() -> None:
    validator = interfaces.ResultValidator
    hints = get_type_hints(validator.validate)
    signature = inspect.signature(validator.validate)

    assert Protocol in validator.__mro__
    assert list(signature.parameters) == ["self", "execution_handle"]
    assert hints["execution_handle"] is ExecutionHandle
    assert hints["return"] is ValidationResult
    assert all(
        parameter.default is inspect.Parameter.empty
        for parameter in signature.parameters.values()
    )


def test_fake_result_validator_satisfies_the_protocol_shape() -> None:
    expected = _make_validation_result()
    validator: ResultValidator = FakeResultValidator(expected)

    result = _run_validation(
        validator,
        _make_execution_handle(context_id="ctx-validator-interface"),
    )

    assert result is expected


def test_result_validator_has_stable_module_and_public_imports() -> None:
    from feedback import ResultValidator as PublicResultValidator
    from feedback.interfaces import ResultValidator as ModuleResultValidator

    assert PublicResultValidator is interfaces.ResultValidator
    assert ModuleResultValidator is interfaces.ResultValidator
    assert "ResultValidator" in feedback.__all__


def test_observer_has_the_point_in_time_observe_signature() -> None:
    observer = interfaces.Observer
    hints = get_type_hints(observer.observe)
    signature = inspect.signature(observer.observe)
    previous_snapshot = signature.parameters["previous_snapshot"]
    cancel_requested = signature.parameters["cancel_requested"]

    assert Protocol in observer.__mro__
    assert list(signature.parameters) == [
        "self",
        "run_context",
        "execution_handle",
        "previous_snapshot",
        "cancel_requested",
    ]
    assert hints["run_context"] is RunContext
    assert hints["execution_handle"] is ExecutionHandle
    assert hints["previous_snapshot"] == ProgressSnapshot | None
    assert hints["cancel_requested"] is bool
    assert hints["return"] is ProgressSnapshot
    assert previous_snapshot.default is None
    assert cancel_requested.kind is inspect.Parameter.KEYWORD_ONLY
    assert cancel_requested.default is False


def test_fake_observer_satisfies_the_protocol_shape() -> None:
    context = _make_run_context()
    handle = _make_execution_handle(context_id=context.context_id)
    observer: interfaces.Observer = FakeObserver()

    snapshot = _observe_once(observer, context, handle)

    assert isinstance(snapshot, ProgressSnapshot)
    assert snapshot.cancel_requested is True


def test_observer_has_stable_module_and_public_imports() -> None:
    from feedback import Observer as PublicObserver
    from feedback.interfaces import Observer as ModuleObserver

    assert PublicObserver is interfaces.Observer
    assert ModuleObserver is interfaces.Observer
    assert "Observer" in feedback.__all__


def test_feedback_adapter_has_only_the_adapt_signature() -> None:
    hints = get_type_hints(FeedbackAdapter.adapt)
    signature = inspect.signature(FeedbackAdapter.adapt)
    public_members = {
        name for name in FeedbackAdapter.__dict__ if not name.startswith("_")
    }

    assert hints["request"] is FindStageRequest
    assert hints["experience_query"] is ExperienceQuery
    assert hints["experiences"] == list[ExperienceCase]
    assert hints["return"] is RunContext
    assert list(signature.parameters) == [
        "self",
        "request",
        "experience_query",
        "experiences",
    ]
    assert public_members == {"adapt"}


def test_fake_feedback_adapter_satisfies_the_protocol_shape() -> None:
    adapter: FeedbackAdapter = FakeFeedbackAdapter()
    request = FindStageRequest(
        request_source="web",
        research_topic="Reliable research agents",
        selection={"venues": ["ICLR"]},
        config_path="/snapshots/find.config.json",
        requested_parameters={"minimum_recommendations": 5},
        working_directory="/workspace",
        project_id="project-001",
    )
    query = ExperienceQuery(limit=5, project_id=request.project_id)

    context = adapter.adapt(request, query, [])

    assert isinstance(context, RunContext)


def test_feedback_adapter_has_a_stable_public_import_without_synonyms() -> None:
    from feedback import FeedbackAdapter as PublicFeedbackAdapter
    from feedback.interfaces import FeedbackAdapter as ModuleFeedbackAdapter

    assert PublicFeedbackAdapter is FeedbackAdapter
    assert ModuleFeedbackAdapter is FeedbackAdapter
    assert "FeedbackAdapter" in feedback.__all__
    for forbidden_name in (
        "FindFeedbackAdapter",
        "AdapterRequest",
        "AdapterResult",
        "AdapterConfig",
    ):
        assert not hasattr(interfaces, forbidden_name)


def test_executor_interface_has_only_the_execute_signature() -> None:
    hints = get_type_hints(Executor.execute)
    signature = inspect.signature(Executor.execute)

    assert hints["run_context"] is RunContext
    assert hints["return"] is ExecutionHandle
    assert list(signature.parameters) == ["self", "run_context"]


def test_executor_interface_connects_run_context_to_execution_handle() -> None:
    context = _make_run_context()
    executor: Executor = FakeExecutor()

    handle = executor.execute(context)

    assert isinstance(handle, ExecutionHandle)
    assert handle.context_id == context.context_id


def test_executor_fake_returns_an_initial_execution_handle() -> None:
    handle = FakeExecutor().execute(_make_run_context())

    assert handle.pid > 0
    assert handle.process_alive is True
    assert handle.exit_code is None
    assert handle.run_id is None
    assert handle.run_dir is None
    assert handle.started_at.tzinfo is not None
    assert handle.stdout_path
    assert handle.stderr_path


def test_executor_public_imports_and_module_remain_interface_only() -> None:
    from feedback import Executor as PublicExecutor
    from feedback.interfaces import Executor as ModuleExecutor

    assert PublicExecutor is Executor
    assert ModuleExecutor is Executor
    assert "Executor" in feedback.__all__
    for forbidden_name in (
        "SubprocessFindExecutor",
        "ExecutionStatus",
        "ExecutionStartError",
        "ExecutorConfig",
    ):
        assert not hasattr(interfaces, forbidden_name)


def test_executor_implementation_uses_runtime_error_for_startup_failure() -> None:
    with pytest.raises(RuntimeError, match="could not be started"):
        FailingExecutor().execute(_make_run_context())


def test_experience_store_has_only_the_search_cases_query_method() -> None:
    hints = get_type_hints(ExperienceStore.search_cases)
    signature = inspect.signature(ExperienceStore.search_cases)
    public_members = {
        name for name in ExperienceStore.__dict__ if not name.startswith("_")
    }

    assert hints["query"] is ExperienceQuery
    assert hints["return"] == list[ExperienceCase]
    assert list(signature.parameters) == ["self", "query"]
    assert public_members == {"search_cases"}


def test_fake_experience_store_satisfies_the_read_only_protocol_shape() -> None:
    store: ExperienceStore = FakeExperienceStore()

    assert store.search_cases(ExperienceQuery(limit=5)) == []


def test_experience_store_has_a_stable_public_import() -> None:
    from feedback import ExperienceStore as PublicExperienceStore

    assert PublicExperienceStore is ExperienceStore
    assert "ExperienceStore" in feedback.__all__
