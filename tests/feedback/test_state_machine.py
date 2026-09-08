from __future__ import annotations

from datetime import datetime, timedelta, timezone
import inspect

import pytest

from feedback import (
    SupervisorCommand,
    SupervisorEvent,
    SupervisorEventType,
    SupervisorState,
    SupervisorStatus,
    TransitionOutcome,
    TransitionRule,
    advance_state,
    resolve_transition,
)
from feedback import state_machine


_BASE_TIME = datetime(2026, 8, 29, 9, 0, tzinfo=timezone.utc)


def _idle_state(*, event_sequence: int = 0) -> SupervisorState:
    return SupervisorState(
        supervisor_id="supervisor-001",
        project_id="project-001",
        root_run_id=None,
        status=SupervisorStatus.IDLE,
        state_revision=0,
        created_at=_BASE_TIME,
        updated_at=_BASE_TIME,
        heartbeat_at=_BASE_TIME,
        producer="test",
        producer_version="1",
        process_alive=False,
        cancel_requested=False,
        recovery_attempts=0,
        recovery_budget_total=1,
        recovery_budget_remaining=1,
        awaiting_approval=False,
        gate_evaluated=False,
        allow_read=False,
        gate_reason="not evaluated",
        terminal=False,
        event_sequence=event_sequence,
        state_path="feedback/supervisor_state.json",
    )


def _running_state() -> SupervisorState:
    return SupervisorState(
        supervisor_id="supervisor-001",
        project_id="project-001",
        root_run_id="find_001",
        status=SupervisorStatus.RUNNING,
        state_revision=3,
        created_at=_BASE_TIME,
        updated_at=_BASE_TIME,
        heartbeat_at=_BASE_TIME,
        producer="test",
        producer_version="1",
        process_alive=True,
        cancel_requested=False,
        recovery_attempts=0,
        recovery_budget_total=1,
        recovery_budget_remaining=1,
        awaiting_approval=False,
        gate_evaluated=False,
        allow_read=False,
        gate_reason="not evaluated",
        terminal=False,
        event_sequence=4,
        state_path="feedback/supervisor_state.json",
        run_context_id="context-001",
        active_run_id="find_001",
        active_pid=1234,
        process_started_at=_BASE_TIME,
    )


def _deciding_state(
    *,
    event_sequence: int = 0,
    recovery_budget_remaining: int = 1,
) -> SupervisorState:
    return SupervisorState(
        supervisor_id="supervisor-001",
        project_id="project-001",
        root_run_id="find_001",
        status=SupervisorStatus.DECIDING,
        state_revision=0,
        created_at=_BASE_TIME,
        updated_at=_BASE_TIME,
        heartbeat_at=_BASE_TIME,
        producer="test",
        producer_version="1",
        process_alive=False,
        cancel_requested=False,
        recovery_attempts=0,
        recovery_budget_total=1,
        recovery_budget_remaining=recovery_budget_remaining,
        awaiting_approval=False,
        gate_evaluated=False,
        allow_read=False,
        gate_reason="not evaluated",
        terminal=False,
        event_sequence=event_sequence,
        state_path="feedback/supervisor_state.json",
        run_context_id="context-001",
        active_anomaly_id="anomaly-001",
    )


def _awaiting_approval_state(
    *,
    event_sequence: int = 0,
    recovery_budget_remaining: int = 1,
) -> SupervisorState:
    return SupervisorState(
        supervisor_id="supervisor-001",
        project_id="project-001",
        root_run_id="find_001",
        status=SupervisorStatus.AWAITING_APPROVAL,
        state_revision=0,
        created_at=_BASE_TIME,
        updated_at=_BASE_TIME,
        heartbeat_at=_BASE_TIME,
        producer="test",
        producer_version="1",
        process_alive=False,
        cancel_requested=False,
        recovery_attempts=0,
        recovery_budget_total=1,
        recovery_budget_remaining=recovery_budget_remaining,
        awaiting_approval=True,
        gate_evaluated=False,
        allow_read=False,
        gate_reason="not evaluated",
        terminal=False,
        event_sequence=event_sequence,
        state_path="feedback/supervisor_state.json",
        run_context_id="context-001",
        active_anomaly_id="anomaly-001",
        active_recovery_decision_id="decision-001",
        pending_approval_decision_id="decision-001",
    )


def _recovering_state(*, event_sequence: int = 0) -> SupervisorState:
    return SupervisorState(
        supervisor_id="supervisor-001",
        project_id="project-001",
        root_run_id="find_001",
        status=SupervisorStatus.RECOVERING,
        state_revision=0,
        created_at=_BASE_TIME,
        updated_at=_BASE_TIME,
        heartbeat_at=_BASE_TIME,
        producer="test",
        producer_version="1",
        process_alive=True,
        cancel_requested=False,
        recovery_attempts=1,
        recovery_budget_total=1,
        recovery_budget_remaining=0,
        awaiting_approval=False,
        gate_evaluated=False,
        allow_read=False,
        gate_reason="not evaluated",
        terminal=False,
        event_sequence=event_sequence,
        state_path="feedback/supervisor_state.json",
        run_context_id="context-001",
        active_run_id="find_002",
        active_anomaly_id="anomaly-001",
        active_recovery_decision_id="decision-001",
        active_pid=2345,
        process_started_at=_BASE_TIME,
    )


def _gating_state() -> SupervisorState:
    running = _running_state()
    validating = advance_state(
        running,
        _event(SupervisorEventType.PROCESS_EXITED, sequence=5),
        updates={"process_alive": False, "exit_code": 0},
    )
    return advance_state(
        validating.state,
        _event(
            SupervisorEventType.VALIDATION_PASSED,
            sequence=6,
            occurred_at=_BASE_TIME + timedelta(minutes=2),
        ),
        updates={"latest_validation_id": "validation-001"},
    ).state


def _preparing_state() -> SupervisorState:
    return advance_state(_idle_state(), _event(SupervisorEventType.START, sequence=1)).state


def _validating_state() -> SupervisorState:
    running = _running_state()
    return advance_state(
        running,
        _next_event(running, SupervisorEventType.PROCESS_EXITED),
        updates={"process_alive": False, "exit_code": 0},
    ).state


