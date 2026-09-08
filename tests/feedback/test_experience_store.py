from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

import feedback
from feedback import (
    EvidenceRef,
    ExperienceCase,
    ExperienceQuery,
    JsonExperienceStore,
    RecoveryAction,
    RecoveryExperienceQuery,
    RiskLevel,
    ValidationStatus,
    select_experience_cases,
)


BASE_TIME = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)


def _case(**overrides: object) -> ExperienceCase:
    values: dict[str, object] = {
        "case_id": "case-001",
        "case_type": "normal",
        "created_at": BASE_TIME,
        "updated_at": BASE_TIME,
        "producer": "experience-store-tests",
        "producer_version": "1.0",
        "verified": True,
        "deprecated": False,
        "context_id": "ctx-001",
        "root_run_id": "find-root-001",
        "final_run_id": "find-final-001",
        "root_cause_status": "unknown",
        "evidence_refs": [EvidenceRef(kind="validation", summary="Passed")],
        "attempt_count": 0,
        "outcome": "success",
        "validation_after_id": "validation-001",
        "validation_after_status": ValidationStatus.PASS,
        "ready_for_read_after": True,
        "risk_level": RiskLevel.LOW,
        "confidence": 1.0,
        "matched_count": 0,
        "applied_count": 0,
        "successful_application_count": 0,
        "project_id": "project-001",
        "context_tags": ["source:arxiv", "venue:iclr"],
    }
    values.update(overrides)
    return ExperienceCase(**values)  # type: ignore[arg-type]


def _query(**overrides: object) -> ExperienceQuery:
    values: dict[str, object] = {"limit": 10}
    values.update(overrides)
    return ExperienceQuery(**values)  # type: ignore[arg-type]


def _recovery_case(**overrides: object) -> ExperienceCase:
    values: dict[str, object] = {
        "case_type": "technical",
        "outcome": "recovered",
        "attempt_count": 1,
        "matched_count": 1,
        "applied_count": 1,
        "successful_application_count": 1,
        "environment_fingerprint": "environment-001",
        "anomaly_id": "anomaly-001",
        "anomaly_kind": "progress_stalled",
        "anomaly_fingerprint": "progress-stalled-001",
        "recovery_action": RecoveryAction.RETRY_NEW_RUN,
    }
    values.update(overrides)
    return _case(**values)


def _recovery_query(**overrides: object) -> RecoveryExperienceQuery:
    values: dict[str, object] = {
        "anomaly_kind": "progress_stalled",
        "limit": 10,
        "project_id": "project-001",
        "environment_fingerprint": "environment-001",
    }
    values.update(overrides)
    return RecoveryExperienceQuery(**values)  # type: ignore[arg-type]


def test_select_experience_cases_handles_empty_input_and_public_import() -> None:
    assert select_experience_cases([], _query()) == []


def test_empty_required_tags_disable_tag_filtering() -> None:
    case = _case(context_tags=[])

    assert select_experience_cases([case], _query(required_context_tags=[])) == [case]


def test_all_required_tags_match_after_normalization_without_mutating_inputs() -> None:
    case = _case(context_tags=[" Source:Arxiv ", "VENUE:ICLR"])
    query = _query(required_context_tags=["source:arxiv", " venue:iclr "])
    cases = [case]
    original_case_tags = list(case.context_tags)
    original_query_tags = list(query.required_context_tags)

    result = select_experience_cases(cases, query)

    assert result == [case]
    assert result is not cases
    assert case.context_tags == original_case_tags
    assert query.required_context_tags == original_query_tags


def test_missing_any_required_tag_excludes_case() -> None:
    case = _case(context_tags=["source:arxiv"])
    query = _query(required_context_tags=["source:arxiv", "venue:iclr"])

    assert select_experience_cases([case], query) == []


def test_project_filter_includes_same_and_global_but_excludes_other_project() -> None:
    same = _case(case_id="same", project_id="project-001")
    global_case = _case(case_id="global", project_id=None)
    other = _case(case_id="other", project_id="project-002")

    result = select_experience_cases(
        [same, global_case, other],
        _query(project_id="project-001"),
    )

    assert {case.case_id for case in result} == {"same", "global"}


