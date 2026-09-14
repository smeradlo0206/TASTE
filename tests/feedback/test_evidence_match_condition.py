from __future__ import annotations

from dataclasses import fields
from datetime import datetime, timezone

import pytest

import feedback
from feedback import (
    EvidenceCollectionRule,
    EvidenceFact,
    EvidenceMatchCondition,
    EvidenceRef,
    ExperienceCase,
    RecoveryAction,
    RiskLevel,
    ValidationStatus,
)
from feedback.contracts import (
    EvidenceMatchCondition as ContractEvidenceMatchCondition,
)


OBSERVED_AT = datetime(2026, 9, 12, 9, 0, tzinfo=timezone.utc)


def _condition(**overrides: object) -> EvidenceMatchCondition:
    values: dict[str, object] = {
        "evidence_code": "find.source_failed_count",
        "operator": "eq",
        "baseline_source": "literal",
        "baseline_ref": None,
        "baseline_value": 0,
    }
    values.update(overrides)
    return EvidenceMatchCondition(**values)  # type: ignore[arg-type]


def _fact(**overrides: object) -> EvidenceFact:
    values: dict[str, object] = {
        "code": "find.source_failed_count",
        "value": 2,
        "source_contract_id": "snapshot-001",
        "source_field": "counts.source_failed",
        "producer": "find-progress-observer",
        "run_id": "find-001",
        "observed_at": OBSERVED_AT,
    }
    values.update(overrides)
    return EvidenceFact(**values)  # type: ignore[arg-type]


def _case(**overrides: object) -> ExperienceCase:
    values: dict[str, object] = {
        "case_id": "case-001",
        "case_type": "technical",
        "created_at": OBSERVED_AT,
        "updated_at": OBSERVED_AT,
        "producer": "experience-recorder",
        "producer_version": "1.0",
        "verified": True,
        "deprecated": False,
        "context_id": "ctx-001",
        "root_run_id": "find-root-001",
        "final_run_id": "find-recovery-001",
        "root_cause_status": "suspected",
        "root_cause": "source_access_degraded",
        "evidence_refs": [
            EvidenceRef(kind="validation", summary="Recovery validation passed")
        ],
        "evidence_facts": [_fact()],
        "attempt_count": 1,
        "outcome": "recovered",
        "validation_after_id": "validation-001",
        "validation_after_status": ValidationStatus.PASS,
        "ready_for_read_after": True,
        "risk_level": RiskLevel.MEDIUM,
        "confidence": 0.7,
        "matched_count": 1,
        "applied_count": 1,
        "successful_application_count": 1,
        "anomaly_id": "anomaly-001",
        "anomaly_kind": "progress_stalled",
        "anomaly_fingerprint": "progress-stalled-source-access",
        "decision_id": "decision-001",
        "recovery_action": RecoveryAction.RETRY_NEW_RUN,
        "applicability_notes": ["Observed source access degradation"],
        "applicability_conditions": [_condition()],
    }
    values.update(overrides)
    return ExperienceCase(**values)  # type: ignore[arg-type]


def _legacy_v1_payload(**overrides: object) -> dict[str, object]:
    payload = _case(
        root_cause_status="unknown",
        root_cause=None,
        evidence_facts=[],
        applicability_notes=[],
        applicability_conditions=[],
    ).to_dict()
    payload["schema_version"] = "find.experience_case.v1"
    payload["confirmed_root_cause"] = None
    payload["applicability_conditions"] = [
        "Source failures were observed",
        "Run used the same source set",
    ]
    payload["non_applicable_conditions"] = ["Credentials were invalid"]
    for name in ("root_cause", "evidence_facts", "applicability_notes"):
        payload.pop(name)
    payload.update(overrides)
    return payload


def test_evidence_match_condition_is_one_public_contract() -> None:
    assert EvidenceMatchCondition is ContractEvidenceMatchCondition
    assert feedback.EvidenceMatchCondition is ContractEvidenceMatchCondition
    assert "EvidenceMatchCondition" in feedback.__all__
    assert {item.name for item in fields(EvidenceMatchCondition)} == {
        "evidence_code",
        "operator",
        "baseline_source",
        "baseline_ref",
        "baseline_value",
        "schema_version",
    }


