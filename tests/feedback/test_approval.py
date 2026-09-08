from __future__ import annotations

from datetime import datetime, timezone
import inspect

import pytest

import feedback
import feedback.approval as approval_module
from feedback import (
    EvidenceRef,
    ExperienceRef,
    FindRecoveryApprovalGate,
    ParameterChange,
    RecoveryAction,
    RecoveryDecision,
    RiskLevel,
)


NOW = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
NEW_RUN_ACTIONS = (
    RecoveryAction.RETRY_NEW_RUN,
    RecoveryAction.RETRY_WITH_PARAMETER_CHANGE,
    RecoveryAction.SKIP_OPTIONAL_SOURCE,
)


def _pending_decision(
    *,
    proposed_action: RecoveryAction | None = RecoveryAction.RETRY_WITH_PARAMETER_CHANGE,
    risk_level: RiskLevel = RiskLevel.MEDIUM,
    proposal_id: str | None = "proposal-approval-001",
    budget_before: int = 2,
) -> RecoveryDecision:
    parameter_changes = (
        [
            ParameterChange(
                name="abstract_scoring_max_workers",
                before=2,
                after=1,
                reason="Reduce request pressure",
            )
        ]
        if proposed_action is RecoveryAction.RETRY_WITH_PARAMETER_CHANGE
        else []
    )
    target_sources = (
        ["semantic_scholar"]
        if proposed_action is RecoveryAction.SKIP_OPTIONAL_SOURCE
        else []
    )
    new_run_required = proposed_action in NEW_RUN_ACTIONS
    return RecoveryDecision(
        decision_id="decision-approval-001",
        run_id="find-approval-001",
        anomaly_id="anomaly-approval-001",
        created_at=NOW,
        decided_at=NOW,
        producer="approval-tests",
        producer_version="1.0",
        action=RecoveryAction.REQUEST_APPROVAL,
        reason="A human choice is required before recovery",
        risk_level=risk_level,
        executable=False,
        new_run_required=new_run_required,
        exploratory=False,
        requires_approval=True,
        approval_status="pending",
        attempt_index=1,
        budget_before=budget_before,
        budget_cost=0,
        budget_after=budget_before,
        max_same_action_attempts=1,
        verification_policy="find.validation.v1",
        required_post_checks=["result_exists", "result_run_id_matches"],
        success_definition="A new Find run passes validation",
        stop_if_failed=True,
        proposal_id=proposal_id,
        proposed_action=proposed_action,
        target_sources=target_sources,
        parameter_changes=parameter_changes,
        matched_experience_refs=[
            ExperienceRef(
                case_id="case-approval-001",
                verified=True,
                case_type="technical",
            )
        ],
        evidence_of_previous_success=[
            EvidenceRef(
                kind="validation",
                contract_id="validation-approval-001",
                summary="A synthetic prior run passed validation",
            )
        ],
        preconditions=["The recovery budget is still available"],
    )


def _automatic_decision() -> RecoveryDecision:
    return RecoveryDecision(
        decision_id="decision-automatic-001",
        run_id="find-approval-001",
        anomaly_id="anomaly-approval-001",
        created_at=NOW,
        decided_at=NOW,
        producer="approval-tests",
        producer_version="1.0",
        action=RecoveryAction.RETRY_WITH_PARAMETER_CHANGE,
        reason="A verified low-risk change can run automatically",
        risk_level=RiskLevel.LOW,
        executable=True,
        new_run_required=True,
        exploratory=False,
        requires_approval=False,
        approval_status="not_required",
        attempt_index=1,
        budget_before=2,
        budget_cost=1,
        budget_after=1,
        max_same_action_attempts=1,
        verification_policy="find.validation.v1",
        required_post_checks=["result_exists"],
        success_definition="A new Find run produces a valid result",
        stop_if_failed=True,
        parameter_changes=[
            ParameterChange(
                name="abstract_scoring_max_workers",
                before=2,
                after=1,
                reason="Reduce request pressure",
            )
        ],
        proposed_new_run_id="find-recovery-existing",
    )


