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


def test_empty_conditions_fail_closed_without_parsing_legacy_notes() -> None:
    document = _document()

    assert "empty applicability_conditions fail closed" in document
    assert "must not produce a recoverydecision" in document
    assert "anomaly.kind alone cannot make the case match" in document
    assert "may remain available as legacy history or advisor context" in document
    assert "must not parse applicability_notes into machine conditions" in document


def test_fact_lookup_requires_one_unambiguous_current_anomaly_fact() -> None:
    document = _document()

    assert "exactly one fact with the requested evidence_code" in document
    assert "multiple facts with the same evidence_code are ambiguous" in document
    assert "even when their values are equal" in document
    assert "must not choose the first, last, minimum, or maximum" in document
    for legacy_source in (
        "evidenceref",
        "process_facts",
        "artifact_facts",
        "timing_facts",
        "missing_evidence",
    ):
        assert legacy_source in document


def test_first_condition_execution_supports_only_literal_baselines() -> None:
    document = _document()

    assert "the first controller evaluator executes only literal baselines" in document
    assert "run_context and fact remain valid contract values" in document
    assert "not executable by the first evaluator" in document
    assert "must not interpret baseline_ref" in document
    assert "continues with the next candidate case" in document


def test_condition_comparisons_use_safe_json_type_rules() -> None:
    document = _document()

    assert "bool is not a number" in document
    assert "nan and infinity never participate in matching" in document
    assert "int and float may be compared numerically" in document
    assert "does not convert strings to numbers or truth values" in document
    assert "arrays compare only with arrays" in document
    assert "objects compare only with objects" in document


def test_case_conditions_are_and_and_nonmatches_do_not_enter_action_stop() -> None:
    document = _document()

    assert "all applicability_conditions in one case must match" in document
    assert "one case represents one complete and branch" in document
    assert "an evidence non-match continues to the next case" in document
    assert "does not enter the matched-but-unsafe-action stop path" in document


def test_advisor_and_suspected_approval_boundaries_are_explicit() -> None:
    document = _document()

    assert "advisor is called at most once after every candidate case fails" in document
    assert "store failure remains a fail-closed infrastructure error" in document
    assert "root_cause_status == suspected requires human approval" in document
    assert "even when the recovery action is marked low risk" in document
    assert "recovery pass does not promote suspected to confirmed" in document


def test_store_does_not_truncate_candidates_before_evidence_filtering() -> None:
    document = _document()

    assert "complete coarse candidate set" in document
    assert "stable pagination" in document
    assert "only after evidence filtering" in document
    assert "must not substitute an arbitrary fixed larger limit" in document


def test_current_implementation_status_matches_the_evidence_pipeline() -> None:
    document = _document()

    assert "evidencematchcondition and experiencecase v2 are implemented" in document
    assert "anomalybuilder validates and propagates upstream facts" in document
    assert "validator does not yet produce evidencefact" in document
    assert "controller does not yet execute evidencematchcondition" in document
    assert "store does not yet match cases against current-run facts" in document
    assert "run_context and fact baseline execution is not implemented" in document
    assert "find.source_failed is not yet produced as an evidencefact" in document
    assert "advisor evidence-definition and collection-rule wiring is not implemented" in document


def test_source_failed_has_no_count_alias_in_the_authoritative_vocabulary() -> None:
    document = _document()

    assert "find.source_failed" in document
    assert "find.source_failed_count" not in document
