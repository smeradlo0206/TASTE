from __future__ import annotations

from pathlib import Path


DOCUMENT_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "architecture"
    / "find-feedback-learning-semantics.md"
)


def _document() -> str:
    text = DOCUMENT_PATH.read_text(encoding="utf-8").replace("`", "")
    return " ".join(text.casefold().split())


def test_source_outcome_categories_are_mutually_exclusive() -> None:
    document = _document()

    assert "limited > failed > ready" in document
    assert "source_limited" in document
    assert "source_failed" in document
    assert "mutually exclusive" in document
    assert "source_rate_limited" in document
    assert "candidate or suspected" in document


def test_runtime_evidence_carriers_use_explicit_v2_migration() -> None:
    document = _document()

    for schema in (
        "find.progress_snapshot.v2",
        "find.validation_result.v2",
        "find.anomaly.v2",
    ):
        assert schema in document
    assert "evidence_facts=[]" in document
    assert "must not synthesize" in document
    assert "unknown schema versions" in document
    assert "does not change jsoncontract" in document


def test_experience_case_v2_has_one_root_cause_field() -> None:
    document = _document()

    assert "find.experience_case.v2" in document
    assert "root_cause: str | none" in document
    assert "unknown | suspected | confirmed" in document
    assert "v1 input compatibility only" in document
    assert "must not appear in v2 output" in document
    assert "must not invent root_cause" in document


def test_applicability_notes_and_structured_conditions_are_distinct() -> None:
    document = _document()

    assert "applicability_notes: list[str]" in document
    assert (
        "applicability_conditions: list[evidencematchcondition]" in document
    )
    assert "applicability_conditions=[]" in document
    assert "must not parse" in document
    assert "automatic recovery" in document


def test_evidence_match_condition_is_inert_and_has_one_field_set() -> None:
    document = _document()

    for field_name in (
        "evidence_code",
        "operator",
        "baseline_source",
        "baseline_ref",
        "baseline_value",
    ):
        assert field_name in document
    for closed_value in ("eq", "gte", "lte", "literal", "run_context", "fact"):
        assert closed_value in document
    assert "does not execute matching" in document
    assert "rootcausematcher" in document


def test_current_implementation_status_names_contracts_and_missing_wiring() -> None:
    document = _document()

    for contract_name in (
        "evidencefact",
        "evidencedefinition",
        "evidencecollectionrule",
    ):
        assert contract_name in document
    assert "not connected to production components" in document
    assert "experiencerecorder protocol" in document
    assert "writer and store write support are not implemented" in document
