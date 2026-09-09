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
    ExecutionHandle,
    ExperienceQuery,
    FindRecoveryApprovalGate,
    ProgressSnapshot,
    ProgressStatus,
    RecoveryAction,
    RecoveryDecision,
    RiskLevel,
    RunContext,
    SupervisorState,
    SupervisorStatus,
    ValidationCheck,
    ValidationResult,
    ValidationStatus,
)
from feedback.supervisor import FeedbackSupervisor


NOW = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
RUN_ID = "find-supervisor-001"


def _run_context(**overrides: object) -> RunContext:
    values: dict[str, object] = {
        "context_id": "ctx-supervisor-001",
        "attempt_index": 0,
        "project_id": "project-supervisor-001",
        "request_source": "web",
        "created_at": NOW,
        "producer": "supervisor-tests",
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
        "recovery_budget": 1,
        "allowed_recovery_actions": [RecoveryAction.RETRY_NEW_RUN],
        "approval_risk_threshold": RiskLevel.MEDIUM,
        "validation_policy_version": "find.validation.v1",
        "experience_query": ExperienceQuery(
            limit=5,
            project_id="project-supervisor-001",
        ),
    }
    values.update(overrides)
    return RunContext(**values)  # type: ignore[arg-type]


def _handle(*, bound: bool = False, alive: bool = True, **overrides: object) -> ExecutionHandle:
    values: dict[str, object] = {
        "context_id": "ctx-supervisor-001",
        "pid": 7101,
        "started_at": NOW,
        "process_alive": alive,
        "stdout_path": "/tmp/find.stdout.log",
        "stderr_path": "/tmp/find.stderr.log",
        "run_id": RUN_ID if bound else None,
        "run_dir": f"/tmp/{RUN_ID}" if bound else None,
        "exit_code": None if alive else 0,
    }
    values.update(overrides)
    return ExecutionHandle(**values)  # type: ignore[arg-type]


def _snapshot(*, run_id: str = "", sequence: int = 0, **overrides: object) -> ProgressSnapshot:
    values: dict[str, object] = {
        "snapshot_id": f"snapshot-{sequence}",
        "run_id": run_id,
        "created_at": NOW,
        "observed_at": NOW,
        "sequence": sequence,
        "producer": "fake-observer",
        "producer_version": "1.0",
        "status": ProgressStatus.STARTING if not run_id else ProgressStatus.RUNNING,
        "phase": "starting" if not run_id else "finding",
        "counts": {},
        "elapsed_seconds": float(sequence),
        "seconds_without_progress": 0.0,
        "process_alive": True,
        "cancel_requested": False,
        "artifact_observations": [],
        "progress_parse_ok": bool(run_id),
        "result_exists": False,
        "source_status_exists": False,
        "source_total": 0,
        "source_ready": 0,
        "source_limited": 0,
        "source_failed": 0,
        "status_reason": "Synthetic supervisor observation",
    }
    values.update(overrides)
    return ProgressSnapshot(**values)  # type: ignore[arg-type]


def _validation(*, run_id: str = RUN_ID, status: ValidationStatus = ValidationStatus.PASS) -> ValidationResult:
    check = ValidationCheck(
        code="run_dir_exists",
        status=status,
        required=True,
        message="Synthetic validation check",
    )
    return ValidationResult(
        validation_id="validation-supervisor-001",
        run_id=run_id,
        created_at=NOW,
        validated_at=NOW,
        validated_run_dir=f"/tmp/{run_id}",
        producer="fake-validator",
        producer_version="1.0",
        policy_version="find.validation.v1",
        duration_ms=1,
        status=status,
        ready_for_read=status is ValidationStatus.PASS,
        summary="Synthetic validation",
        checks=[check],
        passed_check_count=int(status is ValidationStatus.PASS),
        warning_check_count=int(status is ValidationStatus.WARNING),
        blocked_check_count=int(status is ValidationStatus.BLOCK),
        recommendation_target_count=1,
        recommendation_actual_count=1 if status is ValidationStatus.PASS else 0,
        recommendation_shortfall=0 if status is ValidationStatus.PASS else 1,
        strong_recommendation_count=1 if status is ValidationStatus.PASS else 0,
        recommendation_quality_status="ok" if status is ValidationStatus.PASS else "blocked",
        candidate_ids=["paper-001"] if status is ValidationStatus.PASS else [],
        candidate_digest="candidate-digest",
        bridge_probe_status=status,
        input_artifact_refs=[],
        blockers=[] if status is ValidationStatus.PASS else ["Synthetic blocker"],
        failure_codes=[] if status is ValidationStatus.PASS else ["run_dir_exists"],
    )


