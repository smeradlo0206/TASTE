"""Pure Supervisor transition lookup rules."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields, replace
from datetime import datetime
from enum import Enum

from .contracts import SupervisorEvent, SupervisorEventType, SupervisorState, SupervisorStatus


class SupervisorCommand(str, Enum):
    """Declarative follow-up requested by a transition; never executed here."""

    CALL_ADAPTER = "call_adapter"
    START_EXECUTOR = "start_executor"
    POLL_OBSERVER = "poll_observer"
    CALL_VALIDATOR = "call_validator"
    CALL_GATE = "call_gate"
    RECORD_OUTCOME = "record_outcome"
    CALL_ANOMALY_BUILDER = "call_anomaly_builder"
    CALL_RECOVERY_CONTROLLER = "call_recovery_controller"
    WAIT_FOR_APPROVAL = "wait_for_approval"
    EXECUTE_RECOVERY = "execute_recovery"
    CANCEL = "cancel"
    FAIL = "fail"


@dataclass(frozen=True)
class TransitionRule:
    current_status: SupervisorStatus
    event_type: SupervisorEventType
    target_status: SupervisorStatus
    command: SupervisorCommand


@dataclass(frozen=True)
class TransitionOutcome:
    """One validated, immutable Supervisor state transition."""

    previous_status: SupervisorStatus
    next_status: SupervisorStatus
    event_type: SupervisorEventType
    command: SupervisorCommand
    state: SupervisorState


def _rule(
    current_status: SupervisorStatus,
    event_type: SupervisorEventType,
    target_status: SupervisorStatus,
    command: SupervisorCommand,
) -> TransitionRule:
    return TransitionRule(
        current_status=current_status,
        event_type=event_type,
        target_status=target_status,
        command=command,
    )


NONTERMINAL_STATUSES = (
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


BUSINESS_TRANSITION_RULES = (
    _rule(
        SupervisorStatus.IDLE,
        SupervisorEventType.START,
        SupervisorStatus.PREPARING,
        SupervisorCommand.CALL_ADAPTER,
    ),
    _rule(
        SupervisorStatus.PREPARING,
        SupervisorEventType.PREPARED,
        SupervisorStatus.PREPARING,
        SupervisorCommand.START_EXECUTOR,
    ),
    _rule(
        SupervisorStatus.PREPARING,
        SupervisorEventType.NEW_RUN_STARTED,
        SupervisorStatus.RUNNING,
        SupervisorCommand.POLL_OBSERVER,
    ),
    _rule(
        SupervisorStatus.PREPARING,
        SupervisorEventType.PREPARE_FAILED,
        SupervisorStatus.FAILED,
        SupervisorCommand.RECORD_OUTCOME,
    ),
    _rule(
        SupervisorStatus.RUNNING,
        SupervisorEventType.MONITOR_TICK,
        SupervisorStatus.RUNNING,
        SupervisorCommand.POLL_OBSERVER,
    ),
    _rule(
        SupervisorStatus.RUNNING,
        SupervisorEventType.PROCESS_EXITED,
        SupervisorStatus.VALIDATING,
        SupervisorCommand.CALL_VALIDATOR,
    ),
    _rule(
        SupervisorStatus.RUNNING,
        SupervisorEventType.STALL_DETECTED,
        SupervisorStatus.DIAGNOSING,
        SupervisorCommand.CALL_ANOMALY_BUILDER,
    ),
    _rule(
        SupervisorStatus.RUNNING,
        SupervisorEventType.ANOMALY_DETECTED,
        SupervisorStatus.DIAGNOSING,
        SupervisorCommand.CALL_ANOMALY_BUILDER,
    ),
    _rule(
        SupervisorStatus.VALIDATING,
        SupervisorEventType.VALIDATION_PASSED,
        SupervisorStatus.GATING,
        SupervisorCommand.CALL_GATE,
    ),
    _rule(
        SupervisorStatus.VALIDATING,
        SupervisorEventType.VALIDATION_BLOCKED,
        SupervisorStatus.DIAGNOSING,
        SupervisorCommand.CALL_ANOMALY_BUILDER,
    ),
    _rule(
        SupervisorStatus.DIAGNOSING,
        SupervisorEventType.ANOMALY_READY,
        SupervisorStatus.DECIDING,
        SupervisorCommand.CALL_RECOVERY_CONTROLLER,
    ),
    _rule(
        SupervisorStatus.DIAGNOSING,
        SupervisorEventType.DIAGNOSIS_FAILED,
        SupervisorStatus.FAILED,
        SupervisorCommand.RECORD_OUTCOME,
    ),
    _rule(
        SupervisorStatus.DECIDING,
        SupervisorEventType.RETRY_DECIDED,
        SupervisorStatus.RECOVERING,
        SupervisorCommand.EXECUTE_RECOVERY,
    ),
    _rule(
        SupervisorStatus.DECIDING,
        SupervisorEventType.APPROVAL_REQUIRED,
        SupervisorStatus.AWAITING_APPROVAL,
        SupervisorCommand.WAIT_FOR_APPROVAL,
    ),
    _rule(
        SupervisorStatus.AWAITING_APPROVAL,
        SupervisorEventType.APPROVED,
        SupervisorStatus.RECOVERING,
        SupervisorCommand.EXECUTE_RECOVERY,
    ),
    _rule(
        SupervisorStatus.AWAITING_APPROVAL,
        SupervisorEventType.REJECTED,
        SupervisorStatus.BLOCKED,
        SupervisorCommand.RECORD_OUTCOME,
    ),
    _rule(
        SupervisorStatus.RECOVERING,
        SupervisorEventType.NEW_RUN_STARTED,
        SupervisorStatus.RUNNING,
        SupervisorCommand.POLL_OBSERVER,
    ),
    _rule(
        SupervisorStatus.RECOVERING,
        SupervisorEventType.RECOVERY_FAILED,
        SupervisorStatus.DIAGNOSING,
        SupervisorCommand.CALL_ANOMALY_BUILDER,
    ),
    _rule(
        SupervisorStatus.DECIDING,
        SupervisorEventType.STOP_DECIDED,
        SupervisorStatus.BLOCKED,
        SupervisorCommand.RECORD_OUTCOME,
    ),
    _rule(
        SupervisorStatus.GATING,
        SupervisorEventType.GATE_ALLOWED,
        SupervisorStatus.COMPLETED,
        SupervisorCommand.RECORD_OUTCOME,
    ),
    _rule(
        SupervisorStatus.GATING,
        SupervisorEventType.GATE_BLOCKED_RECOVERABLE,
        SupervisorStatus.DIAGNOSING,
        SupervisorCommand.CALL_ANOMALY_BUILDER,
    ),
    _rule(
        SupervisorStatus.GATING,
        SupervisorEventType.GATE_BLOCKED_FINAL,
        SupervisorStatus.BLOCKED,
        SupervisorCommand.RECORD_OUTCOME,
    ),
)


GLOBAL_EXIT_RULES = tuple(
    _rule(
        status,
        SupervisorEventType.CANCEL_REQUESTED,
        SupervisorStatus.CANCELLED,
        SupervisorCommand.CANCEL,
    )
    for status in NONTERMINAL_STATUSES
) + tuple(
    _rule(
        status,
        SupervisorEventType.FATAL_ERROR,
        SupervisorStatus.FAILED,
        SupervisorCommand.FAIL,
    )
    for status in NONTERMINAL_STATUSES
)


def _build_transition_rules(
    declared_rules: tuple[TransitionRule, ...],
) -> dict[tuple[SupervisorStatus, SupervisorEventType], TransitionRule]:
    transition_rules: dict[tuple[SupervisorStatus, SupervisorEventType], TransitionRule] = {}
    for rule in declared_rules:
        key = (rule.current_status, rule.event_type)
        if key in transition_rules:
            raise ValueError(
                "Duplicate Supervisor transition rule "
                f"{rule.current_status.value} + {rule.event_type.value}"
            )
        transition_rules[key] = rule
    return transition_rules


TRANSITION_RULES = _build_transition_rules(BUSINESS_TRANSITION_RULES + GLOBAL_EXIT_RULES)


def resolve_transition(
    current_status: SupervisorStatus,
    event_type: SupervisorEventType,
) -> TransitionRule:
    """Return the declared transition rule for one status and event pair."""

    if not isinstance(current_status, SupervisorStatus):
        raise TypeError("current_status must be a SupervisorStatus")
    if not isinstance(event_type, SupervisorEventType):
        raise TypeError("event_type must be a SupervisorEventType")

    try:
        return TRANSITION_RULES[(current_status, event_type)]
    except KeyError as exc:
        raise ValueError(
            f"Supervisor transition {current_status.value} + {event_type.value} is not allowed"
        ) from exc


_MANAGED_UPDATE_FIELDS = frozenset(
    {
        "status",
        "state_revision",
        "event_sequence",
        "recent_events",
        "updated_at",
        "heartbeat_at",
        "root_run_id",
        "recovery_attempts",
        "recovery_budget_remaining",
    }
)
_SUPERVISOR_STATE_FIELD_NAMES = frozenset(item.name for item in fields(SupervisorState))


def _event_type(event: SupervisorEvent) -> SupervisorEventType:
    if not isinstance(event.event_type, SupervisorEventType):
        raise TypeError("event.event_type must be a SupervisorEventType")
    return event.event_type


def _require_aware_event_time(event: SupervisorEvent) -> datetime:
    occurred_at = event.occurred_at
    if not isinstance(occurred_at, datetime) or occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
        raise ValueError("event.occurred_at must be a timezone-aware datetime")
    return occurred_at


def advance_state(
    state: SupervisorState,
    event: SupervisorEvent,
    *,
    updates: Mapping[str, object] | None = None,
) -> TransitionOutcome:
    """Return a new validated state for one declared Supervisor transition."""

    if not isinstance(state, SupervisorState):
        raise TypeError("state must be a SupervisorState")
    if not isinstance(event, SupervisorEvent):
        raise TypeError("event must be a SupervisorEvent")

    event_type = _event_type(event)
    rule = resolve_transition(state.status, event_type)
    occurred_at = _require_aware_event_time(event)

    if event.sequence != state.event_sequence + 1:
        raise ValueError("event.sequence must equal state.event_sequence + 1")
    if state.recent_events and state.recent_events[-1].sequence >= event.sequence:
        raise ValueError("event.sequence must be greater than the latest recent event sequence")
    if occurred_at < state.updated_at:
        raise ValueError("event.occurred_at must not be earlier than state.updated_at")

    if updates is None:
        copied_updates: dict[str, object] = {}
    elif not isinstance(updates, Mapping):
        raise TypeError("updates must be a mapping or null")
    else:
        copied_updates = dict(updates)

    for field_name in copied_updates:
        if field_name in _MANAGED_UPDATE_FIELDS:
            raise ValueError(f"updates.{field_name} is managed by advance_state")
        if field_name not in _SUPERVISOR_STATE_FIELD_NAMES:
            raise ValueError(f"updates.{field_name} is not a SupervisorState field")

    recovery_updates: dict[str, object] = {}
    if rule.target_status is SupervisorStatus.RECOVERING:
        if state.recovery_budget_remaining <= 0:
            raise ValueError("recovery_budget_remaining must be greater than 0 to enter RECOVERING")
        recovery_updates = {
            "recovery_attempts": state.recovery_attempts + 1,
            "recovery_budget_remaining": state.recovery_budget_remaining - 1,
        }
        if event_type is SupervisorEventType.APPROVED:
            recovery_updates.update(
                {
                    "awaiting_approval": False,
                    "pending_approval_decision_id": None,
                }
            )

    launch_updates: dict[str, object] = {}
    if (
        rule.target_status is SupervisorStatus.RUNNING
        and event_type is SupervisorEventType.NEW_RUN_STARTED
        and state.root_run_id is None
    ):
        active_run_id = copied_updates.get("active_run_id", state.active_run_id)
        if not isinstance(active_run_id, str) or not active_run_id.strip():
            raise ValueError("active_run_id is required to bind root_run_id when starting the first Find run")
        launch_updates["root_run_id"] = active_run_id

    replacement_fields = {
        **copied_updates,
        **recovery_updates,
        **launch_updates,
        "status": rule.target_status,
        "state_revision": state.state_revision + 1,
        "event_sequence": event.sequence,
        "recent_events": [*state.recent_events, event],
        "updated_at": occurred_at,
        "heartbeat_at": occurred_at,
    }
    next_state = replace(state, **replacement_fields)
    return TransitionOutcome(
        previous_status=state.status,
        next_status=rule.target_status,
        event_type=event_type,
        command=rule.command,
        state=next_state,
    )


__all__ = [
    "SupervisorCommand",
    "TransitionOutcome",
    "TransitionRule",
    "TRANSITION_RULES",
    "advance_state",
    "resolve_transition",
]