def _diagnosing_state() -> SupervisorState:
    running = _running_state()
    return advance_state(
        running,
        _next_event(running, SupervisorEventType.STALL_DETECTED),
        updates={"latest_progress_snapshot_id": "progress-001", "process_alive": False},
    ).state


def _next_event(state: SupervisorState, event_type: SupervisorEventType) -> SupervisorEvent:
    return _event(
        event_type,
        sequence=state.event_sequence + 1,
        occurred_at=state.updated_at + timedelta(seconds=1),
    )


def _event(
    event_type: SupervisorEventType,
    *,
    sequence: int,
    occurred_at: datetime | None = None,
) -> SupervisorEvent:
    return SupervisorEvent(
        sequence=sequence,
        occurred_at=occurred_at or _BASE_TIME + timedelta(minutes=1),
        event_type=event_type,
        message=f"event: {event_type.value}",
    )


@pytest.mark.parametrize(
    ("current_status", "event_type", "target_status"),
    [
        (SupervisorStatus.IDLE, SupervisorEventType.START, SupervisorStatus.PREPARING),
        (SupervisorStatus.PREPARING, SupervisorEventType.PREPARED, SupervisorStatus.PREPARING),
        (SupervisorStatus.PREPARING, SupervisorEventType.NEW_RUN_STARTED, SupervisorStatus.RUNNING),
        (SupervisorStatus.RUNNING, SupervisorEventType.MONITOR_TICK, SupervisorStatus.RUNNING),
        (SupervisorStatus.RUNNING, SupervisorEventType.PROCESS_EXITED, SupervisorStatus.VALIDATING),
        (SupervisorStatus.RUNNING, SupervisorEventType.STALL_DETECTED, SupervisorStatus.DIAGNOSING),
        (SupervisorStatus.VALIDATING, SupervisorEventType.VALIDATION_PASSED, SupervisorStatus.GATING),
        (SupervisorStatus.DIAGNOSING, SupervisorEventType.ANOMALY_READY, SupervisorStatus.DECIDING),
        (SupervisorStatus.DECIDING, SupervisorEventType.RETRY_DECIDED, SupervisorStatus.RECOVERING),
        (SupervisorStatus.PREPARING, SupervisorEventType.PREPARE_FAILED, SupervisorStatus.FAILED),
        (SupervisorStatus.VALIDATING, SupervisorEventType.VALIDATION_BLOCKED, SupervisorStatus.DIAGNOSING),
        (SupervisorStatus.RUNNING, SupervisorEventType.ANOMALY_DETECTED, SupervisorStatus.DIAGNOSING),
        (SupervisorStatus.DIAGNOSING, SupervisorEventType.DIAGNOSIS_FAILED, SupervisorStatus.FAILED),
        (
            SupervisorStatus.DECIDING,
            SupervisorEventType.APPROVAL_REQUIRED,
            SupervisorStatus.AWAITING_APPROVAL,
        ),
        (
            SupervisorStatus.AWAITING_APPROVAL,
            SupervisorEventType.APPROVED,
            SupervisorStatus.RECOVERING,
        ),
        (SupervisorStatus.GATING, SupervisorEventType.GATE_ALLOWED, SupervisorStatus.COMPLETED),
    ],
)
def test_resolve_transition_returns_declared_rule(
    current_status: SupervisorStatus,
    event_type: SupervisorEventType,
    target_status: SupervisorStatus,
) -> None:
    rule = resolve_transition(current_status, event_type)

    assert isinstance(rule, TransitionRule)
    assert rule.current_status is current_status
    assert rule.event_type is event_type
    assert rule.target_status is target_status


@pytest.mark.parametrize(
    ("current_status", "event_type"),
    [
        (SupervisorStatus.IDLE, SupervisorEventType.PROCESS_EXITED),
        (SupervisorStatus.IDLE, SupervisorEventType.GATE_ALLOWED),
        (SupervisorStatus.COMPLETED, SupervisorEventType.MONITOR_TICK),
        (SupervisorStatus.BLOCKED, SupervisorEventType.RETRY_DECIDED),
        (SupervisorStatus.FAILED, SupervisorEventType.START),
        (SupervisorStatus.CANCELLED, SupervisorEventType.APPROVED),
        (SupervisorStatus.RUNNING, SupervisorEventType.GATE_ALLOWED),
        (SupervisorStatus.VALIDATING, SupervisorEventType.APPROVED),
    ],
)
def test_resolve_transition_rejects_undeclared_transition(
    current_status: SupervisorStatus,
    event_type: SupervisorEventType,
) -> None:
    expected = f"Supervisor transition {current_status.value} + {event_type.value} is not allowed"

    with pytest.raises(ValueError) as error:
        resolve_transition(current_status, event_type)

    assert str(error.value) == expected


@pytest.mark.parametrize(
    ("current_status", "event_type", "target_status"),
    [
        (SupervisorStatus.RUNNING, SupervisorEventType.CANCEL_REQUESTED, SupervisorStatus.CANCELLED),
        (SupervisorStatus.DIAGNOSING, SupervisorEventType.CANCEL_REQUESTED, SupervisorStatus.CANCELLED),
        (SupervisorStatus.RECOVERING, SupervisorEventType.FATAL_ERROR, SupervisorStatus.FAILED),
        (SupervisorStatus.GATING, SupervisorEventType.FATAL_ERROR, SupervisorStatus.FAILED),
    ],
)
def test_resolve_transition_supports_global_events(
    current_status: SupervisorStatus,
    event_type: SupervisorEventType,
    target_status: SupervisorStatus,
) -> None:
    assert resolve_transition(current_status, event_type).target_status is target_status


def test_resolve_transition_rejects_invalid_input_types() -> None:
    with pytest.raises(TypeError, match="current_status must be a SupervisorStatus"):
        resolve_transition("idle", SupervisorEventType.START)  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="event_type must be a SupervisorEventType"):
        resolve_transition(SupervisorStatus.IDLE, "start")  # type: ignore[arg-type]


