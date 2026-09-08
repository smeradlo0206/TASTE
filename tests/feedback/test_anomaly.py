from __future__ import annotations

from datetime import datetime, timezone
import inspect
from pathlib import Path
from typing import get_type_hints

import pytest

import feedback
from feedback import (
    Anomaly,
    ArtifactRef,
    EvidenceRef,
    FindAnomalyBuilder,
    ProgressSnapshot,
    ProgressStatus,
    ValidationCheck,
    ValidationResult,
    ValidationStatus,
)
from feedback.interfaces import AnomalyBuilder


RUN_ID = "find_20260906_120000_000001"
OBSERVED_AT = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


def _artifact(
    *,
    role: str = "progress",
    exists: bool = True,
    parse_status: str = "valid",
) -> ArtifactRef:
    return ArtifactRef(
        role=role,
        path=f"/runtime/{RUN_ID}/{role}.json",
        required=True,
        exists=exists,
        parse_status=parse_status,
    )


def _progress(
    status: ProgressStatus = ProgressStatus.RUNNING,
    **overrides: object,
) -> ProgressSnapshot:
    values: dict[str, object] = {
        "snapshot_id": "snapshot-anomaly-test",
        "run_id": RUN_ID,
        "created_at": OBSERVED_AT,
        "observed_at": OBSERVED_AT,
        "sequence": 2,
        "producer": "file_progress_observer",
        "producer_version": "1.0",
        "status": status,
        "phase": "finding",
        "counts": {"candidates": 2},
        "elapsed_seconds": 30.0,
        "seconds_without_progress": 0.0,
        "process_alive": True,
        "cancel_requested": False,
        "artifact_observations": [_artifact()],
        "progress_parse_ok": True,
        "result_exists": False,
        "source_status_exists": False,
        "source_total": 1,
        "source_ready": 1,
        "source_limited": 0,
        "source_failed": 0,
        "status_reason": f"Find status is {status.value}",
        "pid": 7301,
        "signals": [],
        "source_signals": [],
        "observation_errors": [],
        "evidence_refs": [],
    }
    values.update(overrides)
    return ProgressSnapshot(**values)  # type: ignore[arg-type]


def _check(
    code: str,
    status: ValidationStatus = ValidationStatus.BLOCK,
    *,
    evidence_refs: list[EvidenceRef] | None = None,
) -> ValidationCheck:
    return ValidationCheck(
        code=code,
        status=status,
        required=status is ValidationStatus.BLOCK,
        message=f"Structured check {code} is {status.value}",
        evidence_refs=[] if evidence_refs is None else evidence_refs,
    )


def _validation(
    *,
    status: ValidationStatus = ValidationStatus.PASS,
    checks: list[ValidationCheck] | None = None,
    **overrides: object,
) -> ValidationResult:
    if checks is None:
        checks = [_check("run_dir_exists", ValidationStatus.PASS)]
    values: dict[str, object] = {
        "validation_id": "validation-anomaly-test",
        "run_id": RUN_ID,
        "created_at": OBSERVED_AT,
        "validated_at": OBSERVED_AT,
        "validated_run_dir": f"/runtime/{RUN_ID}",
        "producer": "find_result_validator",
        "producer_version": "1.0",
        "policy_version": "find.validation.minimum.v1",
        "duration_ms": 4,
        "status": status,
        "ready_for_read": status is ValidationStatus.PASS,
        "summary": f"Validation status is {status.value}",
        "checks": checks,
        "passed_check_count": sum(
            check.status is ValidationStatus.PASS for check in checks
        ),
        "warning_check_count": sum(
            check.status is ValidationStatus.WARNING for check in checks
        ),
        "blocked_check_count": sum(
            check.status is ValidationStatus.BLOCK for check in checks
        ),
        "recommendation_target_count": 2,
        "recommendation_actual_count": 2,
        "recommendation_shortfall": 0,
        "strong_recommendation_count": 2,
        "recommendation_quality_status": "ok",
        "candidate_ids": ["paper-1", "paper-2"],
        "candidate_digest": "candidate-digest",
        "bridge_probe_status": ValidationStatus.PASS,
        "input_artifact_refs": [_artifact(role="result")],
        "bridge_probe_errors": [],
        "warnings": [],
        "blockers": [
            check.message
            for check in checks
            if check.status is ValidationStatus.BLOCK
        ],
        "failure_codes": [
            check.code
            for check in checks
            if check.status is ValidationStatus.BLOCK
        ],
        "evidence_refs": [],
    }
    values.update(overrides)
    return ValidationResult(**values)  # type: ignore[arg-type]


