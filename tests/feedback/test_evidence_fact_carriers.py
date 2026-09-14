from __future__ import annotations

from dataclasses import fields
from datetime import datetime, timedelta, timezone
import json

import pytest

from feedback import (
    Anomaly,
    ArtifactRef,
    EvidenceCollectionRule,
    EvidenceDefinition,
    EvidenceFact,
    EvidenceRef,
    ProgressSnapshot,
    ProgressStatus,
    ValidationCheck,
    ValidationResult,
    ValidationStatus,
)


OBSERVED_AT = datetime(2026, 9, 14, 9, 0, tzinfo=timezone.utc)
RUN_ID = "find-001"


def _fact(**overrides: object) -> EvidenceFact:
    values: dict[str, object] = {
        "code": "find.source_failed",
        "value": {"count": 1, "sources": ["openalex"]},
        "source_contract_id": "snapshot-001",
        "source_field": "source_failed",
        "producer": "progress-observer",
        "run_id": RUN_ID,
        "observed_at": OBSERVED_AT,
    }
    values.update(overrides)
    return EvidenceFact(**values)  # type: ignore[arg-type]


def _snapshot(**overrides: object) -> ProgressSnapshot:
    values: dict[str, object] = {
        "snapshot_id": "snapshot-001",
        "run_id": RUN_ID,
        "created_at": OBSERVED_AT,
        "observed_at": OBSERVED_AT,
        "sequence": 1,
        "producer": "progress-observer",
        "producer_version": "1.0",
        "status": ProgressStatus.RUNNING,
        "phase": "source_fetch",
        "counts": {"raw_title_index_papers": 10},
        "elapsed_seconds": 20.0,
        "seconds_without_progress": 5.0,
        "process_alive": True,
        "cancel_requested": False,
        "artifact_observations": [
            ArtifactRef(
                role="progress",
                path="/runs/find-001/logs/find_progress.json",
                required=True,
                exists=True,
            )
        ],
        "progress_parse_ok": True,
        "result_exists": False,
        "source_status_exists": True,
        "source_total": 2,
        "source_ready": 1,
        "source_limited": 0,
        "source_failed": 1,
        "status_reason": "Source status observed",
        "evidence_refs": [
            EvidenceRef(kind="log", summary="Progress snapshot parsed")
        ],
        "evidence_facts": [],
    }
    values.update(overrides)
    return ProgressSnapshot(**values)  # type: ignore[arg-type]


def _validation_fact(**overrides: object) -> EvidenceFact:
    values: dict[str, object] = {
        "code": "find.result_exists",
        "value": True,
        "source_contract_id": "validation-001",
        "source_field": "checks.result_exists.actual",
        "producer": "find-validator",
        "run_id": RUN_ID,
        "observed_at": OBSERVED_AT,
    }
    values.update(overrides)
    return EvidenceFact(**values)  # type: ignore[arg-type]


def _validation(**overrides: object) -> ValidationResult:
    check = ValidationCheck(
        code="result_exists",
        status=ValidationStatus.PASS,
        required=True,
        message="Result exists",
        expected=True,
        actual=True,
        evidence_refs=[EvidenceRef(kind="artifact", summary="Result was found")],
    )
    values: dict[str, object] = {
        "validation_id": "validation-001",
        "run_id": RUN_ID,
        "created_at": OBSERVED_AT,
        "validated_at": OBSERVED_AT,
        "validated_run_dir": "/runs/find-001",
        "producer": "find-validator",
        "producer_version": "1.0",
        "policy_version": "find.validation.minimum.v1",
        "duration_ms": 10,
        "status": ValidationStatus.PASS,
        "ready_for_read": True,
        "summary": "Validation passed",
        "checks": [check],
        "passed_check_count": 1,
        "warning_check_count": 0,
        "blocked_check_count": 0,
        "recommendation_target_count": 1,
        "recommendation_actual_count": 1,
        "recommendation_shortfall": 0,
        "strong_recommendation_count": 1,
        "recommendation_quality_status": "ok",
        "candidate_ids": ["paper-001"],
        "candidate_digest": "digest",
        "bridge_probe_status": ValidationStatus.PASS,
        "input_artifact_refs": [
            ArtifactRef(
                role="result",
                path="/runs/find-001/final/find_results.json",
                required=True,
                exists=True,
            )
        ],
        "failure_codes": [],
        "evidence_refs": [
            EvidenceRef(kind="validation", summary="All checks passed")
        ],
        "evidence_facts": [],
    }
    values.update(overrides)
    return ValidationResult(**values)  # type: ignore[arg-type]


