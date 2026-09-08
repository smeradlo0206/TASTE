"""Deterministic approval transitions for recovery decisions."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

from .contracts import RecoveryAction, RecoveryDecision


_NEW_RUN_ACTIONS = frozenset(
    {
        RecoveryAction.RETRY_NEW_RUN,
        RecoveryAction.RETRY_WITH_PARAMETER_CHANGE,
        RecoveryAction.SKIP_OPTIONAL_SOURCE,
    }
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_run_id() -> str:
    return f"find-recovery-{uuid4().hex}"


def _validated_copy(decision: RecoveryDecision) -> RecoveryDecision:
    if not isinstance(decision, RecoveryDecision):
        raise TypeError("decision must be a RecoveryDecision")
    try:
        copied = RecoveryDecision.from_json(decision.to_json())
    except (TypeError, ValueError) as exc:
        raise ValueError("decision must satisfy the RecoveryDecision contract") from exc
    for field_name in ("decision_id", "run_id", "anomaly_id"):
        value = getattr(copied, field_name)
        if not value.strip():
            raise ValueError(f"decision {field_name} must be non-empty")
    return copied


def _require_non_empty_text(value: str | None, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _validate_pending(decision: RecoveryDecision) -> None:
    if decision.proposed_action is None:
        raise ValueError("a pending decision must have a proposed action")
    if decision.executable:
        raise ValueError("a pending decision must not be executable")
    if decision.budget_cost != 0 or decision.budget_after != decision.budget_before:
        raise ValueError("a pending decision must not consume recovery budget")
    if decision.proposed_new_run_id is not None:
        raise ValueError("a pending decision must not have a proposed new run ID")
    if (
        decision.approved_by is not None
        or decision.approved_at is not None
        or decision.approval_reason is not None
    ):
        raise ValueError("a pending decision must not contain approval metadata")


def _validate_candidate(decision: RecoveryDecision) -> RecoveryAction:
    action = decision.proposed_action
    if action not in _NEW_RUN_ACTIONS:
        raise ValueError("proposed_action must be an executable recovery action")
    if not decision.new_run_required:
        raise ValueError("the proposed recovery action must require a new run")
    if action is RecoveryAction.RETRY_WITH_PARAMETER_CHANGE:
        if not decision.parameter_changes or decision.target_sources:
            raise ValueError("retry_with_parameter_change has invalid payload")
    elif action is RecoveryAction.SKIP_OPTIONAL_SOURCE:
        if not decision.target_sources or decision.parameter_changes:
            raise ValueError("skip_optional_source has invalid payload")
    elif decision.parameter_changes or decision.target_sources:
        raise ValueError("retry_new_run must not carry parameter or source changes")
    return action


class FindRecoveryApprovalGate:
    """Resolve a human approval choice without executing recovery."""

    def resolve(
        self,
        *,
        decision: RecoveryDecision,
        approved: bool | None = None,
        approved_by: str | None = None,
        reason: str | None = None,
    ) -> RecoveryDecision:
        resolved = _validated_copy(decision)
        if approved is not None and type(approved) is not bool:
            raise TypeError("approved must be a bool or None")

        if not resolved.requires_approval:
            if resolved.approval_status != "not_required":
                raise ValueError("a decision without approval must be not_required")
            if (
                resolved.approved_by is not None
                or resolved.approved_at is not None
                or resolved.approval_reason is not None
            ):
                raise ValueError("a not_required decision must not contain approval metadata")
            if approved is not None or approved_by is not None or reason is not None:
                raise ValueError("approval inputs are not valid for a not_required decision")
            return resolved

        if resolved.approval_status != "pending":
            raise ValueError("an approved or rejected decision cannot be processed again")
        _validate_pending(resolved)

        if approved is None:
            if approved_by is not None or reason is not None:
                raise ValueError("approval metadata requires an approval choice")
            return resolved

        approval_reason = _require_non_empty_text(reason, field_name="reason")
        if not approved:
            if approved_by is not None:
                raise ValueError("approved_by is only valid for an approval")
            return replace(
                resolved,
                approval_status="rejected",
                executable=False,
                approved_at=None,
                approved_by=None,
                approval_reason=approval_reason,
                budget_cost=0,
                budget_after=resolved.budget_before,
                proposed_new_run_id=None,
            )

        approver = _require_non_empty_text(approved_by, field_name="approved_by")
        _validate_candidate(resolved)
        if resolved.budget_before < 1:
            raise ValueError("the recovery budget is exhausted")
        return replace(
            resolved,
            approval_status="approved",
            executable=True,
            approved_by=approver,
            approved_at=_utc_now(),
            approval_reason=approval_reason,
            budget_cost=1,
            budget_after=resolved.budget_before - 1,
            proposed_new_run_id=_new_run_id(),
        )
