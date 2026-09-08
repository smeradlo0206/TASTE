"""LLM-backed generation of untrusted Find recovery proposals."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import json
from uuid import uuid4

from integrations.web_llm import LLMClient

from .contracts import (
    Anomaly,
    EvidenceRef,
    ExperienceCase,
    RecoveryAction,
    RecoveryProposal,
    RiskLevel,
    RunContext,
    SupervisorState,
)


_RESPONSE_FIELDS = frozenset(
    {
        "proposed_action",
        "reason",
        "parameter_changes",
        "target_sources",
        "evidence_ref_ids",
        "confidence",
        "risk_level",
    }
)
_SENSITIVE_KEY_MARKERS = (
    "api_key",
    "apikey",
    "token",
    "secret",
    "password",
    "authorization",
    "credential",
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_proposal_id() -> str:
    return f"proposal-{uuid4().hex}"


def _is_sensitive_key(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in _SENSITIVE_KEY_MARKERS)


def _redact_text(value: str) -> str:
    lowered = value.lower()
    sensitive_markers = (*_SENSITIVE_KEY_MARKERS, "api key", "bearer ", "sk-")
    if any(marker in lowered for marker in sensitive_markers):
        return "[REDACTED]"
    return value


def _safe_json_summary(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _safe_json_summary(item)
            for key, item in value.items()
            if isinstance(key, str) and not _is_sensitive_key(key)
        }
    if isinstance(value, (list, tuple)):
        return [_safe_json_summary(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


def _evidence_index(
    anomaly: Anomaly,
) -> tuple[list[dict[str, object]], dict[str, EvidenceRef]]:
    summaries: list[dict[str, object]] = []
    references: dict[str, EvidenceRef] = {}
    for index, evidence in enumerate(anomaly.evidence_refs):
        evidence_id = f"evidence-{index}"
        references[evidence_id] = evidence
        summaries.append(
            {
                "evidence_ref_id": evidence_id,
                "kind": evidence.kind,
                "contract_id": evidence.contract_id,
                "summary": _redact_text(evidence.summary),
            }
        )
    return summaries, references


def _experience_summary(case: ExperienceCase) -> dict[str, object]:
    parameter_changes: list[dict[str, object]] = []
    for change in case.parameter_changes:
        if _is_sensitive_key(change.name):
            continue
        parameter_changes.append(
            {
                "name": change.name,
                "before": _safe_json_summary(change.before),
                "after": _safe_json_summary(change.after),
                "reason": _redact_text(change.reason),
            }
        )
    return {
        "case_id": case.case_id,
        "anomaly_kind": case.anomaly_kind,
        "recovery_action": (
            case.recovery_action.value if case.recovery_action is not None else None
        ),
        "verified": case.verified,
        "parameter_changes": parameter_changes,
        "successful_application_count": case.successful_application_count,
        "confidence": case.confidence,
        "project_id": case.project_id,
        "environment_fingerprint": case.environment_fingerprint,
        "context_tags": [_redact_text(tag) for tag in case.context_tags],
    }


def _build_prompt(
    anomaly: Anomaly,
    run_context: RunContext,
    supervisor_state: SupervisorState,
    matched_experiences: list[ExperienceCase],
) -> tuple[str, dict[str, EvidenceRef]]:
    evidence_summaries, evidence_by_id = _evidence_index(anomaly)
    payload = {
        "task": (
            "Return exactly one JSON object containing only proposed_action, reason, "
            "parameter_changes, target_sources, evidence_ref_ids, confidence, and "
            "risk_level. Return no Markdown, no explanation, no prefix or suffix, and "
            "no unknown fields. The result is untrusted advice and must not claim "
            "execution or approval."
        ),
        "allowed_actions": [
            action.value
            for action in (
                RecoveryAction.NO_ACTION,
                RecoveryAction.RETRY_NEW_RUN,
                RecoveryAction.RETRY_WITH_PARAMETER_CHANGE,
                RecoveryAction.SKIP_OPTIONAL_SOURCE,
                RecoveryAction.STOP_AND_REPORT,
            )
        ],
        "allowed_risk_levels": [level.value for level in RiskLevel],
        "anomaly": {
            "anomaly_id": anomaly.anomaly_id,
            "context_id": run_context.context_id,
            "run_id": anomaly.run_id,
            "kind": anomaly.kind,
            "blocking": anomaly.blocking,
            "confidence": anomaly.confidence,
            "symptoms": [_redact_text(item) for item in anomaly.symptoms],
            "evidence_refs": evidence_summaries,
            "recovery_eligible": anomaly.recovery_eligible,
            "retryable_signal": anomaly.retryable_signal,
        },
        "run_context": {
            "context_id": run_context.context_id,
            "project_id": run_context.project_id,
            "stage": run_context.stage,
            "attempt_index": run_context.attempt_index,
            "allowed_recovery_actions": [
                action.value for action in run_context.allowed_recovery_actions
            ],
            "recovery_budget": run_context.recovery_budget,
            "effective_parameters": _safe_json_summary(
                run_context.effective_parameters
            ),
        },
        "supervisor_state": {
            "status": supervisor_state.status.value,
            "recovery_attempts": supervisor_state.recovery_attempts,
            "recovery_budget_total": supervisor_state.recovery_budget_total,
            "recovery_budget_remaining": supervisor_state.recovery_budget_remaining,
            "last_recovery_action": (
                supervisor_state.last_recovery_action.value
                if supervisor_state.last_recovery_action is not None
                else None
            ),
            "active_recovery_decision_id": (
                supervisor_state.active_recovery_decision_id
            ),
            "recent_events": [
                {
                    "event_type": event.event_type.value,
                    "message": _redact_text(event.message),
                    "contract_id": event.contract_id,
                }
                for event in supervisor_state.recent_events[-5:]
            ],
        },
        "matched_experiences": [
            _experience_summary(case) for case in matched_experiences
        ],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True), evidence_by_id


def _validate_response(parsed: object) -> dict[str, object]:
    if not isinstance(parsed, dict):
        raise ValueError("response must be a JSON object")
    if set(parsed) != _RESPONSE_FIELDS:
        raise ValueError("response fields do not match the required schema")
    return parsed


def _is_client_response_extraction_failure(exc: Exception) -> bool:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, json.JSONDecodeError) or (
            "returned no extractable text" in str(current).lower()
        ):
            return True
        current = current.__cause__ or current.__context__
    return False


def _select_evidence(
    value: object,
    evidence_by_id: dict[str, EvidenceRef],
) -> list[EvidenceRef]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError("evidence_ref_ids must be a list of strings")
    evidence_ids = list(value)
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ValueError("evidence_ref_ids must not contain duplicates")
    try:
        return [evidence_by_id[evidence_id] for evidence_id in evidence_ids]
    except KeyError as exc:
        raise LookupError("unknown anomaly evidence") from exc


class LLMRecoveryAdvisor:
    """Generate one untrusted proposal through the existing Framework LLM client."""

    def __init__(self, *, llm_client: LLMClient) -> None:
        self._llm_client = llm_client

    def propose(
        self,
        *,
        anomaly: Anomaly,
        run_context: RunContext,
        supervisor_state: SupervisorState,
        matched_experiences: list[ExperienceCase],
    ) -> RecoveryProposal:
        if not isinstance(anomaly, Anomaly):
            raise TypeError("anomaly must be an Anomaly")
        if not isinstance(run_context, RunContext):
            raise TypeError("run_context must be a RunContext")
        if not isinstance(supervisor_state, SupervisorState):
            raise TypeError("supervisor_state must be a SupervisorState")
        if not isinstance(matched_experiences, list):
            raise TypeError("matched_experiences must be a list of ExperienceCase objects")
        for index, case in enumerate(matched_experiences):
            if not isinstance(case, ExperienceCase):
                raise TypeError(
                    f"matched_experiences[{index}] must be an ExperienceCase"
                )

        active_run_id = supervisor_state.active_run_id or supervisor_state.root_run_id
        if (
            supervisor_state.project_id != run_context.project_id
            or supervisor_state.run_context_id != run_context.context_id
            or active_run_id != anomaly.run_id
            or supervisor_state.active_anomaly_id != anomaly.anomaly_id
        ):
            raise ValueError("Recovery Advisor input identity mismatch")

        prompt, evidence_by_id = _build_prompt(
            anomaly,
            run_context,
            supervisor_state,
            matched_experiences,
        )
        try:
            raw_response = self._llm_client.chat(prompt)
        except Exception as exc:
            if _is_client_response_extraction_failure(exc):
                raise RuntimeError(
                    "Recovery Advisor response extraction failed"
                ) from exc
            raise RuntimeError("Recovery Advisor LLM transport failed") from exc

        if raw_response is None or (
            isinstance(raw_response, str) and not raw_response.strip()
        ):
            cause = ValueError("response is empty")
            raise RuntimeError(
                "Recovery Advisor returned an empty response"
            ) from cause
        if not isinstance(raw_response, str):
            cause = TypeError(
                f"response must be text, got {type(raw_response).__name__}"
            )
            raise RuntimeError(
                "Recovery Advisor response extraction failed"
            ) from cause

        try:
            parsed_response = json.loads(raw_response)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "Recovery Advisor returned invalid JSON"
            ) from exc

        try:
            response = _validate_response(parsed_response)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                "Recovery Advisor returned an invalid recovery proposal"
            ) from exc

        try:
            evidence_refs = _select_evidence(
                response["evidence_ref_ids"],
                evidence_by_id,
            )
        except LookupError as exc:
            raise RuntimeError(
                "Recovery Advisor referenced unknown anomaly evidence"
            ) from exc
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                "Recovery Advisor returned an invalid recovery proposal"
            ) from exc

        try:
            return RecoveryProposal(
                proposal_id=_new_proposal_id(),
                context_id=run_context.context_id,
                run_id=anomaly.run_id,
                anomaly_id=anomaly.anomaly_id,
                created_at=_utc_now(),
                proposed_action=RecoveryAction(response["proposed_action"]),
                reason=response["reason"],
                parameter_changes=response["parameter_changes"],
                target_sources=response["target_sources"],
                evidence_refs=evidence_refs,
                confidence=response["confidence"],
                risk_level=RiskLevel(response["risk_level"]),
            )
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                "Recovery Advisor returned an invalid recovery proposal"
            ) from exc