def _anomaly(**overrides: object) -> Anomaly:
    values: dict[str, object] = {
        "anomaly_id": "anomaly-001",
        "run_id": RUN_ID,
        "created_at": OBSERVED_AT,
        "detected_at": OBSERVED_AT,
        "updated_at": OBSERVED_AT,
        "producer": "anomaly-builder",
        "producer_version": "1.0",
        "stage": "find",
        "kind": "progress_stalled",
        "blocking": True,
        "confidence": 0.8,
        "detected_by": ["progress-observer"],
        "supervisor_state_revision": 1,
        "symptoms": ["Progress stalled"],
        "evidence_refs": [
            EvidenceRef(kind="log", summary="Progress remained unchanged")
        ],
        "root_cause_status": "unknown",
        "affected_phase": "source_fetch",
        "downstream_impact": "Read cannot start",
        "partial_results_usable": False,
        "recovery_eligible": True,
        "retryable_signal": True,
        "fingerprint": "progress-stalled-source-fetch",
        "occurrence_count": 1,
        "progress_snapshot_id": "snapshot-001",
        "validation_id": "validation-001",
        "process_facts": {"alive": True},
        "artifact_facts": {"result_exists": False},
        "timing_facts": {"seconds_without_progress": 120.0},
        "missing_evidence": ["result"],
        "evidence_facts": [],
    }
    values.update(overrides)
    return Anomaly(**values)  # type: ignore[arg-type]


def _v1_payload(carrier: object, schema: str) -> dict[str, object]:
    payload = carrier.to_dict()  # type: ignore[attr-defined]
    payload["schema_version"] = schema
    payload.pop("evidence_facts")
    return payload


@pytest.mark.parametrize(
    ("factory", "schema"),
    [
        (_snapshot, "find.progress_snapshot.v2"),
        (_validation, "find.validation_result.v2"),
        (_anomaly, "find.anomaly.v2"),
    ],
)
def test_runtime_carriers_default_to_v2_with_empty_facts(
    factory: object,
    schema: str,
) -> None:
    carrier = factory()  # type: ignore[operator]
    assert carrier.schema_version == schema  # type: ignore[attr-defined]
    assert carrier.evidence_facts == []  # type: ignore[attr-defined]
    assert carrier.to_dict()["evidence_facts"] == []  # type: ignore[attr-defined]


def test_progress_snapshot_accepts_one_and_multiple_matching_facts() -> None:
    first = _fact()
    second = _fact(
        code="find.source_total",
        value=2,
        source_field="source_total",
    )

    assert _snapshot(evidence_facts=[first]).evidence_facts == [first]
    assert _snapshot(evidence_facts=[first, second]).evidence_facts == [first, second]


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("run_id", "find-other"),
        ("producer", "other-observer"),
        ("source_contract_id", "snapshot-other"),
        ("observed_at", OBSERVED_AT + timedelta(seconds=1)),
    ],
)
def test_progress_snapshot_rejects_fact_identity_mismatch(
    field_name: str,
    value: object,
) -> None:
    with pytest.raises(ValueError, match=rf"ProgressSnapshot\.evidence_facts\[0\]\.{field_name}"):
        _snapshot(evidence_facts=[_fact(**{field_name: value})])