def test_project_none_disables_project_filtering() -> None:
    first = _case(case_id="first", project_id="project-001")
    second = _case(case_id="second", project_id="project-002")

    result = select_experience_cases([first, second], _query(project_id=None))

    assert {case.case_id for case in result} == {"first", "second"}


def test_fixed_filters_are_combined() -> None:
    matching = _case(case_id="matching")
    wrong_type = _case(case_id="wrong-type", case_type="preference", user_feedback="Prefer concise output")
    wrong_outcome = _case(case_id="wrong-outcome", outcome="partial")
    unverified = _case(case_id="unverified", verified=False)
    deprecated = _case(case_id="deprecated", deprecated=True)

    result = select_experience_cases(
        [matching, wrong_type, wrong_outcome, unverified, deprecated],
        _query(
            case_types=["normal"],
            outcomes=["success"],
            verified_only=True,
            include_deprecated=False,
            required_context_tags=["source:arxiv"],
        ),
    )

    assert result == [matching]


def test_disabled_optional_filters_allow_unverified_deprecated_cases() -> None:
    case = _case(verified=False, deprecated=True)

    result = select_experience_cases(
        [case],
        _query(verified_only=False, include_deprecated=True),
    )

    assert result == [case]


def test_results_sort_by_updated_at_then_case_id_and_apply_limit() -> None:
    older = _case(case_id="older", updated_at=BASE_TIME - timedelta(minutes=1))
    same_time_b = _case(case_id="b", updated_at=BASE_TIME)
    same_time_a = _case(case_id="a", updated_at=BASE_TIME)

    result = select_experience_cases(
        [older, same_time_b, same_time_a],
        _query(limit=2),
    )

    assert [case.case_id for case in result] == ["a", "b"]


@pytest.mark.parametrize("cases", [("not-a-list",), [object()]])
def test_cases_must_be_a_list_of_experience_cases(cases: object) -> None:
    with pytest.raises(TypeError, match="cases"):
        select_experience_cases(cases, _query())  # type: ignore[arg-type]


def test_query_must_be_an_experience_query() -> None:
    with pytest.raises(TypeError, match="query"):
        select_experience_cases([_case()], {"limit": 10})  # type: ignore[arg-type]


def _write_cases(path: Path, cases: list[ExperienceCase]) -> None:
    path.write_text(
        json.dumps([case.to_dict() for case in cases], ensure_ascii=False),
        encoding="utf-8",
    )


def test_json_store_missing_file_returns_empty_without_creating_it(tmp_path) -> None:
    path = tmp_path / "experience_cases.json"

    assert JsonExperienceStore(path).search_cases(_query()) == []
    assert not path.exists()


def test_json_store_empty_array_returns_empty(tmp_path) -> None:
    path = tmp_path / "experience_cases.json"
    path.write_text("[]", encoding="utf-8")

    assert JsonExperienceStore(path).search_cases(_query()) == []


def test_json_store_restores_one_valid_case(tmp_path) -> None:
    path = tmp_path / "experience_cases.json"
    case = _case()
    _write_cases(path, [case])

    assert JsonExperienceStore(path).search_cases(_query()) == [case]


def test_json_store_reuses_existing_selection_rules(tmp_path) -> None:
    path = tmp_path / "experience_cases.json"
    cases = [
        _case(case_id="same", project_id="project-001"),
        _case(case_id="global", project_id=None),
        _case(case_id="other", project_id="project-002"),
        _case(case_id="unverified", verified=False),
        _case(case_id="deprecated", deprecated=True),
    ]
    query = _query(
        project_id="project-001",
        verified_only=True,
        include_deprecated=False,
    )
    _write_cases(path, cases)

    assert JsonExperienceStore(path).search_cases(query) == select_experience_cases(
        cases,
        query,
    )