def _blocked_validation(
    *codes: str,
    pass_codes: tuple[str, ...] = (),
    **overrides: object,
) -> ValidationResult:
    checks = [*(_check(code, ValidationStatus.PASS) for code in pass_codes)]
    checks.extend(_check(code) for code in codes)
    return _validation(
        status=ValidationStatus.BLOCK,
        checks=checks,
        **overrides,
    )


def _assert_kind(anomaly: Anomaly | None, expected: str) -> Anomaly:
    assert isinstance(anomaly, Anomaly)
    assert anomaly.kind == expected
    return anomaly


def test_build_requires_at_least_one_input() -> None:
    with pytest.raises(ValueError, match="at least one"):
        FindAnomalyBuilder().build()


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"progress_snapshot": object()}, "progress_snapshot"),
        ({"validation_result": object()}, "validation_result"),
    ],
)
def test_build_rejects_invalid_input_types(
    arguments: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(TypeError, match=message):
        FindAnomalyBuilder().build(**arguments)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "status",
    [
        ProgressStatus.STARTING,
        ProgressStatus.RUNNING,
        ProgressStatus.COMPLETED,
        ProgressStatus.SUSPECTED_STALL,
    ],
)
def test_normal_observer_statuses_return_none(status: ProgressStatus) -> None:
    assert FindAnomalyBuilder().build(progress_snapshot=_progress(status)) is None


def test_requested_cancellation_returns_none() -> None:
    snapshot = _progress(
        ProgressStatus.CANCELLED,
        process_alive=False,
        cancel_requested=True,
    )

    assert FindAnomalyBuilder().build(progress_snapshot=snapshot) is None


def test_validator_pass_with_local_warning_returns_none() -> None:
    result = _validation(
        checks=[
            _check("run_dir_exists", ValidationStatus.PASS),
            _check("source_integrity_not_blocking", ValidationStatus.WARNING),
        ],
        warnings=["Source returned a recoverable warning"],
    )
    snapshot = _progress(source_signals=["http_429"])

    assert FindAnomalyBuilder().build(validation_result=result) is None
    assert (
        FindAnomalyBuilder().build(
            progress_snapshot=snapshot,
            validation_result=result,
        )
        is None
    )


def test_missing_progress_is_ignored_while_observer_reports_running() -> None:
    snapshot = _progress(
        ProgressStatus.RUNNING,
        progress_parse_ok=False,
        artifact_observations=[
            _artifact(exists=False, parse_status="missing")
        ],
        observation_errors=["progress file is missing"],
    )

    assert FindAnomalyBuilder().build(progress_snapshot=snapshot) is None


