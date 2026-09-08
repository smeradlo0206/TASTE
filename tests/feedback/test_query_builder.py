from __future__ import annotations

from datetime import datetime, timezone

import feedback
import feedback.query_builder as query_builder
import pytest

from feedback import (
    Anomaly,
    EvidenceRef,
    ExperienceQuery,
    FindFeedbackAdapter,
    FindStageRequest,
    RecoveryExperienceQuery,
    RunContext,
    build_experience_query,
    build_recovery_experience_query,
)


def _request(**overrides: object) -> FindStageRequest:
    values: dict[str, object] = {
        "request_source": "web",
        "project_id": "project-001",
        "research_topic": "Reliable research agents",
        "selection": {
            "venue_ids": ["dblp_icml"],
            "years": [2026],
            "include_arxiv": True,
        },
        "config_path": "/workspace/tmp/finding/input/find.config.json",
        "requested_parameters": {"max_recommended_papers": 7},
        "working_directory": "/workspace",
    }
    values.update(overrides)
    return FindStageRequest(**values)  # type: ignore[arg-type]


def _anomaly(**overrides: object) -> Anomaly:
    observed_at = datetime(2026, 9, 7, 9, 0, tzinfo=timezone.utc)
    values: dict[str, object] = {
        "anomaly_id": "anomaly-query-builder",
        "run_id": "find-query-builder",
        "created_at": observed_at,
        "detected_at": observed_at,
        "updated_at": observed_at,
        "producer": "query-builder-tests",
        "producer_version": "1.0",
        "stage": "find",
        "kind": "progress_stalled",
        "blocking": True,
        "confidence": 0.9,
        "detected_by": ["observer"],
        "supervisor_state_revision": 1,
        "symptoms": ["No progress"],
        "evidence_refs": [EvidenceRef(kind="snapshot", summary="No progress")],
        "root_cause_status": "suspected",
        "affected_phase": "finding",
        "downstream_impact": "Read cannot start",
        "partial_results_usable": False,
        "recovery_eligible": True,
        "retryable_signal": True,
        "fingerprint": "progress-stalled-query-builder",
        "occurrence_count": 1,
    }
    values.update(overrides)
    return Anomaly(**values)  # type: ignore[arg-type]


def _run_context(
    *,
    project_id: str | None = "project-001",
    environment_fingerprint: str | None = "environment-sha256",
    required_context_tags: list[str] | None = None,
) -> RunContext:
    request = _request(project_id=project_id)
    experience_query = ExperienceQuery(
        limit=5,
        required_context_tags=(
            ["source:arxiv"]
            if required_context_tags is None
            else required_context_tags
        ),
    )
    context = FindFeedbackAdapter().adapt(request, experience_query, [])
    context.environment_fingerprint = environment_fingerprint
    return context


def test_build_experience_query_uses_fixed_before_stage_defaults() -> None:
    request = _request()

    query = build_experience_query(request)

    assert isinstance(query, ExperienceQuery)
    assert query.project_id == request.project_id
    assert query.schema_version == "find.experience_query.v1"
    assert query.stage == "find"
    assert query.case_types == ["normal", "technical", "preference"]
    assert query.outcomes == ["success", "recovered"]
    assert query.required_context_tags == []
    assert query.verified_only is True
    assert query.include_deprecated is False
    assert query.limit == 5


def test_build_experience_query_preserves_projectless_requests() -> None:
    query = build_experience_query(_request(project_id=None))

    assert query.project_id is None


def test_build_experience_query_uses_only_explicit_tags() -> None:
    tags = ["reinforcement-learning", "arxiv"]

    query = build_experience_query(_request(), required_context_tags=tags)

    assert query.required_context_tags == tags
    assert query.required_context_tags is not tags


def test_build_experience_query_accepts_a_custom_limit() -> None:
    assert build_experience_query(_request(), limit=10).limit == 10


@pytest.mark.parametrize("limit", [0, 101])
def test_build_experience_query_uses_contract_limit_validation(limit: int) -> None:
    with pytest.raises(ValueError, match=r"ExperienceQuery\.limit"):
        build_experience_query(_request(), limit=limit)


