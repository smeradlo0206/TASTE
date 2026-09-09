"""Deterministic recovery decisions for one diagnosed Find anomaly."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

from .contracts import (
    Anomaly,
    EvidenceRef,
    ExperienceCase,
    ExperienceRef,
    ParameterChange,
    RecoveryAction,
    RecoveryDecision,
    RecoveryProposal,
    RiskLevel,
    RunContext,
    SupervisorState,
    ValidationStatus,
)
from .feedback_adapter import _SAFE_INTEGER_PARAMETERS
from .interfaces import RecoveryAdvisor, RecoveryExperienceStore
from .query_builder import build_recovery_experience_query


_PRODUCER = "find-recovery-controller"
_PRODUCER_VERSION = "1.0"
_MAX_SAME_ACTION_ATTEMPTS = 1
_IDENTITY_OR_INTEGRITY_ANOMALIES = frozenset(
    {"run_id_mismatch", "source_integrity_blocked", "reading_bridge_rejected"}
)
_RECOVERY_ACTIONS = frozenset(
    {
        RecoveryAction.RETRY_NEW_RUN,
        RecoveryAction.RETRY_WITH_PARAMETER_CHANGE,
        RecoveryAction.SKIP_OPTIONAL_SOURCE,
    }
)
_NEW_RUN_ACTIONS = frozenset(
    {
        RecoveryAction.RETRY_NEW_RUN,
        RecoveryAction.RETRY_WITH_PARAMETER_CHANGE,
        RecoveryAction.SKIP_OPTIONAL_SOURCE,
    }
)
_RISK_ORDER = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
}
_REQUIRED_POST_CHECKS = (
    "process_exit_code_ok",
    "result_exists",
    "result_parseable",
    "result_run_id_matches",
    "reading_bridge_probe_passed",
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_decision_id() -> str:
    return f"decision-{uuid4().hex}"


def _new_run_id() -> str:
    return f"find-recovery-{uuid4().hex}"


def _experience_ref(case: ExperienceCase) -> ExperienceRef:
    return ExperienceRef(
        case_id=case.case_id,
        verified=case.verified,
        case_type=case.case_type,
    )


def _normalized_tags(values: list[str]) -> set[str]:
    return {value.strip().lower() for value in values}


def _parameter_changes_are_safe(
    changes: list[ParameterChange],
    run_context: RunContext,
) -> bool:
    current = run_context.effective_parameters
    seen: set[str] = set()
    for change in changes:
        if change.name not in _SAFE_INTEGER_PARAMETERS or change.name in seen:
            return False
        if change.name not in current:
            return False
        if type(change.after) is not int or change.after <= 0:
            return False
        current_value = current[change.name]
        if change.before is not None and change.before != current_value:
            return False
        if change.after == current_value:
            return False
        seen.add(change.name)
    return True


def _proposal_parameter_changes(
    proposal: RecoveryProposal,
    run_context: RunContext,
) -> list[ParameterChange] | None:
    changes: list[ParameterChange] = []
    for name, after in proposal.parameter_changes.items():
        if name not in _SAFE_INTEGER_PARAMETERS:
            return None
        if name not in run_context.effective_parameters:
            return None
        if type(after) is not int or after <= 0:
            return None
        before = run_context.effective_parameters[name]
        if after == before:
            return None
        changes.append(
            ParameterChange(
                name=name,
                before=deepcopy(before),
                after=deepcopy(after),
                reason=proposal.reason,
            )
        )
    return changes


class FindRecoveryController:
    """Choose one safe recovery decision without executing or persisting it."""

    def __init__(
        self,
        *,
        experience_store: RecoveryExperienceStore,
        recovery_advisor: RecoveryAdvisor | None = None,
    ) -> None:
        self._experience_store = experience_store
        self._recovery_advisor = recovery_advisor

    def decide(
        self,
        *,
        anomaly: Anomaly,
        run_context: RunContext,
        supervisor_state: SupervisorState,
    ) -> RecoveryDecision:
        self._validate_inputs(anomaly, run_context, supervisor_state)

        hard_stop_reason = self._hard_stop_reason(
            anomaly,
            run_context,
            supervisor_state,
        )
        if hard_stop_reason is not None:
            return self._build_decision(
                anomaly=anomaly,
                run_context=run_context,
                supervisor_state=supervisor_state,
                action=RecoveryAction.STOP_AND_REPORT,
                reason=hard_stop_reason,
            )

        if not anomaly.blocking:
            return self._build_decision(
                anomaly=anomaly,
                run_context=run_context,
                supervisor_state=supervisor_state,
                action=RecoveryAction.NO_ACTION,
                reason="The structured anomaly is non-blocking and remains under observation",
            )

        try:
            query = build_recovery_experience_query(anomaly, run_context)
            matched_experiences = self._experience_store.search_recovery_cases(query)
            if not isinstance(matched_experiences, list) or any(
                not isinstance(case, ExperienceCase) for case in matched_experiences
            ):
                raise TypeError("Recovery Experience Store returned invalid cases")
        except Exception:
            return self._build_decision(
                anomaly=anomaly,
                run_context=run_context,
                supervisor_state=supervisor_state,
                action=RecoveryAction.STOP_AND_REPORT,
                reason="Recovery experience lookup failed",
            )

        for case in matched_experiences:
            rejection = self._experience_rejection_reason(
                case,
                anomaly,
                run_context,
                supervisor_state,
            )
            if rejection is not None:
                if case.recovery_action is not None:
                    action_rejection = self._action_rejection_reason(
                        case.recovery_action,
                        run_context,
                        supervisor_state,
                    )
                    if action_rejection is not None:
                        return self._build_decision(
                            anomaly=anomaly,
                            run_context=run_context,
                            supervisor_state=supervisor_state,
                            action=RecoveryAction.STOP_AND_REPORT,
                            reason=(
                                "Matched recovery experience was rejected: "
                                f"{action_rejection}"
                            ),
                        )
                continue
            return self._decision_from_experience(
                case,
                anomaly,
                run_context,
                supervisor_state,
            )

        if self._recovery_advisor is None:
            return self._build_decision(
                anomaly=anomaly,
                run_context=run_context,
                supervisor_state=supervisor_state,
                action=RecoveryAction.STOP_AND_REPORT,
                reason="No safe recovery experience or Recovery Advisor is available",
            )

        try:
            proposal = self._recovery_advisor.propose(
                anomaly=anomaly,
                run_context=run_context,
                supervisor_state=supervisor_state,
                matched_experiences=matched_experiences,
            )
        except Exception:
            return self._build_decision(
                anomaly=anomaly,
                run_context=run_context,
                supervisor_state=supervisor_state,
                action=RecoveryAction.STOP_AND_REPORT,
                reason="Recovery Advisor failed",
            )
        if not isinstance(proposal, RecoveryProposal):
            return self._build_decision(
                anomaly=anomaly,
                run_context=run_context,
                supervisor_state=supervisor_state,
                action=RecoveryAction.STOP_AND_REPORT,
                reason="Recovery Advisor returned an invalid proposal",
            )
        return self._decision_from_proposal(
            proposal,
            anomaly,
            run_context,
            supervisor_state,
        )

    @staticmethod
    def _validate_inputs(
        anomaly: Anomaly,
        run_context: RunContext,
        supervisor_state: SupervisorState,
    ) -> None:
        if not isinstance(anomaly, Anomaly):
            raise TypeError("anomaly must be an Anomaly")
        if not isinstance(run_context, RunContext):
            raise TypeError("run_context must be a RunContext")
        if not isinstance(supervisor_state, SupervisorState):
            raise TypeError("supervisor_state must be a SupervisorState")

        active_run_id = supervisor_state.active_run_id or supervisor_state.root_run_id
        if (
            supervisor_state.run_context_id != run_context.context_id
            or supervisor_state.project_id != run_context.project_id
            or active_run_id != anomaly.run_id
            or supervisor_state.active_anomaly_id != anomaly.anomaly_id
        ):
            raise ValueError("Recovery Controller input identity mismatch")

    @staticmethod
    def _hard_stop_reason(
        anomaly: Anomaly,
        run_context: RunContext,
        supervisor_state: SupervisorState,
    ) -> str | None:
        if supervisor_state.cancel_requested:
            return "Recovery stopped because cancellation was requested"
        if supervisor_state.recovery_budget_remaining <= 0:
            return "Recovery budget is exhausted"
        if supervisor_state.recovery_attempts >= run_context.recovery_budget:
            return "Recovery attempt limit is reached"
        if supervisor_state.active_recovery_decision_id is not None:
            return "A conflicting recovery decision is already active"
        if anomaly.kind in _IDENTITY_OR_INTEGRITY_ANOMALIES:
            return "The anomaly blocks safe run identity or data integrity"
        if not anomaly.recovery_eligible:
            return "The anomaly is explicitly ineligible for recovery"
        return None

    @staticmethod
    def _action_rejection_reason(
        action: RecoveryAction,
        run_context: RunContext,
        supervisor_state: SupervisorState,
    ) -> str | None:
        if action not in _RECOVERY_ACTIONS:
            return "the action is not an executable recovery action"
        if action not in run_context.allowed_recovery_actions:
            return "the action is not allowed by the RunContext"
        if supervisor_state.recovery_budget_remaining <= 0:
            return "the recovery budget is exhausted"
        if (
            supervisor_state.last_recovery_action is action
            and supervisor_state.last_action_attempt_count
            >= _MAX_SAME_ACTION_ATTEMPTS
        ):
            return "the same action attempt limit is reached"
        return None

    @classmethod
    def _experience_rejection_reason(
        cls,
        case: ExperienceCase,
        anomaly: Anomaly,
        run_context: RunContext,
        supervisor_state: SupervisorState,
    ) -> str | None:
        required_tags = _normalized_tags(
            run_context.experience_query.required_context_tags
        )
        case_tags = _normalized_tags(case.context_tags)
        if case.verified is not True or case.deprecated is not False:
            return "the case is not currently verified and active"
        if case.case_type != "technical" or case.outcome != "recovered":
            return "the case is not a recovered technical case"
        if (
            case.validation_after_status is not ValidationStatus.PASS
            or case.ready_for_read_after is not True
            or case.successful_application_count <= 0
        ):
            return "the case lacks successful post-recovery validation"
        if case.anomaly_kind != anomaly.kind:
            return "the anomaly kind does not match"
        if run_context.project_id is None:
            if case.project_id is not None:
                return "the project scope does not match"
        elif case.project_id not in {None, run_context.project_id}:
            return "the project scope does not match"
        if run_context.environment_fingerprint is None:
            if case.environment_fingerprint is not None:
                return "the environment scope does not match"
        elif case.environment_fingerprint not in {
            None,
            run_context.environment_fingerprint,
        }:
            return "the environment scope does not match"
        if required_tags and not required_tags.issubset(case_tags):
            return "the required context tags do not match"
        if case.recovery_action is None:
            return "the case has no recovery action"

        action_rejection = cls._action_rejection_reason(
            case.recovery_action,
            run_context,
            supervisor_state,
        )
        if action_rejection is not None:
            return action_rejection
        if _RISK_ORDER[case.risk_level] > _RISK_ORDER[run_context.approval_risk_threshold]:
            return "the case risk exceeds the approval threshold"

        if case.recovery_action is RecoveryAction.RETRY_WITH_PARAMETER_CHANGE:
            if (
                not case.parameter_changes
                or case.target_sources
                or not _parameter_changes_are_safe(case.parameter_changes, run_context)
            ):
                return "the parameter change payload is unsafe"
        elif case.recovery_action is RecoveryAction.SKIP_OPTIONAL_SOURCE:
            if (
                case.parameter_changes
                or not case.target_sources
                or any(
                    source not in run_context.skippable_sources
                    for source in case.target_sources
                )
            ):
                return "the source skip payload is unsafe"
        elif case.recovery_action is RecoveryAction.RETRY_NEW_RUN:
            if case.parameter_changes or case.target_sources:
                return "the unchanged retry contains an action payload"
        else:
            return "the case action is not recoverable"
        return None

    def _decision_from_experience(
        self,
        case: ExperienceCase,
        anomaly: Anomaly,
        run_context: RunContext,
        supervisor_state: SupervisorState,
    ) -> RecoveryDecision:
        assert case.recovery_action is not None
        if case.risk_level is RiskLevel.LOW:
            return self._build_decision(
                anomaly=anomaly,
                run_context=run_context,
                supervisor_state=supervisor_state,
                action=case.recovery_action,
                reason=f"Apply verified recovery experience {case.case_id}",
                risk_level=case.risk_level,
                executable=True,
                parameter_changes=case.parameter_changes,
                target_sources=case.target_sources,
                matched_experiences=[case],
                evidence=case.evidence_refs,
            )
        return self._build_decision(
            anomaly=anomaly,
            run_context=run_context,
            supervisor_state=supervisor_state,
            action=RecoveryAction.REQUEST_APPROVAL,
            proposed_action=case.recovery_action,
            reason=f"Recovery experience {case.case_id} requires approval",
            risk_level=case.risk_level,
            requires_approval=True,
            parameter_changes=case.parameter_changes,
            target_sources=case.target_sources,
            matched_experiences=[case],
            evidence=case.evidence_refs,
        )

    def _decision_from_proposal(
        self,
        proposal: RecoveryProposal,
        anomaly: Anomaly,
        run_context: RunContext,
        supervisor_state: SupervisorState,
    ) -> RecoveryDecision:
        rejection = self._proposal_rejection_reason(
            proposal,
            anomaly,
            run_context,
            supervisor_state,
        )
        if rejection is not None:
            return self._build_decision(
                anomaly=anomaly,
                run_context=run_context,
                supervisor_state=supervisor_state,
                action=RecoveryAction.STOP_AND_REPORT,
                reason=f"Recovery proposal was rejected: {rejection}",
            )
        if proposal.proposed_action in {
            RecoveryAction.STOP_AND_REPORT,
            RecoveryAction.NO_ACTION,
        }:
            return self._build_decision(
                anomaly=anomaly,
                run_context=run_context,
                supervisor_state=supervisor_state,
                action=RecoveryAction.STOP_AND_REPORT,
                reason=(
                    proposal.reason
                    if proposal.proposed_action is RecoveryAction.STOP_AND_REPORT
                    else "Recovery Advisor cannot suppress a confirmed blocking anomaly"
                ),
                risk_level=proposal.risk_level,
                evidence=proposal.evidence_refs,
            )

        parameter_changes = _proposal_parameter_changes(proposal, run_context)
        assert parameter_changes is not None
        if proposal.risk_level is RiskLevel.LOW:
            return self._build_decision(
                anomaly=anomaly,
                run_context=run_context,
                supervisor_state=supervisor_state,
                action=proposal.proposed_action,
                reason=proposal.reason,
                risk_level=proposal.risk_level,
                executable=True,
                parameter_changes=parameter_changes,
                target_sources=proposal.target_sources,
                evidence=proposal.evidence_refs,
            )
        return self._build_decision(
            anomaly=anomaly,
            run_context=run_context,
            supervisor_state=supervisor_state,
            action=RecoveryAction.REQUEST_APPROVAL,
            proposal_id=proposal.proposal_id,
            proposed_action=proposal.proposed_action,
            reason=proposal.reason,
            risk_level=proposal.risk_level,
            requires_approval=True,
            parameter_changes=parameter_changes,
            target_sources=proposal.target_sources,
            evidence=proposal.evidence_refs,
        )

    @classmethod
    def _proposal_rejection_reason(
        cls,
        proposal: RecoveryProposal,
        anomaly: Anomaly,
        run_context: RunContext,
        supervisor_state: SupervisorState,
    ) -> str | None:
        if (
            proposal.context_id != run_context.context_id
            or proposal.run_id != anomaly.run_id
            or proposal.anomaly_id != anomaly.anomaly_id
        ):
            return "proposal identity does not match the active anomaly"
        if any(evidence not in anomaly.evidence_refs for evidence in proposal.evidence_refs):
            return "proposal evidence does not belong to the active anomaly"
        if _RISK_ORDER[proposal.risk_level] > _RISK_ORDER[run_context.approval_risk_threshold]:
            return "proposal risk exceeds the approval threshold"
        if proposal.proposed_action in {
            RecoveryAction.STOP_AND_REPORT,
            RecoveryAction.NO_ACTION,
        }:
            return None

        action_rejection = cls._action_rejection_reason(
            proposal.proposed_action,
            run_context,
            supervisor_state,
        )
        if action_rejection is not None:
            return action_rejection
        changes = _proposal_parameter_changes(proposal, run_context)
        if proposal.proposed_action is RecoveryAction.RETRY_WITH_PARAMETER_CHANGE:
            if not changes or proposal.target_sources:
                return "proposal parameter changes are unsafe"
        elif proposal.proposed_action is RecoveryAction.SKIP_OPTIONAL_SOURCE:
            if (
                proposal.parameter_changes
                or not proposal.target_sources
                or len(proposal.target_sources) != len(set(proposal.target_sources))
                or any(
                    source not in run_context.skippable_sources
                    for source in proposal.target_sources
                )
            ):
                return "proposal source targets are unsafe"
        elif proposal.proposed_action is RecoveryAction.RETRY_NEW_RUN:
            if proposal.parameter_changes or proposal.target_sources:
                return "proposal unchanged retry contains an action payload"
        else:
            return "proposal action is unsupported"
        return None

    @staticmethod
    def _build_decision(
        *,
        anomaly: Anomaly,
        run_context: RunContext,
        supervisor_state: SupervisorState,
        action: RecoveryAction,
        reason: str,
        risk_level: RiskLevel = RiskLevel.LOW,
        executable: bool = False,
        requires_approval: bool = False,
        proposal_id: str | None = None,
        proposed_action: RecoveryAction | None = None,
        parameter_changes: list[ParameterChange] | None = None,
        target_sources: list[str] | None = None,
        matched_experiences: list[ExperienceCase] | None = None,
        evidence: list[EvidenceRef] | None = None,
    ) -> RecoveryDecision:
        planned_action = proposed_action or action
        budget_before = supervisor_state.recovery_budget_remaining
        budget_cost = 1 if executable and planned_action in _RECOVERY_ACTIONS else 0
        now = _utc_now()
        return RecoveryDecision(
            decision_id=_new_decision_id(),
            run_id=anomaly.run_id,
            anomaly_id=anomaly.anomaly_id,
            created_at=now,
            decided_at=now,
            producer=_PRODUCER,
            producer_version=_PRODUCER_VERSION,
            action=action,
            reason=reason,
            risk_level=risk_level,
            executable=executable,
            new_run_required=planned_action in _NEW_RUN_ACTIONS,
            exploratory=False,
            requires_approval=requires_approval,
            approval_status="pending" if requires_approval else "not_required",
            attempt_index=supervisor_state.recovery_attempts + 1,
            budget_before=budget_before,
            budget_cost=budget_cost,
            budget_after=budget_before - budget_cost,
            max_same_action_attempts=_MAX_SAME_ACTION_ATTEMPTS,
            verification_policy=run_context.validation_policy_version,
            required_post_checks=list(_REQUIRED_POST_CHECKS),
            success_definition="All required post-recovery validation checks pass",
            stop_if_failed=True,
            proposal_id=proposal_id,
            proposed_action=proposed_action,
            target_phase=anomaly.affected_phase,
            target_sources=deepcopy([] if target_sources is None else target_sources),
            parameter_changes=deepcopy(
                [] if parameter_changes is None else parameter_changes
            ),
            preserve_artifacts=deepcopy(run_context.expected_artifacts),
            proposed_new_run_id=(
                _new_run_id()
                if executable and planned_action in _NEW_RUN_ACTIONS
                else None
            ),
            matched_experience_refs=[
                _experience_ref(case)
                for case in ([] if matched_experiences is None else matched_experiences)
            ],
            evidence_of_previous_success=deepcopy(
                [] if evidence is None else evidence
            ),
        )
