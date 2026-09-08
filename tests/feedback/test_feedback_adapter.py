from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import sys

import pytest

import feedback
import feedback.feedback_adapter as adapter_module
from feedback import (
    EvidenceRef,
    ExperienceCase,
    ExperienceQuery,
    FeedbackAdapter,
    FindFeedbackAdapter,
    FindStageRequest,
    ParameterChange,
    RecoveryAction,
    RiskLevel,
    RunContext,
    ValidationStatus,
)


OBSERVED_AT = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)


def _request(tmp_path: Path, *, project_id: str | None = "project-001") -> FindStageRequest:
    return FindStageRequest(
        request_source="web" if project_id is not None else "cli",
        research_topic="Reliable research agents",
        selection={
            "venue_ids": ["dblp_icml"],
            "filters": {"years": [2026]},
        },
        config_path=str(tmp_path / "input" / "find.config.json"),
        requested_parameters={
            "abstract_scoring_max_workers": 10,
            "abstract_scoring_batch_size": 10,
            "abstract_scoring_timeout_sec": 180,
            "arxiv_timeout_sec": 15,
            "limits": {"minimum_recommendations": 5},
            "sources": ["arxiv"],
        },
        working_directory=str(tmp_path),
        project_id=project_id,
    )


def _query(*, project_id: str | None = "project-001") -> ExperienceQuery:
    return ExperienceQuery(limit=5, project_id=project_id)


def _change(
    *,
    name: str = "abstract_scoring_max_workers",
    before: object | None = 10,
    after: object = 4,
) -> ParameterChange:
    return ParameterChange(
        name=name,
        before=before,
        after=after,
        reason="Validated low-risk Find tuning",
    )


def _experience_case(**overrides: object) -> ExperienceCase:
    values: dict[str, object] = {
        "case_id": "case-001",
        "case_type": "normal",
        "created_at": OBSERVED_AT,
        "updated_at": OBSERVED_AT,
        "producer": "adapter-tests",
        "producer_version": "1.0",
        "verified": True,
        "deprecated": False,
        "context_id": "ctx-old",
        "root_run_id": "find-root",
        "final_run_id": "find-final",
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
        "parameter_changes": [],
    }
    values.update(overrides)
    return ExperienceCase(**values)  # type: ignore[arg-type]


def test_empty_experiences_build_the_baseline_run_context(tmp_path: Path) -> None:
    request = _request(tmp_path)
    query = _query()

    context = FindFeedbackAdapter().adapt(request, query, [])

    assert isinstance(context, RunContext)
    assert context.attempt_index == 0
    assert context.project_id == request.project_id
    assert context.request_source == request.request_source
    assert context.producer == "find-feedback-adapter"
    assert context.producer_version == "v0"
    assert context.research_topic == request.research_topic
    assert context.working_directory == request.working_directory
    assert context.python_executable == sys.executable
    assert context.expected_artifacts[0].path == "find_results.json"
    assert context.expected_artifacts[0].required is True
    assert context.startup_grace_seconds == 30
    assert context.stall_suspect_seconds == 60
    assert context.stall_confirm_seconds == 120
    assert context.recovery_budget == 1
    assert context.allowed_recovery_actions == [RecoveryAction.RETRY_NEW_RUN]
    assert context.approval_risk_threshold is RiskLevel.MEDIUM
    assert context.validation_policy_version == "find.validation.v1"
    assert context.matched_experience_refs == []
    assert context.applied_experience_refs == []
    assert context.experience_parameter_changes == []
    assert context.parameter_changes == []
    assert context.experience_query is query
    assert context.context_id != request.project_id
    assert context.researcher_profile_path is None
    assert context.researcher_profile_fingerprint is None
    assert context.conda_env is None
    assert context.model_id is None
    assert context.environment_fingerprint is None


def test_parameters_are_equal_but_deeply_independent(tmp_path: Path) -> None:
    request = _request(tmp_path)
    context = FindFeedbackAdapter().adapt(request, _query(), [])

    assert context.requested_parameters == context.effective_parameters
    assert context.requested_parameters is not context.effective_parameters
    assert (
        context.requested_parameters["limits"]
        is not context.effective_parameters["limits"]
    )

    context.requested_parameters["limits"]["minimum_recommendations"] = 99
    assert context.effective_parameters["limits"]["minimum_recommendations"] == 5