def test_validation_result_accepts_empty_and_matching_facts() -> None:
    fact = _validation_fact()

    assert _validation().evidence_facts == []
    assert _validation(evidence_facts=[fact]).evidence_facts == [fact]


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("run_id", "find-other"),
        ("producer", "other-validator"),
        ("source_contract_id", "validation-other"),
        ("observed_at", OBSERVED_AT + timedelta(seconds=1)),
    ],
)
def test_validation_result_rejects_fact_identity_mismatch(
    field_name: str,
    value: object,
) -> None:
    with pytest.raises(ValueError, match=rf"ValidationResult\.evidence_facts\[0\]\.{field_name}"):
        _validation(evidence_facts=[_validation_fact(**{field_name: value})])


def test_anomaly_accepts_snapshot_and_validation_facts_together() -> None:
    snapshot_fact = _fact()
    validation_fact = _validation_fact()

    anomaly = _anomaly(evidence_facts=[snapshot_fact, validation_fact])

    assert anomaly.evidence_facts == [snapshot_fact, validation_fact]


def test_anomaly_does_not_take_ownership_of_fact_producer_or_time() -> None:
    fact = _fact(
        producer="observer-not-anomaly-builder",
        observed_at=OBSERVED_AT - timedelta(minutes=1),
    )

    anomaly = _anomaly(evidence_facts=[fact])

    assert anomaly.evidence_facts[0].producer == "observer-not-anomaly-builder"
    assert anomaly.evidence_facts[0].observed_at != anomaly.detected_at


def test_anomaly_rejects_fact_from_another_run() -> None:
    with pytest.raises(ValueError, match=r"Anomaly\.evidence_facts\[0\]\.run_id"):
        _anomaly(evidence_facts=[_fact(run_id="find-other")])


def test_anomaly_rejects_fact_from_an_unlisted_source_contract() -> None:
    with pytest.raises(
        ValueError,
        match=r"Anomaly\.evidence_facts\[0\]\.source_contract_id",
    ):
        _anomaly(evidence_facts=[_fact(source_contract_id="snapshot-other")])


def test_anomaly_rejects_facts_without_any_source_contract_id() -> None:
    with pytest.raises(
        ValueError,
        match=r"Anomaly\.evidence_facts\[0\]\.source_contract_id",
    ):
        _anomaly(
            progress_snapshot_id=None,
            validation_id=None,
            evidence_facts=[_fact()],
        )


@pytest.mark.parametrize(
    ("factory", "invalid"),
    [
        (_snapshot, {"code": "not-a-fact"}),
        (_validation, EvidenceRef(kind="validation", summary="Reference only")),
        (
            _anomaly,
            EvidenceDefinition(
                code="find.source_failed",
                value_type="integer",
                intended_producer="progress-observer",
                description="Failed source count",
            ),
        ),
        (
            _anomaly,
            EvidenceCollectionRule(
                rule_id="source-failed",
                evidence_code="find.source_failed",
                source_field="source_failed",
                collector="snapshot-field",
                collector_parameters={},
                implementation_status="ready",
            ),
        ),
    ],
)
def test_runtime_carriers_reject_non_fact_elements(
    factory: object,
    invalid: object,
) -> None:
    with pytest.raises(ValueError, match=r"evidence_facts\[0\]"):
        factory(evidence_facts=[invalid])  # type: ignore[operator]


@pytest.mark.parametrize("factory", [_snapshot, _validation, _anomaly])
def test_runtime_carriers_collapse_identical_duplicates_in_first_position(
    factory: object,
) -> None:
    first = _fact() if factory is not _validation else _validation_fact()
    later = _fact(
        code="find.source_total",
        value=2,
        source_field="source_total",
    ) if factory is not _validation else _validation_fact(
        code="find.recommendation_actual_count",
        value=1,
        source_field="recommendation_actual_count",
    )

    carrier = factory(evidence_facts=[first, first, later])  # type: ignore[operator]

    assert carrier.evidence_facts == [first, later]


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("value", 2),
        ("producer", "another-observer"),
        ("run_id", "find-other"),
        ("observed_at", OBSERVED_AT + timedelta(seconds=1)),
    ],
)
def test_duplicate_fact_identity_with_different_content_is_rejected(
    field_name: str,
    value: object,
) -> None:
    with pytest.raises(ValueError, match=r"Anomaly\.evidence_facts\[1\]"):
        _anomaly(evidence_facts=[_fact(), _fact(**{field_name: value})])