def test_find_recovery_approval_gate_has_the_protocol_signature() -> None:
    signature = inspect.signature(FindRecoveryApprovalGate.resolve)

    assert list(signature.parameters) == [
        "self",
        "decision",
        "approved",
        "approved_by",
        "reason",
    ]
    assert signature.parameters["decision"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["decision"].default is inspect.Parameter.empty
    for name in ("approved", "approved_by", "reason"):
        assert signature.parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
        assert signature.parameters[name].default is None
    assert feedback.FindRecoveryApprovalGate is FindRecoveryApprovalGate
    assert "FindRecoveryApprovalGate" in feedback.__all__


def test_not_required_decision_is_returned_as_an_isolated_equal_contract() -> None:
    decision = _automatic_decision()
    before = decision.to_json()

    resolved = FindRecoveryApprovalGate().resolve(decision=decision)

    assert resolved == decision
    assert resolved is not decision
    assert resolved.parameter_changes is not decision.parameter_changes
    assert resolved.parameter_changes[0] is not decision.parameter_changes[0]
    assert resolved.executable is True
    assert resolved.risk_level is RiskLevel.LOW
    assert resolved.approved_at is None
    assert decision.to_json() == before


@pytest.mark.parametrize(
    "extra",
    [
        {"approved": True, "approved_by": "reviewer", "reason": "Approve"},
        {"approved": False, "reason": "Reject"},
        {"approved_by": "reviewer"},
        {"reason": "No choice was supplied"},
    ],
)
def test_not_required_decision_rejects_approval_inputs(extra: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        FindRecoveryApprovalGate().resolve(
            decision=_automatic_decision(),
            **extra,  # type: ignore[arg-type]
        )


def test_pending_without_a_choice_stays_pending_and_is_isolated() -> None:
    decision = _pending_decision()
    before = decision.to_json()

    resolved = FindRecoveryApprovalGate().resolve(decision=decision)

    assert resolved == decision
    assert resolved is not decision
    assert resolved.approval_status == "pending"
    assert resolved.executable is False
    assert resolved.budget_cost == 0
    assert resolved.budget_after == resolved.budget_before
    assert resolved.proposed_new_run_id is None
    assert resolved.approved_by is None
    assert decision.to_json() == before


@pytest.mark.parametrize("field", ["approved_by", "reason"])
def test_pending_without_a_choice_rejects_orphan_approval_metadata(field: str) -> None:
    kwargs = {field: "unexpected"}
    with pytest.raises(ValueError):
        FindRecoveryApprovalGate().resolve(
            decision=_pending_decision(),
            **kwargs,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("proposed_action", "risk_level", "proposal_id"),
    [
        (RecoveryAction.RETRY_NEW_RUN, RiskLevel.MEDIUM, None),
        (
            RecoveryAction.RETRY_WITH_PARAMETER_CHANGE,
            RiskLevel.HIGH,
            "proposal-llm-001",
        ),
        (RecoveryAction.SKIP_OPTIONAL_SOURCE, RiskLevel.MEDIUM, None),
    ],
)
def test_approval_preserves_proposal_payload_and_authorizes_one_new_run(
    proposed_action: RecoveryAction,
    risk_level: RiskLevel,
    proposal_id: str | None,
) -> None:
    decision = _pending_decision(
        proposed_action=proposed_action,
        risk_level=risk_level,
        proposal_id=proposal_id,
    )
    before = decision.to_json()
    started_at = datetime.now(timezone.utc)

    resolved = FindRecoveryApprovalGate().resolve(
        decision=decision,
        approved=True,
        approved_by="reviewer-001",
        reason="The bounded recovery is approved",
    )
    finished_at = datetime.now(timezone.utc)

    assert resolved.decision_id == decision.decision_id
    assert resolved.run_id == decision.run_id
    assert resolved.anomaly_id == decision.anomaly_id
    assert resolved.action is RecoveryAction.REQUEST_APPROVAL
    assert resolved.proposal_id == proposal_id
    assert resolved.proposed_action is proposed_action
    assert resolved.risk_level is risk_level
    assert resolved.parameter_changes == decision.parameter_changes
    assert resolved.target_sources == decision.target_sources
    assert resolved.evidence_of_previous_success == decision.evidence_of_previous_success
    assert resolved.approval_status == "approved"
    assert resolved.requires_approval is True
    assert resolved.executable is True
    assert resolved.approved_by == "reviewer-001"
    assert resolved.approval_reason == "The bounded recovery is approved"
    assert started_at <= resolved.approved_at <= finished_at  # type: ignore[operator]
    assert resolved.budget_cost == 1
    assert resolved.budget_after == resolved.budget_before - 1
    assert resolved.new_run_required is True
    assert resolved.proposed_new_run_id
    assert resolved.proposed_new_run_id.startswith("find-recovery-")
    assert RecoveryDecision.from_json(resolved.to_json()) == resolved
    assert decision.to_json() == before


def test_rejection_preserves_candidate_but_never_spends_or_executes() -> None:
    decision = _pending_decision(
        proposed_action=RecoveryAction.SKIP_OPTIONAL_SOURCE,
        proposal_id=None,
        risk_level=RiskLevel.HIGH,
    )
    before = decision.to_json()

    resolved = FindRecoveryApprovalGate().resolve(
        decision=decision,
        approved=False,
        reason="The recovery is not authorized",
    )

    assert resolved.decision_id == decision.decision_id
    assert resolved.run_id == decision.run_id
    assert resolved.anomaly_id == decision.anomaly_id
    assert resolved.action is RecoveryAction.REQUEST_APPROVAL
    assert resolved.proposed_action is RecoveryAction.SKIP_OPTIONAL_SOURCE
    assert resolved.risk_level is RiskLevel.HIGH
    assert resolved.target_sources == decision.target_sources
    assert resolved.evidence_of_previous_success == decision.evidence_of_previous_success
    assert resolved.approval_status == "rejected"
    assert resolved.executable is False
    assert resolved.approved_at is None
    assert resolved.approved_by is None
    assert resolved.approval_reason == "The recovery is not authorized"
    assert resolved.budget_cost == 0
    assert resolved.budget_after == resolved.budget_before
    assert resolved.proposed_new_run_id is None
    assert RecoveryDecision.from_json(resolved.to_json()) == resolved
    assert decision.to_json() == before


@pytest.mark.parametrize(
    "approved",
    [True, False],
)
def test_resolved_decisions_cannot_be_processed_again(approved: bool) -> None:
    pending = _pending_decision()
    resolved = FindRecoveryApprovalGate().resolve(
        decision=pending,
        approved=approved,
        approved_by="reviewer-001" if approved else None,
        reason="Resolve once",
    )

    with pytest.raises(ValueError):
        FindRecoveryApprovalGate().resolve(
            decision=resolved,
            approved=approved,
            approved_by="reviewer-001" if approved else None,
            reason="Resolve twice",
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"approved": True, "reason": "Missing approver"},
        {"approved": True, "approved_by": "reviewer-001"},
        {"approved": False},
        {"approved": False, "approved_by": "rejector", "reason": "Reject"},
        {"approved": 1, "approved_by": "reviewer-001", "reason": "Wrong type"},
    ],
)
def test_invalid_approval_inputs_are_rejected(kwargs: dict[str, object]) -> None:
    with pytest.raises((TypeError, ValueError)):
        FindRecoveryApprovalGate().resolve(
            decision=_pending_decision(),
            **kwargs,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "proposed_action",
    [RecoveryAction.NO_ACTION, RecoveryAction.STOP_AND_REPORT],
)
def test_non_recovery_candidates_cannot_be_approved(
    proposed_action: RecoveryAction,
) -> None:
    with pytest.raises(ValueError):
        FindRecoveryApprovalGate().resolve(
            decision=_pending_decision(
                proposed_action=proposed_action,
                proposal_id="proposal-invalid-action",
            ),
            approved=True,
            approved_by="reviewer-001",
            reason="Should not be executable",
        )


