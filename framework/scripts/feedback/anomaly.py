"""Pure anomaly classification for structured Find feedback evidence."""

from __future__ import annotations

from uuid import uuid4

from .contracts import (
    Anomaly,
    ArtifactRef,
    EvidenceRef,
    ProgressSnapshot,
    ProgressStatus,
    ValidationCheck,
    ValidationResult,
    ValidationStatus,
)


_PRODUCER = "find_anomaly_builder"
_PRODUCER_VERSION = "1.0"
_ABNORMAL_PROGRESS_STATUSES = frozenset(
    {ProgressStatus.FAILED, ProgressStatus.STALLED, ProgressStatus.UNKNOWN}
)
_KIND_PRIORITY = {
    "process_exited_nonzero": 0,
    "startup_failed": 1,
    "completion_without_result": 2,
    "result_missing": 3,
    "result_unparseable": 4,
    "run_id_mismatch": 5,
    "source_integrity_blocked": 6,
    "empty_recommendations": 7,
    "recommendation_shortfall": 8,
    "reading_bridge_rejected": 9,
    "progress_stalled": 10,
    "progress_missing": 11,
    "progress_unparseable": 12,
}
_SYMPTOMS = {
    "startup_failed": "Find failed during startup",
    "progress_missing": "Structured Find progress is missing",
    "progress_unparseable": "Structured Find progress is not parseable",
    "progress_stalled": "Find progress reached the confirmed stall state",
    "process_exited_nonzero": "Find process did not exit successfully",
    "completion_without_result": "Find exited successfully without a result",
    "result_missing": "Find result is missing",
    "result_unparseable": "Find result is not parseable",
    "run_id_mismatch": "Find result run ID does not match the bound run",
    "empty_recommendations": "Find produced no usable recommendations",
    "recommendation_shortfall": "Find recommendations are below the required count",
    "source_integrity_blocked": "Find source integrity validation is blocked",
    "reading_bridge_rejected": "Find output does not meet Reading bridge requirements",
}
_RESULT_KINDS = frozenset(
    {
        "completion_without_result",
        "result_missing",
        "result_unparseable",
        "run_id_mismatch",
        "empty_recommendations",
        "recommendation_shortfall",
        "reading_bridge_rejected",
    }
)
_RECOVERY_ELIGIBLE_KINDS = frozenset(
    {
        "progress_stalled",
        "process_exited_nonzero",
        "empty_recommendations",
        "recommendation_shortfall",
    }
)
_RETRYABLE_SIGNAL_KINDS = frozenset(
    {
        "progress_stalled",
        "process_exited_nonzero",
    }
)


def _progress_artifact(snapshot: ProgressSnapshot) -> ArtifactRef | None:
    return next(
        (
            artifact
            for artifact in snapshot.artifact_observations
            if artifact.role == "progress"
        ),
        None,
    )


def _progress_kind(snapshot: ProgressSnapshot) -> str | None:
    if snapshot.status not in _ABNORMAL_PROGRESS_STATUSES:
        return None
    if snapshot.exit_code is not None and snapshot.exit_code != 0:
        return "process_exited_nonzero"
    if (
        snapshot.status in {ProgressStatus.FAILED, ProgressStatus.UNKNOWN}
        and snapshot.phase.strip().lower() == "starting"
        and not snapshot.process_alive
    ):
        return "startup_failed"
    if snapshot.status is ProgressStatus.STALLED:
        return "progress_stalled"

    artifact = _progress_artifact(snapshot)
    if artifact is None:
        return None
    if artifact.parse_status == "missing" or artifact.exists is False:
        return "progress_missing"
    if artifact.parse_status == "invalid":
        return "progress_unparseable"
    return None


def _check_status(
    result: ValidationResult,
    code: str,
) -> ValidationStatus | None:
    return next(
        (check.status for check in result.checks if check.code == code),
        None,
    )


def _blocked_codes(result: ValidationResult) -> list[str]:
    codes = [
        check.code
        for check in result.checks
        if check.status is ValidationStatus.BLOCK
    ]
    for code in result.failure_codes:
        if code not in codes:
            codes.append(code)
    return codes