@pytest.mark.parametrize(
    ("snapshot", "expected_kind"),
    [
        (
            _progress(
                ProgressStatus.STALLED,
                seconds_without_progress=120.0,
            ),
            "progress_stalled",
        ),
        (
            _progress(
                ProgressStatus.UNKNOWN,
                progress_parse_ok=False,
                artifact_observations=[
                    _artifact(exists=False, parse_status="missing")
                ],
                observation_errors=["progress file is missing"],
            ),
            "progress_missing",
        ),
        (
            _progress(
                ProgressStatus.UNKNOWN,
                progress_parse_ok=False,
                artifact_observations=[_artifact(parse_status="invalid")],
                observation_errors=["progress JSON could not be parsed"],
            ),
            "progress_unparseable",
        ),
        (
            _progress(
                ProgressStatus.FAILED,
                process_alive=False,
                exit_code=7,
            ),
            "process_exited_nonzero",
        ),
        (
            _progress(
                ProgressStatus.FAILED,
                run_id="",
                phase="starting",
                process_alive=False,
                exit_code=None,
            ),
            "startup_failed",
        ),
    ],
)
def test_observer_anomalies_map_from_structured_fields(
    snapshot: ProgressSnapshot,
    expected_kind: str,
) -> None:
    anomaly = _assert_kind(
        FindAnomalyBuilder().build(progress_snapshot=snapshot),
        expected_kind,
    )

    assert anomaly.progress_snapshot_id == snapshot.snapshot_id
    assert anomaly.validation_id is None
    assert anomaly.run_id == snapshot.run_id


@pytest.mark.parametrize(
    ("result", "expected_kind"),
    [
        (
            _blocked_validation("process_exit_code_ok"),
            "process_exited_nonzero",
        ),
        (_blocked_validation("result_exists"), "result_missing"),
        (_blocked_validation("result_parseable"), "result_unparseable"),
        (_blocked_validation("result_run_id_matches"), "run_id_mismatch"),
        (
            _blocked_validation("source_integrity_not_blocking"),
            "source_integrity_blocked",
        ),
        (
            _blocked_validation(
                "recommendation_count_sufficient",
                recommendation_target_count=3,
                recommendation_actual_count=2,
                recommendation_shortfall=1,
            ),
            "recommendation_shortfall",
        ),
        (
            _blocked_validation("reading_candidate_ids_unique"),
            "reading_bridge_rejected",
        ),
        (
            _blocked_validation("reading_bridge_probe_passed"),
            "reading_bridge_rejected",
        ),
    ],
)
def test_validator_blocks_map_from_structured_checks(
    result: ValidationResult,
    expected_kind: str,
) -> None:
    anomaly = _assert_kind(
        FindAnomalyBuilder().build(validation_result=result),
        expected_kind,
    )

    assert anomaly.validation_id == result.validation_id
    assert anomaly.progress_snapshot_id is None
    assert anomaly.run_id == result.run_id


def test_successful_completion_without_result_has_specific_kind() -> None:
    result = _blocked_validation(
        "result_exists",
        pass_codes=("process_exit_code_ok",),
    )

    _assert_kind(
        FindAnomalyBuilder().build(validation_result=result),
        "completion_without_result",
    )


def test_zero_candidates_map_to_empty_recommendations_before_bridge_rejection() -> None:
    result = _blocked_validation(
        "reading_bridge_probe_passed",
        recommendation_actual_count=0,
        strong_recommendation_count=0,
        candidate_ids=[],
    )

    _assert_kind(
        FindAnomalyBuilder().build(validation_result=result),
        "empty_recommendations",
    )


@pytest.mark.parametrize(
    ("progress_run_id", "validation_run_id"),
    [(RUN_ID, "find-other"), ("", RUN_ID), (RUN_ID, "")],
)
def test_dual_inputs_require_the_same_bound_run(
    progress_run_id: str,
    validation_run_id: str,
) -> None:
    with pytest.raises(ValueError, match="same bound run"):
        FindAnomalyBuilder().build(
            progress_snapshot=_progress(
                ProgressStatus.STALLED,
                run_id=progress_run_id,
            ),
            validation_result=_blocked_validation(
                "result_exists",
                run_id=validation_run_id,
            ),
        )


