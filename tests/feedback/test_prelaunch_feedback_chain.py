from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path

import pytest

from feedback import (
    EvidenceRef,
    ExperienceCase,
    FindFeedbackAdapter,
    JsonExperienceStore,
    ParameterChange,
    RecoveryAction,
    RiskLevel,
    ValidationStatus,
    build_experience_query,
    build_find_stage_request,
)


OBSERVED_AT = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
TEST_PROJECT_ID = "feedback-prelaunch-test-project"


def _request(tmp_path: Path, *, parameters: dict[str, object] | None = None):
    return build_find_stage_request(
        request_source="cli",
        project_id=TEST_PROJECT_ID,
        research_topic="Synthetic offline feedback validation",
        selection={"include_arxiv": False, "venue_ids": []},
        config_path=str(tmp_path / "input" / "find.config.json"),
        requested_parameters=parameters
        or {
            "abstract_scoring_max_workers": 2,
            "runtime_tuning": {
                "ABSTRACT_SCORING_MAX_WORKERS": "2",
                "ABSTRACT_SCORING_WORKER_CAP": "2",
                "UNRELATED_SETTING": "preserved",
            },
            "other": {"preserved": True},
        },
        working_directory=str(tmp_path),
    )


def _case(
    *,
    case_id: str,
    parameter_changes: list[ParameterChange],
    **overrides: object,
) -> ExperienceCase:
    values: dict[str, object] = {
        "case_id": case_id,
        "case_type": "normal",
        "created_at": OBSERVED_AT,
        "updated_at": OBSERVED_AT,
        "producer": "feedback-prelaunch-test",
        "producer_version": "synthetic-test-only",
        "verified": True,
        "deprecated": False,
        "context_id": "ctx-synthetic-test-only",
        "root_run_id": "find-synthetic-root",
        "final_run_id": "find-synthetic-final",
        "root_cause_status": "unknown",
        "evidence_refs": [
            EvidenceRef(kind="test", summary="Synthetic test-only offline evidence")
        ],
        "attempt_count": 0,
        "outcome": "success",
        "validation_after_id": "validation-synthetic",
        "validation_after_status": ValidationStatus.PASS,
        "ready_for_read_after": True,
        "risk_level": RiskLevel.LOW,
        "confidence": 1.0,
        "matched_count": 0,
        "applied_count": 0,
        "successful_application_count": 0,
        "project_id": TEST_PROJECT_ID,
        "context_tags": ["synthetic", "test-only"],
        "parameter_changes": parameter_changes,
    }
    values.update(overrides)
    return ExperienceCase(**values)  # type: ignore[arg-type]


def _change(
    *,
    name: str = "abstract_scoring_max_workers",
    before: object | None = 2,
    after: object = 1,
) -> ParameterChange:
    return ParameterChange(
        name=name,
        before=before,
        after=after,
        reason="Synthetic test-only prelaunch validation",
    )


@pytest.mark.parametrize("write_empty_file", [False, True])
def test_empty_or_missing_store_keeps_prelaunch_inputs_unchanged(
    tmp_path: Path,
    write_empty_file: bool,
) -> None:
    store_path = tmp_path / "offline-experience-cases.json"
    if write_empty_file:
        store_path.write_text("[]\n", encoding="utf-8")
    request = _request(tmp_path)
    query = build_experience_query(request)
    request_before = deepcopy(request.to_dict())
    query_before = deepcopy(query.to_dict())

    matched = JsonExperienceStore(store_path).search_cases(query)
    context = FindFeedbackAdapter().adapt(request, query, matched)

    assert matched == []
    assert context.matched_experience_refs == []
    assert context.applied_experience_refs == []
    assert context.parameter_changes == []
    assert context.experience_parameter_changes == []
    assert context.requested_parameters == context.effective_parameters
    assert request.to_dict() == request_before
    assert query.to_dict() == query_before
    assert store_path.exists() is write_empty_file


def test_project_scoped_synthetic_case_flows_from_store_to_effective_parameters(
    tmp_path: Path,
) -> None:
    store_path = tmp_path / "offline-experience-cases.json"
    synthetic_case = _case(case_id="case-synthetic-worker-one", parameter_changes=[_change()])
    store_path.write_text(
        json.dumps([synthetic_case.to_dict()]),
        encoding="utf-8",
    )
    request = _request(tmp_path)
    query = build_experience_query(request)
    request_before = deepcopy(request.to_dict())
    query_before = deepcopy(query.to_dict())

    matched = JsonExperienceStore(store_path).search_cases(query)
    context = FindFeedbackAdapter().adapt(request, query, matched)
    other_project_query = build_experience_query(
        build_find_stage_request(
            request_source="cli",
            project_id="different-test-project",
            research_topic=request.research_topic,
            selection=request.selection,
            config_path=request.config_path,
            requested_parameters=request.requested_parameters,
            working_directory=request.working_directory,
        )
    )

    assert [case.case_id for case in matched] == [synthetic_case.case_id]
    assert [ref.case_id for ref in context.matched_experience_refs] == [
        synthetic_case.case_id
    ]
    assert [ref.case_id for ref in context.applied_experience_refs] == [
        synthetic_case.case_id
    ]
    assert request.requested_parameters["abstract_scoring_max_workers"] == 2
    assert context.requested_parameters["abstract_scoring_max_workers"] == 2
    assert context.effective_parameters["abstract_scoring_max_workers"] == 1
    assert context.requested_parameters["runtime_tuning"] == {
        "ABSTRACT_SCORING_MAX_WORKERS": "2",
        "ABSTRACT_SCORING_WORKER_CAP": "2",
        "UNRELATED_SETTING": "preserved",
    }
    assert context.effective_parameters["runtime_tuning"] == {
        "ABSTRACT_SCORING_MAX_WORKERS": "1",
        "ABSTRACT_SCORING_WORKER_CAP": "1",
        "UNRELATED_SETTING": "preserved",
    }
    assert context.effective_parameters["other"] == {"preserved": True}
    assert context.selection == request.selection
    assert request.to_dict() == request_before
    assert query.to_dict() == query_before
    assert JsonExperienceStore(store_path).search_cases(other_project_query) == []