def test_build_experience_query_uses_contract_tag_validation() -> None:
    with pytest.raises(ValueError, match=r"ExperienceQuery\.required_context_tags\[0\]"):
        build_experience_query(_request(), required_context_tags=[""])


def test_build_experience_query_does_not_generate_tags_from_request_content() -> None:
    request = _request(
        research_topic="Offline reinforcement learning",
        selection={"venue_ids": ["dblp_icml"], "include_arxiv": True},
    )

    assert build_experience_query(request).required_context_tags == []


def test_build_experience_query_has_a_stable_public_import() -> None:
    assert feedback.build_experience_query is build_experience_query
    assert "build_experience_query" in feedback.__all__


def test_query_builder_does_not_import_or_expose_downstream_components() -> None:
    forbidden_names = {
        "select_experience_cases",
        "ExperienceCase",
        "ExperienceStore",
        "subprocess",
        "json",
    }

    assert not forbidden_names & set(vars(query_builder))


def test_build_recovery_experience_query_maps_existing_evidence() -> None:
    anomaly = _anomaly(kind="result_unparseable")
    run_context = _run_context(
        project_id="project-recovery",
        environment_fingerprint="environment-recovery",
        required_context_tags=["source:arxiv", "model:configured"],
    )

    query = build_recovery_experience_query(anomaly, run_context)

    assert isinstance(query, RecoveryExperienceQuery)
    assert query.anomaly_kind == anomaly.kind
    assert query.project_id == run_context.project_id
    assert query.environment_fingerprint == run_context.environment_fingerprint
    assert (
        query.required_context_tags
        == run_context.experience_query.required_context_tags
    )
    assert query.required_context_tags is not run_context.experience_query.required_context_tags
    assert query.limit == 10
    assert query.verified_only is True
    assert query.include_deprecated is False


def test_build_recovery_experience_query_accepts_custom_limit_and_empty_scopes() -> None:
    query = build_recovery_experience_query(
        _anomaly(),
        _run_context(
            project_id=None,
            environment_fingerprint=None,
            required_context_tags=[],
        ),
        limit=3,
    )

    assert query.limit == 3
    assert query.project_id is None
    assert query.environment_fingerprint is None
    assert query.required_context_tags == []


@pytest.mark.parametrize("limit", [0, 101, True, "10"])
def test_build_recovery_experience_query_uses_contract_limit_validation(
    limit: object,
) -> None:
    with pytest.raises(ValueError, match=r"RecoveryExperienceQuery\.limit"):
        build_recovery_experience_query(
            _anomaly(),
            _run_context(),
            limit=limit,  # type: ignore[arg-type]
        )


def test_build_recovery_experience_query_rejects_wrong_contract_types() -> None:
    with pytest.raises(TypeError, match="anomaly must be an Anomaly"):
        build_recovery_experience_query({}, _run_context())  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="run_context must be a RunContext"):
        build_recovery_experience_query(_anomaly(), {})  # type: ignore[arg-type]


def test_build_recovery_experience_query_copies_tags_without_mutating_inputs() -> None:
    anomaly = _anomaly()
    run_context = _run_context(required_context_tags=["source:arxiv"])
    before = (anomaly.to_json(), run_context.to_json())

    query = build_recovery_experience_query(anomaly, run_context)
    query.required_context_tags.append("model:configured")

    assert run_context.experience_query.required_context_tags == ["source:arxiv"]
    assert (anomaly.to_json(), run_context.to_json()) == before


def test_recovery_query_builder_has_stable_module_and_public_imports() -> None:
    from feedback import build_recovery_experience_query as PublicBuilder
    from feedback.query_builder import build_recovery_experience_query as ModuleBuilder

    assert PublicBuilder is query_builder.build_recovery_experience_query
    assert ModuleBuilder is query_builder.build_recovery_experience_query
    assert build_recovery_experience_query is query_builder.build_recovery_experience_query
    assert "build_recovery_experience_query" in feedback.__all__