def test_dual_input_returns_one_anomaly_using_stable_priority() -> None:
    snapshot = _progress(ProgressStatus.STALLED)
    result = _blocked_validation(
        "process_exit_code_ok",
        "result_exists",
    )

    first = _assert_kind(
        FindAnomalyBuilder().build(
            progress_snapshot=snapshot,
            validation_result=result,
        ),
        "process_exited_nonzero",
    )
    second = _assert_kind(
        FindAnomalyBuilder().build(
            progress_snapshot=snapshot,
            validation_result=result,
        ),
        "process_exited_nonzero",
    )

    assert first.symptoms == second.symptoms
    assert first.evidence_refs == second.evidence_refs
    assert first.fingerprint == second.fingerprint


def test_duplicate_evidence_is_removed_and_error_codes_are_preserved() -> None:
    shared = EvidenceRef(
        kind="validation",
        summary="shared structured evidence",
        contract_id="shared-contract",
    )
    snapshot = _progress(
        ProgressStatus.STALLED,
        evidence_refs=[shared],
        signals=["timeout"],
        source_signals=["http_429"],
    )
    result = _blocked_validation(
        "result_parseable",
    )
    result.evidence_refs.append(shared)
    result.checks[0].evidence_refs.append(shared)

    anomaly = _assert_kind(
        FindAnomalyBuilder().build(
            progress_snapshot=snapshot,
            validation_result=result,
        ),
        "result_unparseable",
    )
    evidence_keys = [evidence.to_json() for evidence in anomaly.evidence_refs]

    assert len(evidence_keys) == len(set(evidence_keys))
    assert any("result_parseable" in evidence.summary for evidence in anomaly.evidence_refs)
    assert any("timeout" in evidence.summary for evidence in anomaly.evidence_refs)
    assert any("http_429" in evidence.summary for evidence in anomaly.evidence_refs)


def test_build_does_not_modify_input_contracts() -> None:
    snapshot = _progress(
        ProgressStatus.STALLED,
        signals=["timeout"],
        evidence_refs=[EvidenceRef(kind="snapshot", summary="existing evidence")],
    )
    result = _blocked_validation("result_parseable")
    before = (snapshot.to_json(), result.to_json())

    FindAnomalyBuilder().build(
        progress_snapshot=snapshot,
        validation_result=result,
    )

    assert (snapshot.to_json(), result.to_json()) == before


def test_public_import_signature_and_protocol_substitution() -> None:
    from feedback import FindAnomalyBuilder as PublicFindAnomalyBuilder
    from feedback.anomaly import FindAnomalyBuilder as ModuleFindAnomalyBuilder

    assert PublicFindAnomalyBuilder is FindAnomalyBuilder
    assert ModuleFindAnomalyBuilder is FindAnomalyBuilder
    assert "FindAnomalyBuilder" in feedback.__all__
    signature = inspect.signature(FindAnomalyBuilder.build)
    hints = get_type_hints(FindAnomalyBuilder.build)
    assert list(signature.parameters) == [
        "self",
        "progress_snapshot",
        "validation_result",
    ]
    assert signature.parameters["progress_snapshot"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["validation_result"].kind is inspect.Parameter.KEYWORD_ONLY
    assert hints["progress_snapshot"] == ProgressSnapshot | None
    assert hints["validation_result"] == ValidationResult | None
    assert hints["return"] == Anomaly | None
    assert {
        name for name in FindAnomalyBuilder.__dict__ if not name.startswith("_")
    } == {"build"}

    builder: AnomalyBuilder = FindAnomalyBuilder()
    assert isinstance(
        builder.build(progress_snapshot=_progress(ProgressStatus.STALLED)),
        Anomaly,
    )


def test_build_never_reads_or_writes_files(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_file_access(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("FindAnomalyBuilder must not access files")

    monkeypatch.setattr(Path, "open", fail_file_access)
    monkeypatch.setattr(Path, "read_text", fail_file_access)
    monkeypatch.setattr(Path, "write_text", fail_file_access)

    anomaly = FindAnomalyBuilder().build(
        progress_snapshot=_progress(ProgressStatus.STALLED)
    )

    assert isinstance(anomaly, Anomaly)