def _anomaly(*, anomaly_id: str = "anomaly-supervisor-001", run_id: str = RUN_ID, fingerprint: str = "supervisor-fingerprint") -> Anomaly:
    return Anomaly(
        anomaly_id=anomaly_id,
        run_id=run_id,
        created_at=NOW,
        detected_at=NOW,
        updated_at=NOW,
        producer="fake-anomaly-builder",
        producer_version="1.0",
        stage="find",
        kind="progress_stalled" if run_id else "progress_missing",
        blocking=True,
        confidence=0.8,
        detected_by=["synthetic-test"],
        supervisor_state_revision=0,
        symptoms=["Synthetic anomaly"],
        evidence_refs=[EvidenceRef(kind="snapshot", summary="Synthetic evidence")],
        root_cause_status="suspected",
        affected_phase="finding",
        downstream_impact="Read cannot start",
        partial_results_usable=False,
        recovery_eligible=True,
        retryable_signal=True,
        fingerprint=fingerprint,
        occurrence_count=1,
    )


def _decision(anomaly: Anomaly, action: RecoveryAction = RecoveryAction.STOP_AND_REPORT) -> RecoveryDecision:
    return RecoveryDecision(
        decision_id=f"decision-{anomaly.anomaly_id}",
        run_id=anomaly.run_id,
        anomaly_id=anomaly.anomaly_id,
        created_at=NOW,
        decided_at=NOW,
        producer="fake-controller",
        producer_version="1.0",
        action=action,
        reason="Synthetic decision",
        risk_level=RiskLevel.LOW,
        executable=False,
        new_run_required=False,
        exploratory=False,
        requires_approval=False,
        approval_status="not_required",
        attempt_index=1,
        budget_before=1,
        budget_cost=0,
        budget_after=1,
        max_same_action_attempts=1,
        verification_policy="find.validation.v1",
        required_post_checks=[],
        success_definition="Synthetic completion",
        stop_if_failed=True,
    )


def _pending_decision(anomaly: Anomaly) -> RecoveryDecision:
    return RecoveryDecision(
        decision_id=f"decision-pending-{anomaly.anomaly_id}",
        run_id=anomaly.run_id,
        anomaly_id=anomaly.anomaly_id,
        created_at=NOW,
        decided_at=NOW,
        producer="fake-controller",
        producer_version="1.0",
        action=RecoveryAction.REQUEST_APPROVAL,
        reason="Synthetic approval is required",
        risk_level=RiskLevel.MEDIUM,
        executable=False,
        new_run_required=True,
        exploratory=False,
        requires_approval=True,
        approval_status="pending",
        attempt_index=1,
        budget_before=1,
        budget_cost=0,
        budget_after=1,
        max_same_action_attempts=1,
        verification_policy="find.validation.v1",
        required_post_checks=["result_exists"],
        success_definition="A new Find run passes validation",
        stop_if_failed=True,
        proposal_id="proposal-supervisor-001",
        proposed_action=RecoveryAction.RETRY_NEW_RUN,
    )


def _automatic_decision(anomaly: Anomaly) -> RecoveryDecision:
    decision = _pending_decision(anomaly)
    return RecoveryDecision.from_dict(
        {
            **decision.to_dict(),
            "action": RecoveryAction.RETRY_NEW_RUN.value,
            "risk_level": RiskLevel.LOW.value,
            "executable": True,
            "requires_approval": False,
            "approval_status": "not_required",
            "budget_cost": 1,
            "budget_after": 0,
            "proposal_id": None,
            "proposed_action": None,
            "proposed_new_run_id": "find-recovery-supervisor-001",
        }
    )


def _state(**overrides: object) -> SupervisorState:
    values: dict[str, object] = {
        "supervisor_id": "supervisor-001",
        "project_id": "project-supervisor-001",
        "root_run_id": None,
        "status": SupervisorStatus.PREPARING,
        "state_revision": 0,
        "created_at": NOW,
        "updated_at": NOW,
        "heartbeat_at": NOW,
        "producer": "supervisor-tests",
        "producer_version": "1.0",
        "process_alive": False,
        "cancel_requested": False,
        "recovery_attempts": 0,
        "recovery_budget_total": 1,
        "recovery_budget_remaining": 1,
        "awaiting_approval": False,
        "gate_evaluated": False,
        "allow_read": False,
        "gate_reason": "Preparing Find supervision",
        "terminal": False,
        "event_sequence": 0,
        "state_path": "/tmp/supervisor-state.json",
        "run_context_id": "ctx-supervisor-001",
    }
    values.update(overrides)
    return SupervisorState(**values)  # type: ignore[arg-type]