def _validation_kind(result: ValidationResult) -> str | None:
    if result.status is not ValidationStatus.BLOCK:
        return None

    blocked = set(_blocked_codes(result))
    if "process_exit_code_ok" in blocked:
        return "process_exited_nonzero"
    if "result_exists" in blocked:
        if _check_status(result, "process_exit_code_ok") is ValidationStatus.PASS:
            return "completion_without_result"
        return "result_missing"
    if "result_parseable" in blocked:
        return "result_unparseable"
    if "result_run_id_matches" in blocked or "run_id_mismatch" in blocked:
        return "run_id_mismatch"
    if "source_integrity_not_blocking" in blocked:
        return "source_integrity_blocked"
    if result.recommendation_actual_count == 0 or not result.candidate_ids:
        return "empty_recommendations"
    if (
        blocked.intersection(
            {"strong_recommendations_present", "recommendation_count_sufficient"}
        )
        and result.recommendation_shortfall > 0
    ):
        return "recommendation_shortfall"
    if blocked.intersection(
        {"reading_candidate_ids_unique", "reading_bridge_probe_passed"}
    ):
        return "reading_bridge_rejected"
    return None


def _evidence_key(evidence: EvidenceRef) -> tuple[object, ...]:
    return (
        evidence.kind,
        evidence.summary,
        evidence.path,
        evidence.contract_id,
        evidence.line_start,
        evidence.line_end,
        evidence.sha256,
    )


def _copy_evidence(evidence: EvidenceRef) -> EvidenceRef:
    return EvidenceRef.from_dict(evidence.to_dict())


def _append_evidence(
    collected: list[EvidenceRef],
    seen: set[tuple[object, ...]],
    evidence: EvidenceRef,
) -> None:
    key = _evidence_key(evidence)
    if key in seen:
        return
    seen.add(key)
    collected.append(_copy_evidence(evidence))


def _artifact_evidence(artifact: ArtifactRef, contract_id: str) -> EvidenceRef:
    exists = "unknown" if artifact.exists is None else str(artifact.exists).lower()
    parse_status = artifact.parse_status or "unknown"
    return EvidenceRef(
        kind="artifact",
        summary=(
            f"{artifact.role} artifact: exists={exists}, "
            f"parse_status={parse_status}"
        ),
        path=artifact.path,
        contract_id=contract_id,
    )


def _collect_evidence(
    progress_snapshot: ProgressSnapshot | None,
    validation_result: ValidationResult | None,
) -> list[EvidenceRef]:
    collected: list[EvidenceRef] = []
    seen: set[tuple[object, ...]] = set()

    if progress_snapshot is not None:
        for evidence in progress_snapshot.evidence_refs:
            _append_evidence(collected, seen, evidence)
        _append_evidence(
            collected,
            seen,
            EvidenceRef(
                kind="snapshot",
                summary=f"Progress status: {progress_snapshot.status.value}",
                contract_id=progress_snapshot.snapshot_id,
            ),
        )
        for artifact in progress_snapshot.artifact_observations:
            _append_evidence(
                collected,
                seen,
                _artifact_evidence(artifact, progress_snapshot.snapshot_id),
            )
        for signal in progress_snapshot.signals:
            _append_evidence(
                collected,
                seen,
                EvidenceRef(
                    kind="log",
                    summary=f"Observer signal: {signal}",
                    contract_id=progress_snapshot.snapshot_id,
                ),
            )
        for signal in progress_snapshot.source_signals:
            _append_evidence(
                collected,
                seen,
                EvidenceRef(
                    kind="log",
                    summary=f"Observer source signal: {signal}",
                    contract_id=progress_snapshot.snapshot_id,
                ),
            )
        for error in progress_snapshot.observation_errors:
            _append_evidence(
                collected,
                seen,
                EvidenceRef(
                    kind="snapshot",
                    summary=f"Observation error: {error}",
                    contract_id=progress_snapshot.snapshot_id,
                ),
            )

    if validation_result is not None:
        for evidence in validation_result.evidence_refs:
            _append_evidence(collected, seen, evidence)
        for check in validation_result.checks:
            for evidence in check.evidence_refs:
                _append_evidence(collected, seen, evidence)
        _append_evidence(
            collected,
            seen,
            EvidenceRef(
                kind="validation",
                summary=f"Validation status: {validation_result.status.value}",
                contract_id=validation_result.validation_id,
            ),
        )
        for artifact in validation_result.input_artifact_refs:
            _append_evidence(
                collected,
                seen,
                _artifact_evidence(artifact, validation_result.validation_id),
            )
        for code in _blocked_codes(validation_result):
            _append_evidence(
                collected,
                seen,
                EvidenceRef(
                    kind="validation",
                    summary=f"Blocked validation code: {code}",
                    contract_id=validation_result.validation_id,
                ),
            )
    return collected