def test_advance_state_returns_immutable_preparing_outcome() -> None:
    state = _idle_state()
    event = _event(SupervisorEventType.START, sequence=1)

    outcome = advance_state(state, event)

    assert isinstance(outcome, TransitionOutcome)
    assert outcome.previous_status is SupervisorStatus.IDLE
    assert outcome.next_status is SupervisorStatus.PREPARING
    assert outcome.event_type is SupervisorEventType.START
    assert outcome.command is SupervisorCommand.CALL_ADAPTER
    assert outcome.state.status is SupervisorStatus.PREPARING
    assert outcome.state.state_revision == 1
    assert outcome.state.event_sequence == 1
    assert outcome.state.recent_events == [event]
    assert outcome.state.updated_at == event.occurred_at
    assert outcome.state.heartbeat_at == event.occurred_at

    assert state.status is SupervisorStatus.IDLE
    assert state.state_revision == 0
    assert state.recent_events == []


def test_advance_state_applies_required_updates_atomically() -> None:
    state = _running_state()
    event = _event(SupervisorEventType.PROCESS_EXITED, sequence=5)

    outcome = advance_state(state, event, updates={"process_alive": False})

    assert outcome.next_status is SupervisorStatus.VALIDATING
    assert outcome.command is SupervisorCommand.CALL_VALIDATOR
    assert outcome.state.process_alive is False
    assert outcome.state.active_run_id == "find_001"


def test_advance_state_rejects_non_continuous_event_sequence() -> None:
    with pytest.raises(ValueError, match=r"event.sequence must equal state.event_sequence \+ 1"):
        advance_state(_idle_state(), _event(SupervisorEventType.START, sequence=2))


def test_advance_state_rejects_event_before_state_update() -> None:
    event = _event(
        SupervisorEventType.START,
        sequence=1,
        occurred_at=_BASE_TIME - timedelta(seconds=1),
    )

    with pytest.raises(ValueError, match="event.occurred_at must not be earlier than state.updated_at"):
        advance_state(_idle_state(), event)


@pytest.mark.parametrize(
    "field_name",
    [
        "status",
        "state_revision",
        "event_sequence",
        "recent_events",
        "updated_at",
        "heartbeat_at",
        "root_run_id",
    ],
)
def test_advance_state_rejects_reserved_updates(field_name: str) -> None:
    with pytest.raises(ValueError, match=f"updates\\.{field_name} is managed by advance_state"):
        advance_state(
            _idle_state(),
            _event(SupervisorEventType.START, sequence=1),
            updates={field_name: None},
        )


def test_advance_state_rejects_updates_that_violate_new_status_contract() -> None:
    with pytest.raises(ValueError, match="SupervisorState.process_alive"):
        advance_state(
            _idle_state(),
            _event(SupervisorEventType.START, sequence=1),
            updates={"process_alive": True},
        )


def test_advance_state_rejects_new_event_not_after_recent_events() -> None:
    recent_event = _event(SupervisorEventType.START, sequence=2)
    state = SupervisorState(
        supervisor_id="supervisor-001",
        project_id="project-001",
        root_run_id=None,
        status=SupervisorStatus.IDLE,
        state_revision=0,
        created_at=_BASE_TIME,
        updated_at=_BASE_TIME,
        heartbeat_at=_BASE_TIME,
        producer="test",
        producer_version="1",
        process_alive=False,
        cancel_requested=False,
        recovery_attempts=0,
        recovery_budget_total=1,
        recovery_budget_remaining=1,
        awaiting_approval=False,
        gate_evaluated=False,
        allow_read=False,
        gate_reason="not evaluated",
        terminal=False,
        event_sequence=1,
        state_path="feedback/supervisor_state.json",
        recent_events=[recent_event],
    )

    with pytest.raises(ValueError, match="event.sequence must be greater than the latest recent event sequence"):
        advance_state(state, _event(SupervisorEventType.START, sequence=2))