def test_ineligible_cases_are_filtered_or_matched_without_application(
    tmp_path: Path,
) -> None:
    store_path = tmp_path / "offline-experience-cases.json"
    cases = [
        _case(case_id="unverified", parameter_changes=[_change()], verified=False),
        _case(case_id="deprecated", parameter_changes=[_change()], deprecated=True),
        _case(case_id="failed", parameter_changes=[_change()], outcome="failed"),
        _case(case_id="medium", parameter_changes=[_change()], risk_level=RiskLevel.MEDIUM),
        _case(case_id="high", parameter_changes=[_change()], risk_level=RiskLevel.HIGH),
        _case(case_id="nonwhitelist", parameter_changes=[_change(name="max_papers")]),
        _case(case_id="before-mismatch", parameter_changes=[_change(before=3)]),
        _case(case_id="zero", parameter_changes=[_change(after=0)]),
        _case(case_id="negative", parameter_changes=[_change(after=-1)]),
        _case(case_id="string", parameter_changes=[_change(after="1")]),
        _case(case_id="bool", parameter_changes=[_change(after=True)]),
    ]
    store_path.write_text(json.dumps([case.to_dict() for case in cases]), encoding="utf-8")
    request = _request(tmp_path)
    query = build_experience_query(request, limit=20)
    matched = JsonExperienceStore(store_path).search_cases(query)
    context = FindFeedbackAdapter().adapt(request, query, matched)
    missing_target_request = _request(tmp_path, parameters={"other": 2})
    missing_target_context = FindFeedbackAdapter().adapt(
        missing_target_request,
        build_experience_query(missing_target_request, limit=20),
        JsonExperienceStore(store_path).search_cases(
            build_experience_query(missing_target_request, limit=20)
        ),
    )

    assert {case.case_id for case in matched} == {
        "medium",
        "high",
        "nonwhitelist",
        "before-mismatch",
        "zero",
        "negative",
        "string",
        "bool",
    }
    assert context.applied_experience_refs == []
    assert context.effective_parameters == context.requested_parameters
    assert missing_target_context.applied_experience_refs == []
    assert missing_target_context.effective_parameters == {"other": 2}


def test_invalid_store_json_surfaces_the_contract_error(tmp_path: Path) -> None:
    store_path = tmp_path / "offline-experience-cases.json"
    store_path.write_text('[{"case_id": "not-a-contract"}]', encoding="utf-8")
    request = _request(tmp_path)

    with pytest.raises(ValueError, match=r"cases\[0\] is invalid"):
        JsonExperienceStore(store_path).search_cases(build_experience_query(request))


def test_runtime_tuning_preserves_the_adapter_worker_value(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finding_root = Path(__file__).resolve().parents[2] / "modules" / "finding"
    spec = importlib.util.spec_from_file_location(
        "finding_main_prelaunch_test",
        finding_root / "main.py",
    )
    assert spec and spec.loader
    finding_main = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(finding_main)
    runtime = finding_main._private_import("finding_runtime")
    pipeline = finding_main._private_import("flow.pipeline")

    store_path = tmp_path / "offline-experience-cases.json"
    store_path.write_text(
        json.dumps([_case(case_id="worker-one", parameter_changes=[_change()]).to_dict()]),
        encoding="utf-8",
    )
    request = _request(
        tmp_path,
        parameters={
            "abstract_scoring_max_workers": 2,
            "runtime_tuning": {
                "ABSTRACT_SCORING_MAX_WORKERS": "2",
                "ABSTRACT_SCORING_WORKER_CAP": "2",
            },
        },
    )
    query = build_experience_query(request)
    context = FindFeedbackAdapter().adapt(
        request,
        query,
        JsonExperienceStore(store_path).search_cases(query),
    )
    config = runtime.AppConfig(**context.effective_parameters)
    runtime_environment: dict[str, str] = {}
    applied = runtime.apply_runtime_tuning_env(config, runtime_environment)

    monkeypatch.setenv(
        "ABSTRACT_SCORING_MAX_WORKERS",
        runtime_environment["ABSTRACT_SCORING_MAX_WORKERS"],
    )
    monkeypatch.setenv(
        "ABSTRACT_SCORING_WORKER_CAP",
        runtime_environment["ABSTRACT_SCORING_WORKER_CAP"],
    )

    assert context.effective_parameters["abstract_scoring_max_workers"] == 1
    assert context.requested_parameters["abstract_scoring_max_workers"] == 2
    assert context.effective_parameters["runtime_tuning"] == {
        "ABSTRACT_SCORING_MAX_WORKERS": "1",
        "ABSTRACT_SCORING_WORKER_CAP": "1",
    }
    assert applied == {
        "ABSTRACT_SCORING_MAX_WORKERS": "1",
        "ABSTRACT_SCORING_WORKER_CAP": "1",
    }
    assert pipeline._adaptive_final_scoring_workers(config, prompt_count=1) == 1