def test_json_store_preserves_tag_filtering(tmp_path) -> None:
    path = tmp_path / "experience_cases.json"
    cases = [
        _case(case_id="matching", context_tags=["SOURCE:ARXIV", "venue:iclr"]),
        _case(case_id="missing", context_tags=["source:arxiv"]),
    ]
    query = _query(required_context_tags=["source:arxiv", "VENUE:ICLR"])
    _write_cases(path, cases)

    assert JsonExperienceStore(path).search_cases(query) == [cases[0]]


def test_json_store_preserves_limit_and_sorting(tmp_path) -> None:
    path = tmp_path / "experience_cases.json"
    cases = [
        _case(case_id="older", updated_at=BASE_TIME - timedelta(minutes=1)),
        _case(case_id="b", updated_at=BASE_TIME),
        _case(case_id="a", updated_at=BASE_TIME),
    ]
    query = _query(limit=2)
    _write_cases(path, cases)

    assert JsonExperienceStore(path).search_cases(query) == select_experience_cases(
        cases,
        query,
    )


def test_json_store_rejects_invalid_json(tmp_path) -> None:
    path = tmp_path / "experience_cases.json"
    path.write_text("[not-json", encoding="utf-8")

    with pytest.raises(ValueError):
        JsonExperienceStore(path).search_cases(_query())


def test_json_store_rejects_non_array_root(tmp_path) -> None:
    path = tmp_path / "experience_cases.json"
    path.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="JSON array"):
        JsonExperienceStore(path).search_cases(_query())


def test_json_store_rejects_non_object_item_with_index(tmp_path) -> None:
    path = tmp_path / "experience_cases.json"
    path.write_text('["invalid"]', encoding="utf-8")

    with pytest.raises(ValueError, match=r"cases\[0\]"):
        JsonExperienceStore(path).search_cases(_query())


def test_json_store_rejects_invalid_case_with_index(tmp_path) -> None:
    path = tmp_path / "experience_cases.json"
    path.write_text("[{}]", encoding="utf-8")

    with pytest.raises(ValueError, match=r"cases\[0\].*ExperienceCase"):
        JsonExperienceStore(path).search_cases(_query())


def test_json_experience_store_has_a_stable_public_import() -> None:
    assert feedback.JsonExperienceStore is JsonExperienceStore
    assert "JsonExperienceStore" in feedback.__all__


def test_recovery_store_missing_file_returns_empty_without_creating_it(
    tmp_path: Path,
) -> None:
    path = tmp_path / "experience_cases.json"

    assert JsonExperienceStore(path).search_recovery_cases(_recovery_query()) == []
    assert not path.exists()


def test_recovery_store_empty_array_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "experience_cases.json"
    path.write_text("[]", encoding="utf-8")

    assert JsonExperienceStore(path).search_recovery_cases(_recovery_query()) == []


@pytest.mark.parametrize("payload", ["[", "{}", "[{}]"])
def test_recovery_store_preserves_explicit_invalid_store_errors(
    tmp_path: Path,
    payload: str,
) -> None:
    path = tmp_path / "experience_cases.json"
    path.write_text(payload, encoding="utf-8")

    with pytest.raises(ValueError):
        JsonExperienceStore(path).search_recovery_cases(_recovery_query())


def test_recovery_store_rejects_non_query_before_loading(tmp_path: Path) -> None:
    path = tmp_path / "experience_cases.json"
    path.write_text("[", encoding="utf-8")

    with pytest.raises(TypeError, match="query must be a RecoveryExperienceQuery"):
        JsonExperienceStore(path).search_recovery_cases({})  # type: ignore[arg-type]


