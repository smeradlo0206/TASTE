from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
import inspect

import pytest

from feedback import EvidenceFact, FindAnomalyBuilder, ProgressStatus
from test_anomaly import OBSERVED_AT, RUN_ID, _artifact, _blocked_validation, _progress


def _snapshot_fact(
    *,
    code: str = "find.seconds_without_progress",
    value: object = 120.0,
    source_field: str = "seconds_without_progress",
) -> EvidenceFact:
    return EvidenceFact(
        code=code,
        value=value,
        source_contract_id="snapshot-anomaly-test",
        source_field=source_field,
        producer="file_progress_observer",
        run_id=RUN_ID,
        observed_at=OBSERVED_AT,
    )


def _validation_fact(
    *,
    code: str = "test.validation_observation",
    value: object = False,
    source_field: str = "checks.result_exists.actual",
) -> EvidenceFact:
    return EvidenceFact(
        code=code,
        value=value,
        source_contract_id="validation-anomaly-test",
        source_field=source_field,
        producer="find_result_validator",
        run_id=RUN_ID,
        observed_at=OBSERVED_AT,
    )


def test_snapshot_facts_are_propagated_without_recomputation() -> None:
    fact = _snapshot_fact()
    snapshot = _progress(ProgressStatus.STALLED, evidence_facts=[fact])

    anomaly = FindAnomalyBuilder().build(progress_snapshot=snapshot)

    assert anomaly is not None
    assert anomaly.kind == "progress_stalled"
    assert anomaly.evidence_facts == [fact]
    assert anomaly.evidence_facts[0].code == fact.code
    assert anomaly.evidence_facts[0].value == fact.value


def test_validation_facts_are_propagated_without_recomputation() -> None:
    fact = _validation_fact()
    validation = _blocked_validation("result_exists", evidence_facts=[fact])

    anomaly = FindAnomalyBuilder().build(validation_result=validation)

    assert anomaly is not None
    assert anomaly.kind == "result_missing"
    assert anomaly.evidence_facts == [fact]


def test_snapshot_facts_precede_validation_facts_and_keep_source_order() -> None:
    snapshot_facts = [
        _snapshot_fact(),
        _snapshot_fact(
            code="find.source_total",
            value=4,
            source_field="source_total",
        ),
    ]
    validation_facts = [
        _validation_fact(),
        _validation_fact(
            code="test.validation_candidate_count",
            value=0,
            source_field="recommendation_actual_count",
        ),
    ]
    snapshot = _progress(ProgressStatus.STALLED, evidence_facts=snapshot_facts)
    validation = _blocked_validation(
        "result_exists",
        evidence_facts=validation_facts,
    )

    anomaly = FindAnomalyBuilder().build(
        progress_snapshot=snapshot,
        validation_result=validation,
    )

    assert anomaly is not None
    assert [fact.code for fact in anomaly.evidence_facts] == [
        "find.seconds_without_progress",
        "find.source_total",
        "test.validation_observation",
        "test.validation_candidate_count",
    ]


def test_anomaly_deep_copies_propagated_fact_values() -> None:
    snapshot = _progress(
        ProgressStatus.STALLED,
        evidence_facts=[
            _snapshot_fact(value={"counts": [1, 2], "phase": {"name": "scoring"}})
        ],
    )
    parent_fact = snapshot.evidence_facts[0]

    anomaly = FindAnomalyBuilder().build(progress_snapshot=snapshot)
    assert anomaly is not None

    parent_fact.value["counts"].append(3)  # type: ignore[index,union-attr]
    parent_fact.value["phase"]["name"] = "changed"  # type: ignore[index,union-attr]

    assert anomaly.evidence_facts[0] is not parent_fact
    assert anomaly.evidence_facts[0].value == {
        "counts": [1, 2],
        "phase": {"name": "scoring"},
    }


def test_same_kind_preserves_different_fact_values() -> None:
    first = FindAnomalyBuilder().build(
        progress_snapshot=_progress(
            ProgressStatus.STALLED,
            evidence_facts=[_snapshot_fact(value=120.0)],
        )
    )
    second = FindAnomalyBuilder().build(
        progress_snapshot=_progress(
            ProgressStatus.STALLED,
            evidence_facts=[_snapshot_fact(value=180.0)],
        )
    )

    assert first is not None and second is not None
    assert first.kind == second.kind == "progress_stalled"
    assert first.evidence_facts[0].value == 120.0
    assert second.evidence_facts[0].value == 180.0