@pytest.mark.parametrize("operator", ["eq", "gte", "lte"])
def test_literal_condition_accepts_closed_operators(operator: str) -> None:
    value: object = {"status": "limited"} if operator == "eq" else 1
    condition = _condition(operator=operator, baseline_value=value)

    assert condition.operator == operator
    assert condition.schema_version == "feedback.evidence_match_condition.v1"


@pytest.mark.parametrize("baseline_source", ["run_context", "fact"])
def test_reference_condition_accepts_closed_sources(baseline_source: str) -> None:
    condition = _condition(
        baseline_source=baseline_source,
        baseline_ref="effective_parameters.source_failure_limit",
        baseline_value=None,
    )

    assert condition.baseline_source == baseline_source
    assert condition.baseline_ref == "effective_parameters.source_failure_limit"


@pytest.mark.parametrize("field_name", ["evidence_code", "operator", "baseline_source"])
def test_condition_rejects_blank_required_strings(field_name: str) -> None:
    with pytest.raises(ValueError, match=rf"EvidenceMatchCondition\.{field_name}"):
        _condition(**{field_name: " "})


@pytest.mark.parametrize("operator", ["gt", "lt", "contains", "matches"])
def test_condition_rejects_unknown_operator(operator: str) -> None:
    with pytest.raises(ValueError, match=r"EvidenceMatchCondition\.operator"):
        _condition(operator=operator)


@pytest.mark.parametrize("baseline_source", ["expression", "script", "command"])
def test_condition_rejects_unknown_baseline_source(baseline_source: str) -> None:
    with pytest.raises(ValueError, match=r"EvidenceMatchCondition\.baseline_source"):
        _condition(baseline_source=baseline_source)


@pytest.mark.parametrize(
    "overrides",
    [
        {"baseline_source": "literal", "baseline_ref": "some.path"},
        {"baseline_source": "literal", "baseline_value": None},
        {"baseline_source": "run_context", "baseline_ref": None, "baseline_value": None},
        {"baseline_source": "run_context", "baseline_ref": " ", "baseline_value": None},
        {"baseline_source": "fact", "baseline_ref": "find.other", "baseline_value": 1},
    ],
)
def test_condition_enforces_source_specific_baseline_shape(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match=r"EvidenceMatchCondition\.baseline_"):
        _condition(**overrides)


@pytest.mark.parametrize("operator", ["gte", "lte"])
@pytest.mark.parametrize("baseline_value", [True, "1", [], {}, float("nan"), float("inf")])
def test_ordered_literal_condition_requires_a_finite_non_boolean_number(
    operator: str,
    baseline_value: object,
) -> None:
    with pytest.raises(ValueError, match=r"EvidenceMatchCondition\.baseline_value"):
        _condition(operator=operator, baseline_value=baseline_value)


@pytest.mark.parametrize(
    "baseline_value",
    [object(), {"nested": float("nan")}, {"nested": float("inf")}, "Bearer credential"],
)
def test_literal_condition_rejects_unsafe_json_values(baseline_value: object) -> None:
    with pytest.raises(ValueError, match=r"EvidenceMatchCondition\.baseline_value"):
        _condition(baseline_value=baseline_value)


def test_condition_round_trips_and_isolates_nested_literal_value() -> None:
    baseline = {"statuses": ["failed"], "options": {"strict": True}}
    condition = _condition(baseline_value=baseline)
    baseline["statuses"].append("changed")
    baseline["options"]["strict"] = False

    restored_from_dict = EvidenceMatchCondition.from_dict(condition.to_dict())
    restored_from_json = EvidenceMatchCondition.from_json(condition.to_json())
    serialized = condition.to_dict()
    serialized_value = serialized["baseline_value"]
    assert isinstance(serialized_value, dict)
    serialized_value["statuses"].append("serialized-change")

    assert condition.baseline_value == {
        "statuses": ["failed"],
        "options": {"strict": True},
    }
    assert restored_from_dict == condition
    assert restored_from_dict is not condition
    assert restored_from_json == condition


