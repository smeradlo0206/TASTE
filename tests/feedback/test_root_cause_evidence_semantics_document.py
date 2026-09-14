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


def test_kind_fact_baseline_and_root_cause_are_distinct() -> None:
    document = _document()

    assert "anomaly.kind is a symptom classification" in document
    assert "coarse screening only" in document
    assert "must not match an experience by itself" in document
    assert "evidencefact is an actually observed value" in document
    assert "a baseline defines what an evidencefact value is compared with" in document
    assert "a root cause is a diagnostic conclusion" in document


def test_controller_advisor_and_recovery_pass_boundaries_remain_fixed() -> None:
    document = _document()

    assert "controller calls the store" in document
    assert "controller owns deterministic experience matching" in document
    assert "advisor is called only when no reliable experience matches" in document
    assert "recovery pass proves that the recovery method worked" in document
    assert "does not automatically confirm the root cause" in document


def test_source_access_degraded_is_the_only_initial_root_cause() -> None:
    document = _document()

    assert document.count("root_cause_code:") == 1
    assert "root_cause_code: source_access_degraded" in document
    assert "find.source_total" in document
    assert "find.source_limited" in document
    assert "find.source_failed" in document
    assert "baseline_source: literal" in document
    assert "baseline_value: 1" in document
    assert "limited branch" in document
    assert "failed branch" in document
    assert "not matched and the root cause remains unknown" in document
    assert "must receive approval" in document


def test_candidate_volume_overload_is_not_frozen_without_real_evidence() -> None:
    document = _document()

    assert "candidate_volume_overload is not frozen as a deterministic root cause" in document
    assert "eligible unique candidates before the scoring limit" in document
    assert "whether that limit actually truncated the run" in document
    assert "must return unknown" in document
    assert "may then call advisor" in document
    assert "root_cause_code: candidate_volume_overload" not in document


def test_first_fact_codes_map_to_exact_observed_fields() -> None:
    document = _document()

    mappings = {
        "find.raw_title_index_papers": "counts.raw_title_index_papers",
        "find.title_score_input_papers": "counts.title_score_input_papers",
        "find.evaluated_candidates": "counts.evaluated_candidates",
        "find.llm_scored_candidates": "counts.llm_scored_candidates",
        "find.source_total": "source_total",
        "find.source_ready": "source_ready",
        "find.source_limited": "source_limited",
        "find.source_failed": "source_failed",
        "find.seconds_without_progress": "seconds_without_progress",
    }
    for fact_code, source_field in mappings.items():
        assert fact_code in document
        assert source_field in document

    assert "raw title-index volume, not the final candidate count" in document
    assert "not proof that all candidates were evaluated" in document
    assert "seconds_without_progress is a timing observation, not a root cause" in document


def test_no_general_root_cause_catalog_is_created() -> None:
    document = _document()

    assert "does not create a general root-cause catalog" in document
    assert "no rootcausematcher" in document
    assert "no curator" in document