class FakeObserver:
    def __init__(self, results: list[object]) -> None:
        self.results = list(results)
        self.calls: list[tuple[RunContext, ExecutionHandle, ProgressSnapshot | None, bool]] = []

    def observe(
        self,
        run_context: RunContext,
        execution_handle: ExecutionHandle,
        previous_snapshot: ProgressSnapshot | None = None,
        *,
        cancel_requested: bool = False,
    ) -> ProgressSnapshot:
        self.calls.append((run_context, execution_handle, previous_snapshot, cancel_requested))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result  # type: ignore[return-value]


class FakeValidator:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[ExecutionHandle] = []

    def validate(self, execution_handle: ExecutionHandle) -> ValidationResult:
        self.calls.append(execution_handle)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result  # type: ignore[return-value]


class FakeAnomalyBuilder:
    def __init__(self, results: list[object]) -> None:
        self.results = list(results)
        self.calls: list[tuple[ProgressSnapshot | None, ValidationResult | None]] = []

    def build(
        self,
        *,
        progress_snapshot: ProgressSnapshot | None = None,
        validation_result: ValidationResult | None = None,
    ) -> Anomaly | None:
        self.calls.append((progress_snapshot, validation_result))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result  # type: ignore[return-value]


class FakeController:
    def __init__(self, decisions: list[object]) -> None:
        self.decisions = list(decisions)
        self.calls: list[tuple[Anomaly, RunContext, SupervisorState]] = []

    def decide(
        self,
        *,
        anomaly: Anomaly,
        run_context: RunContext,
        supervisor_state: SupervisorState,
    ) -> RecoveryDecision:
        self.calls.append((anomaly, run_context, supervisor_state))
        result = self.decisions.pop(0)
        if isinstance(result, Exception):
            raise result
        return result  # type: ignore[return-value]


class RecordingApprovalGate:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[
            tuple[RecoveryDecision, bool | None, str | None, str | None]
        ] = []

    def resolve(
        self,
        *,
        decision: RecoveryDecision,
        approved: bool | None = None,
        approved_by: str | None = None,
        reason: str | None = None,
    ) -> RecoveryDecision:
        self.calls.append((decision, approved, approved_by, reason))
        if self.error is not None:
            raise self.error
        return FindRecoveryApprovalGate().resolve(
            decision=decision,
            approved=approved,
            approved_by=approved_by,
            reason=reason,
        )


def _supervisor(
    *,
    snapshots: list[object] | None = None,
    validation: object | None = None,
    anomalies: list[object] | None = None,
    decisions: list[object] | None = None,
    initial_state: SupervisorState | None = None,
    approval_gate: object | None = None,
) -> tuple[FeedbackSupervisor, FakeObserver, FakeValidator, FakeAnomalyBuilder, FakeController]:
    observer = FakeObserver([_snapshot()] if snapshots is None else snapshots)
    validator = FakeValidator(_validation() if validation is None else validation)
    builder = FakeAnomalyBuilder([None] if anomalies is None else anomalies)
    controller = FakeController([] if decisions is None else decisions)
    supervisor = FeedbackSupervisor(
        observer=observer,
        result_validator=validator,
        anomaly_builder=builder,
        recovery_controller=controller,
        initial_state=_state() if initial_state is None else initial_state,
        approval_gate=approval_gate,
    )
    return supervisor, observer, validator, builder, controller


def test_public_api_and_entrypoint_signatures() -> None:
    assert feedback.FeedbackSupervisor is FeedbackSupervisor
    monitor = inspect.signature(FeedbackSupervisor.on_monitor_tick)
    assert list(monitor.parameters) == [
        "self",
        "run_context",
        "execution_handle",
        "cancel_requested",
    ]
    assert monitor.parameters["cancel_requested"].default is False
    exited = inspect.signature(FeedbackSupervisor.on_process_exited)
    assert list(exited.parameters) == ["self", "run_context", "execution_handle"]
    resolve = inspect.signature(FeedbackSupervisor.resolve_recovery_approval)
    assert list(resolve.parameters) == [
        "self",
        "decision_id",
        "approved",
        "approved_by",
        "reason",
    ]
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in list(resolve.parameters.values())[1:]
    )