def test_adapter_copies_inputs_without_mutating_request_or_query(tmp_path: Path) -> None:
    request = _request(tmp_path)
    query = _query()
    request_before = deepcopy(request.to_dict())
    query_before = deepcopy(query.to_dict())

    context = FindFeedbackAdapter().adapt(request, query, [])
    assert request.to_dict() == request_before
    assert query.to_dict() == query_before

    request.selection["venue_ids"].append("openreview_iclr_2026")
    request.selection["filters"]["years"].append(2025)
    request.requested_parameters["limits"]["minimum_recommendations"] = 99
    request.requested_parameters["sources"].append("biorxiv")

    assert context.selection == {
        "venue_ids": ["dblp_icml"],
        "filters": {"years": [2026]},
    }
    assert context.requested_parameters == {
        "abstract_scoring_max_workers": 10,
        "abstract_scoring_batch_size": 10,
        "abstract_scoring_timeout_sec": 180,
        "arxiv_timeout_sec": 15,
        "limits": {"minimum_recommendations": 5},
        "sources": ["arxiv"],
    }
    assert query.to_dict() == query_before
    assert context.experience_query is query


def test_projectless_request_paths_command_identity_and_time(tmp_path: Path) -> None:
    request = _request(tmp_path, project_id=None)
    query = _query(project_id=None)

    adapter = FindFeedbackAdapter()
    context = adapter.adapt(request, query, [])
    another_context = adapter.adapt(request, query, [])

    config_path = Path(request.config_path)
    assert context.project_id is None
    assert context.config_snapshot_path == str(config_path)
    assert context.input_snapshot_path == str(config_path.with_name("input.json"))
    assert context.selection_snapshot_path == str(
        config_path.with_name("selection.json")
    )
    assert context.command_redacted == [
        sys.executable,
        "modules/finding/main.py",
        "--action",
        "find",
        "--config-json",
        context.config_snapshot_path,
        "--input-json",
        context.input_snapshot_path,
    ]
    assert context.context_id
    assert context.context_id != another_context.context_id
    assert context.created_at.tzinfo is timezone.utc


@pytest.mark.parametrize(
    ("request_value", "query_value", "experiences_value", "message"),
    [
        ({}, _query(), [], "request must be a FindStageRequest"),
        (None, _query(), [], "request must be a FindStageRequest"),
        (None, {}, [], "request must be a FindStageRequest"),
    ],
)
def test_adapter_rejects_invalid_request_type(
    request_value: object,
    query_value: object,
    experiences_value: object,
    message: str,
) -> None:
    with pytest.raises(TypeError, match=message):
        FindFeedbackAdapter().adapt(  # type: ignore[arg-type]
            request_value,
            query_value,
            experiences_value,
        )


def test_adapter_rejects_invalid_query_and_experience_types(tmp_path: Path) -> None:
    request = _request(tmp_path)
    query = _query()
    adapter = FindFeedbackAdapter()

    with pytest.raises(TypeError, match="experience_query must be an ExperienceQuery"):
        adapter.adapt(request, {}, [])  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="experiences must be a list"):
        adapter.adapt(request, query, ())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match=r"experiences\[0\] must be an ExperienceCase"):
        adapter.adapt(request, query, [object()])  # type: ignore[list-item]


def test_all_experiences_are_matched_in_input_order(tmp_path: Path) -> None:
    experiences = [
        _experience_case(case_id="unverified", verified=False),
        _experience_case(case_id="high-risk", risk_level=RiskLevel.HIGH),
        _experience_case(case_id="empty"),
    ]

    context = FindFeedbackAdapter().adapt(_request(tmp_path), _query(), experiences)

    assert [ref.case_id for ref in context.matched_experience_refs] == [
        "unverified",
        "high-risk",
        "empty",
    ]
    assert all(ref.similarity is None for ref in context.matched_experience_refs)
    assert all(ref.summary is None for ref in context.matched_experience_refs)
    assert context.applied_experience_refs == []