def test_advance_state_runs_complete_normal_flow() -> None:
    initial_state = _idle_state()
    events = [
        _event(SupervisorEventType.START, sequence=1, occurred_at=_BASE_TIME + timedelta(minutes=1)),
        _event(SupervisorEventType.PREPARED, sequence=2, occurred_at=_BASE_TIME + timedelta(minutes=2)),
        _event(
            SupervisorEventType.NEW_RUN_STARTED,
            sequence=3,
            occurred_at=_BASE_TIME + timedelta(minutes=3),
        ),
        _event(
            SupervisorEventType.MONITOR_TICK,
            sequence=4,
            occurred_at=_BASE_TIME + timedelta(minutes=4),
        ),
        _event(
            SupervisorEventType.PROCESS_EXITED,
            sequence=5,
            occurred_at=_BASE_TIME + timedelta(minutes=5),
        ),
        _event(
            SupervisorEventType.VALIDATION_PASSED,
            sequence=6,
            occurred_at=_BASE_TIME + timedelta(minutes=6),
        ),
        _event(
            SupervisorEventType.GATE_ALLOWED,
            sequence=7,
            occurred_at=_BASE_TIME + timedelta(minutes=7),
        ),
    ]

    preparing = advance_state(initial_state, events[0])
    assert preparing.previous_status is SupervisorStatus.IDLE
    assert preparing.next_status is SupervisorStatus.PREPARING
    assert preparing.event_type is SupervisorEventType.START
    assert preparing.command is SupervisorCommand.CALL_ADAPTER
    assert preparing.state.status is SupervisorStatus.PREPARING
    assert preparing.state.run_context_id is None
    assert preparing.state.active_run_id is None
    assert preparing.state.process_alive is False

    prepared = advance_state(
        preparing.state,
        events[1],
        updates={
            "run_context_id": "context-001",
        },
    )
    assert prepared.previous_status is SupervisorStatus.PREPARING
    assert prepared.next_status is SupervisorStatus.PREPARING
    assert prepared.event_type is SupervisorEventType.PREPARED
    assert prepared.command is SupervisorCommand.START_EXECUTOR
    assert prepared.state.run_context_id == "context-001"
    assert prepared.state.root_run_id is None
    assert prepared.state.active_run_id is None
    assert prepared.state.process_alive is False

    running = advance_state(
        prepared.state,
        events[2],
        updates={
            "active_run_id": "find_001",
            "process_alive": True,
            "process_started_at": events[2].occurred_at,
            "active_pid": 1234,
        },
    )
    assert running.previous_status is SupervisorStatus.PREPARING
    assert running.next_status is SupervisorStatus.RUNNING
    assert running.event_type is SupervisorEventType.NEW_RUN_STARTED
    assert running.command is SupervisorCommand.POLL_OBSERVER
    assert running.state.status is SupervisorStatus.RUNNING
    assert running.state.root_run_id == "find_001"
    assert running.state.active_run_id == "find_001"
    assert running.state.process_alive is True
    assert running.state.process_started_at == events[2].occurred_at
    assert running.state.active_pid == 1234

    monitored = advance_state(
        running.state,
        events[3],
        updates={"latest_progress_snapshot_id": "progress-001"},
    )
    assert monitored.previous_status is SupervisorStatus.RUNNING
    assert monitored.next_status is SupervisorStatus.RUNNING
    assert monitored.event_type is SupervisorEventType.MONITOR_TICK
    assert monitored.command is SupervisorCommand.POLL_OBSERVER
    assert monitored.state.latest_progress_snapshot_id == "progress-001"
    assert monitored.state.heartbeat_at == events[3].occurred_at

    validating = advance_state(
        monitored.state,
        events[4],
        updates={"process_alive": False, "exit_code": 0},
    )
    assert validating.previous_status is SupervisorStatus.RUNNING
    assert validating.next_status is SupervisorStatus.VALIDATING
    assert validating.event_type is SupervisorEventType.PROCESS_EXITED
    assert validating.command is SupervisorCommand.CALL_VALIDATOR
    assert validating.state.process_alive is False
    assert validating.state.exit_code == 0

    gating = advance_state(
        validating.state,
        events[5],
        updates={"latest_validation_id": "validation-001"},
    )
    assert gating.previous_status is SupervisorStatus.VALIDATING
    assert gating.next_status is SupervisorStatus.GATING
    assert gating.event_type is SupervisorEventType.VALIDATION_PASSED
    assert gating.command is SupervisorCommand.CALL_GATE
    assert gating.state.latest_validation_id == "validation-001"

    completed = advance_state(
        gating.state,
        events[6],
        updates={
            "gate_evaluated": True,
            "allow_read": True,
            "terminal": True,
            "final_status": SupervisorStatus.COMPLETED,
            "terminal_reason": "Find validation passed and the Read gate is open.",
            "process_alive": False,
            "gate_validation_id": "validation-001",
        },
    )
    assert completed.previous_status is SupervisorStatus.GATING
    assert completed.next_status is SupervisorStatus.COMPLETED
    assert completed.event_type is SupervisorEventType.GATE_ALLOWED
    assert completed.command is SupervisorCommand.RECORD_OUTCOME
    assert completed.state.status is SupervisorStatus.COMPLETED
    assert completed.state.terminal is True
    assert completed.state.allow_read is True
    assert completed.state.gate_evaluated is True
    assert completed.state.process_alive is False
    assert completed.state.final_status is SupervisorStatus.COMPLETED

    for index, outcome in enumerate(
        (preparing, prepared, running, monitored, validating, gating, completed),
        start=1,
    ):
        assert outcome.state.state_revision == index
        assert outcome.state.event_sequence == index
        assert outcome.state.recent_events == events[:index]
        assert outcome.state.updated_at == events[index - 1].occurred_at
        assert outcome.state.heartbeat_at == events[index - 1].occurred_at

    assert initial_state.status is SupervisorStatus.IDLE
    assert initial_state.state_revision == 0
    assert initial_state.event_sequence == 0
    assert initial_state.recent_events == []
    assert [event.sequence for event in completed.state.recent_events] == [1, 2, 3, 4, 5, 6, 7]


def test_new_run_started_requires_real_execution_evidence() -> None:
    preparing = advance_state(_idle_state(), _event(SupervisorEventType.START, sequence=1)).state
    prepared = advance_state(
        preparing,
        _next_event(preparing, SupervisorEventType.PREPARED),
        updates={"run_context_id": "context-001"},
    ).state

    with pytest.raises(ValueError, match="active_run_id is required"):
        advance_state(
            prepared,
            _next_event(prepared, SupervisorEventType.NEW_RUN_STARTED),
            updates={"process_alive": True, "active_pid": 1234},
        )

    with pytest.raises(ValueError, match=r"SupervisorState\.active_pid"):
        advance_state(
            prepared,
            _next_event(prepared, SupervisorEventType.NEW_RUN_STARTED),
            updates={"active_run_id": "find_001", "process_alive": True},
        )


def test_advance_state_runs_stall_recovery_flow() -> None:
    failed_run = _running_state()
    events = [
        _event(SupervisorEventType.STALL_DETECTED, sequence=5, occurred_at=_BASE_TIME + timedelta(minutes=1)),
        _event(SupervisorEventType.ANOMALY_READY, sequence=6, occurred_at=_BASE_TIME + timedelta(minutes=2)),
        _event(SupervisorEventType.RETRY_DECIDED, sequence=7, occurred_at=_BASE_TIME + timedelta(minutes=3)),
        _event(SupervisorEventType.NEW_RUN_STARTED, sequence=8, occurred_at=_BASE_TIME + timedelta(minutes=4)),
    ]

    diagnosing = advance_state(
        failed_run,
        events[0],
        updates={"latest_progress_snapshot_id": "progress-001"},
    )
    assert diagnosing.previous_status is SupervisorStatus.RUNNING
    assert diagnosing.next_status is SupervisorStatus.DIAGNOSING
    assert diagnosing.command is SupervisorCommand.CALL_ANOMALY_BUILDER
    assert diagnosing.state.latest_progress_snapshot_id == "progress-001"

    deciding = advance_state(
        diagnosing.state,
        events[1],
        updates={"active_anomaly_id": "anomaly-001"},
    )
    assert deciding.previous_status is SupervisorStatus.DIAGNOSING
    assert deciding.next_status is SupervisorStatus.DECIDING
    assert deciding.command is SupervisorCommand.CALL_RECOVERY_CONTROLLER
    assert deciding.state.active_anomaly_id == "anomaly-001"

    recovering = advance_state(
        deciding.state,
        events[2],
        updates={
            "active_recovery_decision_id": "decision-001",
        },
    )
    assert recovering.previous_status is SupervisorStatus.DECIDING
    assert recovering.next_status is SupervisorStatus.RECOVERING
    assert recovering.command is SupervisorCommand.EXECUTE_RECOVERY
    assert recovering.state.recovery_attempts == 1
    assert recovering.state.recovery_budget_remaining == 0
    assert recovering.state.awaiting_approval is False

    resumed = advance_state(
        recovering.state,
        events[3],
        updates={
            "active_run_id": "find_002",
            "process_alive": True,
            "exit_code": None,
            "process_started_at": events[3].occurred_at,
            "active_pid": 4321,
        },
    )
    assert resumed.previous_status is SupervisorStatus.RECOVERING
    assert resumed.next_status is SupervisorStatus.RUNNING
    assert resumed.command is SupervisorCommand.POLL_OBSERVER
    assert resumed.state.root_run_id == "find_001"
    assert resumed.state.active_run_id == "find_002"
    assert failed_run.active_run_id == "find_001"
    assert resumed.state.process_alive is True
    assert resumed.state.active_pid == 4321
    assert resumed.state.recovery_attempts == 1
    assert resumed.state.recovery_budget_remaining == 0
    assert [event.sequence for event in resumed.state.recent_events] == [5, 6, 7, 8]