def test_monitor_tick_passes_previous_snapshot_and_cancel_without_validating_or_deciding() -> None:
    first = _snapshot(sequence=0)
    second = _snapshot(sequence=1, cancel_requested=True)
    supervisor, observer, validator, builder, controller = _supervisor(
        snapshots=[first, second],
        anomalies=[None, None],
    )
    context = _run_context()
    handle = _handle()

    assert supervisor.on_monitor_tick(run_context=context, execution_handle=handle) is None
    assert supervisor.on_monitor_tick(
        run_context=context,
        execution_handle=handle,
        cancel_requested=True,
    ) is None

    assert observer.calls[0][2] is None
    assert observer.calls[1][2] == first
    assert observer.calls[1][3] is True
    assert builder.calls == [(first, None), (second, None)]
    assert validator.calls == []
    assert controller.calls == []
    assert supervisor.state.latest_progress_snapshot_id == second.snapshot_id


def test_monitor_tick_keeps_unbound_anomaly_in_memory_without_controller_call() -> None:
    anomaly = _anomaly(run_id="")
    supervisor, _, _, _, controller = _supervisor(anomalies=[anomaly])

    assert supervisor.on_monitor_tick(
        run_context=_run_context(),
        execution_handle=_handle(),
    ) is None

    assert controller.calls == []
    assert supervisor._last_anomaly == anomaly


@pytest.mark.parametrize(
    ("run_context", "handle", "cancel_requested", "message"),
    [
        (object(), _handle(), False, "run_context"),
        (_run_context(), object(), False, "execution_handle"),
        (_run_context(), _handle(), 1, "cancel_requested"),
    ],
)
def test_monitor_tick_rejects_invalid_input_types(
    run_context: object,
    handle: object,
    cancel_requested: object,
    message: str,
) -> None:
    supervisor, *_ = _supervisor()
    with pytest.raises(TypeError, match=message):
        supervisor.on_monitor_tick(
            run_context=run_context,  # type: ignore[arg-type]
            execution_handle=handle,  # type: ignore[arg-type]
            cancel_requested=cancel_requested,  # type: ignore[arg-type]
        )
    assert supervisor.trace_summary["monitor_calls"] == 0


def test_monitor_tick_rejects_context_mismatch() -> None:
    supervisor, *_ = _supervisor()
    with pytest.raises(ValueError, match="context_id"):
        supervisor.on_monitor_tick(
            run_context=_run_context(),
            execution_handle=_handle(context_id="ctx-other"),
        )


@pytest.mark.parametrize(
    ("snapshots", "anomalies", "message"),
    [
        ([RuntimeError("private observer detail")], [None], "observer"),
        ([_snapshot()], [RuntimeError("private builder detail")], "anomaly builder"),
        ([object()], [None], "observer"),
        ([_snapshot()], [object()], "anomaly builder"),
    ],
)
def test_monitor_component_failures_are_wrapped_once_and_stop_the_chain(
    snapshots: list[object],
    anomalies: list[object],
    message: str,
) -> None:
    supervisor, observer, validator, builder, controller = _supervisor(
        snapshots=snapshots,
        anomalies=anomalies,
    )
    with pytest.raises(RuntimeError, match=message) as caught:
        supervisor.on_monitor_tick(
            run_context=_run_context(),
            execution_handle=_handle(),
        )
    assert caught.value.__cause__ is not None
    assert len(observer.calls) == 1
    assert validator.calls == []
    assert controller.calls == []
    assert len(builder.calls) <= 1


def test_process_exited_validates_then_builds_matching_evidence_and_decides_once() -> None:
    snapshot = _snapshot(run_id=RUN_ID)
    validation = _validation()
    anomaly = _anomaly()
    decision = _decision(anomaly)
    supervisor, _, validator, builder, controller = _supervisor(
        snapshots=[snapshot],
        validation=validation,
        anomalies=[None, anomaly],
        decisions=[decision],
    )
    context = _run_context()
    running_handle = _handle(bound=True)
    supervisor.on_monitor_tick(run_context=context, execution_handle=running_handle)
    terminal_handle = _handle(bound=True, alive=False)

    result = supervisor.on_process_exited(
        run_context=context,
        execution_handle=terminal_handle,
    )

    assert result == decision
    assert validator.calls == [terminal_handle]
    assert builder.calls[-1] == (snapshot, validation)
    assert len(controller.calls) == 1
    passed_anomaly, passed_context, passed_state = controller.calls[0]
    assert passed_anomaly == anomaly
    assert passed_context == context
    assert passed_state.status is SupervisorStatus.DECIDING
    assert passed_state.root_run_id == RUN_ID
    assert passed_state.active_run_id == RUN_ID
    assert passed_state.active_anomaly_id == anomaly.anomaly_id
    assert supervisor.state.active_recovery_decision_id == decision.decision_id