@pytest.mark.parametrize(
    "case_overrides",
    [
        {"verified": False},
        {"deprecated": True},
        {"outcome": "failed"},
        {"outcome": "partial"},
        {"outcome": "cancelled"},
        {"risk_level": RiskLevel.MEDIUM},
        {"risk_level": RiskLevel.HIGH},
    ],
)
def test_ineligible_experiences_are_matched_but_not_applied(
    tmp_path: Path,
    case_overrides: dict[str, object],
) -> None:
    experience = _experience_case(
        parameter_changes=[_change()],
        **case_overrides,
    )

    context = FindFeedbackAdapter().adapt(_request(tmp_path), _query(), [experience])

    assert [ref.case_id for ref in context.matched_experience_refs] == ["case-001"]
    assert context.applied_experience_refs == []
    assert context.experience_parameter_changes == []
    assert context.effective_parameters["abstract_scoring_max_workers"] == 10


@pytest.mark.parametrize("outcome", ["success", "recovered"])
def test_low_whitelisted_integer_change_is_applied(
    tmp_path: Path,
    outcome: str,
) -> None:
    request = _request(tmp_path)
    experience = _experience_case(
        outcome=outcome,
        parameter_changes=[_change(after=4)],
    )

    context = FindFeedbackAdapter().adapt(request, _query(), [experience])

    assert request.requested_parameters["abstract_scoring_max_workers"] == 10
    assert context.requested_parameters["abstract_scoring_max_workers"] == 10
    assert context.effective_parameters["abstract_scoring_max_workers"] == 4
    assert [ref.case_id for ref in context.applied_experience_refs] == ["case-001"]
    assert context.experience_parameter_changes == experience.parameter_changes
    assert context.parameter_changes == experience.parameter_changes
    assert context.parameter_changes is not context.experience_parameter_changes
    assert context.parameter_changes[0] is not context.experience_parameter_changes[0]


@pytest.mark.parametrize(
    ("name", "before", "after", "expected_runtime_tuning"),
    [
        (
            "abstract_scoring_max_workers",
            10,
            4,
            {
                "ABSTRACT_SCORING_MAX_WORKERS": "4",
                "ABSTRACT_SCORING_WORKER_CAP": "4",
            },
        ),
        (
            "abstract_scoring_batch_size",
            10,
            5,
            {
                "ABSTRACT_SCORING_BATCH_SIZE": "5",
                "ABSTRACT_SCORING_MAX_BATCH_SIZE": "5",
            },
        ),
        (
            "abstract_scoring_timeout_sec",
            180,
            240,
            {"ABSTRACT_SCORING_TIMEOUT_SEC": "240"},
        ),
        (
            "arxiv_timeout_sec",
            15,
            30,
            {"ARXIV_TIMEOUT_SEC": "30"},
        ),
    ],
)
def test_applied_safe_parameters_sync_their_runtime_tuning(
    tmp_path: Path,
    name: str,
    before: int,
    after: int,
    expected_runtime_tuning: dict[str, str],
) -> None:
    request = _request(tmp_path)
    request.requested_parameters["runtime_tuning"] = {"UNRELATED_SETTING": "preserved"}
    context = FindFeedbackAdapter().adapt(
        request,
        _query(),
        [_experience_case(parameter_changes=[_change(name=name, before=before, after=after)])],
    )

    assert context.effective_parameters[name] == after
    assert context.effective_parameters["runtime_tuning"] == {
        "UNRELATED_SETTING": "preserved",
        **expected_runtime_tuning,
    }
    assert request.requested_parameters["runtime_tuning"] == {
        "UNRELATED_SETTING": "preserved"
    }