def test_advance_state_waits_for_approval_then_recovers() -> None:
    deciding = _deciding_state()
    approval_required = _event(SupervisorEventType.APPROVAL_REQUIRED, sequence=1)
    awaiting = advance_state(
        deciding,
        approval_required,
        updates={
            "active_recovery_decision_id": "decision-001",
            "pending_approval_decision_id": "decision-001",
            "awaiting_approval": True,
        },
    )
    assert awaiting.next_status is SupervisorStatus.AWAITING_APPROVAL
    assert awaiting.command is SupervisorCommand.WAIT_FOR_APPROVAL
    assert awaiting.state.active_anomaly_id == "anomaly-001"
    assert awaiting.state.active_recovery_decision_id == "decision-001"

    approved = _event(SupervisorEventType.APPROVED, sequence=2, occurred_at=_BASE_TIME + timedelta(minutes=2))
    recovering = advance_state(
        awaiting.state,
        approved,
    )
    assert recovering.next_status is SupervisorStatus.RECOVERING
    assert recovering.command is SupervisorCommand.EXECUTE_RECOVERY
    assert recovering.state.active_anomaly_id == "anomaly-001"
    assert recovering.state.active_recovery_decision_id == "decision-001"
    assert recovering.state.awaiting_approval is False
    assert recovering.state.pending_approval_decision_id is None
    assert recovering.state.recovery_attempts == 1
    assert recovering.state.recovery_budget_remaining == 0


def test_advance_state_blocks_when_user_rejects_approval() -> None:
    awaiting = _awaiting_approval_state()

    outcome = advance_state(
        awaiting,
        _event(SupervisorEventType.REJECTED, sequence=1),
        updates={
            "awaiting_approval": False,
            "pending_approval_decision_id": None,
            "terminal": True,
            "process_alive": False,
            "allow_read": False,
            "terminal_reason": "User rejected the high-risk recovery.",
            "final_status": SupervisorStatus.BLOCKED,
        },
    )

    assert outcome.next_status is SupervisorStatus.BLOCKED
    assert outcome.command is SupervisorCommand.RECORD_OUTCOME
    assert outcome.state.terminal is True
    assert outcome.state.allow_read is False
    assert outcome.state.terminal_reason == "User rejected the high-risk recovery."
    assert outcome.state.final_status is SupervisorStatus.BLOCKED


def test_advance_state_returns_to_diagnosing_when_recovery_fails() -> None:
    recovering = _recovering_state()

    outcome = advance_state(
        recovering,
        _event(SupervisorEventType.RECOVERY_FAILED, sequence=1),
        updates={
            "latest_progress_snapshot_id": "progress-recovery-failed",
            "process_alive": False,
            "active_pid": None,
            "process_started_at": None,
            "exit_code": 1,
        },
    )

    assert outcome.next_status is SupervisorStatus.DIAGNOSING
    assert outcome.command is SupervisorCommand.CALL_ANOMALY_BUILDER
    assert outcome.state.latest_progress_snapshot_id == "progress-recovery-failed"
    assert outcome.state.process_alive is False
    assert outcome.state.active_pid is None
    assert outcome.state.process_started_at is None
    assert outcome.state.exit_code == 1
    assert outcome.state.recovery_attempts == recovering.recovery_attempts
    assert outcome.state.recovery_budget_remaining == recovering.recovery_budget_remaining


def test_advance_state_blocks_when_recovery_is_stopped() -> None:
    deciding = _deciding_state()

    outcome = advance_state(
        deciding,
        _event(SupervisorEventType.STOP_DECIDED, sequence=1),
        updates={
            "terminal": True,
            "process_alive": False,
            "allow_read": False,
            "terminal_reason": "Recovery is not permitted for this run.",
            "final_status": SupervisorStatus.BLOCKED,
        },
    )

    assert outcome.next_status is SupervisorStatus.BLOCKED
    assert outcome.command is SupervisorCommand.RECORD_OUTCOME
    assert outcome.state.terminal is True
    assert outcome.state.final_status is SupervisorStatus.BLOCKED


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("recovery_attempts", 1),
        ("recovery_budget_remaining", 0),
    ],
)
def test_advance_state_rejects_manual_recovery_budget_updates(
    field_name: str,
    value: int,
) -> None:
    state = _deciding_state()

    with pytest.raises(ValueError, match=f"updates\\.{field_name} is managed by advance_state"):
        advance_state(
            state,
            _next_event(state, SupervisorEventType.RETRY_DECIDED),
            updates={field_name: value, "active_recovery_decision_id": "decision-001"},
        )

    assert state.recovery_attempts == 0
    assert state.recovery_budget_remaining == 1


def test_recovery_entry_paths_consume_the_same_budget() -> None:
    deciding = _deciding_state()
    retry_outcome = advance_state(
        deciding,
        _next_event(deciding, SupervisorEventType.RETRY_DECIDED),
        updates={"active_recovery_decision_id": "decision-retry-001"},
    )

    awaiting = _awaiting_approval_state()
    approved_outcome = advance_state(
        awaiting,
        _next_event(awaiting, SupervisorEventType.APPROVED),
    )

    assert retry_outcome.state.recovery_attempts == approved_outcome.state.recovery_attempts == 1
    assert retry_outcome.state.recovery_budget_remaining == approved_outcome.state.recovery_budget_remaining == 0
    assert approved_outcome.state.awaiting_approval is False
    assert approved_outcome.state.pending_approval_decision_id is None