def test_recovery_store_returns_a_qualified_recovery_case(tmp_path: Path) -> None:
    path = tmp_path / "experience_cases.json"
    case = _recovery_case()
    _write_cases(path, [case])

    result = JsonExperienceStore(path).search_recovery_cases(_recovery_query())

    assert result == [case]


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "verified": False,
            "outcome": "failed",
            "validation_after_status": ValidationStatus.BLOCK,
            "ready_for_read_after": False,
        },
        {"deprecated": True},
        {"case_type": "normal"},
        {"case_type": "preference", "user_feedback": "Test-only preference"},
        {"outcome": "success"},
        {"outcome": "failed"},
        {"outcome": "partial"},
        {"outcome": "cancelled"},
        {
            "outcome": "partial",
            "validation_after_status": ValidationStatus.WARNING,
        },
        {
            "outcome": "failed",
            "validation_after_status": ValidationStatus.BLOCK,
        },
        {"outcome": "partial", "ready_for_read_after": False},
        {
            "anomaly_id": "anomaly-other",
            "anomaly_kind": "result_missing",
            "anomaly_fingerprint": "result-missing-001",
        },
        {"recovery_action": None},
        {
            "matched_count": 0,
            "applied_count": 0,
            "successful_application_count": 0,
        },
    ],
)
def test_recovery_store_filters_unqualified_cases(
    tmp_path: Path,
    overrides: dict[str, object],
) -> None:
    path = tmp_path / "experience_cases.json"
    _write_cases(path, [_recovery_case(**overrides)])

    assert JsonExperienceStore(path).search_recovery_cases(_recovery_query()) == []


def test_recovery_store_uses_exact_project_then_global_fallback(
    tmp_path: Path,
) -> None:
    path = tmp_path / "experience_cases.json"
    exact = _recovery_case(case_id="exact", project_id="project-001")
    global_case = _recovery_case(case_id="global", project_id=None)
    other = _recovery_case(case_id="other", project_id="project-002")
    _write_cases(path, [global_case, other, exact])

    result = JsonExperienceStore(path).search_recovery_cases(_recovery_query())

    assert [case.case_id for case in result] == ["exact", "global"]


def test_recovery_store_projectless_query_only_allows_global_cases(
    tmp_path: Path,
) -> None:
    path = tmp_path / "experience_cases.json"
    global_case = _recovery_case(case_id="global", project_id=None)
    scoped = _recovery_case(case_id="scoped", project_id="project-001")
    _write_cases(path, [scoped, global_case])

    result = JsonExperienceStore(path).search_recovery_cases(
        _recovery_query(project_id=None)
    )

    assert result == [global_case]


def test_recovery_store_uses_exact_environment_then_generic_fallback(
    tmp_path: Path,
) -> None:
    path = tmp_path / "experience_cases.json"
    exact = _recovery_case(case_id="exact", environment_fingerprint="environment-001")
    generic = _recovery_case(case_id="generic", environment_fingerprint=None)
    other = _recovery_case(case_id="other", environment_fingerprint="environment-002")
    _write_cases(path, [generic, other, exact])

    result = JsonExperienceStore(path).search_recovery_cases(_recovery_query())

    assert [case.case_id for case in result] == ["exact", "generic"]


def test_recovery_store_unknown_environment_only_allows_generic_cases(
    tmp_path: Path,
) -> None:
    path = tmp_path / "experience_cases.json"
    generic = _recovery_case(case_id="generic", environment_fingerprint=None)
    scoped = _recovery_case(
        case_id="scoped",
        environment_fingerprint="environment-001",
    )
    _write_cases(path, [scoped, generic])

    result = JsonExperienceStore(path).search_recovery_cases(
        _recovery_query(environment_fingerprint=None)
    )

    assert result == [generic]


def test_recovery_store_requires_all_normalized_context_tags(
    tmp_path: Path,
) -> None:
    path = tmp_path / "experience_cases.json"
    matching = _recovery_case(
        case_id="matching",
        context_tags=[" Source:Arxiv ", "MODEL:CONFIGURED", "extra"],
    )
    missing = _recovery_case(
        case_id="missing",
        context_tags=["source:arxiv"],
    )
    _write_cases(path, [missing, matching])

    result = JsonExperienceStore(path).search_recovery_cases(
        _recovery_query(
            required_context_tags=["source:arxiv", " model:configured "],
        )
    )

    assert result == [matching]