def _unique_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def _symptoms(
    primary_kind: str,
    progress_kind: str | None,
    validation_kind: str | None,
    progress_snapshot: ProgressSnapshot | None,
    validation_result: ValidationResult | None,
) -> list[str]:
    values = [_SYMPTOMS[primary_kind]]
    for kind in (validation_kind, progress_kind):
        if kind is not None and kind != primary_kind:
            values.append(f"Additional structured anomaly: {kind}")
    if validation_result is not None:
        values.extend(
            f"Blocked validation code: {code}"
            for code in _blocked_codes(validation_result)
        )
    if progress_snapshot is not None:
        values.extend(
            f"Observer signal: {signal}"
            for signal in [
                *progress_snapshot.signals,
                *progress_snapshot.source_signals,
            ]
        )
    return _unique_strings(values)


def _artifact_facts(
    progress_snapshot: ProgressSnapshot | None,
    validation_result: ValidationResult | None,
) -> dict[str, object]:
    facts: dict[str, object] = {}
    if progress_snapshot is not None:
        facts.update(
            {
                "progress_parse_ok": progress_snapshot.progress_parse_ok,
                "result_exists": progress_snapshot.result_exists,
                "source_status_exists": progress_snapshot.source_status_exists,
                "source_total": progress_snapshot.source_total,
                "source_ready": progress_snapshot.source_ready,
                "source_limited": progress_snapshot.source_limited,
                "source_failed": progress_snapshot.source_failed,
            }
        )
    if validation_result is not None:
        facts.update(
            {
                "validation_failure_codes": _blocked_codes(validation_result),
                "recommendation_target_count": validation_result.recommendation_target_count,
                "recommendation_actual_count": validation_result.recommendation_actual_count,
                "recommendation_shortfall": validation_result.recommendation_shortfall,
                "candidate_count": len(validation_result.candidate_ids),
            }
        )
    return facts


def _optional_anomaly_fields(
    primary_kind: str,
    progress_snapshot: ProgressSnapshot | None,
    validation_result: ValidationResult | None,
) -> dict[str, object]:
    values: dict[str, object] = {}
    if progress_snapshot is not None:
        values.update(
            {
                "progress_snapshot_id": progress_snapshot.snapshot_id,
                "process_facts": {
                    "pid": progress_snapshot.pid,
                    "process_alive": progress_snapshot.process_alive,
                    "exit_code": progress_snapshot.exit_code,
                },
                "timing_facts": {
                    "elapsed_seconds": progress_snapshot.elapsed_seconds,
                    "seconds_without_progress": progress_snapshot.seconds_without_progress,
                },
            }
        )
    if validation_result is not None:
        values["validation_id"] = validation_result.validation_id
        timing_facts = dict(values.get("timing_facts", {}))
        timing_facts["validation_duration_ms"] = validation_result.duration_ms
        values["timing_facts"] = timing_facts

    artifact_facts = _artifact_facts(progress_snapshot, validation_result)
    if artifact_facts:
        values["artifact_facts"] = artifact_facts

    missing_evidence = {
        "progress_missing": ["progress artifact"],
        "progress_unparseable": ["valid progress evidence"],
        "completion_without_result": ["result artifact"],
        "result_missing": ["result artifact"],
        "result_unparseable": ["parseable result evidence"],
    }.get(primary_kind)
    if missing_evidence is not None:
        values["missing_evidence"] = missing_evidence

    if primary_kind.startswith("progress_"):
        values["affected_artifacts"] = ["progress"]
    elif primary_kind in _RESULT_KINDS:
        values["affected_artifacts"] = ["find_results"]
    elif primary_kind == "source_integrity_blocked":
        values["affected_artifacts"] = ["source_status"]
    return values