@pytest.mark.parametrize(
    ("state", "event_type", "updates"),
    [
        (
            _deciding_state(recovery_budget_remaining=0),
            SupervisorEventType.RETRY_DECIDED,
            {"active_recovery_decision_id": "decision-retry-001"},
        ),
        (
            _awaiting_approval_state(recovery_budget_remaining=0),
            SupervisorEventType.APPROVED,
            {},
        ),
    ],
    ids=["automatic_retry", "approved_retry"],
)
def test_recovery_entry_rejects_exhausted_budget_without_mutating_state(
    state: SupervisorState,
    event_type: SupervisorEventType,
    updates: dict[str, object],
) -> None:
    event = _next_event(state, event_type)

    with pytest.raises(ValueError, match="recovery_budget_remaining must be greater than 0"):
        advance_state(state, event, updates=updates)

    assert state.status in {SupervisorStatus.DECIDING, SupervisorStatus.AWAITING_APPROVAL}
    assert state.recovery_attempts == 0
    assert state.recovery_budget_remaining == 0
    assert state.event_sequence == 0


def test_advance_state_returns_to_diagnosing_for_recoverable_gate_block() -> None:
    gating = _gating_state()

    outcome = advance_state(
        gating,
        _next_event(gating, SupervisorEventType.GATE_BLOCKED_RECOVERABLE),
        updates={"terminal": False, "allow_read": False},
    )

    assert outcome.previous_status is SupervisorStatus.GATING
    assert outcome.next_status is SupervisorStatus.DIAGNOSING
    assert outcome.command is SupervisorCommand.CALL_ANOMALY_BUILDER
    assert outcome.state.latest_validation_id == "validation-001"
    assert outcome.state.terminal is False
    assert outcome.state.allow_read is False


def test_advance_state_blocks_for_final_gate_failure() -> None:
    gating = _gating_state()

    outcome = advance_state(
        gating,
        _next_event(gating, SupervisorEventType.GATE_BLOCKED_FINAL),
        updates={
            "gate_evaluated": True,
            "allow_read": False,
            "terminal": True,
            "process_alive": False,
            "terminal_reason": "Find output cannot pass the Read gate.",
            "final_status": SupervisorStatus.BLOCKED,
        },
    )

    assert outcome.next_status is SupervisorStatus.BLOCKED
    assert outcome.command is SupervisorCommand.RECORD_OUTCOME
    assert outcome.state.gate_evaluated is True
    assert outcome.state.terminal is True
    assert outcome.state.final_status is SupervisorStatus.BLOCKED


@pytest.mark.parametrize(
    "state",
    [
        _idle_state(),
        _running_state(),
        _recovering_state(),
    ],
    ids=["preparing", "running", "recovering"],
)
def test_advance_state_cancels_nonterminal_statuses(state: SupervisorState) -> None:
    if state.status is SupervisorStatus.IDLE:
        state = advance_state(state, _next_event(state, SupervisorEventType.START)).state

    outcome = advance_state(
        state,
        _next_event(state, SupervisorEventType.CANCEL_REQUESTED),
        updates={
            "cancel_requested": True,
            "cancel_requested_at": state.updated_at + timedelta(seconds=1),
            "process_alive": False,
            "terminal": True,
            "allow_read": False,
            "terminal_reason": "Cancellation requested by the user.",
            "final_status": SupervisorStatus.CANCELLED,
        },
    )

    assert outcome.next_status is SupervisorStatus.CANCELLED
    assert outcome.command is SupervisorCommand.CANCEL
    assert outcome.state.cancel_requested is True
    assert outcome.state.terminal is True
    assert outcome.state.final_status is SupervisorStatus.CANCELLED


def test_advance_state_cancels_diagnosing_status() -> None:
    running = _running_state()
    diagnosing = advance_state(
        running,
        _next_event(running, SupervisorEventType.STALL_DETECTED),
        updates={"latest_progress_snapshot_id": "progress-001"},
    ).state

    outcome = advance_state(
        diagnosing,
        _next_event(diagnosing, SupervisorEventType.CANCEL_REQUESTED),
        updates={
            "cancel_requested": True,
            "cancel_requested_at": diagnosing.updated_at + timedelta(seconds=1),
            "process_alive": False,
            "terminal": True,
            "allow_read": False,
            "terminal_reason": "Cancellation requested by the user.",
            "final_status": SupervisorStatus.CANCELLED,
        },
    )

    assert outcome.next_status is SupervisorStatus.CANCELLED
    assert outcome.command is SupervisorCommand.CANCEL


@pytest.mark.parametrize(
    "state",
    [
        _deciding_state(),
        _gating_state(),
    ],
    ids=["deciding", "gating"],
)
def test_advance_state_fails_nonterminal_statuses(state: SupervisorState) -> None:
    outcome = advance_state(
        state,
        _next_event(state, SupervisorEventType.FATAL_ERROR),
        updates={
            "process_alive": False,
            "allow_read": False,
            "terminal": True,
            "last_error": "The supervisor encountered an unrecoverable internal error.",
            "final_status": SupervisorStatus.FAILED,
        },
    )

    assert outcome.next_status is SupervisorStatus.FAILED
    assert outcome.command is SupervisorCommand.FAIL
    assert outcome.state.terminal is True
    assert outcome.state.final_status is SupervisorStatus.FAILED


def test_advance_state_fails_preparing_and_validating_statuses() -> None:
    preparing = advance_state(_idle_state(), _event(SupervisorEventType.START, sequence=1)).state
    running = _running_state()
    validating = advance_state(
        running,
        _next_event(running, SupervisorEventType.PROCESS_EXITED),
        updates={"process_alive": False, "exit_code": 0},
    ).state

    for state in (preparing, validating):
        outcome = advance_state(
            state,
            _next_event(state, SupervisorEventType.FATAL_ERROR),
            updates={
                "process_alive": False,
                "allow_read": False,
                "terminal": True,
                "terminal_reason": "The supervisor encountered an unrecoverable internal error.",
                "final_status": SupervisorStatus.FAILED,
            },
        )
        assert outcome.next_status is SupervisorStatus.FAILED
        assert outcome.command is SupervisorCommand.FAIL