def test_first_process_exit_establishes_root_run_id() -> None:
    supervisor, _, validator, _, _ = _supervisor(
        validation=_validation(),
        anomalies=[None],
    )
    handle = _handle(bound=True, alive=False)

    assert supervisor.state.root_run_id is None
    assert supervisor.on_process_exited(
        run_context=_run_context(),
        execution_handle=handle,
    ) is None

    assert supervisor.state.root_run_id == RUN_ID
    assert supervisor.state.active_run_id == RUN_ID
    assert validator.calls == [handle]


def test_recovery_process_exit_preserves_root_and_validates_active_run() -> None:
    root_run_id = "find-supervisor-root-001"
    recovery_run_id = "find-supervisor-recovery-001"
    recovery_state = _state(
        root_run_id=root_run_id,
        status=SupervisorStatus.RECOVERING,
        recovery_attempts=1,
        recovery_budget_remaining=0,
        active_anomaly_id="anomaly-supervisor-root-001",
        active_recovery_decision_id="decision-supervisor-root-001",
    )
    supervisor, _, validator, _, _ = _supervisor(
        validation=_validation(run_id=recovery_run_id),
        anomalies=[None],
        initial_state=recovery_state,
    )
    handle = _handle(
        bound=True,
        alive=False,
        run_id=recovery_run_id,
        run_dir=f"/tmp/{recovery_run_id}",
    )

    assert supervisor.on_process_exited(
        run_context=_run_context(),
        execution_handle=handle,
    ) is None

    assert supervisor.state.root_run_id == root_run_id
    assert supervisor.state.active_run_id == recovery_run_id
    assert validator.calls == [handle]
    assert validator.calls[0].run_id == recovery_run_id
    assert supervisor.trace_summary["validation_status"] == "pass"


def test_pending_decision_is_saved_as_an_isolated_copy_and_updates_state() -> None:
    anomaly = _anomaly()
    decision = _pending_decision(anomaly)
    supervisor, *_ = _supervisor(
        validation=_validation(status=ValidationStatus.BLOCK),
        anomalies=[anomaly],
        decisions=[decision],
    )

    result = supervisor.on_process_exited(
        run_context=_run_context(),
        execution_handle=_handle(bound=True, alive=False),
    )
    first = supervisor.pending_decision

    assert result == decision
    assert first == decision
    assert first is not decision
    first.required_post_checks.append("caller-mutation")
    assert supervisor.pending_decision.required_post_checks == ["result_exists"]
    assert supervisor.state.status is SupervisorStatus.AWAITING_APPROVAL
    assert supervisor.state.awaiting_approval is True
    assert supervisor.state.active_recovery_decision_id == decision.decision_id
    assert supervisor.state.pending_approval_decision_id == decision.decision_id
    assert supervisor.state.recovery_attempts == 0
    assert supervisor.state.recovery_budget_remaining == 1


def test_pending_decision_can_be_approved_once_without_mutating_controller_output() -> None:
    anomaly = _anomaly()
    decision = _pending_decision(anomaly)
    decision_before = decision.to_json()
    gate = RecordingApprovalGate()
    supervisor, *_ = _supervisor(
        validation=_validation(status=ValidationStatus.BLOCK),
        anomalies=[anomaly],
        decisions=[decision],
        approval_gate=gate,
    )
    supervisor.on_process_exited(
        run_context=_run_context(),
        execution_handle=_handle(bound=True, alive=False),
    )

    resolved = supervisor.resolve_recovery_approval(
        decision_id=decision.decision_id,
        approved=True,
        approved_by="reviewer-001",
        reason="Approve the bounded retry",
    )

    assert resolved.approval_status == "approved"
    assert resolved.executable is True
    assert resolved.budget_after == 0
    assert resolved is not gate.calls[0][0]
    assert gate.calls[0][0] is not decision
    assert decision.to_json() == decision_before
    assert supervisor.pending_decision is None
    assert supervisor.state.status is SupervisorStatus.DECIDING
    assert supervisor.state.awaiting_approval is False
    assert supervisor.state.active_recovery_decision_id == decision.decision_id
    assert supervisor.state.pending_approval_decision_id is None
    assert supervisor.state.recovery_budget_remaining == resolved.budget_after
    assert supervisor.state.recovery_attempts == 0
    with pytest.raises(ValueError, match="pending"):
        supervisor.resolve_recovery_approval(
            decision_id=decision.decision_id,
            approved=True,
            approved_by="reviewer-001",
            reason="Resolve twice",
        )
    assert len(gate.calls) == 1