def test_condition_rejects_wrong_schema_and_unknown_or_executable_fields() -> None:
    with pytest.raises(ValueError, match=r"EvidenceMatchCondition\.schema_version"):
        _condition(schema_version="feedback.evidence_match_condition.v2")

    for field_name in (
        "expression",
        "script",
        "command",
        "path",
        "module",
        "callable",
        "evaluator",
        "enabled",
        "active",
        "root_cause",
        "confidence",
    ):
        payload = _condition().to_dict()
        payload[field_name] = "unsafe"
        with pytest.raises(ValueError, match=rf"{field_name}.*unknown"):
            EvidenceMatchCondition.from_dict(payload)

    assert not hasattr(EvidenceMatchCondition, "evaluate")
    assert not hasattr(EvidenceMatchCondition, "matches")


def test_experience_case_v2_has_one_root_cause_and_one_condition_field() -> None:
    case_fields = {item.name for item in fields(ExperienceCase)}

    assert {
        "root_cause",
        "root_cause_status",
        "evidence_facts",
        "applicability_notes",
        "applicability_conditions",
    } <= case_fields
    assert not {
        "confirmed_root_cause",
        "candidate_root_cause",
        "root_cause_code",
        "non_applicable_conditions",
        "evidence_match_conditions",
        "match_conditions",
    } & case_fields
    assert _case().schema_version == "find.experience_case.v2"


@pytest.mark.parametrize(
    ("root_cause_status", "root_cause"),
    [("unknown", None), ("suspected", None), ("suspected", "source_access_degraded"), ("confirmed", "source_access_degraded")],
)
def test_experience_case_v2_root_cause_relationships(
    root_cause_status: str,
    root_cause: str | None,
) -> None:
    case = _case(root_cause_status=root_cause_status, root_cause=root_cause)

    assert case.root_cause_status == root_cause_status
    assert case.root_cause == root_cause


def test_confirmed_root_cause_requires_text_but_recovery_pass_does_not_confirm() -> None:
    with pytest.raises(ValueError, match=r"ExperienceCase\.root_cause"):
        _case(root_cause_status="confirmed", root_cause=None)

    recovered = _case(root_cause_status="suspected", root_cause="source_access_degraded")
    assert recovered.outcome == "recovered"
    assert recovered.validation_after_status is ValidationStatus.PASS
    assert recovered.root_cause_status == "suspected"


def test_experience_case_v2_rejects_wrong_nested_types_and_string_conditions() -> None:
    with pytest.raises(ValueError, match=r"ExperienceCase\.evidence_facts\[0\]"):
        _case(evidence_facts=[{"code": "not-a-contract"}])
    with pytest.raises(ValueError, match=r"ExperienceCase\.applicability_conditions\[0\]"):
        _case(applicability_conditions=["human prose"])
    with pytest.raises(ValueError, match=r"ExperienceCase\.applicability_conditions\[0\]"):
        _case(
            applicability_conditions=[
                EvidenceCollectionRule(
                    rule_id="source-failure-count",
                    evidence_code="find.source_failed_count",
                    source_field="counts.source_failed",
                    collector="progress_snapshot_field",
                    collector_parameters={},
                    implementation_status="ready",
                )
            ]
        )
    with pytest.raises(ValueError, match=r"ExperienceCase\.applicability_notes\[0\]"):
        _case(applicability_notes=[_condition()])


def test_experience_case_v2_round_trip_restores_nested_contract_types() -> None:
    original = _case()

    restored_from_dict = ExperienceCase.from_dict(original.to_dict())
    restored_from_json = ExperienceCase.from_json(original.to_json())

    assert restored_from_dict == original
    assert restored_from_dict is not original
    assert isinstance(restored_from_dict.evidence_facts[0], EvidenceFact)
    assert isinstance(
        restored_from_dict.applicability_conditions[0], EvidenceMatchCondition
    )
    assert restored_from_json == original