def test_terminal_statuses_reject_normal_business_events() -> None:
    invalid_pairs = (
        (SupervisorStatus.COMPLETED, SupervisorEventType.MONITOR_TICK),
        (SupervisorStatus.BLOCKED, SupervisorEventType.RETRY_DECIDED),
        (SupervisorStatus.FAILED, SupervisorEventType.START),
        (SupervisorStatus.CANCELLED, SupervisorEventType.APPROVED),
    )

    for status, event_type in invalid_pairs:
        with pytest.raises(ValueError, match="is not allowed"):
            resolve_transition(status, event_type)


def test_feedback_package_exports_supervisor_state_machine_api() -> None:
    import feedback

    expected_exports = {
        "SupervisorStatus": SupervisorStatus,
        "SupervisorEventType": SupervisorEventType,
        "SupervisorEvent": SupervisorEvent,
        "SupervisorCommand": SupervisorCommand,
        "TransitionRule": TransitionRule,
        "TransitionOutcome": TransitionOutcome,
        "resolve_transition": resolve_transition,
        "advance_state": advance_state,
    }

    for name, value in expected_exports.items():
        assert getattr(feedback, name) is value
        assert name in feedback.__all__


def test_state_machine_remains_a_pure_transition_module() -> None:
    source = inspect.getsource(state_machine).lower()
    forbidden_tokens = (
        "subprocess",
        "popen",
        "requests",
        "urllib",
        "socket",
        "open(",
        ".runtime",
        "modules/finding",
        "modules/reading",
        "claude",
        "experience store",
    )

    assert not [token for token in forbidden_tokens if token in source]


def test_supervisor_state_and_event_definitions_are_complete() -> None:
    assert len(SupervisorStatus) == 13
    assert {member.name: member.value for member in SupervisorEventType} == {
        "START": "start",
        "PREPARED": "prepared",
        "PREPARE_FAILED": "prepare_failed",
        "MONITOR_TICK": "monitor_tick",
        "PROCESS_EXITED": "process_exited",
        "STALL_DETECTED": "stall_detected",
        "VALIDATION_PASSED": "validation_passed",
        "VALIDATION_BLOCKED": "validation_blocked",
        "ANOMALY_DETECTED": "anomaly_detected",
        "ANOMALY_READY": "anomaly_ready",
        "DIAGNOSIS_FAILED": "diagnosis_failed",
        "RETRY_DECIDED": "retry_decided",
        "APPROVAL_REQUIRED": "approval_required",
        "APPROVED": "approved",
        "REJECTED": "rejected",
        "NEW_RUN_STARTED": "new_run_started",
        "RECOVERY_FAILED": "recovery_failed",
        "STOP_DECIDED": "stop_decided",
        "GATE_ALLOWED": "gate_allowed",
        "GATE_BLOCKED_RECOVERABLE": "gate_blocked_recoverable",
        "GATE_BLOCKED_FINAL": "gate_blocked_final",
        "CANCEL_REQUESTED": "cancel_requested",
        "FATAL_ERROR": "fatal_error",
    }


def test_all_declared_transition_rules_resolve() -> None:
    assert len(state_machine.TRANSITION_RULES) == 40

    for rule in state_machine.TRANSITION_RULES.values():
        assert resolve_transition(rule.current_status, rule.event_type) is rule


def test_nonterminal_statuses_match_the_supervisor_contract() -> None:
    assert state_machine.NONTERMINAL_STATUSES == (
        SupervisorStatus.IDLE,
        SupervisorStatus.PREPARING,
        SupervisorStatus.RUNNING,
        SupervisorStatus.VALIDATING,
        SupervisorStatus.DIAGNOSING,
        SupervisorStatus.DECIDING,
        SupervisorStatus.AWAITING_APPROVAL,
        SupervisorStatus.RECOVERING,
        SupervisorStatus.GATING,
    )


@pytest.mark.parametrize(
    "state_factory",
    [
        _idle_state,
        _preparing_state,
        _running_state,
        _validating_state,
        _diagnosing_state,
        _deciding_state,
        _awaiting_approval_state,
        _recovering_state,
        _gating_state,
    ],
    ids=[status.value for status in state_machine.NONTERMINAL_STATUSES],
)
def test_every_nonterminal_status_can_be_cancelled(
    state_factory: object,
) -> None:
    state = state_factory()  # type: ignore[operator]
    event = _next_event(state, SupervisorEventType.CANCEL_REQUESTED)

    outcome = advance_state(
        state,
        event,
        updates={
            "cancel_requested": True,
            "cancel_requested_at": event.occurred_at,
            "process_alive": False,
            "terminal": True,
            "allow_read": False,
            "terminal_reason": "Cancellation requested by the user.",
            "final_status": SupervisorStatus.CANCELLED,
        },
    )

    assert outcome.next_status is SupervisorStatus.CANCELLED
    assert outcome.command is SupervisorCommand.CANCEL
    assert outcome.state.cancel_requested is True
    assert outcome.state.cancel_requested_at == event.occurred_at
    assert outcome.state.process_alive is False
    assert outcome.state.terminal is True
    assert outcome.state.allow_read is False
    assert outcome.state.final_status is SupervisorStatus.CANCELLED
    assert outcome.state.terminal_reason


@pytest.mark.parametrize(
    "state_factory",
    [
        _idle_state,
        _preparing_state,
        _running_state,
        _validating_state,
        _diagnosing_state,
        _deciding_state,
        _awaiting_approval_state,
        _recovering_state,
        _gating_state,
    ],
    ids=[status.value for status in state_machine.NONTERMINAL_STATUSES],
)
def test_every_nonterminal_status_can_fail(
    state_factory: object,
) -> None:
    state = state_factory()  # type: ignore[operator]
    event = _next_event(state, SupervisorEventType.FATAL_ERROR)

    outcome = advance_state(
        state,
        event,
        updates={
            "process_alive": False,
            "terminal": True,
            "allow_read": False,
            "last_error": "The supervisor encountered an unrecoverable internal error.",
            "final_status": SupervisorStatus.FAILED,
        },
    )

    assert outcome.next_status is SupervisorStatus.FAILED
    assert outcome.command is SupervisorCommand.FAIL
    assert outcome.state.process_alive is False
    assert outcome.state.terminal is True
    assert outcome.state.allow_read is False
    assert outcome.state.final_status is SupervisorStatus.FAILED
    assert outcome.state.last_error