@pytest.mark.parametrize("approved", [None, True, False])
def test_pending_without_a_candidate_is_always_rejected(
    approved: bool | None,
) -> None:
    kwargs: dict[str, object] = {"approved": approved}
    if approved is True:
        kwargs.update(approved_by="reviewer-001", reason="Approve")
    elif approved is False:
        kwargs["reason"] = "Reject"

    with pytest.raises(ValueError):
        FindRecoveryApprovalGate().resolve(
            decision=_pending_decision(proposed_action=None, proposal_id=None),
            **kwargs,  # type: ignore[arg-type]
        )


def test_nested_request_approval_candidate_cannot_be_approved() -> None:
    nested = _pending_decision()
    nested.proposed_action = RecoveryAction.REQUEST_APPROVAL

    with pytest.raises(ValueError):
        FindRecoveryApprovalGate().resolve(
            decision=nested,
            approved=True,
            approved_by="reviewer-001",
            reason="Should not be executable",
        )


def test_approval_rejects_exhausted_or_internally_invalid_decisions() -> None:
    exhausted = _pending_decision(budget_before=0)
    invalid_identity = _pending_decision()
    invalid_identity.run_id = ""
    invalid_budget = _pending_decision()
    invalid_budget.budget_after = invalid_budget.budget_before - 1

    for decision in (exhausted, invalid_identity, invalid_budget):
        with pytest.raises(ValueError):
            FindRecoveryApprovalGate().resolve(
                decision=decision,
                approved=True,
                approved_by="reviewer-001",
                reason="Should not be executable",
            )


def test_non_decision_input_is_rejected() -> None:
    with pytest.raises(TypeError):
        FindRecoveryApprovalGate().resolve(decision={})  # type: ignore[arg-type]


def test_approval_module_has_no_execution_or_external_service_dependencies() -> None:
    source = inspect.getsource(approval_module)

    for forbidden in (
        "subprocess",
        "os.kill",
        ".terminate(",
        ".wait(",
        "Executor",
        "Framework",
        "ExperienceStore",
        "RecoveryAdvisor",
        "LLM",
    ):
        assert forbidden not in source