@pytest.mark.parametrize(
    ("first_overrides", "second_overrides", "expected_ids"),
    [
        (
            {"case_id": "fewer"},
            {
                "case_id": "more",
                "matched_count": 3,
                "applied_count": 3,
                "successful_application_count": 3,
            },
            ["more", "fewer"],
        ),
        (
            {"case_id": "lower", "confidence": 0.5},
            {"case_id": "higher", "confidence": 0.9},
            ["higher", "lower"],
        ),
        (
            {"case_id": "older", "updated_at": BASE_TIME - timedelta(minutes=1)},
            {"case_id": "newer", "updated_at": BASE_TIME},
            ["newer", "older"],
        ),
        (
            {"case_id": "b"},
            {"case_id": "a"},
            ["a", "b"],
        ),
    ],
)
def test_recovery_store_applies_stable_recovery_ranking_tiebreakers(
    tmp_path: Path,
    first_overrides: dict[str, object],
    second_overrides: dict[str, object],
    expected_ids: list[str],
) -> None:
    path = tmp_path / "experience_cases.json"
    cases = [
        _recovery_case(**first_overrides),
        _recovery_case(**second_overrides),
    ]
    _write_cases(path, cases)

    result = JsonExperienceStore(path).search_recovery_cases(_recovery_query())

    assert [case.case_id for case in result] == expected_ids


def test_recovery_store_applies_limit_after_complete_sorting(tmp_path: Path) -> None:
    path = tmp_path / "experience_cases.json"
    cases = [
        _recovery_case(case_id="one"),
        _recovery_case(
            case_id="three",
            matched_count=3,
            applied_count=3,
            successful_application_count=3,
        ),
        _recovery_case(
            case_id="two",
            matched_count=2,
            applied_count=2,
            successful_application_count=2,
        ),
    ]
    _write_cases(path, cases)

    result = JsonExperienceStore(path).search_recovery_cases(
        _recovery_query(limit=2)
    )

    assert [case.case_id for case in result] == ["three", "two"]


def test_recovery_store_order_does_not_depend_on_json_input_order(
    tmp_path: Path,
) -> None:
    path = tmp_path / "experience_cases.json"
    cases = [
        _recovery_case(case_id="b"),
        _recovery_case(case_id="a"),
    ]
    store = JsonExperienceStore(path)
    _write_cases(path, cases)
    first = store.search_recovery_cases(_recovery_query())
    _write_cases(path, list(reversed(cases)))
    second = store.search_recovery_cases(_recovery_query())

    assert [case.case_id for case in first] == ["a", "b"]
    assert [case.case_id for case in second] == ["a", "b"]


def test_recovery_store_does_not_modify_file_query_or_case_contracts(
    tmp_path: Path,
) -> None:
    path = tmp_path / "experience_cases.json"
    case = _recovery_case(context_tags=["source:arxiv"])
    query = _recovery_query(required_context_tags=["source:arxiv"])
    _write_cases(path, [case])
    original_bytes = path.read_bytes()
    original_query = query.to_json()
    original_case = case.to_json()

    result = JsonExperienceStore(path).search_recovery_cases(query)

    assert result == [case]
    assert path.read_bytes() == original_bytes
    assert query.to_json() == original_query
    assert case.to_json() == original_case
    assert result[0].matched_count == 1
    assert result[0].last_matched_at is None


def test_recovery_search_does_not_change_original_search_cases_behavior(
    tmp_path: Path,
) -> None:
    path = tmp_path / "experience_cases.json"
    cases = [
        _case(case_id="normal", project_id="project-001"),
        _recovery_case(case_id="recovered", project_id="project-002"),
    ]
    _write_cases(path, cases)
    store = JsonExperienceStore(path)
    original_query = _query(project_id=None)
    before = store.search_cases(original_query)

    store.search_recovery_cases(_recovery_query())
    after = store.search_cases(original_query)

    assert before == after
    assert {case.case_id for case in after} == {"normal", "recovered"}