def test_facts_do_not_change_existing_kind_classification() -> None:
    without_facts = FindAnomalyBuilder().build(
        progress_snapshot=_progress(ProgressStatus.STALLED)
    )
    with_facts = FindAnomalyBuilder().build(
        progress_snapshot=_progress(
            ProgressStatus.STALLED,
            evidence_facts=[_snapshot_fact(value=0.0)],
        )
    )

    assert without_facts is not None and with_facts is not None
    assert without_facts.kind == with_facts.kind == "progress_stalled"


def test_identical_duplicate_facts_are_folded_by_anomaly_contract() -> None:
    snapshot = _progress(
        ProgressStatus.STALLED,
        evidence_facts=[_snapshot_fact()],
    )
    snapshot.evidence_facts.append(deepcopy(snapshot.evidence_facts[0]))

    anomaly = FindAnomalyBuilder().build(progress_snapshot=snapshot)

    assert anomaly is not None
    assert anomaly.evidence_facts == [snapshot.evidence_facts[0]]


def test_conflicting_duplicate_facts_are_rejected_by_anomaly_contract() -> None:
    snapshot = _progress(
        ProgressStatus.STALLED,
        evidence_facts=[_snapshot_fact(value=120.0)],
    )
    snapshot.evidence_facts.append(_snapshot_fact(value=180.0))

    with pytest.raises(ValueError, match="evidence_facts.*conflicts"):
        FindAnomalyBuilder().build(progress_snapshot=snapshot)


@pytest.mark.parametrize(
    ("parent_kind", "field_name", "bad_value"),
    [
        ("snapshot", "run_id", "find-tampered"),
        ("snapshot", "producer", "tampered_observer"),
        ("snapshot", "source_contract_id", "snapshot-tampered"),
        ("snapshot", "observed_at", OBSERVED_AT + timedelta(seconds=1)),
        ("validation", "run_id", "find-tampered"),
        ("validation", "producer", "tampered_validator"),
        ("validation", "source_contract_id", "validation-tampered"),
        ("validation", "observed_at", OBSERVED_AT + timedelta(seconds=1)),
    ],
)
def test_builder_rejects_fact_identity_mutated_after_parent_construction(
    parent_kind: str,
    field_name: str,
    bad_value: object,
) -> None:
    if parent_kind == "snapshot":
        snapshot = _progress(
            ProgressStatus.STALLED,
            evidence_facts=[_snapshot_fact()],
        )
        setattr(snapshot.evidence_facts[0], field_name, bad_value)
        call = {"progress_snapshot": snapshot}
    else:
        validation = _blocked_validation(
            "result_exists",
            evidence_facts=[_validation_fact()],
        )
        setattr(validation.evidence_facts[0], field_name, bad_value)
        call = {"validation_result": validation}

    with pytest.raises(ValueError, match=rf"evidence_facts\[0\].*{field_name}"):
        FindAnomalyBuilder().build(**call)


def test_missing_evidence_does_not_create_an_evidence_fact() -> None:
    snapshot = _progress(
        ProgressStatus.UNKNOWN,
        artifact_observations=[
            _artifact(role="progress", exists=False, parse_status="missing")
        ],
        evidence_facts=[],
    )

    anomaly = FindAnomalyBuilder().build(progress_snapshot=snapshot)

    assert anomaly is not None
    assert anomaly.kind == "progress_missing"
    assert anomaly.missing_evidence == ["progress artifact"]
    assert anomaly.evidence_facts == []


def test_builder_keeps_two_keyword_only_inputs_and_no_runtime_dependencies() -> None:
    signature = inspect.signature(FindAnomalyBuilder.build)

    assert list(signature.parameters) == [
        "self",
        "progress_snapshot",
        "validation_result",
    ]
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for name, parameter in signature.parameters.items()
        if name != "self"
    )
    builder = FindAnomalyBuilder()
    assert vars(builder) == {}
    assert not hasattr(builder, "advisor")
    assert not hasattr(builder, "store")