def test_pending_decision_can_be_rejected_once_without_spending_budget() -> None:
    anomaly = _anomaly()
    decision = _pending_decision(anomaly)
    gate = RecordingApprovalGate()
    supervisor, *_ = _supervisor(
        validation=_validation(status=ValidationStatus.BLOCK),
        anomalies=[anomaly],
        decisions=[decision],
        approval_gate=gate,
    )
    supervisor.on_process_exited(
        run_context=_run_context(),
        execution_handle=_handle(bound=True, alive=False),
    )

    resolved = supervisor.resolve_recovery_approval(
        decision_id=decision.decision_id,
        approved=False,
        reason="Reject the retry",
    )

    assert resolved.approval_status == "rejected"
    assert resolved.executable is False
    assert resolved.budget_after == decision.budget_before
    assert supervisor.pending_decision is None
    assert supervisor.state.status is SupervisorStatus.DECIDING
    assert supervisor.state.awaiting_approval is False
    assert supervisor.state.active_recovery_decision_id is None
    assert supervisor.state.pending_approval_decision_id is None
    assert supervisor.state.recovery_budget_remaining == 1
    assert supervisor.state.recovery_attempts == 0


@pytest.mark.parametrize("approved", [0, 1, "true", None])
def test_approval_choice_is_a_strict_bool(approved: object) -> None:
    supervisor, *_ = _supervisor()
    with pytest.raises(TypeError, match="approved"):
        supervisor.resolve_recovery_approval(
            decision_id="decision-pending",
            approved=approved,  # type: ignore[arg-type]
            reason="Invalid choice",
        )


def test_approval_rejects_missing_pending_wrong_identity_and_missing_gate() -> None:
    supervisor, *_ = _supervisor()
    with pytest.raises(ValueError, match="pending"):
        supervisor.resolve_recovery_approval(
            decision_id="decision-unknown",
            approved=False,
            reason="No pending decision",
        )

    anomaly = _anomaly()
    decision = _pending_decision(anomaly)
    supervisor, *_ = _supervisor(
        validation=_validation(status=ValidationStatus.BLOCK),
        anomalies=[anomaly],
        decisions=[decision],
    )
    supervisor.on_process_exited(
        run_context=_run_context(),
        execution_handle=_handle(bound=True, alive=False),
    )
    with pytest.raises(ValueError, match="decision_id"):
        supervisor.resolve_recovery_approval(
            decision_id="decision-other",
            approved=False,
            reason="Wrong identity",
        )
    with pytest.raises(RuntimeError, match="approval gate"):
        supervisor.resolve_recovery_approval(
            decision_id=decision.decision_id,
            approved=False,
            reason="No gate",
        )


def test_gate_failure_is_wrapped_and_pending_state_is_preserved() -> None:
    anomaly = _anomaly()
    decision = _pending_decision(anomaly)
    gate = RecordingApprovalGate(RuntimeError("private approval content"))
    supervisor, *_ = _supervisor(
        validation=_validation(status=ValidationStatus.BLOCK),
        anomalies=[anomaly],
        decisions=[decision],
        approval_gate=gate,
    )
    supervisor.on_process_exited(
        run_context=_run_context(),
        execution_handle=_handle(bound=True, alive=False),
    )

    with pytest.raises(
        RuntimeError,
        match="^Feedback Supervisor approval resolution failed$",
    ) as caught:
        supervisor.resolve_recovery_approval(
            decision_id=decision.decision_id,
            approved=False,
            reason="Trigger the gate error",
        )

    assert isinstance(caught.value.__cause__, RuntimeError)
    assert supervisor.pending_decision == decision
    assert supervisor.state.awaiting_approval is True


def test_automatic_low_risk_decision_never_enters_the_approval_gate() -> None:
    anomaly = _anomaly()
    decision = _automatic_decision(anomaly)
    gate = RecordingApprovalGate()
    supervisor, *_ = _supervisor(
        validation=_validation(status=ValidationStatus.BLOCK),
        anomalies=[anomaly],
        decisions=[decision],
        approval_gate=gate,
    )

    result = supervisor.on_process_exited(
        run_context=_run_context(),
        execution_handle=_handle(bound=True, alive=False),
    )

    assert result == decision
    assert supervisor.pending_decision is None
    assert supervisor.state.awaiting_approval is False
    assert gate.calls == []


@pytest.mark.parametrize("snapshot_run_id", ["", "find-other-run"])
def test_process_exited_does_not_merge_unbound_or_other_run_snapshot(snapshot_run_id: str) -> None:
    snapshot = _snapshot(run_id=snapshot_run_id)
    validation = _validation()
    supervisor, _, _, builder, _ = _supervisor(
        snapshots=[snapshot],
        validation=validation,
        anomalies=[None, None],
    )
    context = _run_context()
    supervisor.on_monitor_tick(
        run_context=context,
        execution_handle=_handle(bound=bool(snapshot_run_id)),
    )

    assert supervisor.on_process_exited(
        run_context=context,
        execution_handle=_handle(bound=True, alive=False),
    ) is None
    assert builder.calls[-1] == (None, validation)