def test_multiple_applied_safe_parameters_sync_only_their_runtime_tuning(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    request.requested_parameters["runtime_tuning"] = {"UNRELATED_SETTING": "preserved"}
    context = FindFeedbackAdapter().adapt(
        request,
        _query(),
        [
            _experience_case(
                parameter_changes=[
                    _change(after=4),
                    _change(
                        name="abstract_scoring_batch_size",
                        before=10,
                        after=5,
                    ),
                ]
            )
        ],
    )

    assert context.effective_parameters["abstract_scoring_max_workers"] == 4
    assert context.effective_parameters["abstract_scoring_batch_size"] == 5
    assert context.effective_parameters["runtime_tuning"] == {
        "UNRELATED_SETTING": "preserved",
        "ABSTRACT_SCORING_MAX_WORKERS": "4",
        "ABSTRACT_SCORING_WORKER_CAP": "4",
        "ABSTRACT_SCORING_BATCH_SIZE": "5",
        "ABSTRACT_SCORING_MAX_BATCH_SIZE": "5",
    }


def test_applied_change_creates_only_its_required_runtime_tuning(tmp_path: Path) -> None:
    request = _request(tmp_path)
    context = FindFeedbackAdapter().adapt(
        request,
        _query(),
        [_experience_case(parameter_changes=[_change(after=4)])],
    )

    assert context.effective_parameters["runtime_tuning"] == {
        "ABSTRACT_SCORING_MAX_WORKERS": "4",
        "ABSTRACT_SCORING_WORKER_CAP": "4",
    }
    assert "runtime_tuning" not in request.requested_parameters


@pytest.mark.parametrize(
    "experiences",
    [
        [],
        [_experience_case(verified=False, parameter_changes=[_change(after=4)])],
        [_experience_case(parameter_changes=[_change(after=10)])],
    ],
)
def test_unapplied_changes_leave_existing_runtime_tuning_unchanged(
    tmp_path: Path,
    experiences: list[ExperienceCase],
) -> None:
    request = _request(tmp_path)
    request.requested_parameters["runtime_tuning"] = {"UNRELATED_SETTING": "preserved"}
    request_before = deepcopy(request.to_dict())

    context = FindFeedbackAdapter().adapt(request, _query(), experiences)

    assert context.effective_parameters == request_before["requested_parameters"]
    assert context.effective_parameters["runtime_tuning"] == {"UNRELATED_SETTING": "preserved"}
    assert request.to_dict() == request_before


@pytest.mark.parametrize(
    "change",
    [
        _change(name="max_recommended_papers", before=20, after=5),
        _change(after=True),
        _change(after="4"),
        _change(after=0),
        _change(after=-1),
        _change(name="abstract_scoring_missing", before=None, after=4),
    ],
)
def test_invalid_or_nonwhitelisted_changes_are_not_applied(
    tmp_path: Path,
    change: ParameterChange,
) -> None:
    experience = _experience_case(parameter_changes=[change])

    context = FindFeedbackAdapter().adapt(_request(tmp_path), _query(), [experience])

    assert context.applied_experience_refs == []
    assert context.parameter_changes == []
    assert context.experience_parameter_changes == []
    assert context.effective_parameters == context.requested_parameters


def test_before_must_match_current_value_but_none_is_allowed(tmp_path: Path) -> None:
    mismatched = _experience_case(
        case_id="mismatched",
        parameter_changes=[_change(before=9, after=4)],
    )
    no_precondition = _experience_case(
        case_id="no-precondition",
        parameter_changes=[
            _change(
                name="abstract_scoring_batch_size",
                before=None,
                after=5,
            )
        ],
    )

    context = FindFeedbackAdapter().adapt(
        _request(tmp_path),
        _query(),
        [mismatched, no_precondition],
    )

    assert context.effective_parameters["abstract_scoring_max_workers"] == 10
    assert context.effective_parameters["abstract_scoring_batch_size"] == 5
    assert [ref.case_id for ref in context.applied_experience_refs] == [
        "no-precondition"
    ]


def test_first_legal_change_for_a_parameter_wins(tmp_path: Path) -> None:
    request = _request(tmp_path)
    request.requested_parameters["runtime_tuning"] = {"UNRELATED_SETTING": "preserved"}
    first = _experience_case(
        case_id="first",
        parameter_changes=[_change(after=6)],
    )
    second = _experience_case(
        case_id="second",
        parameter_changes=[_change(after=2)],
    )

    context = FindFeedbackAdapter().adapt(
        request,
        _query(),
        [first, second],
    )

    assert context.effective_parameters["abstract_scoring_max_workers"] == 6
    assert context.effective_parameters["runtime_tuning"] == {
        "UNRELATED_SETTING": "preserved",
        "ABSTRACT_SCORING_MAX_WORKERS": "6",
        "ABSTRACT_SCORING_WORKER_CAP": "6",
    }
    assert [change.after for change in context.parameter_changes] == [6]
    assert [ref.case_id for ref in context.applied_experience_refs] == ["first"]
    assert [ref.case_id for ref in context.matched_experience_refs] == [
        "first",
        "second",
    ]


def test_first_legal_change_can_follow_an_invalid_change(tmp_path: Path) -> None:
    invalid = _experience_case(
        case_id="invalid",
        parameter_changes=[_change(after=False)],
    )
    legal = _experience_case(
        case_id="legal",
        parameter_changes=[_change(after=3)],
    )

    context = FindFeedbackAdapter().adapt(
        _request(tmp_path),
        _query(),
        [invalid, legal],
    )

    assert context.effective_parameters["abstract_scoring_max_workers"] == 3
    assert [ref.case_id for ref in context.applied_experience_refs] == ["legal"]


def test_only_cases_with_an_actual_change_are_applied(tmp_path: Path) -> None:
    unchanged = _experience_case(
        case_id="unchanged",
        parameter_changes=[_change(after=10)],
    )
    changed = _experience_case(
        case_id="changed",
        parameter_changes=[_change(name="arxiv_timeout_sec", before=15, after=30)],
    )

    context = FindFeedbackAdapter().adapt(
        _request(tmp_path),
        _query(),
        [unchanged, changed],
    )

    matched_ids = {ref.case_id for ref in context.matched_experience_refs}
    applied_ids = {ref.case_id for ref in context.applied_experience_refs}
    assert applied_ids == {"changed"}
    assert applied_ids < matched_ids
    assert context.effective_parameters["arxiv_timeout_sec"] == 30


def test_inputs_are_unchanged_and_context_round_trips_after_application(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    request.requested_parameters["runtime_tuning"] = {"UNRELATED_SETTING": "preserved"}
    query = _query()
    cases = [
        _experience_case(
            parameter_changes=[
                _change(name="abstract_scoring_timeout_sec", before=180, after=240)
            ]
        )
    ]
    request_before = deepcopy(request.to_dict())
    query_before = deepcopy(query.to_dict())
    cases_before = deepcopy([case.to_dict() for case in cases])

    context = FindFeedbackAdapter().adapt(request, query, cases)
    restored = RunContext.from_json(context.to_json())

    assert request.to_dict() == request_before
    assert query.to_dict() == query_before
    assert [case.to_dict() for case in cases] == cases_before
    assert restored == context
    assert restored.effective_parameters["abstract_scoring_timeout_sec"] == 240
    assert restored.effective_parameters["runtime_tuning"] == {
        "UNRELATED_SETTING": "preserved",
        "ABSTRACT_SCORING_TIMEOUT_SEC": "240",
    }


def test_adapter_does_not_create_snapshot_or_artifact_files(tmp_path: Path) -> None:
    assert list(tmp_path.rglob("*")) == []

    context = FindFeedbackAdapter().adapt(_request(tmp_path), _query(), [])

    assert list(tmp_path.rglob("*")) == []
    assert not Path(context.config_snapshot_path).exists()
    assert not Path(context.input_snapshot_path).exists()
    assert not Path(context.selection_snapshot_path).exists()
    for forbidden_name in (
        "JsonExperienceStore",
        "ExperienceStore",
        "Executor",
        "subprocess",
    ):
        assert not hasattr(adapter_module, forbidden_name)


def test_find_feedback_adapter_is_stably_exported_and_matches_protocol() -> None:
    from feedback import FindFeedbackAdapter as PublicFindFeedbackAdapter
    from feedback.feedback_adapter import FindFeedbackAdapter as ModuleFindFeedbackAdapter

    adapter: FeedbackAdapter = FindFeedbackAdapter()

    assert isinstance(adapter, FindFeedbackAdapter)
    assert PublicFindFeedbackAdapter is FindFeedbackAdapter
    assert ModuleFindFeedbackAdapter is FindFeedbackAdapter
    assert "FindFeedbackAdapter" in feedback.__all__