class FindAnomalyBuilder:
    """Build one deterministic anomaly classification from structured evidence."""

    def build(
        self,
        *,
        progress_snapshot: ProgressSnapshot | None = None,
        validation_result: ValidationResult | None = None,
    ) -> Anomaly | None:
        if progress_snapshot is None and validation_result is None:
            raise ValueError("at least one anomaly evidence input is required")
        if progress_snapshot is not None and not isinstance(
            progress_snapshot, ProgressSnapshot
        ):
            raise TypeError("progress_snapshot must be a ProgressSnapshot or None")
        if validation_result is not None and not isinstance(
            validation_result, ValidationResult
        ):
            raise TypeError("validation_result must be a ValidationResult or None")
        if progress_snapshot is not None and validation_result is not None:
            if (
                not progress_snapshot.run_id
                or not validation_result.run_id
                or progress_snapshot.run_id != validation_result.run_id
            ):
                raise ValueError("dual inputs must describe the same bound run")

        progress_kind = (
            _progress_kind(progress_snapshot)
            if progress_snapshot is not None
            else None
        )
        validation_kind = (
            _validation_kind(validation_result)
            if validation_result is not None
            else None
        )
        kinds = [kind for kind in (progress_kind, validation_kind) if kind is not None]
        if not kinds:
            return None
        primary_kind = min(kinds, key=_KIND_PRIORITY.__getitem__)

        run_id = (
            validation_result.run_id
            if validation_result is not None
            else progress_snapshot.run_id  # type: ignore[union-attr]
        )
        recovery_eligible = bool(run_id) and primary_kind in _RECOVERY_ELIGIBLE_KINDS
        retryable_signal = (
            recovery_eligible and primary_kind in _RETRYABLE_SIGNAL_KINDS
        )
        detected_at = max(
            [
                *(
                    [progress_snapshot.observed_at]
                    if progress_snapshot is not None
                    else []
                ),
                *(
                    [validation_result.validated_at]
                    if validation_result is not None
                    else []
                ),
            ]
        )
        source_id = (
            run_id
            or (
                progress_snapshot.snapshot_id
                if progress_snapshot is not None
                else validation_result.validation_id  # type: ignore[union-attr]
            )
        )
        detected_by = _unique_strings(
            [
                *(
                    [progress_snapshot.producer]
                    if progress_snapshot is not None
                    else []
                ),
                *(
                    [validation_result.producer]
                    if validation_result is not None
                    else []
                ),
            ]
        )
        if not detected_by:
            detected_by = [_PRODUCER]

        return Anomaly(
            anomaly_id=f"anomaly-{uuid4().hex}",
            run_id=run_id,
            created_at=detected_at,
            detected_at=detected_at,
            updated_at=detected_at,
            producer=_PRODUCER,
            producer_version=_PRODUCER_VERSION,
            stage="find",
            kind=primary_kind,
            blocking=True,
            confidence=0.5,
            detected_by=detected_by,
            supervisor_state_revision=0,
            symptoms=_symptoms(
                primary_kind,
                progress_kind,
                validation_kind,
                progress_snapshot,
                validation_result,
            ),
            evidence_refs=_collect_evidence(progress_snapshot, validation_result),
            root_cause_status="unknown",
            affected_phase=(
                progress_snapshot.phase
                if progress_snapshot is not None and progress_snapshot.phase
                else "finding"
            ),
            downstream_impact=(
                "Find output is not ready for Read"
                if validation_result is not None
                else "Find completion cannot be confirmed"
            ),
            partial_results_usable=False,
            recovery_eligible=recovery_eligible,
            retryable_signal=retryable_signal,
            fingerprint=f"{primary_kind}:{source_id}",
            occurrence_count=1,
            **_optional_anomaly_fields(
                primary_kind,
                progress_snapshot,
                validation_result,
            ),
        )