def test_same_fact_code_with_different_source_contracts_can_coexist() -> None:
    first = _fact()
    second = _validation_fact(
        code=first.code,
        value=first.value,
        source_field=first.source_field,
    )

    assert _anomaly(evidence_facts=[first, second]).evidence_facts == [first, second]


def test_same_code_and_source_contract_with_different_source_fields_can_coexist() -> None:
    first = _fact()
    second = _fact(source_field="counts.source_failed")

    assert _anomaly(evidence_facts=[first, second]).evidence_facts == [first, second]


@pytest.mark.parametrize("factory", [_snapshot, _validation, _anomaly])
def test_runtime_carriers_deep_copy_fact_objects_and_nested_values(
    factory: object,
) -> None:
    fact = _fact() if factory is not _validation else _validation_fact(
        value={"checks": [True]}
    )
    supplied = [fact]
    carrier = factory(evidence_facts=supplied)  # type: ignore[operator]
    supplied.clear()
    key = "sources" if factory is not _validation else "checks"
    fact.value[key].append("changed")

    assert len(carrier.evidence_facts) == 1
    assert carrier.evidence_facts[0] is not fact
    assert "changed" not in carrier.evidence_facts[0].value[key]


@pytest.mark.parametrize(
    ("factory", "fact"),
    [
        (_snapshot, _fact()),
        (_validation, _validation_fact()),
        (_anomaly, _fact()),
    ],
)
def test_runtime_carriers_restore_fact_types_from_dict_and_json(
    factory: object,
    fact: EvidenceFact,
) -> None:
    original = factory(evidence_facts=[fact])  # type: ignore[operator]
    restored_from_dict = type(original).from_dict(original.to_dict())
    restored_from_json = type(original).from_json(original.to_json())

    assert restored_from_dict == original
    assert restored_from_dict is not original
    assert isinstance(restored_from_dict.evidence_facts[0], EvidenceFact)
    assert restored_from_dict.evidence_facts[0] is not original.evidence_facts[0]
    assert restored_from_json == original
    assert isinstance(restored_from_json.evidence_facts[0], EvidenceFact)


@pytest.mark.parametrize(
    ("factory", "contract_type", "v1_schema"),
    [
        (_snapshot, ProgressSnapshot, "find.progress_snapshot.v1"),
        (_validation, ValidationResult, "find.validation_result.v1"),
        (_anomaly, Anomaly, "find.anomaly.v1"),
    ],
)
def test_v1_payload_migrates_to_v2_without_synthesizing_facts(
    factory: object,
    contract_type: type[object],
    v1_schema: str,
) -> None:
    original = factory()  # type: ignore[operator]
    payload = _v1_payload(original, v1_schema)

    restored = contract_type.from_dict(payload)  # type: ignore[attr-defined]
    restored_from_json = contract_type.from_json(  # type: ignore[attr-defined]
        json.dumps(payload)
    )

    assert restored.evidence_facts == []
    assert restored.schema_version == v1_schema.removesuffix("v1") + "v2"
    assert restored.to_dict()["evidence_facts"] == []
    assert restored_from_json == restored