@pytest.mark.parametrize(
    "terminal_status",
    (
        SupervisorStatus.COMPLETED,
        SupervisorStatus.BLOCKED,
        SupervisorStatus.FAILED,
        SupervisorStatus.CANCELLED,
    ),
)
@pytest.mark.parametrize(
    "event_type",
    (
        SupervisorEventType.START,
        SupervisorEventType.MONITOR_TICK,
        SupervisorEventType.RETRY_DECIDED,
        SupervisorEventType.CANCEL_REQUESTED,
        SupervisorEventType.FATAL_ERROR,
    ),
)
def test_terminal_statuses_reject_business_and_global_events(
    terminal_status: SupervisorStatus,
    event_type: SupervisorEventType,
) -> None:
    with pytest.raises(ValueError, match="is not allowed"):
        resolve_transition(terminal_status, event_type)


def test_transition_rule_builder_rejects_duplicate_keys() -> None:
    rule = state_machine.BUSINESS_TRANSITION_RULES[0]

    with pytest.raises(ValueError, match="Duplicate Supervisor transition rule"):
        state_machine._build_transition_rules((rule, rule))


def test_supervisor_commands_are_exactly_the_commands_used_by_rules() -> None:
    assert {command.name for command in SupervisorCommand} == {
        "CALL_ADAPTER",
        "START_EXECUTOR",
        "POLL_OBSERVER",
        "CALL_VALIDATOR",
        "CALL_GATE",
        "CALL_ANOMALY_BUILDER",
        "CALL_RECOVERY_CONTROLLER",
        "WAIT_FOR_APPROVAL",
        "EXECUTE_RECOVERY",
        "RECORD_OUTCOME",
        "CANCEL",
        "FAIL",
    }

    used_commands = {rule.command for rule in state_machine.TRANSITION_RULES.values()}
    assert used_commands == set(SupervisorCommand)
    assert all(
        isinstance(rule.command, SupervisorCommand)
        for rule in state_machine.TRANSITION_RULES.values()
    )


def test_normal_completion_uses_gate_and_outcome_commands() -> None:
    validation_rule = resolve_transition(
        SupervisorStatus.VALIDATING,
        SupervisorEventType.VALIDATION_PASSED,
    )
    completion_rule = resolve_transition(
        SupervisorStatus.GATING,
        SupervisorEventType.GATE_ALLOWED,
    )

    assert validation_rule.command is SupervisorCommand.CALL_GATE
    assert completion_rule.command is SupervisorCommand.RECORD_OUTCOME


def test_advance_state_records_prepare_failure_without_a_run_context() -> None:
    preparing = advance_state(_idle_state(), _event(SupervisorEventType.START, sequence=1)).state

    failed = advance_state(
        preparing,
        _next_event(preparing, SupervisorEventType.PREPARE_FAILED),
        updates={
            "terminal": True,
            "process_alive": False,
            "allow_read": False,
            "final_status": SupervisorStatus.FAILED,
            "last_error": "The adapter could not prepare the Find run.",
        },
    )

    assert failed.next_status is SupervisorStatus.FAILED
    assert failed.command is SupervisorCommand.RECORD_OUTCOME
    assert failed.state.run_context_id is None
    assert failed.state.terminal is True
    assert failed.state.final_status is SupervisorStatus.FAILED

    with pytest.raises(ValueError, match="is not allowed"):
        advance_state(
            failed.state,
            _next_event(failed.state, SupervisorEventType.MONITOR_TICK),
        )


def test_advance_state_requires_validation_evidence_for_validation_block() -> None:
    running = _running_state()
    validating = advance_state(
        running,
        _next_event(running, SupervisorEventType.PROCESS_EXITED),
        updates={"process_alive": False, "exit_code": 0},
    ).state

    with pytest.raises(ValueError, match=r"SupervisorState\.latest_progress_snapshot_id"):
        advance_state(
            validating,
            _next_event(validating, SupervisorEventType.VALIDATION_BLOCKED),
        )

    diagnosing = advance_state(
        validating,
        _next_event(validating, SupervisorEventType.VALIDATION_BLOCKED),
        updates={"latest_validation_id": "validation-blocked-001"},
    )

    assert diagnosing.next_status is SupervisorStatus.DIAGNOSING
    assert diagnosing.command is SupervisorCommand.CALL_ANOMALY_BUILDER
    assert diagnosing.state.latest_validation_id == "validation-blocked-001"
    assert diagnosing.state.process_alive is False
    assert diagnosing.state.terminal is False


def test_advance_state_requires_observation_evidence_for_detected_anomaly() -> None:
    running = _running_state()

    with pytest.raises(ValueError, match=r"SupervisorState\.latest_progress_snapshot_id"):
        advance_state(
            running,
            _next_event(running, SupervisorEventType.ANOMALY_DETECTED),
        )

    diagnosing = advance_state(
        running,
        _next_event(running, SupervisorEventType.ANOMALY_DETECTED),
        updates={"latest_progress_snapshot_id": "progress-anomaly-001"},
    )

    assert diagnosing.next_status is SupervisorStatus.DIAGNOSING
    assert diagnosing.command is SupervisorCommand.CALL_ANOMALY_BUILDER
    assert diagnosing.state.latest_progress_snapshot_id == "progress-anomaly-001"
    assert diagnosing.state.terminal is False


def test_advance_state_records_diagnosis_failure() -> None:
    running = _running_state()
    diagnosing = advance_state(
        running,
        _next_event(running, SupervisorEventType.STALL_DETECTED),
        updates={"latest_progress_snapshot_id": "progress-stalled-001", "process_alive": False},
    ).state

    failed = advance_state(
        diagnosing,
        _next_event(diagnosing, SupervisorEventType.DIAGNOSIS_FAILED),
        updates={
            "terminal": True,
            "process_alive": False,
            "allow_read": False,
            "final_status": SupervisorStatus.FAILED,
            "terminal_reason": "Diagnosis could not establish a safe recovery path.",
        },
    )

    assert failed.next_status is SupervisorStatus.FAILED
    assert failed.command is SupervisorCommand.RECORD_OUTCOME
    assert failed.state.terminal is True
    assert failed.state.final_status is SupervisorStatus.FAILED