def test_process_exited_pass_without_anomaly_never_calls_controller_or_observer() -> None:
    supervisor, observer, validator, builder, controller = _supervisor(
        snapshots=[],
        validation=_validation(),
        anomalies=[None],
    )
    result = supervisor.on_process_exited(
        run_context=_run_context(),
        execution_handle=_handle(bound=True, alive=False),
    )
    assert result is None
    assert observer.calls == []
    assert len(validator.calls) == 1
    assert len(builder.calls) == 1
    assert controller.calls == []


def test_duplicate_terminal_anomaly_is_not_decided_twice() -> None:
    anomaly = _anomaly()
    decision = _decision(anomaly)
    supervisor, _, _, _, controller = _supervisor(
        validation=_validation(),
        anomalies=[anomaly, anomaly],
        decisions=[decision],
    )
    context = _run_context()
    handle = _handle(bound=True, alive=False)

    assert supervisor.on_process_exited(run_context=context, execution_handle=handle) == decision
    assert supervisor.on_process_exited(run_context=context, execution_handle=handle) is None
    assert len(controller.calls) == 1


def test_active_decision_blocks_new_anomaly_but_no_action_allows_one() -> None:
    first = _anomaly(anomaly_id="anomaly-first", fingerprint="first")
    second = _anomaly(anomaly_id="anomaly-second", fingerprint="second")
    active = _decision(first)
    supervisor, _, _, _, controller = _supervisor(
        validation=_validation(),
        anomalies=[first, second],
        decisions=[active],
    )
    context = _run_context()
    handle = _handle(bound=True, alive=False)
    assert supervisor.on_process_exited(run_context=context, execution_handle=handle) == active
    assert supervisor.on_process_exited(run_context=context, execution_handle=handle) is None
    assert len(controller.calls) == 1

    no_action = _decision(first, RecoveryAction.NO_ACTION)
    follow_up = _decision(second)
    supervisor, _, _, _, controller = _supervisor(
        validation=_validation(),
        anomalies=[first, second],
        decisions=[no_action, follow_up],
    )
    assert supervisor.on_process_exited(run_context=context, execution_handle=handle) == no_action
    assert supervisor.state.active_recovery_decision_id is None
    assert supervisor.on_process_exited(run_context=context, execution_handle=handle) == follow_up
    assert len(controller.calls) == 2


@pytest.mark.parametrize(
    ("handle", "message"),
    [
        (_handle(bound=True, alive=True), "still running"),
        (_handle(bound=True, alive=False, exit_code=None), "exit_code"),
        (_handle(bound=False, alive=False), "run_id"),
    ],
)
def test_process_exited_rejects_incomplete_terminal_handle(handle: ExecutionHandle, message: str) -> None:
    supervisor, *_ = _supervisor()
    with pytest.raises(ValueError, match=message):
        supervisor.on_process_exited(
            run_context=_run_context(),
            execution_handle=handle,
        )


@pytest.mark.parametrize(
    ("validation", "anomalies", "decisions", "message"),
    [
        (RuntimeError("private validator detail"), [None], [], "validator"),
        (_validation(), [RuntimeError("private builder detail")], [], "anomaly builder"),
        (_validation(), [_anomaly()], [RuntimeError("private controller detail")], "recovery controller"),
        (object(), [None], [], "validator"),
        (_validation(), [object()], [], "anomaly builder"),
        (_validation(), [_anomaly()], [object()], "recovery controller"),
    ],
)
def test_terminal_component_failures_are_wrapped_with_cause_and_not_retried(
    validation: object,
    anomalies: list[object],
    decisions: list[object],
    message: str,
) -> None:
    supervisor, observer, validator, builder, controller = _supervisor(
        snapshots=[],
        validation=validation,
        anomalies=anomalies,
        decisions=decisions,
    )
    with pytest.raises(RuntimeError, match=message) as caught:
        supervisor.on_process_exited(
            run_context=_run_context(),
            execution_handle=_handle(bound=True, alive=False),
        )
    assert caught.value.__cause__ is not None
    assert observer.calls == []
    assert len(validator.calls) == 1
    assert len(builder.calls) <= 1
    assert len(controller.calls) <= 1