@pytest.mark.parametrize(
    ("factory", "contract_type", "v1_schema"),
    [
        (_snapshot, ProgressSnapshot, "find.progress_snapshot.v1"),
        (_validation, ValidationResult, "find.validation_result.v1"),
        (_anomaly, Anomaly, "find.anomaly.v1"),
    ],
)
def test_v1_payload_rejects_v2_fact_field(
    factory: object,
    contract_type: type[object],
    v1_schema: str,
) -> None:
    payload = _v1_payload(factory(), v1_schema)  # type: ignore[operator]
    payload["evidence_facts"] = []

    with pytest.raises(ValueError, match=r"evidence_facts.*unknown"):
        contract_type.from_dict(payload)  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("factory", "contract_type", "unknown_schema"),
    [
        (_snapshot, ProgressSnapshot, "find.progress_snapshot.v3"),
        (_validation, ValidationResult, "find.validation_result.v3"),
        (_anomaly, Anomaly, "find.anomaly.v3"),
    ],
)
def test_runtime_carriers_reject_unknown_schema_versions(
    factory: object,
    contract_type: type[object],
    unknown_schema: str,
) -> None:
    payload = factory().to_dict()  # type: ignore[operator]
    payload["schema_version"] = unknown_schema

    with pytest.raises(ValueError, match=r"schema_version"):
        contract_type.from_dict(payload)  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("factory", "v1_schema"),
    [
        (_snapshot, "find.progress_snapshot.v1"),
        (_validation, "find.validation_result.v1"),
        (_anomaly, "find.anomaly.v1"),
    ],
)
def test_direct_construction_rejects_v1_schema(
    factory: object,
    v1_schema: str,
) -> None:
    with pytest.raises(ValueError, match=r"schema_version"):
        factory(schema_version=v1_schema)  # type: ignore[operator]


@pytest.mark.parametrize("factory", [_snapshot, _validation, _anomaly])
def test_v2_runtime_carriers_reject_unknown_fields(factory: object) -> None:
    original = factory()  # type: ignore[operator]
    payload = original.to_dict()
    payload["unexpected_field"] = "value"

    with pytest.raises(ValueError, match=r"unexpected_field.*unknown"):
        type(original).from_dict(payload)


def test_existing_snapshot_fields_retain_their_meaning_in_v2_round_trip() -> None:
    original = _snapshot()
    restored = ProgressSnapshot.from_json(original.to_json())

    assert restored.counts == {"raw_title_index_papers": 10}
    assert restored.status is ProgressStatus.RUNNING
    assert restored.phase == "source_fetch"
    assert restored.seconds_without_progress == 5.0
    assert (restored.source_total, restored.source_ready) == (2, 1)
    assert (restored.source_limited, restored.source_failed) == (0, 1)
    assert restored.evidence_refs == original.evidence_refs
    assert restored.artifact_observations == original.artifact_observations


def test_existing_validation_fields_retain_their_meaning_in_v2_round_trip() -> None:
    original = _validation()
    restored = ValidationResult.from_json(original.to_json())

    assert restored.checks == original.checks
    assert restored.checks[0].expected is True
    assert restored.checks[0].actual is True
    assert restored.failure_codes == []
    assert restored.candidate_ids == ["paper-001"]
    assert restored.recommendation_actual_count == 1
    assert restored.evidence_refs == original.evidence_refs


def test_existing_anomaly_fields_retain_their_meaning_in_v2_round_trip() -> None:
    original = _anomaly()
    restored = Anomaly.from_json(original.to_json())

    assert restored.kind == "progress_stalled"
    assert restored.evidence_refs == original.evidence_refs
    assert restored.process_facts == {"alive": True}
    assert restored.artifact_facts == {"result_exists": False}
    assert restored.timing_facts == {"seconds_without_progress": 120.0}
    assert restored.root_cause is None
    assert restored.root_cause_status == "unknown"
    assert restored.missing_evidence == ["result"]


def test_carrier_field_sets_add_only_evidence_facts_to_existing_contracts() -> None:
    for contract_type in (ProgressSnapshot, ValidationResult, Anomaly):
        names = {item.name for item in fields(contract_type)}
        assert "evidence_facts" in names
        assert not {
            "observed_facts",
            "facts",
            "fact_refs",
            "candidate_root_cause",
            "root_cause_code",
        } & names
