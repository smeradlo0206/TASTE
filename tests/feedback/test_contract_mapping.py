from __future__ import annotations

from datetime import datetime, timezone

import pytest

from feedback import (
    ArtifactRef,
    EvidenceCollectionRule,
    EvidenceDefinition,
    EvidenceFact,
    EvidenceRef,
    ExperienceCase,
    ExperienceQuery,
    ExperienceRef,
    FindStageRequest,
    RecoveryAction,
    RiskLevel,
    RunContext,
    SupervisorState,
    SupervisorStatus,
    ValidationStatus,
    select_experience_cases,
)


OBSERVED_AT = datetime(2026, 8, 31, 13, 0, tzinfo=timezone.utc)


def _evidence_fact() -> EvidenceFact:
    return EvidenceFact(
        code="find.candidate_count",
        value={"counts": [1, 2], "details": {"phase": "scoring"}},
        source_contract_id="snapshot-mapping",
        source_field="counts.candidates",
        producer="contract-mapping-observer",
        run_id="find-mapping",
        observed_at=OBSERVED_AT,
    )


def _evidence_definition() -> EvidenceDefinition:
    return EvidenceDefinition(
        code="find.candidate_count",
        value_type="integer",
        intended_producer="contract-mapping-observer",
        description="Number of candidates actually observed by Find.",
    )


def _evidence_collection_rule() -> EvidenceCollectionRule:
    return EvidenceCollectionRule(
        rule_id="find-candidate-count-from-progress",
        evidence_code="find.candidate_count",
        source_field="counts.candidates",
        collector="progress_snapshot_field",
        collector_parameters={
            "path": ["scoring", "queue_depth"],
            "options": {"strict": True},
        },
        implementation_status="ready",
    )


def _case(*, case_id: str, project_id: str | None, context_tags: list[str]) -> ExperienceCase:
    return ExperienceCase(
        case_id=case_id,
        case_type="normal",
        created_at=OBSERVED_AT,
        updated_at=OBSERVED_AT,
        producer="contract-mapping-tests",
        producer_version="1.0",
        verified=True,
        deprecated=False,
        context_id=f"ctx-{case_id}",
        root_run_id=f"root-{case_id}",
        final_run_id=f"final-{case_id}",
        root_cause_status="unknown",
        evidence_refs=[EvidenceRef(kind="validation", summary="Passed")],
        attempt_count=0,
        outcome="success",
        validation_after_id=f"validation-{case_id}",
        validation_after_status=ValidationStatus.PASS,
        ready_for_read_after=True,
        risk_level=RiskLevel.LOW,
        confidence=1.0,
        matched_count=0,
        applied_count=0,
        successful_application_count=0,
        project_id=project_id,
        context_tags=context_tags,
    )


def _run_context(
    *,
    project_id: str | None,
    query: ExperienceQuery,
    selected_cases: list[ExperienceCase],
) -> RunContext:
    return RunContext(
        context_id="ctx-before-stage",
        attempt_index=0,
        project_id=project_id,
        request_source="web" if project_id is not None else "cli",
        created_at=OBSERVED_AT,
        producer="contract-mapping-tests",
        producer_version="1.0",
        research_topic="Reliable research agents",
        selection_snapshot_path="/snapshots/selection.json",
        selection={"venues": ["ICLR"]},
        command_redacted=["python", "modules/finding/main.py"],
        working_directory="/workspace",
        python_executable="/opt/miniforge3/envs/taste/bin/python",
        config_snapshot_path="/snapshots/find-redacted.json",
        input_snapshot_path="/snapshots/input.json",
        requested_parameters={"minimum_recommendations": 5},
        effective_parameters={"minimum_recommendations": 5},
        expected_artifacts=[
            ArtifactRef(role="result", path="final/find_results.json", required=True)
        ],
        startup_grace_seconds=30,
        stall_suspect_seconds=60,
        stall_confirm_seconds=120,
        recovery_budget=1,
        allowed_recovery_actions=[RecoveryAction.RETRY_NEW_RUN],
        approval_risk_threshold=RiskLevel.MEDIUM,
        validation_policy_version="find.validation.v1",
        experience_query=query,
        matched_experience_refs=[
            ExperienceRef(
                case_id=case.case_id,
                verified=case.verified,
                case_type=case.case_type,
            )
            for case in selected_cases
        ],
    )


def _supervisor_state(*, project_id: str | None) -> SupervisorState:
    return SupervisorState(
        supervisor_id="supervisor-before-stage",
        project_id=project_id,
        root_run_id=None,
        status=SupervisorStatus.IDLE,
        state_revision=0,
        created_at=OBSERVED_AT,
        updated_at=OBSERVED_AT,
        heartbeat_at=OBSERVED_AT,
        producer="contract-mapping-tests",
        producer_version="1.0",
        process_alive=False,
        cancel_requested=False,
        recovery_attempts=0,
        recovery_budget_total=1,
        recovery_budget_remaining=1,
        awaiting_approval=False,
        gate_evaluated=False,
        allow_read=False,
        gate_reason="Supervisor is idle",
        terminal=False,
        event_sequence=0,
        state_path="feedback/supervisor_state.json",
    )