def test_experience_case_v2_copies_nested_inputs_and_serialized_output() -> None:
    fact = _fact(value={"sources": ["openalex"]})
    condition = _condition(baseline_value={"limit": [0]})
    facts = [fact]
    conditions = [condition]
    notes = ["Same source set"]
    case = _case(
        evidence_facts=facts,
        applicability_conditions=conditions,
        applicability_notes=notes,
    )
    facts.append(_fact(code="find.source_limited_count"))
    conditions.append(_condition(evidence_code="find.source_limited_count"))
    notes.append("changed")
    fact.value["sources"].append("mutated-original-fact")
    condition.baseline_value["limit"].append(1)
    serialized = case.to_dict()
    serialized["evidence_facts"][0]["value"]["sources"].append("changed")
    serialized["applicability_conditions"][0]["baseline_value"]["limit"].append(1)

    assert len(case.evidence_facts) == 1
    assert len(case.applicability_conditions) == 1
    assert case.applicability_notes == ["Same source set"]
    assert case.evidence_facts[0].value == {"sources": ["openalex"]}
    assert case.applicability_conditions[0].baseline_value == {"limit": [0]}


def test_v1_migration_preserves_text_as_notes_without_synthesizing_facts() -> None:
    restored = ExperienceCase.from_dict(_legacy_v1_payload())

    assert restored.schema_version == "find.experience_case.v2"
    assert restored.root_cause is None
    assert restored.root_cause_status == "unknown"
    assert restored.evidence_facts == []
    assert restored.applicability_conditions == []
    assert restored.applicability_notes == [
        "Source failures were observed",
        "Run used the same source set",
        "non_applicable: Credentials were invalid",
    ]
    assert not {
        "confirmed_root_cause",
        "non_applicable_conditions",
    } & set(restored.to_dict())


def test_v1_migration_maps_confirmed_root_cause_without_status_promotion() -> None:
    confirmed = ExperienceCase.from_dict(
        _legacy_v1_payload(
            root_cause_status="confirmed",
            confirmed_root_cause="source_access_degraded",
        )
    )
    suspected = ExperienceCase.from_dict(
        _legacy_v1_payload(root_cause_status="suspected")
    )

    assert confirmed.root_cause == "source_access_degraded"
    assert confirmed.root_cause_status == "confirmed"
    assert suspected.root_cause is None
    assert suspected.root_cause_status == "suspected"


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": "find.experience_case.v3"},
        {"schema_version": "find.experience_case.v0"},
    ],
)
def test_experience_case_rejects_unknown_schema_versions(payload: dict[str, object]) -> None:
    with pytest.raises(ValueError, match=r"ExperienceCase\.schema_version"):
        ExperienceCase.from_dict(payload)


@pytest.mark.parametrize(
    "legacy_field",
    [
        "confirmed_root_cause",
        "candidate_root_cause",
        "root_cause_code",
        "non_applicable_conditions",
        "evidence_match_conditions",
        "match_conditions",
    ],
)
def test_v2_rejects_legacy_or_synonymous_fields(legacy_field: str) -> None:
    payload = _case().to_dict()
    payload[legacy_field] = [] if "conditions" in legacy_field else "duplicate"

    with pytest.raises(ValueError, match=rf"{legacy_field}.*unknown"):
        ExperienceCase.from_dict(payload)


def test_v2_rejects_an_unrelated_unknown_field() -> None:
    payload = _case(applicability_conditions=[]).to_dict()
    payload["unexpected_field"] = "value"

    with pytest.raises(ValueError, match=r"unexpected_field.*unknown"):
        ExperienceCase.from_dict(payload)


def test_v1_migration_rejects_v2_fields_in_legacy_payload() -> None:
    for field_name, value in (
        ("root_cause", "source_access_degraded"),
        ("evidence_facts", []),
        ("applicability_notes", []),
        ("match_conditions", []),
    ):
        payload = _legacy_v1_payload()
        payload[field_name] = value
        with pytest.raises(ValueError, match=rf"{field_name}.*unknown"):
            ExperienceCase.from_dict(payload)