def test_supervisor_does_not_mutate_inputs_or_expose_mutable_internal_state() -> None:
    context = _run_context()
    handle = _handle()
    state = _state()
    snapshot = _snapshot()
    anomaly = _anomaly(run_id="")
    originals = tuple(deepcopy(value.to_dict()) for value in (context, handle, state, snapshot, anomaly))
    supervisor, *_ = _supervisor(
        snapshots=[snapshot],
        anomalies=[anomaly],
        initial_state=state,
    )

    supervisor.on_monitor_tick(run_context=context, execution_handle=handle)
    external_state = supervisor.state
    external_state.gate_reason = "caller mutation"

    assert tuple(value.to_dict() for value in (context, handle, state, snapshot, anomaly)) == originals
    assert supervisor.state.gate_reason == "Preparing Find supervision"


def test_trace_summary_starts_empty_and_counts_successful_monitor_calls() -> None:
    first = _snapshot(sequence=0)
    second = _snapshot(sequence=1, status=ProgressStatus.RUNNING)
    supervisor, *_ = _supervisor(
        snapshots=[first, second],
        anomalies=[None, None],
    )

    assert supervisor.trace_summary == {
        "monitor_calls": 0,
        "observer_status": None,
        "validation_status": None,
        "anomaly_kind": None,
        "controller_called": False,
        "recovery_decision_action": None,
    }

    supervisor.on_monitor_tick(
        run_context=_run_context(),
        execution_handle=_handle(),
    )
    assert supervisor.trace_summary["monitor_calls"] == 1
    assert supervisor.trace_summary["observer_status"] == "starting"

    supervisor.on_monitor_tick(
        run_context=_run_context(),
        execution_handle=_handle(),
    )
    assert supervisor.trace_summary["monitor_calls"] == 2
    assert supervisor.trace_summary["observer_status"] == "running"


def test_trace_summary_records_normal_terminal_validation_without_controller() -> None:
    supervisor, *_ = _supervisor(
        snapshots=[],
        validation=_validation(),
        anomalies=[None],
    )

    assert supervisor.on_process_exited(
        run_context=_run_context(),
        execution_handle=_handle(bound=True, alive=False),
    ) is None
    assert supervisor.trace_summary == {
        "monitor_calls": 0,
        "observer_status": None,
        "validation_status": "pass",
        "anomaly_kind": None,
        "controller_called": False,
        "recovery_decision_action": None,
    }


def test_trace_summary_records_anomaly_and_recovery_decision() -> None:
    anomaly = _anomaly()
    decision = _decision(anomaly)
    supervisor, *_ = _supervisor(
        snapshots=[],
        validation=_validation(status=ValidationStatus.BLOCK),
        anomalies=[anomaly],
        decisions=[decision],
    )

    assert supervisor.on_process_exited(
        run_context=_run_context(),
        execution_handle=_handle(bound=True, alive=False),
    ) == decision
    assert supervisor.trace_summary == {
        "monitor_calls": 0,
        "observer_status": None,
        "validation_status": "block",
        "anomaly_kind": anomaly.kind,
        "controller_called": True,
        "recovery_decision_action": "stop_and_report",
    }


def test_trace_summary_counts_controller_call_that_raises() -> None:
    anomaly = _anomaly()
    supervisor, *_ = _supervisor(
        snapshots=[],
        validation=_validation(status=ValidationStatus.BLOCK),
        anomalies=[anomaly],
        decisions=[RuntimeError("private controller detail")],
    )

    with pytest.raises(RuntimeError, match="recovery controller"):
        supervisor.on_process_exited(
            run_context=_run_context(),
            execution_handle=_handle(bound=True, alive=False),
        )

    assert supervisor.trace_summary["controller_called"] is True
    assert supervisor.trace_summary["recovery_decision_action"] is None
    assert supervisor.trace_summary["anomaly_kind"] == anomaly.kind


def test_trace_summary_does_not_count_duplicate_terminal_anomaly_twice() -> None:
    anomaly = _anomaly()
    decision = _decision(anomaly)
    supervisor, *_ = _supervisor(
        validation=_validation(status=ValidationStatus.BLOCK),
        anomalies=[anomaly, anomaly],
        decisions=[decision],
    )
    context = _run_context()
    handle = _handle(bound=True, alive=False)

    assert supervisor.on_process_exited(run_context=context, execution_handle=handle) == decision
    assert supervisor.on_process_exited(run_context=context, execution_handle=handle) is None
    assert supervisor.trace_summary["controller_called"] is True
    assert supervisor._controller_call_count == 1


def test_trace_summary_returns_a_fresh_detached_mapping() -> None:
    supervisor, *_ = _supervisor()

    first = supervisor.trace_summary
    first["monitor_calls"] = 99
    first["observer_status"] = "caller-mutation"

    assert supervisor.trace_summary["monitor_calls"] == 0
    assert supervisor.trace_summary["observer_status"] is None