@pytest.mark.parametrize("project_id", ["project-001", None])
def test_find_before_stage_contract_chain_is_compatible(project_id: str | None) -> None:
    request = FindStageRequest(
        request_source="web" if project_id is not None else "cli",
        research_topic="Reliable research agents",
        selection={"venues": ["ICLR"]},
        config_path="/workspace/find.config.json",
        requested_parameters={"minimum_recommendations": 5},
        working_directory="/workspace",
        project_id=project_id,
    )
    query = ExperienceQuery(
        limit=10,
        project_id=request.project_id,
        required_context_tags=["source:arxiv"],
        case_types=["normal"],
        outcomes=["success"],
        verified_only=True,
        include_deprecated=False,
    )
    cases = [
        _case(
            case_id="matching",
            project_id=project_id,
            context_tags=["source:arxiv"],
        ),
        _case(
            case_id="missing-tag",
            project_id=project_id,
            context_tags=["venue:iclr"],
        ),
        _case(
            case_id="other-project",
            project_id="project-002",
            context_tags=["source:arxiv"],
        ),
    ]

    selected_cases = select_experience_cases(cases, query)
    expected_ids = {"matching"}
    if project_id is None:
        expected_ids.add("other-project")
    assert {case.case_id for case in selected_cases} == expected_ids

    context = _run_context(
        project_id=request.project_id,
        query=query,
        selected_cases=selected_cases,
    )
    state = _supervisor_state(project_id=context.project_id)
    restored_context = RunContext.from_json(context.to_json())
    restored_state = SupervisorState.from_json(state.to_json())

    assert query.project_id == request.project_id
    assert context.project_id == request.project_id
    assert context.experience_query == query
    assert state.project_id == context.project_id
    assert restored_context.project_id == request.project_id
    assert isinstance(restored_context.experience_query, ExperienceQuery)
    assert restored_context.experience_query == query
    assert restored_state.project_id == context.project_id
    for payload in (context.to_dict(), selected_cases[0].to_dict()):
        assert not {
            "topic_fingerprint",
            "config_fingerprint",
            "selection_fingerprint",
        } & set(payload)


@pytest.mark.parametrize(
    "original",
    [_evidence_fact(), _evidence_definition(), _evidence_collection_rule()],
)
def test_evidence_contracts_support_dict_and_json_round_trips(
    original: EvidenceFact | EvidenceDefinition | EvidenceCollectionRule,
) -> None:
    restored_from_dict = type(original).from_dict(original.to_dict())
    restored_from_json = type(original).from_json(original.to_json())

    assert restored_from_dict == original
    assert restored_from_dict is not original
    assert restored_from_json == original
    assert restored_from_json is not original


def test_evidence_fact_copies_nested_value_input_and_serialized_output() -> None:
    value = {
        "counts": [1, 2],
        "details": {"phase": "scoring"},
    }
    fact = EvidenceFact(
        code="find.candidate_count",
        value=value,
        source_contract_id="snapshot-mapping",
        source_field="counts.candidates",
        producer="contract-mapping-observer",
        run_id="find-mapping",
        observed_at=OBSERVED_AT,
    )

    value["counts"].append(3)
    value["details"]["phase"] = "changed"
    serialized = fact.to_dict()
    serialized_value = serialized["value"]
    assert isinstance(serialized_value, dict)
    serialized_value["counts"].append(4)
    serialized_value["details"]["phase"] = "serialized-change"

    assert fact.value == {
        "counts": [1, 2],
        "details": {"phase": "scoring"},
    }


def test_collection_rule_copies_nested_parameter_input_and_serialized_output() -> None:
    parameters = {
        "path": ["scoring", "queue_depth"],
        "options": {"strict": True},
    }
    rule = EvidenceCollectionRule(
        rule_id="find-queue-depth",
        evidence_code="find.queue_depth",
        source_field="queue.depth",
        collector="progress_snapshot_field",
        collector_parameters=parameters,
        implementation_status="needs_instrumentation",
    )

    parameters["path"].append("changed")
    parameters["options"]["strict"] = False
    serialized = rule.to_dict()
    serialized_parameters = serialized["collector_parameters"]
    assert isinstance(serialized_parameters, dict)
    serialized_parameters["path"].append("serialized-change")
    serialized_parameters["options"]["strict"] = False

    assert rule.collector_parameters == {
        "path": ["scoring", "queue_depth"],
        "options": {"strict": True},
    }


@pytest.mark.parametrize(
    "original",
    [_evidence_fact(), _evidence_definition(), _evidence_collection_rule()],
)
def test_evidence_contracts_reject_unknown_fields(
    original: EvidenceFact | EvidenceDefinition | EvidenceCollectionRule,
) -> None:
    payload = original.to_dict()
    payload["unexpected_field"] = "value"

    with pytest.raises(ValueError, match=r"unexpected_field.*unknown"):
        type(original).from_dict(payload)


def test_evidence_contract_mapping_restores_types_and_schema_versions() -> None:
    fact = EvidenceFact.from_json(_evidence_fact().to_json())
    rule = EvidenceCollectionRule.from_json(_evidence_collection_rule().to_json())

    assert isinstance(fact.observed_at, datetime)
    assert fact.observed_at.tzinfo is not None
    assert isinstance(rule.collector_parameters, dict)
    assert fact.schema_version == "feedback.evidence_fact.v1"
    assert rule.schema_version == "feedback.evidence_collection_rule.v1"


def test_evidence_contracts_have_one_public_definition_each() -> None:
    import feedback
    from feedback.contracts import (
        EvidenceCollectionRule as ContractEvidenceCollectionRule,
        EvidenceDefinition as ContractEvidenceDefinition,
        EvidenceFact as ContractEvidenceFact,
    )

    assert feedback.EvidenceFact is ContractEvidenceFact
    assert feedback.EvidenceDefinition is ContractEvidenceDefinition
    assert feedback.EvidenceCollectionRule is ContractEvidenceCollectionRule
    assert EvidenceFact is ContractEvidenceFact
    assert EvidenceDefinition is ContractEvidenceDefinition
    assert EvidenceCollectionRule is ContractEvidenceCollectionRule
    for name in (
        "EvidenceFact",
        "EvidenceDefinition",
        "EvidenceCollectionRule",
    ):
        assert name in feedback.__all__
