from __future__ import annotations

import inspect
import json
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from enum import Enum

import pytest

import feedback
import feedback.contracts as contracts
from feedback.contracts import JsonContract
from feedback import (
    Anomaly,
    ArtifactRef,
    EvidenceCollectionRule,
    EvidenceDefinition,
    EvidenceFact,
    EvidenceRef,
    ExperienceCase,
    ExperienceQuery,
    ExperienceRef,
    ExecutionHandle,
    FindStageRequest,
    ParameterChange,
    ProgressStatus,
    RecoveryAction,
    RecoveryDecision,
    RecoveryExperienceQuery,
    RecoveryProposal,
    RiskLevel,
    ProgressSnapshot,
    RunContext,
    SupervisorEvent,
    SupervisorEventType,
    SupervisorState,
    SupervisorStatus,
    ValidationCheck,
    ValidationResult,
    ValidationStatus,
)


ENUM_CASES = (
    (
        ProgressStatus,
        {
            "STARTING": "starting",
            "RUNNING": "running",
            "SUSPECTED_STALL": "suspected_stall",
            "STALLED": "stalled",
            "COMPLETED": "completed",
            "FAILED": "failed",
            "CANCELLED": "cancelled",
            "UNKNOWN": "unknown",
        },
    ),
    (
        ValidationStatus,
        {
            "PASS": "pass",
            "WARNING": "warning",
            "BLOCK": "block",
        },
    ),
    (
        RecoveryAction,
        {
            "NO_ACTION": "no_action",
            "RETRY_NEW_RUN": "retry_new_run",
            "RETRY_WITH_PARAMETER_CHANGE": "retry_with_parameter_change",
            "SKIP_OPTIONAL_SOURCE": "skip_optional_source",
            "REQUEST_APPROVAL": "request_approval",
            "STOP_AND_REPORT": "stop_and_report",
        },
    ),
    (
        SupervisorStatus,
        {
            "IDLE": "idle",
            "PREPARING": "preparing",
            "RUNNING": "running",
            "VALIDATING": "validating",
            "DIAGNOSING": "diagnosing",
            "DECIDING": "deciding",
            "AWAITING_APPROVAL": "awaiting_approval",
            "RECOVERING": "recovering",
            "GATING": "gating",
            "COMPLETED": "completed",
            "BLOCKED": "blocked",
            "FAILED": "failed",
            "CANCELLED": "cancelled",
        },
    ),
    (
        SupervisorEventType,
        {
            "START": "start",
            "PREPARED": "prepared",
            "PREPARE_FAILED": "prepare_failed",
            "MONITOR_TICK": "monitor_tick",
            "PROCESS_EXITED": "process_exited",
            "STALL_DETECTED": "stall_detected",
            "VALIDATION_PASSED": "validation_passed",
            "VALIDATION_BLOCKED": "validation_blocked",
            "ANOMALY_DETECTED": "anomaly_detected",
            "ANOMALY_READY": "anomaly_ready",
            "DIAGNOSIS_FAILED": "diagnosis_failed",
            "RETRY_DECIDED": "retry_decided",
            "APPROVAL_REQUIRED": "approval_required",
            "STOP_DECIDED": "stop_decided",
            "APPROVED": "approved",
            "REJECTED": "rejected",
            "NEW_RUN_STARTED": "new_run_started",
            "RECOVERY_FAILED": "recovery_failed",
            "GATE_ALLOWED": "gate_allowed",
            "GATE_BLOCKED_RECOVERABLE": "gate_blocked_recoverable",
            "GATE_BLOCKED_FINAL": "gate_blocked_final",
            "CANCEL_REQUESTED": "cancel_requested",
            "FATAL_ERROR": "fatal_error",
        },
    ),
    (
        RiskLevel,
        {
            "LOW": "low",
            "MEDIUM": "medium",
            "HIGH": "high",
        },
    ),
)


@pytest.mark.parametrize(("enum_type", "expected_members"), ENUM_CASES)
def test_enum_members_and_values_match_contract(enum_type: type[Enum], expected_members: dict[str, str]) -> None:
    assert {member.name: member.value for member in enum_type} == expected_members


@pytest.mark.parametrize(("enum_type", "expected_members"), ENUM_CASES)
def test_enums_have_string_behavior(enum_type: type[Enum], expected_members: dict[str, str]) -> None:
    for expected_value in expected_members.values():
        member = enum_type(expected_value)
        assert isinstance(member, str)
        assert member == expected_value


@pytest.mark.parametrize(("enum_type", "expected_members"), ENUM_CASES)
def test_invalid_enum_values_fail(enum_type: type[Enum], expected_members: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        enum_type("not_a_contract_value")


@pytest.mark.parametrize(("enum_type", "expected_members"), ENUM_CASES)
def test_enum_values_are_json_serializable(enum_type: type[Enum], expected_members: dict[str, str]) -> None:
    for expected_value in expected_members.values():
        assert json.loads(json.dumps({"value": enum_type(expected_value)})) == {"value": expected_value}


@dataclass
class ExampleChild(JsonContract):
    observed_at: datetime
    status: ProgressStatus


@dataclass
class ExampleContract(JsonContract):
    title: str
    created_at: datetime
    status: ValidationStatus
    children: list[ExampleChild]
    metadata: dict[str, object]


def _example_contract() -> ExampleContract:
    return ExampleContract(
        title="研究摘要",
        created_at=datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc),
        status=ValidationStatus.PASS,
        children=[
            ExampleChild(
                observed_at=datetime(2026, 8, 29, 12, 1, tzinfo=timezone.utc),
                status=ProgressStatus.RUNNING,
            )
        ],
        metadata={"attempt": 1, "labels": ["论文", "反馈"]},
    )


def test_json_contract_round_trip_supports_nested_dataclasses() -> None:
    original = _example_contract()

    restored = ExampleContract.from_json(original.to_json())

    assert restored == original
    assert isinstance(restored.children[0], ExampleChild)
    assert restored.children[0].status is ProgressStatus.RUNNING
    assert restored.created_at.tzinfo is not None


def test_json_contract_uses_utf8_and_stable_key_order() -> None:
    contract = _example_contract()
    payload = contract.to_json()

    assert "研究摘要" in payload
    assert "\\u7814" not in payload
    assert payload == json.dumps(
        contract.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def test_json_contract_rejects_unknown_and_missing_fields_with_paths() -> None:
    data = _example_contract().to_dict()

    with pytest.raises(ValueError, match=r"ExampleContract\.unexpected is unknown"):
        ExampleContract.from_dict({**data, "unexpected": True})

    missing_status = {name: value for name, value in data.items() if name != "status"}
    with pytest.raises(ValueError, match=r"ExampleContract\.status is required"):
        ExampleContract.from_dict(missing_status)


def test_json_contract_rejects_naive_datetime_and_invalid_enum_values() -> None:
    naive = ExampleContract(
        title="naive",
        created_at=datetime(2026, 8, 29, 12, 0),
        status=ValidationStatus.PASS,
        children=[],
        metadata={},
    )
    with pytest.raises(ValueError, match=r"ExampleContract\.created_at"):
        naive.to_dict()

    data = _example_contract().to_dict()
    data["status"] = "invalid"
    with pytest.raises(ValueError, match=r"ExampleContract\.status has invalid value"):
        ExampleContract.from_dict(data)


def test_json_contract_copies_mapping_input_during_parsing() -> None:
    data = _example_contract().to_dict()
    metadata = data["metadata"]
    restored = ExampleContract.from_dict(data)
    assert isinstance(metadata, dict)
    metadata["attempt"] = 2

    assert restored.metadata == {"attempt": 1, "labels": ["论文", "反馈"]}


def test_internal_contracts_round_trip_and_nested_evidence() -> None:
    observed_at = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
    evidence = EvidenceRef(
        kind="validation",
        path="reports/source_status.md",
        line_start=2,
        line_end=4,
        summary="ICLR source status was checked",
    )
    contracts_to_check = [
        ArtifactRef(
            role="find_results",
            path="final/find_results.json",
            required=True,
            exists=True,
            size_bytes=42,
            modified_at=observed_at,
        ),
        evidence,
        ExperienceRef(
            case_id="technical-001",
            verified=True,
            case_type="technical",
            similarity=0.75,
            summary="Retry with a new run after source timeout",
        ),
        ParameterChange(
            name="title_llm_workers",
            before={"workers": 10},
            after={"workers": 1},
            reason="Reduce provider rate pressure",
        ),
        ValidationCheck(
            code="find.output.present",
            status=ValidationStatus.PASS,
            required=True,
            message="Required output exists",
            evidence_refs=[evidence],
        ),
        SupervisorEvent(
            sequence=0,
            occurred_at=observed_at,
            event_type=SupervisorEventType.VALIDATION_PASSED,
            message="Validation completed",
        ),
    ]

    for contract in contracts_to_check:
        restored = type(contract).from_json(contract.to_json())
        assert restored == contract

    validation = contracts_to_check[4]
    assert isinstance(validation, ValidationCheck)
    assert validation.evidence_refs[0] == evidence
    assert isinstance(validation.evidence_refs[0], EvidenceRef)


def test_internal_contracts_reject_invalid_values() -> None:
    with pytest.raises(ValueError, match=r"ArtifactRef\.size_bytes"):
        ArtifactRef(role="result", path="result.json", required=True, size_bytes=-1)

    with pytest.raises(ValueError, match=r"EvidenceRef\.line_start"):
        EvidenceRef(kind="log", summary="No secret", line_start=0)

    with pytest.raises(ValueError, match=r"EvidenceRef\.line_end"):
        EvidenceRef(kind="log", summary="No secret", line_start=3, line_end=2)

    with pytest.raises(ValueError, match=r"ExperienceRef\.similarity"):
        ExperienceRef(case_id="case", verified=True, case_type="technical", similarity=1.1)

    with pytest.raises(ValueError, match=r"ExperienceRef\.case_type"):
        ExperienceRef(case_id="case", verified=True, case_type="other")

    with pytest.raises(ValueError, match=r"SupervisorEvent\.sequence"):
        SupervisorEvent(
            sequence=-1,
            occurred_at=datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc),
            event_type=SupervisorEventType.START,
            message="Invalid sequence",
        )

    with pytest.raises(ValueError, match=r"SupervisorEvent\.occurred_at"):
        SupervisorEvent(
            sequence=0,
            occurred_at=datetime(2026, 8, 29, 12, 0),
            event_type=SupervisorEventType.START,
            message="Naive datetime",
        )


def test_supervisor_event_requires_a_declared_event_type() -> None:
    observed_at = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
    event = SupervisorEvent(
        sequence=0,
        occurred_at=observed_at,
        event_type=SupervisorEventType.START,
        message="Supervisor started.",
    )

    assert event.event_type is SupervisorEventType.START

    for invalid_event_type in ("start", "typo_event"):
        with pytest.raises(ValueError, match=r"SupervisorEvent\.event_type"):
            SupervisorEvent(
                sequence=0,
                occurred_at=observed_at,
                event_type=invalid_event_type,  # type: ignore[arg-type]
                message="Invalid event type.",
            )


def test_supervisor_event_json_round_trip_preserves_event_type() -> None:
    event = SupervisorEvent(
        sequence=1,
        occurred_at=datetime(2026, 8, 29, 12, 1, tzinfo=timezone.utc),
        event_type=SupervisorEventType.VALIDATION_BLOCKED,
        message="Validation blocked the Read gate.",
    )

    assert event.to_dict()["event_type"] == "validation_blocked"
    restored = SupervisorEvent.from_json(event.to_json())

    assert restored == event
    assert restored.event_type is SupervisorEventType.VALIDATION_BLOCKED


def test_validation_check_uses_independent_default_lists() -> None:
    first = ValidationCheck(
        code="first",
        status=ValidationStatus.PASS,
        required=True,
        message="First check",
    )
    second = ValidationCheck(
        code="second",
        status=ValidationStatus.PASS,
        required=True,
        message="Second check",
    )

    first.evidence_refs.append(EvidenceRef(kind="log", summary="First evidence"))
    assert second.evidence_refs == []


def _minimal_run_context(**overrides: object) -> RunContext:
    values: dict[str, object] = {
        "context_id": "ctx-001",
        "attempt_index": 0,
        "project_id": "project-001",
        "request_source": "web",
        "created_at": datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc),
        "producer": "feedback-tests",
        "producer_version": "1.0",
        "research_topic": "Reliable research agents",
        "selection_snapshot_path": "/snapshots/selection.json",
        "selection": {"venues": ["ICLR"]},
        "command_redacted": ["python", "modules/finding/main.py"],
        "working_directory": "/workspace",
        "python_executable": "/opt/miniforge3/envs/taste/bin/python",
        "config_snapshot_path": "/tmp/find/find.config.json",
        "input_snapshot_path": "/tmp/find/input.json",
        "requested_parameters": {"minimum_recommendations": 5},
        "effective_parameters": {"minimum_recommendations": 5},
        "expected_artifacts": [
            ArtifactRef(
                role="result",
                path="final/find_results.json",
                required=True,
            )
        ],
        "startup_grace_seconds": 30,
        "stall_suspect_seconds": 60,
        "stall_confirm_seconds": 120,
        "recovery_budget": 1,
        "allowed_recovery_actions": [RecoveryAction.RETRY_NEW_RUN],
        "approval_risk_threshold": RiskLevel.MEDIUM,
        "validation_policy_version": "find.validation.v1",
        "experience_query": ExperienceQuery(
            limit=10,
            project_id="project-001",
            case_types=["normal", "technical"],
            outcomes=["success", "recovered"],
            required_context_tags=["source:arxiv"],
        ),
    }
    values.update(overrides)
    return RunContext(**values)  # type: ignore[arg-type]


def test_run_context_supports_prelaunch_initial_and_recovery_plans() -> None:
    initial = _minimal_run_context()
    recovery = _minimal_run_context(
        context_id="ctx-002",
        attempt_index=1,
    )

    assert initial.schema_version == "find.run_context.v1"
    assert recovery.attempt_index == 1
    payload = initial.to_dict()
    assert not {"run_id", "root_run_id", "run_dir", "progress_path", "result_path", "source_status_path", "manifest_path"} & set(payload)
    assert not {
        "topic_fingerprint",
        "config_fingerprint",
        "selection_fingerprint",
    } & set(payload)


def test_run_context_keeps_distinct_find_input_snapshot_paths() -> None:
    context = _minimal_run_context(
        config_snapshot_path="/tmp/find/find.config.json",
        input_snapshot_path="/tmp/find/input.json",
        selection_snapshot_path="/tmp/find/selection.json",
    )

    assert context.config_snapshot_path == "/tmp/find/find.config.json"
    assert context.input_snapshot_path == "/tmp/find/input.json"
    assert context.selection_snapshot_path == "/tmp/find/selection.json"
    assert RunContext.from_json(context.to_json()).input_snapshot_path == "/tmp/find/input.json"


@pytest.mark.parametrize("input_snapshot_path", ["", "   "])
def test_run_context_rejects_empty_input_snapshot_path(input_snapshot_path: str) -> None:
    with pytest.raises(ValueError, match=r"RunContext\.input_snapshot_path"):
        _minimal_run_context(input_snapshot_path=input_snapshot_path)


def test_run_context_requires_input_snapshot_path() -> None:
    values = _minimal_run_context().to_dict()
    values.pop("input_snapshot_path")

    with pytest.raises(TypeError, match="input_snapshot_path"):
        RunContext(**values)  # type: ignore[arg-type]


def test_run_context_allows_projectless_requests() -> None:
    context = _minimal_run_context(
        project_id=None,
        experience_query=ExperienceQuery(limit=10, project_id=None),
    )

    assert context.project_id is None
    assert context.experience_query.project_id is None


def test_run_context_rejects_cross_field_violations() -> None:
    with pytest.raises(ValueError, match=r"RunContext\.project_id"):
        _minimal_run_context(project_id=" ")

    with pytest.raises(ValueError, match=r"RunContext\.experience_query"):
        _minimal_run_context(experience_query={"limit": 10})

    with pytest.raises(ValueError, match=r"RunContext\.created_at"):
        _minimal_run_context(created_at=datetime(2026, 8, 29, 12, 0))

    with pytest.raises(ValueError, match=r"RunContext\.requested_parameters\.token"):
        _minimal_run_context(requested_parameters={"token": "secret"})

    with pytest.raises(ValueError, match=r"RunContext\.requested_parameters\.unsupported"):
        _minimal_run_context(requested_parameters={"unsupported": object()})

    with pytest.raises(ValueError, match=r"RunContext\.stall_confirm_seconds"):
        _minimal_run_context(stall_suspect_seconds=120, stall_confirm_seconds=60)

    with pytest.raises(ValueError, match=r"RunContext\.recovery_budget"):
        _minimal_run_context(recovery_budget=-1)

    unmatched = ExperienceRef(case_id="not-matched", verified=True, case_type="technical")
    with pytest.raises(ValueError, match=r"RunContext\.applied_experience_refs\[0\]"):
        _minimal_run_context(applied_experience_refs=[unmatched])

    payload = _minimal_run_context().to_dict()
    payload["topic_fingerprint"] = "legacy-topic"
    with pytest.raises(ValueError, match=r"RunContext\.topic_fingerprint is unknown"):
        RunContext.from_dict(payload)

    payload = _minimal_run_context().to_dict()
    payload["unexpected"] = True
    with pytest.raises(ValueError, match=r"RunContext\.unexpected is unknown"):
        RunContext.from_dict(payload)


def test_run_context_round_trip_restores_nested_contracts() -> None:
    experience = ExperienceRef(
        case_id="technical-001",
        verified=True,
        case_type="technical",
        similarity=0.9,
    )
    parameter_change = ParameterChange(
        name="title_llm_workers",
        before={"workers": 10},
        after={"workers": 1},
        reason="Reduce request pressure",
        source_case_id=experience.case_id,
    )
    original = _minimal_run_context(
        parameter_changes=[parameter_change],
        matched_experience_refs=[experience],
        applied_experience_refs=[experience],
        experience_parameter_changes=[parameter_change],
    )

    restored = RunContext.from_json(original.to_json())

    assert restored == original
    assert isinstance(restored.expected_artifacts[0], ArtifactRef)
    assert isinstance(restored.matched_experience_refs[0], ExperienceRef)
    assert isinstance(restored.parameter_changes[0], ParameterChange)
    assert isinstance(restored.experience_query, ExperienceQuery)
    assert restored.experience_query.required_context_tags == ["source:arxiv"]
    assert restored.approval_risk_threshold is RiskLevel.MEDIUM
    assert restored.allowed_recovery_actions == [RecoveryAction.RETRY_NEW_RUN]


def _minimal_find_stage_request(**overrides: object) -> FindStageRequest:
    values: dict[str, object] = {
        "request_source": "web",
        "research_topic": "Reliable research agents",
        "selection": {"venue_ids": ["ICLR"], "years": [2026]},
        "config_path": "/workspace/tmp/finding/input/find.config.json",
        "requested_parameters": {"minimum_recommendations": 5},
        "working_directory": "/workspace",
    }
    values.update(overrides)
    return FindStageRequest(**values)  # type: ignore[arg-type]


def test_find_stage_request_supports_minimal_framework_request() -> None:
    request = _minimal_find_stage_request()

    assert request.project_id is None
    assert request.schema_version == "find.stage_request.v1"
    assert request.stage == "find"


def test_find_stage_request_supports_framework_metadata_and_controls() -> None:
    original = _minimal_find_stage_request(
        project_id="research-agent",
        force_new_find=True,
        restart_full_cycle=True,
        human_approved_new_find=True,
        approval_reason="Refresh the literature after the research scope changed.",
    )

    restored = FindStageRequest.from_json(original.to_json())

    assert restored == original
    assert restored.project_id == "research-agent"
    assert restored.force_new_find is True
    assert restored.restart_full_cycle is True
    assert restored.human_approved_new_find is True


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("schema_version", "find.stage_request.v2"),
        ("stage", "read"),
        ("project_id", " "),
        ("approval_reason", " "),
    ],
)
def test_find_stage_request_rejects_invalid_fixed_or_string_fields(
    field_name: str,
    value: object,
) -> None:
    with pytest.raises(ValueError, match=rf"FindStageRequest\.{field_name}"):
        _minimal_find_stage_request(**{field_name: value})


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("selection", ["ICLR"]),
        ("selection", {1: "ICLR"}),
        ("selection", {"venue_ids": object()}),
        ("requested_parameters", ["minimum_recommendations"]),
        ("requested_parameters", {1: 5}),
        ("requested_parameters", {"minimum_recommendations": object()}),
    ],
)
def test_find_stage_request_rejects_invalid_mappings(field_name: str, value: object) -> None:
    with pytest.raises(ValueError, match=rf"FindStageRequest\.{field_name}"):
        _minimal_find_stage_request(**{field_name: value})


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("selection", {"credentials": {"api_key": "secret"}}),
        ("requested_parameters", {"token": "secret"}),
        ("requested_parameters", {"nested": {"authorization": "Bearer secret"}}),
    ],
)
def test_find_stage_request_rejects_sensitive_mappings(field_name: str, value: object) -> None:
    with pytest.raises(ValueError, match=rf"FindStageRequest\.{field_name}"):
        _minimal_find_stage_request(**{field_name: value})


def test_find_stage_request_keeps_the_existing_sensitive_key_set() -> None:
    request = _minimal_find_stage_request(
        selection={"notes": {"password": "not-a-project-api-key"}},
        requested_parameters={"secret": "ordinary-request-label"},
    )

    assert request.selection["notes"] == {"password": "not-a-project-api-key"}
    assert request.requested_parameters == {"secret": "ordinary-request-label"}


def test_find_stage_request_copies_input_mappings() -> None:
    selection = {
        "venue_ids": ["ICLR"],
        "filters": {"years": [2026]},
    }
    requested_parameters = {
        "limits": {"minimum_recommendations": 5},
        "sources": ["arxiv"],
    }
    request = _minimal_find_stage_request(
        selection=selection,
        requested_parameters=requested_parameters,
    )

    selection["venue_ids"].append("NeurIPS")
    selection["filters"]["years"].append(2025)
    requested_parameters["limits"]["minimum_recommendations"] = 99
    requested_parameters["sources"].append("biorxiv")

    assert request.selection == {
        "venue_ids": ["ICLR"],
        "filters": {"years": [2026]},
    }
    assert request.requested_parameters == {
        "limits": {"minimum_recommendations": 5},
        "sources": ["arxiv"],
    }


def test_run_context_copies_nested_mapping_inputs() -> None:
    selection = {"venues": ["ICLR"], "filters": {"years": [2026]}}
    requested_parameters = {"limits": {"minimum_recommendations": 5}}
    context = _minimal_run_context(
        selection=selection,
        requested_parameters=requested_parameters,
    )

    selection["venues"].append("NeurIPS")
    selection["filters"]["years"].append(2025)
    requested_parameters["limits"]["minimum_recommendations"] = 99

    assert context.selection == {"venues": ["ICLR"], "filters": {"years": [2026]}}
    assert context.requested_parameters == {"limits": {"minimum_recommendations": 5}}


def test_find_stage_request_rejects_unknown_json_fields_and_excludes_execution_identity() -> None:
    payload = _minimal_find_stage_request().to_dict()
    assert not {
        "producer",
        "producer_version",
        "requested_at",
        "output_root",
        "feedback_enabled",
        "framework_job_id",
        "request_id",
        "run_id",
        "root_run_id",
        "run_dir",
        "context_id",
        "supervisor_id",
        "launch_plan_id",
        "pid",
        "exit_code",
    } & set(payload)

    payload["unexpected"] = True
    with pytest.raises(ValueError, match=r"FindStageRequest\.unexpected is unknown"):
        FindStageRequest.from_dict(payload)


def _minimal_experience_query(**overrides: object) -> ExperienceQuery:
    values: dict[str, object] = {"limit": 10}
    values.update(overrides)
    return ExperienceQuery(**values)  # type: ignore[arg-type]


def test_experience_query_supports_minimal_before_stage_filters() -> None:
    query = _minimal_experience_query()

    assert query.schema_version == "find.experience_query.v1"
    assert query.stage == "find"
    assert query.project_id is None
    assert query.case_types == []
    assert query.outcomes == []
    assert query.required_context_tags == []
    assert query.verified_only is True
    assert query.include_deprecated is False


def test_experience_query_supports_complete_fixed_filters_and_round_trip() -> None:
    original = _minimal_experience_query(
        limit=25,
        project_id="research-agent",
        case_types=["normal", "technical"],
        outcomes=["success", "recovered"],
        required_context_tags=["source:arxiv", "venue:iclr"],
        verified_only=False,
        include_deprecated=True,
    )

    restored = ExperienceQuery.from_json(original.to_json())

    assert restored == original
    assert ExperienceQuery is contracts.ExperienceQuery


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("schema_version", "find.experience_query.v2"),
        ("stage", "read"),
        ("limit", 0),
        ("limit", 101),
        ("limit", True),
        ("limit", 1.5),
        ("project_id", " "),
        ("verified_only", "true"),
        ("include_deprecated", 0),
    ],
)
def test_experience_query_rejects_invalid_scalar_fields(field_name: str, value: object) -> None:
    with pytest.raises(ValueError, match=rf"ExperienceQuery\.{field_name}"):
        _minimal_experience_query(**{field_name: value})


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("case_types", "normal"),
        ("case_types", [1]),
        ("case_types", ["unsupported"]),
        ("outcomes", "success"),
        ("outcomes", [1]),
        ("outcomes", ["unsupported"]),
        ("required_context_tags", "source:arxiv"),
        ("required_context_tags", [1]),
        ("required_context_tags", [" "]),
    ],
)
def test_experience_query_rejects_invalid_list_filters(field_name: str, value: object) -> None:
    with pytest.raises(ValueError, match=rf"ExperienceQuery\.{field_name}"):
        _minimal_experience_query(**{field_name: value})


def test_experience_query_copies_input_lists_and_allows_empty_filters() -> None:
    case_types = ["normal"]
    outcomes = ["success"]
    required_context_tags = ["source:arxiv"]
    query = _minimal_experience_query(
        case_types=case_types,
        outcomes=outcomes,
        required_context_tags=required_context_tags,
    )

    case_types.append("technical")
    outcomes.append("recovered")
    required_context_tags.append("venue:iclr")

    assert query.case_types == ["normal"]
    assert query.outcomes == ["success"]
    assert query.required_context_tags == ["source:arxiv"]
    assert _minimal_experience_query(case_types=[], outcomes=[]).case_types == []
    assert _minimal_experience_query(case_types=[], outcomes=[]).outcomes == []


def test_experience_query_rejects_unknown_fields_and_excludes_future_filters() -> None:
    payload = _minimal_experience_query().to_dict()
    assert not {
        "query_id",
        "purpose",
        "created_at",
        "producer",
        "producer_version",
        "topic_fingerprint",
        "config_fingerprint",
        "selection_fingerprint",
        "environment_fingerprint",
        "anomaly_kinds",
        "recovery_actions",
        "max_risk_level",
        "run_id",
        "root_run_id",
    } & set(payload)

    payload["unexpected"] = True
    with pytest.raises(ValueError, match=r"ExperienceQuery\.unexpected is unknown"):
        ExperienceQuery.from_dict(payload)


def _minimal_recovery_experience_query(
    **overrides: object,
) -> RecoveryExperienceQuery:
    values: dict[str, object] = {
        "anomaly_kind": "progress_stalled",
        "limit": 10,
    }
    values.update(overrides)
    return RecoveryExperienceQuery(**values)  # type: ignore[arg-type]


def test_recovery_experience_query_supports_minimal_recovery_filters() -> None:
    query = _minimal_recovery_experience_query()

    assert query.schema_version == "find.recovery_experience_query.v1"
    assert query.stage == "find"
    assert query.anomaly_kind == "progress_stalled"
    assert query.limit == 10
    assert query.project_id is None
    assert query.environment_fingerprint is None
    assert query.required_context_tags == []
    assert query.verified_only is True
    assert query.include_deprecated is False


def test_recovery_experience_query_round_trips_complete_contract() -> None:
    original = _minimal_recovery_experience_query(
        anomaly_kind="result_unparseable",
        limit=25,
        project_id="research-agent",
        environment_fingerprint="environment-sha256",
        required_context_tags=["source:arxiv", "model:configured"],
    )

    restored = RecoveryExperienceQuery.from_json(original.to_json())

    assert restored == original
    assert RecoveryExperienceQuery is contracts.RecoveryExperienceQuery
    assert feedback.RecoveryExperienceQuery is RecoveryExperienceQuery
    assert "RecoveryExperienceQuery" in feedback.__all__


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("schema_version", "find.recovery_experience_query.v2"),
        ("stage", "read"),
        ("anomaly_kind", ""),
        ("anomaly_kind", "unsupported"),
        ("anomaly_kind", 1),
        ("limit", 0),
        ("limit", 101),
        ("limit", True),
        ("limit", 1.5),
        ("project_id", " "),
        ("environment_fingerprint", " "),
        ("verified_only", False),
        ("verified_only", "true"),
        ("verified_only", 1),
        ("include_deprecated", True),
        ("include_deprecated", "false"),
        ("include_deprecated", 0),
    ],
)
def test_recovery_experience_query_rejects_invalid_scalar_fields(
    field_name: str,
    value: object,
) -> None:
    with pytest.raises(
        ValueError,
        match=rf"RecoveryExperienceQuery\.{field_name}",
    ):
        _minimal_recovery_experience_query(**{field_name: value})


@pytest.mark.parametrize(
    "value",
    ["source:arxiv", [1], [" "]],
)
def test_recovery_experience_query_rejects_invalid_context_tags(
    value: object,
) -> None:
    with pytest.raises(
        ValueError,
        match=r"RecoveryExperienceQuery\.required_context_tags",
    ):
        _minimal_recovery_experience_query(required_context_tags=value)


def test_recovery_experience_query_copies_context_tags() -> None:
    required_context_tags = ["source:arxiv"]

    query = _minimal_recovery_experience_query(
        required_context_tags=required_context_tags,
    )
    required_context_tags.append("model:configured")

    assert query.required_context_tags == ["source:arxiv"]


def test_recovery_experience_query_rejects_unknown_and_unowned_filters() -> None:
    payload = _minimal_recovery_experience_query().to_dict()
    assert not {
        "outcomes",
        "recovery_actions",
        "validation_after_status",
        "ready_for_read_after",
        "successful_application_count",
    } & set(payload)

    payload["recovery_action"] = RecoveryAction.RETRY_NEW_RUN.value
    with pytest.raises(
        ValueError,
        match=r"RecoveryExperienceQuery\.recovery_action is unknown",
    ):
        RecoveryExperienceQuery.from_dict(payload)


def _minimal_recovery_proposal(**overrides: object) -> RecoveryProposal:
    values: dict[str, object] = {
        "proposal_id": "proposal-001",
        "context_id": "ctx-001",
        "run_id": "find-001",
        "anomaly_id": "anomaly-001",
        "created_at": datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc),
        "proposed_action": RecoveryAction.NO_ACTION,
        "reason": "No safe recovery change is indicated",
        "confidence": 0.8,
        "risk_level": RiskLevel.LOW,
    }
    values.update(overrides)
    return RecoveryProposal(**values)  # type: ignore[arg-type]


def test_recovery_proposal_minimal_defaults_and_json_round_trip() -> None:
    original = _minimal_recovery_proposal(
        evidence_refs=[EvidenceRef(kind="validation", summary="Validation blocked")],
    )

    restored = RecoveryProposal.from_json(original.to_json())

    assert restored == original
    assert restored.schema_version == "find.recovery_proposal.v1"
    assert restored.parameter_changes == {}
    assert restored.target_sources == []
    assert isinstance(restored.evidence_refs[0], EvidenceRef)
    assert feedback.RecoveryProposal is RecoveryProposal
    assert "RecoveryProposal" in feedback.__all__


def test_recovery_proposal_rejects_other_schema_versions() -> None:
    with pytest.raises(ValueError, match=r"RecoveryProposal\.schema_version"):
        _minimal_recovery_proposal(schema_version="find.recovery_proposal.v2")


@pytest.mark.parametrize("field_name", ["proposal_id", "context_id", "anomaly_id", "reason"])
def test_recovery_proposal_rejects_blank_required_identity_and_reason(
    field_name: str,
) -> None:
    with pytest.raises(ValueError, match=rf"RecoveryProposal\.{field_name}"):
        _minimal_recovery_proposal(**{field_name: " "})


def test_recovery_proposal_preserves_upstream_run_id_string_semantics() -> None:
    assert _minimal_recovery_proposal(run_id="").run_id == ""
    with pytest.raises(ValueError, match=r"RecoveryProposal\.run_id"):
        _minimal_recovery_proposal(run_id=None)


@pytest.mark.parametrize("confidence", [0, 1, 0.5])
def test_recovery_proposal_accepts_bounded_finite_confidence(
    confidence: float,
) -> None:
    assert _minimal_recovery_proposal(confidence=confidence).confidence == confidence


@pytest.mark.parametrize("confidence", [-0.1, 1.1, True, float("nan"), float("inf")])
def test_recovery_proposal_rejects_invalid_confidence(confidence: object) -> None:
    with pytest.raises(ValueError, match=r"RecoveryProposal\.confidence"):
        _minimal_recovery_proposal(confidence=confidence)


@pytest.mark.parametrize("risk_level", ["low", "medium", "high", None])
def test_recovery_proposal_requires_existing_risk_level_enum(
    risk_level: object,
) -> None:
    with pytest.raises(ValueError, match=r"RecoveryProposal\.risk_level"):
        _minimal_recovery_proposal(risk_level=risk_level)


def test_recovery_proposal_rejects_request_approval_action() -> None:
    with pytest.raises(ValueError, match=r"RecoveryProposal\.proposed_action"):
        _minimal_recovery_proposal(proposed_action=RecoveryAction.REQUEST_APPROVAL)


def test_recovery_proposal_parameter_change_payload_rules() -> None:
    proposal = _minimal_recovery_proposal(
        proposed_action=RecoveryAction.RETRY_WITH_PARAMETER_CHANGE,
        parameter_changes={"abstract_scoring_max_workers": 1},
    )
    assert proposal.parameter_changes == {"abstract_scoring_max_workers": 1}

    with pytest.raises(ValueError, match=r"RecoveryProposal\.parameter_changes"):
        _minimal_recovery_proposal(
            proposed_action=RecoveryAction.RETRY_WITH_PARAMETER_CHANGE,
        )
    with pytest.raises(ValueError, match=r"RecoveryProposal\.target_sources"):
        _minimal_recovery_proposal(
            proposed_action=RecoveryAction.RETRY_WITH_PARAMETER_CHANGE,
            parameter_changes={"workers": 1},
            target_sources=["semantic_scholar"],
        )


def test_recovery_proposal_skip_source_payload_rules() -> None:
    proposal = _minimal_recovery_proposal(
        proposed_action=RecoveryAction.SKIP_OPTIONAL_SOURCE,
        target_sources=["semantic_scholar"],
    )
    assert proposal.target_sources == ["semantic_scholar"]

    with pytest.raises(ValueError, match=r"RecoveryProposal\.target_sources"):
        _minimal_recovery_proposal(proposed_action=RecoveryAction.SKIP_OPTIONAL_SOURCE)
    with pytest.raises(ValueError, match=r"RecoveryProposal\.parameter_changes"):
        _minimal_recovery_proposal(
            proposed_action=RecoveryAction.SKIP_OPTIONAL_SOURCE,
            parameter_changes={"workers": 1},
            target_sources=["semantic_scholar"],
        )


@pytest.mark.parametrize(
    "action",
    [
        RecoveryAction.NO_ACTION,
        RecoveryAction.RETRY_NEW_RUN,
        RecoveryAction.STOP_AND_REPORT,
    ],
)
def test_recovery_proposal_other_actions_reject_action_payloads(
    action: RecoveryAction,
) -> None:
    with pytest.raises(ValueError, match=r"RecoveryProposal\.parameter_changes"):
        _minimal_recovery_proposal(
            proposed_action=action,
            parameter_changes={"workers": 1},
        )
    with pytest.raises(ValueError, match=r"RecoveryProposal\.target_sources"):
        _minimal_recovery_proposal(
            proposed_action=action,
            target_sources=["semantic_scholar"],
        )


@pytest.mark.parametrize(
    "parameter_changes",
    [{"": 1}, {1: "invalid"}, {"workers": object()}, "not-a-mapping"],
)
def test_recovery_proposal_validates_parameter_change_keys_and_json_values(
    parameter_changes: object,
) -> None:
    with pytest.raises(ValueError, match=r"RecoveryProposal\.parameter_changes"):
        _minimal_recovery_proposal(
            proposed_action=RecoveryAction.RETRY_WITH_PARAMETER_CHANGE,
            parameter_changes=parameter_changes,
        )


@pytest.mark.parametrize("target_sources", [[""], [" "], [1], "arxiv"])
def test_recovery_proposal_validates_target_sources(target_sources: object) -> None:
    with pytest.raises(ValueError, match=r"RecoveryProposal\.target_sources"):
        _minimal_recovery_proposal(
            proposed_action=RecoveryAction.SKIP_OPTIONAL_SOURCE,
            target_sources=target_sources,
        )


def test_recovery_proposal_requires_evidence_refs_and_copies_mutable_inputs() -> None:
    nested = {"runtime_tuning": {"workers": 1}}
    sources = ["semantic_scholar"]
    evidence = [EvidenceRef(kind="log", summary="Source failed")]
    retry = _minimal_recovery_proposal(
        proposed_action=RecoveryAction.RETRY_WITH_PARAMETER_CHANGE,
        parameter_changes=nested,
        evidence_refs=evidence,
    )
    skip = _minimal_recovery_proposal(
        proposed_action=RecoveryAction.SKIP_OPTIONAL_SOURCE,
        target_sources=sources,
    )

    nested["runtime_tuning"]["workers"] = 9  # type: ignore[index]
    sources.append("openalex")
    evidence.clear()

    assert retry.parameter_changes == {"runtime_tuning": {"workers": 1}}
    assert len(retry.evidence_refs) == 1
    assert skip.target_sources == ["semantic_scholar"]
    with pytest.raises(ValueError, match=r"RecoveryProposal\.evidence_refs\[0\]"):
        _minimal_recovery_proposal(evidence_refs=[object()])


@pytest.mark.parametrize(
    "field_name",
    [
        "executable",
        "requires_approval",
        "approved",
        "approval_status",
        "budget_consumed",
        "pid",
        "shell_command",
        "command",
        "source_patch",
        "source_code",
        "long_term_config_path",
    ],
)
def test_recovery_proposal_rejects_unowned_fields(field_name: str) -> None:
    payload = _minimal_recovery_proposal().to_dict()
    payload[field_name] = "not-owned"

    with pytest.raises(ValueError, match=rf"RecoveryProposal\.{field_name} is unknown"):
        RecoveryProposal.from_dict(payload)


def _minimal_execution_handle(**overrides: object) -> ExecutionHandle:
    values: dict[str, object] = {
        "context_id": "ctx-001",
        "pid": 6102,
        "started_at": datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
        "process_alive": True,
        "stdout_path": "/runtime/logs/find.stdout.log",
        "stderr_path": "/runtime/logs/find.stderr.log",
    }
    values.update(overrides)
    return ExecutionHandle(**values)  # type: ignore[arg-type]


def test_execution_handle_supports_running_and_discovered_run_states() -> None:
    running = _minimal_execution_handle()
    discovered = _minimal_execution_handle(
        run_id="find_20260901_120000_000001",
        run_dir="/workspace/.runtime/runs/find_20260901_120000_000001",
    )

    assert running.process_alive is True
    assert running.exit_code is None
    assert running.run_id is None
    assert running.run_dir is None
    assert discovered.run_id == "find_20260901_120000_000001"


@pytest.mark.parametrize("exit_code", [0, 1, None])
def test_execution_handle_supports_nonrunning_process_facts(exit_code: int | None) -> None:
    handle = _minimal_execution_handle(process_alive=False, exit_code=exit_code)

    assert handle.process_alive is False
    assert handle.exit_code == exit_code


def test_execution_handle_json_round_trip_and_run_context_reference() -> None:
    context = _minimal_run_context()
    original = _minimal_execution_handle(
        context_id=context.context_id,
        process_alive=False,
        exit_code=0,
        run_id="find_20260901_120000_000001",
        run_dir="/workspace/.runtime/runs/find_20260901_120000_000001",
    )

    restored = ExecutionHandle.from_json(original.to_json())

    assert restored == original
    assert restored.context_id == context.context_id
    assert isinstance(restored.started_at, datetime)
    assert restored.started_at.tzinfo is not None
    assert type(restored.pid) is int
    assert type(restored.process_alive) is bool


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("schema_version", "find.execution_handle.v2"),
        ("stage", "read"),
        ("context_id", " "),
        ("pid", 0),
        ("pid", -1),
        ("pid", True),
        ("pid", "6102"),
        ("process_alive", 1),
        ("process_alive", "true"),
        ("stdout_path", " "),
        ("stderr_path", ""),
        ("exit_code", True),
        ("exit_code", "0"),
        ("exit_code", 0.0),
    ],
)
def test_execution_handle_rejects_invalid_scalar_fields(field_name: str, value: object) -> None:
    with pytest.raises(ValueError, match=rf"ExecutionHandle\.{field_name}"):
        _minimal_execution_handle(**{field_name: value})


def test_execution_handle_rejects_invalid_time_run_identity_and_live_exit_code() -> None:
    with pytest.raises(ValueError, match=r"ExecutionHandle\.started_at"):
        _minimal_execution_handle(started_at=datetime(2026, 9, 1, 12, 0))
    with pytest.raises(ValueError, match=r"ExecutionHandle\.run_id"):
        _minimal_execution_handle(run_id="find_20260901_120000_000001")
    with pytest.raises(ValueError, match=r"ExecutionHandle\.run_id"):
        _minimal_execution_handle(run_dir="/workspace/.runtime/runs/find_20260901_120000_000001")
    with pytest.raises(ValueError, match=r"ExecutionHandle\.run_id"):
        _minimal_execution_handle(run_id=" ", run_dir="/workspace/.runtime/runs/find")
    with pytest.raises(ValueError, match=r"ExecutionHandle\.run_dir"):
        _minimal_execution_handle(run_id="find_20260901_120000_000001", run_dir=" ")
    with pytest.raises(ValueError, match=r"ExecutionHandle\.exit_code"):
        _minimal_execution_handle(exit_code=0)


def test_execution_handle_rejects_unknown_json_fields_and_excludes_extra_data() -> None:
    payload = _minimal_execution_handle().to_dict()
    assert not {
        "execution_id",
        "handle_id",
        "request_id",
        "project_id",
        "root_run_id",
        "active_run_id",
        "attempt_index",
        "created_at",
        "finished_at",
        "working_directory",
        "command_redacted",
        "producer",
        "producer_version",
        "launch_error_code",
        "launch_error_message",
        "command_fingerprint",
        "status",
        "research_topic",
        "selection",
        "requested_parameters",
        "effective_parameters",
        "progress",
        "validation",
        "recovery",
        "experience_cases",
    } & set(payload)

    payload["unexpected"] = True
    with pytest.raises(ValueError, match=r"ExecutionHandle\.unexpected is unknown"):
        ExecutionHandle.from_dict(payload)


def _minimal_progress_snapshot(**overrides: object) -> ProgressSnapshot:
    observed_at = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
    values: dict[str, object] = {
        "snapshot_id": "snapshot-001",
        "run_id": "find_20260829_120000_000001",
        "created_at": observed_at,
        "observed_at": observed_at,
        "sequence": 0,
        "producer": "progress-observer",
        "producer_version": "1.0",
        "status": ProgressStatus.STARTING,
        "phase": "initializing",
        "counts": {"titles": 0, "candidates": 0},
        "elapsed_seconds": 0.0,
        "seconds_without_progress": 0.0,
        "process_alive": True,
        "cancel_requested": False,
        "artifact_observations": [
            ArtifactRef(
                role="progress",
                path="/runs/find_20260829_120000_000001/logs/find_progress.json",
                required=True,
                exists=True,
                size_bytes=42,
            )
        ],
        "progress_parse_ok": True,
        "result_exists": False,
        "source_status_exists": False,
        "source_total": 0,
        "source_ready": 0,
        "source_limited": 0,
        "source_failed": 0,
        "status_reason": "Process startup was observed",
    }
    values.update(overrides)
    return ProgressSnapshot(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("status", "phase", "current", "total", "percent"),
    [
        (ProgressStatus.STARTING, "initializing", None, None, 0),
        (ProgressStatus.RUNNING, "title_screening", 3, 10, 30),
        (ProgressStatus.SUSPECTED_STALL, "source_fetch", 3, 10, 30),
        (ProgressStatus.COMPLETED, "completed", 10, 10, 100),
    ],
)
def test_progress_snapshot_supports_observed_statuses(
    status: ProgressStatus,
    phase: str,
    current: int | None,
    total: int | None,
    percent: int | None,
) -> None:
    snapshot = _minimal_progress_snapshot(
        status=status,
        phase=phase,
        current=current,
        total=total,
        percent=percent,
        status_reason=f"Observed {status.value}",
    )

    assert snapshot.status is status
    assert snapshot.phase == phase


def test_progress_snapshot_rejects_invalid_measurements() -> None:
    with pytest.raises(ValueError, match=r"ProgressSnapshot\.current"):
        _minimal_progress_snapshot(current=2, total=1)

    with pytest.raises(ValueError, match=r"ProgressSnapshot\.percent"):
        _minimal_progress_snapshot(percent=101)

    with pytest.raises(ValueError, match=r"ProgressSnapshot\.counts\.titles"):
        _minimal_progress_snapshot(counts={"titles": -1})

    with pytest.raises(ValueError, match=r"ProgressSnapshot\.pid"):
        _minimal_progress_snapshot(pid=0)

    with pytest.raises(ValueError, match=r"ProgressSnapshot\.elapsed_seconds"):
        _minimal_progress_snapshot(elapsed_seconds=-0.1)


def test_progress_snapshot_round_trip_restores_artifacts_and_evidence() -> None:
    evidence = EvidenceRef(
        kind="snapshot",
        path="logs/find_progress.json",
        summary="Progress JSON was parsed",
    )
    original = _minimal_progress_snapshot(
        status=ProgressStatus.RUNNING,
        phase="title_screening",
        current=3,
        total=10,
        percent=30,
        evidence_refs=[evidence],
        source_signals=["ICLR:ready"],
        signals=["progress_advanced"],
        observation_errors=["NeurIPS source status unavailable"],
    )

    restored = ProgressSnapshot.from_json(original.to_json())

    assert restored == original
    assert isinstance(restored.artifact_observations[0], ArtifactRef)
    assert isinstance(restored.evidence_refs[0], EvidenceRef)
    assert restored.status is ProgressStatus.RUNNING


def _validation_check(
    code: str,
    status: ValidationStatus = ValidationStatus.PASS,
    *,
    required: bool = True,
) -> ValidationCheck:
    return ValidationCheck(
        code=code,
        status=status,
        required=required,
        message=f"{code} is {status.value}",
    )


def _minimal_validation_result(**overrides: object) -> ValidationResult:
    validated_at = datetime(2026, 8, 29, 12, 5, tzinfo=timezone.utc)
    checks = [_validation_check("run_dir_exists")]
    values: dict[str, object] = {
        "validation_id": "validation-001",
        "run_id": "find_20260829_120000_000001",
        "created_at": validated_at,
        "validated_at": validated_at,
        "validated_run_dir": "/runs/find_20260829_120000_000001",
        "producer": "find-validator",
        "producer_version": "1.0",
        "policy_version": "find.validation.v1",
        "duration_ms": 25,
        "status": ValidationStatus.PASS,
        "ready_for_read": True,
        "summary": "Find output passed deterministic validation",
        "checks": checks,
        "passed_check_count": 1,
        "warning_check_count": 0,
        "blocked_check_count": 0,
        "recommendation_target_count": 5,
        "recommendation_actual_count": 5,
        "recommendation_shortfall": 0,
        "strong_recommendation_count": 3,
        "recommendation_quality_status": "ok",
        "candidate_ids": ["paper-001", "paper-002"],
        "candidate_digest": "candidate-digest",
        "bridge_probe_status": ValidationStatus.PASS,
        "input_artifact_refs": [
            ArtifactRef(
                role="result",
                path="/runs/find_20260829_120000_000001/final/find_results.json",
                required=True,
                exists=True,
            )
        ],
    }
    values.update(overrides)
    return ValidationResult(**values)  # type: ignore[arg-type]


def test_validation_result_supports_all_pass_and_non_blocking_warning() -> None:
    all_pass = _minimal_validation_result()
    warning = _minimal_validation_result(
        status=ValidationStatus.WARNING,
        ready_for_read=False,
        checks=[
            _validation_check("run_dir_exists"),
            _validation_check(
                "source_integrity_not_blocking",
                ValidationStatus.WARNING,
                required=False,
            ),
        ],
        passed_check_count=1,
        warning_check_count=1,
        warnings=["One optional source was unavailable"],
    )

    assert all_pass.ready_for_read is True
    assert warning.status is ValidationStatus.WARNING
    assert warning.warning_check_count == 1


def test_validation_result_rejects_blocking_and_count_inconsistencies() -> None:
    required_block = _validation_check("result_exists", ValidationStatus.BLOCK)
    with pytest.raises(ValueError, match=r"ValidationResult\.status"):
        _minimal_validation_result(
            checks=[required_block],
            status=ValidationStatus.PASS,
            ready_for_read=False,
            passed_check_count=0,
            blocked_check_count=1,
        )

    with pytest.raises(ValueError, match=r"ValidationResult\.ready_for_read"):
        _minimal_validation_result(blockers=["Missing required result"])

    with pytest.raises(ValueError, match=r"ValidationResult\.passed_check_count"):
        _minimal_validation_result(passed_check_count=0)


def test_validation_result_rejects_duplicate_candidate_ids() -> None:
    with pytest.raises(ValueError, match=r"ValidationResult\.candidate_ids"):
        _minimal_validation_result(candidate_ids=["paper-001", "paper-001"])


def test_validation_result_round_trip_restores_nested_contracts() -> None:
    evidence = EvidenceRef(
        kind="validation",
        path="final/find_results.json",
        summary="Result JSON parsed successfully",
    )
    original = _minimal_validation_result(
        checks=[
            ValidationCheck(
                code="result_parseable",
                status=ValidationStatus.PASS,
                required=True,
                message="Result JSON is parseable",
                evidence_refs=[evidence],
            )
        ],
        evidence_refs=[evidence],
        downstream_input_preview={"candidate_count": 2, "has_abstracts": True},
    )

    restored = ValidationResult.from_json(original.to_json())

    assert restored == original
    assert isinstance(restored.checks[0], ValidationCheck)
    assert isinstance(restored.evidence_refs[0], EvidenceRef)
    assert isinstance(restored.input_artifact_refs[0], ArtifactRef)
    assert restored.status is ValidationStatus.PASS


def _minimal_anomaly(**overrides: object) -> Anomaly:
    detected_at = datetime(2026, 8, 29, 12, 10, tzinfo=timezone.utc)
    values: dict[str, object] = {
        "anomaly_id": "anomaly-001",
        "run_id": "find_20260829_120000_000001",
        "created_at": detected_at,
        "detected_at": detected_at,
        "updated_at": detected_at,
        "producer": "anomaly-builder",
        "producer_version": "1.0",
        "stage": "find",
        "kind": "progress_stalled",
        "blocking": True,
        "confidence": 0.8,
        "detected_by": ["progress_observer"],
        "supervisor_state_revision": 3,
        "symptoms": ["No progress update for 120 seconds"],
        "evidence_refs": [
            EvidenceRef(
                kind="snapshot",
                path="logs/find_progress.json",
                summary="Progress remained at 30 percent",
            )
        ],
        "root_cause_status": "suspected",
        "affected_phase": "title_screening",
        "downstream_impact": "Read cannot start until Find completes",
        "partial_results_usable": False,
        "recovery_eligible": True,
        "retryable_signal": True,
        "fingerprint": "progress-stalled-title-screening",
        "occurrence_count": 1,
    }
    values.update(overrides)
    return Anomaly(**values)  # type: ignore[arg-type]


def test_anomaly_supports_suspected_and_confirmed_root_causes() -> None:
    suspected = _minimal_anomaly()
    confirmed = _minimal_anomaly(
        anomaly_id="anomaly-002",
        kind="result_missing",
        root_cause_status="confirmed",
        root_cause="Find completed without writing the required result artifact",
        evidence_refs=[
            EvidenceRef(
                kind="artifact",
                path="final/find_results.json",
                summary="Required result artifact was absent",
            )
        ],
    )

    assert suspected.root_cause_status == "suspected"
    assert confirmed.root_cause_status == "confirmed"


def test_anomaly_rejects_missing_evidence_and_invalid_measurements() -> None:
    with pytest.raises(ValueError, match=r"Anomaly\.symptoms"):
        _minimal_anomaly(symptoms=[])

    with pytest.raises(ValueError, match=r"Anomaly\.evidence_refs"):
        _minimal_anomaly(evidence_refs=[])

    with pytest.raises(ValueError, match=r"Anomaly\.confidence"):
        _minimal_anomaly(confidence=1.1)

    with pytest.raises(ValueError, match=r"Anomaly\.root_cause"):
        _minimal_anomaly(root_cause_status="confirmed")

    with pytest.raises(ValueError, match=r"Anomaly\.occurrence_count"):
        _minimal_anomaly(occurrence_count=0)


def test_anomaly_json_round_trip() -> None:
    original = _minimal_anomaly(
        process_facts={"pid": 1234, "alive": True},
        artifact_facts={"result_exists": False},
        timing_facts={"seconds_without_progress": 120.0},
        hypotheses=[{"cause": "provider timeout", "confidence": 0.5}],
        missing_evidence=["stderr tail"],
        affected_sources=["ICLR"],
        affected_artifacts=["logs/find_progress.json"],
    )

    restored = Anomaly.from_json(original.to_json())

    assert restored == original
    assert isinstance(restored.evidence_refs[0], EvidenceRef)
    assert restored.process_facts == {"pid": 1234, "alive": True}


def _minimal_recovery_decision(**overrides: object) -> RecoveryDecision:
    decided_at = datetime(2026, 8, 29, 12, 15, tzinfo=timezone.utc)
    values: dict[str, object] = {
        "decision_id": "decision-001",
        "run_id": "find_20260829_120000_000001",
        "anomaly_id": "anomaly-001",
        "created_at": decided_at,
        "decided_at": decided_at,
        "producer": "recovery-controller",
        "producer_version": "1.0",
        "action": RecoveryAction.NO_ACTION,
        "reason": "Record the anomaly without changing the run",
        "risk_level": RiskLevel.LOW,
        "executable": False,
        "new_run_required": False,
        "exploratory": False,
        "requires_approval": False,
        "approval_status": "not_required",
        "attempt_index": 1,
        "budget_before": 2,
        "budget_cost": 0,
        "budget_after": 2,
        "max_same_action_attempts": 2,
        "verification_policy": "find.validation.v1",
        "required_post_checks": ["result_exists"],
        "success_definition": "The decision was recorded",
        "stop_if_failed": True,
    }
    values.update(overrides)
    return RecoveryDecision(**values)  # type: ignore[arg-type]


def test_recovery_decision_supports_no_action_and_low_risk_execution() -> None:
    no_action = _minimal_recovery_decision()
    low_risk = _minimal_recovery_decision(
        decision_id="decision-002",
        action=RecoveryAction.RETRY_WITH_PARAMETER_CHANGE,
        reason="Retry with lower LLM concurrency",
        executable=True,
        new_run_required=True,
        budget_before=2,
        budget_cost=1,
        budget_after=1,
        proposed_new_run_id="find_20260829_121500_000002",
        parameter_changes=[
            ParameterChange(
                name="title_llm_workers",
                before=10,
                after=1,
                reason="Lower request pressure",
            )
        ],
        command_preview_redacted=["python", "modules/finding/main.py"],
    )

    assert no_action.action is RecoveryAction.NO_ACTION
    assert low_risk.executable is True
    assert low_risk.proposed_new_run_id.startswith("find_")


def test_recovery_decision_requires_high_risk_approval_before_execution() -> None:
    pending = _minimal_recovery_decision(
        risk_level=RiskLevel.HIGH,
        action=RecoveryAction.RETRY_NEW_RUN,
        executable=False,
        new_run_required=True,
        proposed_new_run_id="find_20260829_121600_000003",
        requires_approval=True,
        approval_status="pending",
    )
    approved = _minimal_recovery_decision(
        decision_id="decision-003",
        risk_level=RiskLevel.HIGH,
        action=RecoveryAction.RETRY_NEW_RUN,
        executable=True,
        new_run_required=True,
        proposed_new_run_id="find_20260829_121700_000004",
        requires_approval=True,
        approval_status="approved",
        approved_by="user-001",
        approved_at=datetime(2026, 8, 29, 12, 16, tzinfo=timezone.utc),
    )

    assert pending.executable is False
    assert approved.executable is True


def test_recovery_decision_rejects_invalid_recovery_controls() -> None:
    with pytest.raises(ValueError, match=r"RecoveryDecision\.proposed_new_run_id"):
        _minimal_recovery_decision(
            action=RecoveryAction.RETRY_NEW_RUN,
            executable=True,
            new_run_required=True,
        )

    with pytest.raises(ValueError, match=r"RecoveryDecision\.budget_after"):
        _minimal_recovery_decision(
            action=RecoveryAction.RETRY_NEW_RUN,
            new_run_required=True,
            budget_cost=1,
            budget_after=2,
        )

    with pytest.raises(ValueError, match=r"RecoveryDecision\.budget_cost"):
        _minimal_recovery_decision(
            action=RecoveryAction.RETRY_NEW_RUN,
            executable=True,
            new_run_required=True,
            proposed_new_run_id="find_20260829_121800_000005",
            budget_before=0,
            budget_cost=1,
            budget_after=0,
        )

    with pytest.raises(ValueError, match=r"RecoveryDecision\.parameter_changes"):
        _minimal_recovery_decision(
            action=RecoveryAction.STOP_AND_REPORT,
            parameter_changes=[
                ParameterChange(name="workers", after=1, reason="Should not be present")
            ],
        )


def test_recovery_decision_json_round_trip() -> None:
    experience = ExperienceRef(case_id="technical-001", verified=True, case_type="technical")
    evidence = EvidenceRef(
        kind="validation",
        summary="The same retry recovered a previous run",
    )
    original = _minimal_recovery_decision(
        action=RecoveryAction.RETRY_NEW_RUN,
        reason="Use a validated retry playbook",
        executable=True,
        new_run_required=True,
        budget_before=2,
        budget_cost=1,
        budget_after=1,
        proposed_new_run_id="find_20260829_121900_000006",
        target_sources=["ICLR"],
        action_parameters={"retry_delay_seconds": 5},
        preserve_artifacts=[
            ArtifactRef(role="progress", path="logs/find_progress.json", required=False)
        ],
        matched_playbook_id="playbook-001",
        matched_experience_refs=[experience],
        evidence_of_previous_success=[evidence],
        preconditions=["Budget available"],
    )

    restored = RecoveryDecision.from_json(original.to_json())

    assert restored == original
    assert isinstance(restored.preserve_artifacts[0], ArtifactRef)
    assert isinstance(restored.matched_experience_refs[0], ExperienceRef)
    assert isinstance(restored.evidence_of_previous_success[0], EvidenceRef)
    assert restored.action is RecoveryAction.RETRY_NEW_RUN


def _minimal_experience_case(**overrides: object) -> ExperienceCase:
    observed_at = datetime(2026, 8, 29, 12, 20, tzinfo=timezone.utc)
    values: dict[str, object] = {
        "case_id": "case-001",
        "case_type": "normal",
        "created_at": observed_at,
        "updated_at": observed_at,
        "producer": "experience-recorder",
        "producer_version": "1.0",
        "verified": True,
        "deprecated": False,
        "context_id": "ctx-001",
        "root_run_id": "find_20260829_120000_000001",
        "final_run_id": "find_20260829_120000_000001",
        "root_cause_status": "unknown",
        "evidence_refs": [
            EvidenceRef(kind="validation", summary="Validation result passed")
        ],
        "attempt_count": 0,
        "outcome": "success",
        "validation_after_id": "validation-001",
        "validation_after_status": ValidationStatus.PASS,
        "ready_for_read_after": True,
        "risk_level": RiskLevel.LOW,
        "confidence": 0.9,
        "matched_count": 0,
        "applied_count": 0,
        "successful_application_count": 0,
    }
    values.update(overrides)
    return ExperienceCase(**values)  # type: ignore[arg-type]


def test_experience_case_supports_normal_technical_and_preference_cases() -> None:
    normal = _minimal_experience_case()
    technical = _minimal_experience_case(
        case_id="case-technical-001",
        case_type="technical",
        root_cause_status="confirmed",
        root_cause="Source API timed out",
        anomaly_id="anomaly-001",
        anomaly_kind="progress_stalled",
        anomaly_fingerprint="progress-stalled-title-screening",
        decision_id="decision-001",
        recovery_action=RecoveryAction.RETRY_NEW_RUN,
        attempt_count=1,
        outcome="recovered",
        new_run_ids=["find_20260829_122000_000002"],
        matched_count=2,
        applied_count=1,
        successful_application_count=1,
    )
    preference = _minimal_experience_case(
        case_id="case-preference-001",
        case_type="preference",
        user_feedback="Prefer narrower and more novel recommendations",
        user_labels=["novelty"],
        preference_scope=["reinforcement_learning"],
    )

    assert normal.case_type == "normal"
    assert technical.outcome == "recovered"
    assert preference.case_type == "preference"


def test_experience_case_rejects_invalid_case_relationships() -> None:
    with pytest.raises(ValueError, match=r"ExperienceCase\.validation_after_status"):
        _minimal_experience_case(
            outcome="recovered",
            validation_after_status=ValidationStatus.WARNING,
        )

    with pytest.raises(ValueError, match=r"ExperienceCase\.anomaly_id"):
        _minimal_experience_case(case_type="technical")

    with pytest.raises(ValueError, match=r"ExperienceCase\.user_feedback"):
        _minimal_experience_case(case_type="preference")

    with pytest.raises(ValueError, match=r"ExperienceCase\.successful_application_count"):
        _minimal_experience_case(
            matched_count=1,
            applied_count=0,
            successful_application_count=1,
        )


def test_experience_case_json_round_trip() -> None:
    original = _minimal_experience_case(
        context_tags=["deepseek", "low-concurrency"],
        parameter_changes=[
            ParameterChange(
                name="title_llm_workers",
                before=10,
                after=1,
                reason="Lower request pressure",
            )
        ],
        approval_record={"status": "approved"},
        execution_started_at=datetime(2026, 8, 29, 12, 19, tzinfo=timezone.utc),
        execution_finished_at=datetime(2026, 8, 29, 12, 20, tzinfo=timezone.utc),
        duration_seconds=60.0,
        last_matched_at=datetime(2026, 8, 29, 12, 21, tzinfo=timezone.utc),
    )

    restored = ExperienceCase.from_json(original.to_json())

    assert restored == original
    assert isinstance(restored.evidence_refs[0], EvidenceRef)
    assert isinstance(restored.parameter_changes[0], ParameterChange)
    assert restored.validation_after_status is ValidationStatus.PASS


def _skip_optional_source_experience_case(**overrides: object) -> ExperienceCase:
    values: dict[str, object] = {
        "case_id": "case-skip-source-001",
        "case_type": "technical",
        "root_cause_status": "confirmed",
        "root_cause": "Optional metadata source is unavailable",
        "anomaly_id": "anomaly-skip-source-001",
        "anomaly_kind": "progress_stalled",
        "anomaly_fingerprint": "progress-stalled-optional-source",
        "decision_id": "decision-skip-source-001",
        "recovery_action": RecoveryAction.SKIP_OPTIONAL_SOURCE,
        "target_sources": ["semantic_scholar"],
        "attempt_count": 1,
        "outcome": "recovered",
        "matched_count": 1,
        "applied_count": 1,
        "successful_application_count": 1,
    }
    values.update(overrides)
    return _minimal_experience_case(**values)


def test_experience_case_target_sources_support_skip_and_json_round_trip() -> None:
    original = _skip_optional_source_experience_case(
        target_sources=["semantic_scholar", "openalex"]
    )

    restored = ExperienceCase.from_json(original.to_json())

    assert restored == original
    assert restored.target_sources == ["semantic_scholar", "openalex"]


def test_experience_case_target_sources_preserve_legacy_default_and_independence() -> None:
    first = _minimal_experience_case()
    second = _minimal_experience_case(case_id="case-002")
    payload = first.to_dict()
    payload.pop("target_sources")

    restored = ExperienceCase.from_dict(payload)
    first.target_sources.append("semantic_scholar")

    assert restored.target_sources == []
    assert second.target_sources == []


@pytest.mark.parametrize(
    "target_sources",
    ["semantic_scholar", ("semantic_scholar",), [""], [" "], [1]],
)
def test_experience_case_rejects_invalid_target_sources(target_sources: object) -> None:
    with pytest.raises(ValueError, match=r"ExperienceCase\.target_sources"):
        _skip_optional_source_experience_case(target_sources=target_sources)


def test_experience_case_rejects_duplicate_target_sources() -> None:
    with pytest.raises(ValueError, match=r"ExperienceCase\.target_sources"):
        _skip_optional_source_experience_case(
            target_sources=["semantic_scholar", "semantic_scholar"]
        )


def test_experience_case_skip_optional_source_requires_target_sources() -> None:
    with pytest.raises(ValueError, match=r"ExperienceCase\.target_sources"):
        _skip_optional_source_experience_case(target_sources=[])


@pytest.mark.parametrize(
    "recovery_action",
    [
        None,
        RecoveryAction.NO_ACTION,
        RecoveryAction.RETRY_NEW_RUN,
        RecoveryAction.RETRY_WITH_PARAMETER_CHANGE,
        RecoveryAction.REQUEST_APPROVAL,
        RecoveryAction.STOP_AND_REPORT,
    ],
)
def test_experience_case_other_actions_reject_target_sources(
    recovery_action: RecoveryAction | None,
) -> None:
    with pytest.raises(ValueError, match=r"ExperienceCase\.target_sources"):
        _minimal_experience_case(
            recovery_action=recovery_action,
            target_sources=["semantic_scholar"],
        )


def test_experience_case_copies_target_sources_input() -> None:
    target_sources = ["semantic_scholar"]

    case = _skip_optional_source_experience_case(target_sources=target_sources)
    target_sources.append("openalex")

    assert case.target_sources == ["semantic_scholar"]


def test_experience_case_rejects_blank_context_tags_and_legacy_fingerprints() -> None:
    with pytest.raises(ValueError, match=r"ExperienceCase\.context_tags\[0\]"):
        _minimal_experience_case(context_tags=[" "])

    payload = _minimal_experience_case().to_dict()
    assert not {
        "topic_fingerprint",
        "config_fingerprint",
        "selection_fingerprint",
    } & set(payload)
    payload["config_fingerprint"] = "legacy-config"
    with pytest.raises(ValueError, match=r"ExperienceCase\.config_fingerprint is unknown"):
        ExperienceCase.from_dict(payload)


def _minimal_supervisor_state(**overrides: object) -> SupervisorState:
    observed_at = datetime(2026, 8, 29, 12, 30, tzinfo=timezone.utc)
    values: dict[str, object] = {
        "supervisor_id": "supervisor-001",
        "project_id": "project-001",
        "root_run_id": None,
        "status": SupervisorStatus.IDLE,
        "state_revision": 0,
        "created_at": observed_at,
        "updated_at": observed_at,
        "heartbeat_at": observed_at,
        "producer": "find-supervisor",
        "producer_version": "1.0",
        "run_context_id": None,
        "process_alive": False,
        "cancel_requested": False,
        "recovery_attempts": 0,
        "recovery_budget_total": 2,
        "recovery_budget_remaining": 2,
        "awaiting_approval": False,
        "gate_evaluated": False,
        "allow_read": False,
        "gate_reason": "Supervisor is idle",
        "terminal": False,
        "event_sequence": 0,
        "state_path": "feedback/supervisor_state.json",
    }
    values.update(overrides)
    if (
        "root_run_id" not in overrides
        and values["status"]
        in {
            SupervisorStatus.RUNNING,
            SupervisorStatus.VALIDATING,
            SupervisorStatus.DIAGNOSING,
            SupervisorStatus.DECIDING,
            SupervisorStatus.AWAITING_APPROVAL,
            SupervisorStatus.RECOVERING,
            SupervisorStatus.GATING,
            SupervisorStatus.COMPLETED,
            SupervisorStatus.BLOCKED,
        }
    ):
        values["root_run_id"] = "find_20260829_120000_000001"
    return SupervisorState(**values)  # type: ignore[arg-type]


def test_supervisor_state_allows_projectless_control_flow() -> None:
    state = _minimal_supervisor_state(project_id=None)

    assert state.project_id is None
    assert SupervisorState.from_json(state.to_json()).project_id is None


def test_supervisor_state_rejects_blank_project_id() -> None:
    with pytest.raises(ValueError, match=r"SupervisorState\.project_id"):
        _minimal_supervisor_state(project_id=" ")


@pytest.mark.parametrize(
    "state_overrides",
    [
        {},
        {
            "status": SupervisorStatus.PREPARING,
            "gate_reason": "Preparing a Find run",
        },
        {
            "status": SupervisorStatus.RUNNING,
            "run_context_id": "ctx-001",
            "active_run_id": "find_20260829_120000_000001",
            "active_pid": 1234,
            "process_alive": True,
            "gate_reason": "Find is running",
        },
        {
            "status": SupervisorStatus.VALIDATING,
            "run_context_id": "ctx-001",
            "active_run_id": "find_20260829_120000_000001",
            "gate_reason": "Validator is checking Find outputs",
        },
        {
            "status": SupervisorStatus.DIAGNOSING,
            "run_context_id": "ctx-001",
            "latest_progress_snapshot_id": "snapshot-001",
            "gate_reason": "Diagnosing an unexpected progress state",
        },
        {
            "status": SupervisorStatus.DECIDING,
            "run_context_id": "ctx-001",
            "active_anomaly_id": "anomaly-001",
            "gate_reason": "Choosing a controlled recovery decision",
        },
        {
            "status": SupervisorStatus.AWAITING_APPROVAL,
            "run_context_id": "ctx-001",
            "active_anomaly_id": "anomaly-001",
            "active_recovery_decision_id": "decision-001",
            "awaiting_approval": True,
            "pending_approval_decision_id": "decision-001",
            "gate_reason": "Awaiting approval for a high-risk recovery",
        },
        {
            "status": SupervisorStatus.RECOVERING,
            "run_context_id": "ctx-001",
            "active_anomaly_id": "anomaly-001",
            "active_recovery_decision_id": "decision-001",
            "recovery_attempts": 1,
            "recovery_budget_remaining": 1,
            "gate_reason": "Applying an approved recovery decision",
        },
        {
            "status": SupervisorStatus.GATING,
            "run_context_id": "ctx-001",
            "latest_validation_id": "validation-001",
            "gate_reason": "Evaluating whether Read may begin",
        },
        {
            "status": SupervisorStatus.COMPLETED,
            "run_context_id": "ctx-001",
            "terminal": True,
            "final_status": SupervisorStatus.COMPLETED,
            "gate_evaluated": True,
            "allow_read": True,
            "gate_validation_id": "validation-001",
            "gate_reason": "Validation passed; Read is allowed",
        },
        {
            "status": SupervisorStatus.BLOCKED,
            "run_context_id": "ctx-001",
            "terminal": True,
            "final_status": SupervisorStatus.BLOCKED,
            "terminal_reason": "Required Find result is unavailable",
            "gate_reason": "Read is blocked",
        },
        {
            "status": SupervisorStatus.FAILED,
            "run_context_id": "ctx-001",
            "terminal": True,
            "final_status": SupervisorStatus.FAILED,
            "last_error": "The process exited unexpectedly",
            "gate_reason": "Find failed",
        },
        {
            "status": SupervisorStatus.CANCELLED,
            "run_context_id": "ctx-001",
            "terminal": True,
            "final_status": SupervisorStatus.CANCELLED,
            "cancel_requested": True,
            "gate_reason": "User cancelled the run",
        },
    ],
)
def test_supervisor_state_supports_every_declared_status(state_overrides: dict[str, object]) -> None:
    state = _minimal_supervisor_state(**state_overrides)

    assert isinstance(state.status, SupervisorStatus)


@pytest.mark.parametrize(
    "state_overrides",
    [
        {
            "status": SupervisorStatus.FAILED,
            "terminal": True,
            "final_status": SupervisorStatus.FAILED,
            "last_error": "Preparation failed before a RunContext was created.",
        },
        {
            "status": SupervisorStatus.CANCELLED,
            "terminal": True,
            "final_status": SupervisorStatus.CANCELLED,
            "cancel_requested": True,
            "gate_reason": "The user cancelled before preparation completed.",
        },
    ],
)
def test_supervisor_state_allows_early_terminal_states_without_run_context(
    state_overrides: dict[str, object],
) -> None:
    state = _minimal_supervisor_state(**state_overrides)

    assert state.run_context_id is None


def test_supervisor_state_rejects_invalid_cross_field_states() -> None:
    with pytest.raises(ValueError, match=r"SupervisorState\.run_context_id"):
        _minimal_supervisor_state(
            status=SupervisorStatus.RUNNING,
            active_run_id="find_20260829_120000_000001",
            process_alive=True,
        )

    with pytest.raises(ValueError, match=r"SupervisorState\.active_run_id"):
        _minimal_supervisor_state(
            status=SupervisorStatus.RUNNING,
            run_context_id="ctx-001",
            process_alive=True,
        )

    with pytest.raises(ValueError, match=r"SupervisorState\.process_alive"):
        _minimal_supervisor_state(
            status=SupervisorStatus.RUNNING,
            run_context_id="ctx-001",
            active_run_id="find_20260829_120000_000001",
        )

    with pytest.raises(ValueError, match=r"SupervisorState\.process_alive"):
        _minimal_supervisor_state(
            status=SupervisorStatus.VALIDATING,
            run_context_id="ctx-001",
            active_run_id="find_20260829_120000_000001",
            process_alive=True,
        )

    with pytest.raises(ValueError, match=r"SupervisorState\.latest_progress_snapshot_id"):
        _minimal_supervisor_state(
            status=SupervisorStatus.DIAGNOSING,
            run_context_id="ctx-001",
        )

    with pytest.raises(ValueError, match=r"SupervisorState\.active_anomaly_id"):
        _minimal_supervisor_state(
            status=SupervisorStatus.DECIDING,
            run_context_id="ctx-001",
        )

    with pytest.raises(ValueError, match=r"SupervisorState\.active_recovery_decision_id"):
        _minimal_supervisor_state(
            status=SupervisorStatus.AWAITING_APPROVAL,
            run_context_id="ctx-001",
            active_anomaly_id="anomaly-001",
            awaiting_approval=True,
            pending_approval_decision_id="decision-001",
        )

    with pytest.raises(ValueError, match=r"SupervisorState\.pending_approval_decision_id"):
        _minimal_supervisor_state(
            status=SupervisorStatus.AWAITING_APPROVAL,
            run_context_id="ctx-001",
            active_anomaly_id="anomaly-001",
            active_recovery_decision_id="decision-001",
            awaiting_approval=True,
        )

    with pytest.raises(ValueError, match=r"SupervisorState\.active_anomaly_id"):
        _minimal_supervisor_state(
            status=SupervisorStatus.RECOVERING,
            run_context_id="ctx-001",
            active_recovery_decision_id="decision-001",
            recovery_attempts=1,
            recovery_budget_remaining=1,
        )

    with pytest.raises(ValueError, match=r"SupervisorState\.active_recovery_decision_id"):
        _minimal_supervisor_state(
            status=SupervisorStatus.RECOVERING,
            run_context_id="ctx-001",
            active_anomaly_id="anomaly-001",
            recovery_attempts=1,
            recovery_budget_remaining=1,
        )

    with pytest.raises(ValueError, match=r"SupervisorState\.latest_validation_id"):
        _minimal_supervisor_state(
            status=SupervisorStatus.GATING,
            run_context_id="ctx-001",
        )

    with pytest.raises(ValueError, match=r"SupervisorState\.allow_read"):
        _minimal_supervisor_state(
            status=SupervisorStatus.COMPLETED,
            run_context_id="ctx-001",
            terminal=True,
            final_status=SupervisorStatus.COMPLETED,
            gate_evaluated=True,
            gate_validation_id="validation-001",
        )

    with pytest.raises(ValueError, match=r"SupervisorState\.terminal_reason"):
        _minimal_supervisor_state(
            status=SupervisorStatus.BLOCKED,
            run_context_id="ctx-001",
            terminal=True,
            final_status=SupervisorStatus.BLOCKED,
        )

    with pytest.raises(ValueError, match=r"SupervisorState\.last_error"):
        _minimal_supervisor_state(
            status=SupervisorStatus.FAILED,
            run_context_id="ctx-001",
            terminal=True,
            final_status=SupervisorStatus.FAILED,
        )

    with pytest.raises(ValueError, match=r"SupervisorState\.cancel_requested"):
        _minimal_supervisor_state(
            status=SupervisorStatus.CANCELLED,
            run_context_id="ctx-001",
            terminal=True,
            final_status=SupervisorStatus.CANCELLED,
        )

    with pytest.raises(ValueError, match=r"SupervisorState\.terminal"):
        _minimal_supervisor_state(terminal=True)

    with pytest.raises(ValueError, match=r"SupervisorState\.recovery_budget_remaining"):
        _minimal_supervisor_state(recovery_budget_remaining=3)


def test_supervisor_state_rejects_unsorted_recent_events() -> None:
    observed_at = datetime(2026, 8, 29, 12, 31, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match=r"SupervisorState\.recent_events"):
        _minimal_supervisor_state(
            recent_events=[
                SupervisorEvent(
                    sequence=2,
                    occurred_at=observed_at,
                    event_type=SupervisorEventType.MONITOR_TICK,
                    message="Second event",
                ),
                SupervisorEvent(
                    sequence=1,
                    occurred_at=observed_at,
                    event_type=SupervisorEventType.START,
                    message="First event",
                ),
            ]
        )


def test_supervisor_state_json_round_trip() -> None:
    observed_at = datetime(2026, 8, 29, 12, 32, tzinfo=timezone.utc)
    original = _minimal_supervisor_state(
        status=SupervisorStatus.RUNNING,
        run_context_id="ctx-001",
        active_run_id="find_20260829_120000_000001",
        active_pid=1234,
        process_started_at=observed_at,
        process_alive=True,
        recent_events=[
            SupervisorEvent(
                sequence=0,
                occurred_at=observed_at,
                event_type=SupervisorEventType.NEW_RUN_STARTED,
                message="Find process started",
            )
        ],
    )

    restored = SupervisorState.from_json(original.to_json())

    assert restored == original
    assert isinstance(restored.status, SupervisorStatus)
    assert isinstance(restored.recent_events[0], SupervisorEvent)
    assert restored.recent_events[0].event_type is SupervisorEventType.NEW_RUN_STARTED


def test_parameter_change_copies_direct_mapping_input() -> None:
    after = {"workers": 1}
    change = ParameterChange(
        name="title_llm_workers",
        after=after,
        reason="Reduce request pressure",
    )

    after["workers"] = 10

    assert change.after == {"workers": 1}


def test_anomaly_confirmed_root_cause_requires_direct_evidence() -> None:
    with pytest.raises(ValueError, match=r"Anomaly\.evidence_refs"):
        _minimal_anomaly(
            root_cause_status="confirmed",
            root_cause="The progress report was missing",
            evidence_refs=[
                EvidenceRef(
                    kind="snapshot",
                    summary="Snapshot only records the observed state",
                )
            ],
        )


def test_recovery_decision_rejects_unapproved_high_risk_execution() -> None:
    with pytest.raises(ValueError, match=r"RecoveryDecision\.executable"):
        _minimal_recovery_decision(
            action=RecoveryAction.RETRY_NEW_RUN,
            risk_level=RiskLevel.HIGH,
            executable=True,
            new_run_required=True,
            proposed_new_run_id="find_20260829_123000_000007",
            requires_approval=True,
            approval_status="pending",
        )


def test_supervisor_state_recovering_requires_decision_as_well_as_anomaly() -> None:
    with pytest.raises(ValueError, match=r"SupervisorState\.active_recovery_decision_id"):
        _minimal_supervisor_state(
            status=SupervisorStatus.RECOVERING,
            run_context_id="ctx-001",
            active_anomaly_id="anomaly-001",
            recovery_attempts=1,
            recovery_budget_remaining=1,
        )


def test_all_top_level_contracts_support_full_json_conversion_without_python_repr() -> None:
    observed_at = datetime(2026, 8, 29, 12, 40, tzinfo=timezone.utc)
    objects = [
        _minimal_run_context(),
        _minimal_progress_snapshot(
            status=ProgressStatus.RUNNING,
            current=1,
            total=2,
            percent=50,
        ),
        _minimal_validation_result(
            downstream_input_preview={"candidate_count": 2},
        ),
        _minimal_anomaly(process_facts={"pid": 1234}),
        _minimal_recovery_decision(action_parameters={"retry_delay_seconds": 5}),
        _minimal_experience_case(approval_record={"status": "not_required"}),
        _minimal_supervisor_state(
            recent_events=[
                SupervisorEvent(
                    sequence=0,
                    occurred_at=observed_at,
                    event_type=SupervisorEventType.PREPARED,
                    message="Supervisor prepared the run",
                )
            ]
        ),
    ]

    for original in objects:
        as_dict = original.to_dict()
        payload = original.to_json()
        restored = type(original).from_json(payload)

        assert json.loads(payload) == as_dict
        assert restored == original
        assert restored.created_at == original.created_at
        assert "datetime.datetime(" not in payload
        assert "ProgressStatus." not in payload
        assert "ValidationStatus." not in payload
        assert "RecoveryAction." not in payload
        assert "SupervisorStatus." not in payload
        assert "RiskLevel." not in payload

    run_context = objects[0]
    progress_snapshot = objects[1]
    validation_result = objects[2]
    anomaly = objects[3]
    recovery_decision = objects[4]
    experience_case = objects[5]
    supervisor_state = objects[6]
    assert isinstance(run_context, RunContext)
    assert isinstance(progress_snapshot, ProgressSnapshot)
    assert isinstance(validation_result, ValidationResult)
    assert isinstance(anomaly, Anomaly)
    assert isinstance(recovery_decision, RecoveryDecision)
    assert isinstance(experience_case, ExperienceCase)
    assert isinstance(supervisor_state, SupervisorState)
    assert isinstance(run_context.expected_artifacts[0], ArtifactRef)
    assert isinstance(progress_snapshot.status, ProgressStatus)
    assert isinstance(validation_result.checks[0], ValidationCheck)
    assert isinstance(anomaly.evidence_refs[0], EvidenceRef)
    assert isinstance(recovery_decision.action, RecoveryAction)
    assert isinstance(experience_case.validation_after_status, ValidationStatus)
    assert isinstance(supervisor_state.status, SupervisorStatus)


def _progress_snapshot_from_fixed_find_payload(
    payload: dict[str, object],
    *,
    result_exists: bool,
) -> ProgressSnapshot:
    """Test-only mapping of fixed Find progress fields; this is not an Observer."""

    updated_at_value = payload["updated_at"]
    raw_phase_value = payload["phase"]
    counts_value = payload["counts"]
    assert isinstance(updated_at_value, str)
    assert isinstance(raw_phase_value, str)
    assert isinstance(counts_value, dict)
    updated_at = datetime.fromisoformat(updated_at_value.replace("Z", "+00:00"))
    live_progress = payload.get("live_progress")
    assert live_progress is None or isinstance(live_progress, dict)
    phase = live_progress.get("phase", raw_phase_value) if live_progress else raw_phase_value
    assert isinstance(phase, str)

    return ProgressSnapshot(
        snapshot_id=f"snapshot-{payload['run_id']}",
        run_id=str(payload["run_id"]),
        created_at=updated_at,
        observed_at=updated_at,
        sequence=0,
        producer="fixed-find-shape-test",
        producer_version="1.0",
        status=(
            ProgressStatus.COMPLETED
            if raw_phase_value == "complete"
            else ProgressStatus.RUNNING
        ),
        phase=phase,
        raw_phase=raw_phase_value,
        current=live_progress.get("current") if live_progress else None,
        total=live_progress.get("total") if live_progress else None,
        percent=live_progress.get("percent") if live_progress else None,
        message=live_progress.get("message") if live_progress else None,
        counts=counts_value,
        progress_updated_at=updated_at,
        elapsed_seconds=0.0,
        seconds_without_progress=0.0,
        process_alive=raw_phase_value != "complete",
        cancel_requested=False,
        artifact_observations=[],
        progress_parse_ok=True,
        result_exists=result_exists,
        source_status_exists=True,
        source_total=0,
        source_ready=0,
        source_limited=0,
        source_failed=0,
        status_reason="Mapped from a fixed Find progress payload",
    )


def test_progress_snapshot_carries_fixed_success_find_fields() -> None:
    success_progress = {
        "run_id": "find_20260810_062018_982678",
        "updated_at": "2026-08-10T06:39:24.059402Z",
        "phase": "complete",
        "counts": {
            "raw_title_index_papers": 20,
            "llm_scored_candidates": 18,
        },
    }

    snapshot = _progress_snapshot_from_fixed_find_payload(
        success_progress,
        result_exists=True,
    )

    assert snapshot.run_id == success_progress["run_id"]
    assert snapshot.status is ProgressStatus.COMPLETED
    assert snapshot.phase == "complete"
    assert snapshot.counts == success_progress["counts"]
    assert snapshot.progress_updated_at == datetime(
        2026, 8, 10, 6, 39, 24, 59402, tzinfo=timezone.utc
    )


def test_progress_snapshot_carries_fixed_running_live_progress_fields() -> None:
    running_progress = {
        "run_id": "find_20260822_051206_346734",
        "updated_at": "2026-08-22T05:25:57.495977Z",
        "phase": "llm_title_filter",
        "live_progress": {
            "phase": "llm_title_filter",
            "current": 36,
            "total": 54,
            "percent": 67,
            "message": "ICML: scoring title batch 37/54",
        },
        "counts": {"raw_title_index_papers": 54, "llm_scored_candidates": 36},
    }

    snapshot = _progress_snapshot_from_fixed_find_payload(
        running_progress,
        result_exists=False,
    )

    assert snapshot.status is ProgressStatus.RUNNING
    assert snapshot.phase == "llm_title_filter"
    assert (snapshot.current, snapshot.total, snapshot.percent) == (36, 54, 67)
    assert snapshot.message == "ICML: scoring title batch 37/54"
    assert snapshot.counts == running_progress["counts"]


def test_fixed_find_shape_faults_are_carried_without_runtime_access() -> None:
    complete_without_result = {
        "run_id": "find_20260810_062018_982678",
        "updated_at": "2026-08-10T06:39:24.059402Z",
        "phase": "complete",
        "counts": {"raw_title_index_papers": 20, "llm_scored_candidates": 18},
    }
    completed_snapshot = _progress_snapshot_from_fixed_find_payload(
        complete_without_result,
        result_exists=False,
    )
    result_missing_validation = _minimal_validation_result(
        status=ValidationStatus.BLOCK,
        ready_for_read=False,
        checks=[_validation_check("result_exists", ValidationStatus.BLOCK)],
        passed_check_count=0,
        blocked_check_count=1,
    )
    malformed_snapshot = _minimal_progress_snapshot(
        status=ProgressStatus.UNKNOWN,
        progress_parse_ok=False,
        observation_errors=["Progress JSON could not be parsed"],
        status_reason="Progress payload was malformed",
    )
    stalled_snapshot = _minimal_progress_snapshot(
        status=ProgressStatus.SUSPECTED_STALL,
        phase="llm_title_filter",
        progress_updated_at=datetime(2026, 8, 22, 5, 0, tzinfo=timezone.utc),
        observed_at=datetime(2026, 8, 22, 5, 30, tzinfo=timezone.utc),
        seconds_without_progress=1800.0,
        status_reason="updated_at did not change for the stall interval",
    )

    assert completed_snapshot.status is ProgressStatus.COMPLETED
    assert completed_snapshot.result_exists is False
    assert result_missing_validation.status is ValidationStatus.BLOCK
    assert malformed_snapshot.progress_parse_ok is False
    assert malformed_snapshot.observation_errors == ["Progress JSON could not be parsed"]
    assert stalled_snapshot.status is ProgressStatus.SUSPECTED_STALL
    assert stalled_snapshot.seconds_without_progress == 1800.0


def test_fixed_find_shape_rejects_invalid_live_progress_and_records_run_id_mismatch() -> None:
    invalid_live_progress = {
        "run_id": "find_20260822_051206_346734",
        "updated_at": "2026-08-22T05:25:57.495977Z",
        "phase": "llm_title_filter",
        "live_progress": {
            "phase": "llm_title_filter",
            "current": 55,
            "total": 54,
            "percent": 100,
            "message": "Invalid batch count",
        },
        "counts": {"raw_title_index_papers": 54},
    }
    with pytest.raises(ValueError, match=r"ProgressSnapshot\.current"):
        _progress_snapshot_from_fixed_find_payload(invalid_live_progress, result_exists=False)

    run_id_mismatch_validation = _minimal_validation_result(
        status=ValidationStatus.BLOCK,
        ready_for_read=False,
        checks=[
            _validation_check(
                "progress_run_id_matches",
                ValidationStatus.BLOCK,
            )
        ],
        passed_check_count=0,
        blocked_check_count=1,
        failure_codes=["run_id_mismatch"],
    )
    assert run_id_mismatch_validation.status is ValidationStatus.BLOCK
    assert run_id_mismatch_validation.failure_codes == ["run_id_mismatch"]


def test_contract_module_defines_conversion_base_enums_and_internal_contracts() -> None:
    exported_classes = {
        name
        for name, value in vars(contracts).items()
        if isinstance(value, type) and value.__module__ == contracts.__name__
    }

    assert exported_classes == {
        "JsonContract",
        "ProgressStatus",
        "ValidationStatus",
            "RecoveryAction",
            "SupervisorStatus",
            "SupervisorEventType",
            "RiskLevel",
        "ArtifactRef",
        "EvidenceCollectionRule",
        "EvidenceDefinition",
        "EvidenceFact",
        "EvidenceMatchCondition",
        "EvidenceRef",
        "ExperienceRef",
        "ExperienceQuery",
        "RecoveryExperienceQuery",
        "RecoveryProposal",
        "ExecutionHandle",
        "ParameterChange",
        "ValidationCheck",
        "SupervisorEvent",
        "FindStageRequest",
        "RunContext",
        "ProgressSnapshot",
        "ValidationResult",
        "Anomaly",
        "RecoveryDecision",
        "ExperienceCase",
        "SupervisorState",
    }


def test_recovery_decision_preserves_legacy_defaults_and_old_json() -> None:
    decision = _minimal_recovery_decision()

    assert decision.proposal_id is None
    assert decision.proposed_action is None

    legacy_payload = decision.to_dict()
    legacy_payload.pop("proposal_id")
    legacy_payload.pop("proposed_action")
    restored = RecoveryDecision.from_dict(legacy_payload)

    assert restored.proposal_id is None
    assert restored.proposed_action is None


def test_recovery_decision_round_trips_proposal_approval_identity() -> None:
    decision = _minimal_recovery_decision(
        action=RecoveryAction.REQUEST_APPROVAL,
        requires_approval=True,
        approval_status="pending",
        proposal_id="proposal-001",
        proposed_action=RecoveryAction.RETRY_WITH_PARAMETER_CHANGE,
    )

    restored = RecoveryDecision.from_json(decision.to_json())

    assert restored == decision
    assert restored.proposal_id == "proposal-001"
    assert restored.proposed_action is RecoveryAction.RETRY_WITH_PARAMETER_CHANGE


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "action": RecoveryAction.REQUEST_APPROVAL,
            "requires_approval": True,
            "approval_status": "pending",
            "proposal_id": " ",
            "proposed_action": RecoveryAction.RETRY_NEW_RUN,
        },
        {
            "action": RecoveryAction.REQUEST_APPROVAL,
            "requires_approval": True,
            "approval_status": "pending",
            "proposal_id": 1,
            "proposed_action": RecoveryAction.RETRY_NEW_RUN,
        },
        {
            "proposal_id": "proposal-001",
            "proposed_action": RecoveryAction.RETRY_NEW_RUN,
        },
        {
            "action": RecoveryAction.REQUEST_APPROVAL,
            "requires_approval": True,
            "approval_status": "pending",
            "proposal_id": "proposal-001",
        },
        {"proposed_action": RecoveryAction.RETRY_NEW_RUN},
        {
            "action": RecoveryAction.REQUEST_APPROVAL,
            "requires_approval": True,
            "approval_status": "pending",
            "proposed_action": RecoveryAction.REQUEST_APPROVAL,
        },
        {
            "action": RecoveryAction.REQUEST_APPROVAL,
            "requires_approval": True,
            "approval_status": "pending",
            "proposed_action": "retry_new_run",
        },
    ],
)
def test_recovery_decision_rejects_invalid_proposal_linkage(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match=r"RecoveryDecision\.(proposal_id|proposed_action)"):
        _minimal_recovery_decision(**overrides)


def test_supervisor_state_tracks_last_action_attempts_and_old_json() -> None:
    default_state = _minimal_supervisor_state()
    counted_state = _minimal_supervisor_state(
        last_recovery_action=RecoveryAction.RETRY_NEW_RUN,
        last_action_attempt_count=1,
    )

    assert default_state.last_action_attempt_count == 0
    assert SupervisorState.from_json(counted_state.to_json()) == counted_state

    legacy_payload = default_state.to_dict()
    legacy_payload.pop("last_action_attempt_count")
    assert SupervisorState.from_dict(legacy_payload).last_action_attempt_count == 0


@pytest.mark.parametrize("value", [-1, True, "1", 1.0])
def test_supervisor_state_rejects_invalid_last_action_attempt_count(
    value: object,
) -> None:
    with pytest.raises(ValueError, match=r"SupervisorState\.last_action_attempt_count"):
        _minimal_supervisor_state(last_action_attempt_count=value)


def test_supervisor_state_requires_an_action_for_positive_action_count() -> None:
    with pytest.raises(ValueError, match=r"SupervisorState\.last_action_attempt_count"):
        _minimal_supervisor_state(last_action_attempt_count=1)


def test_run_context_has_conservative_recovery_defaults_and_old_json() -> None:
    context = _minimal_run_context()
    another_context = _minimal_run_context(context_id="ctx-002")

    assert context.external_costs_authorized is False
    assert context.skippable_sources == []
    context.skippable_sources.append("semantic_scholar")
    assert another_context.skippable_sources == []
    context.skippable_sources.clear()

    legacy_payload = context.to_dict()
    legacy_payload.pop("external_costs_authorized")
    legacy_payload.pop("skippable_sources")
    restored = RunContext.from_dict(legacy_payload)

    assert restored.external_costs_authorized is False
    assert restored.skippable_sources == []


@pytest.mark.parametrize("value", [0, 1, "true", None])
def test_run_context_requires_strict_external_cost_authorization(
    value: object,
) -> None:
    with pytest.raises(ValueError, match=r"RunContext\.external_costs_authorized"):
        _minimal_run_context(external_costs_authorized=value)


@pytest.mark.parametrize(
    "value",
    ["arxiv", [""], [" "], [1], ["arxiv", "arxiv"]],
)
def test_run_context_rejects_invalid_skippable_sources(value: object) -> None:
    with pytest.raises(ValueError, match=r"RunContext\.skippable_sources"):
        _minimal_run_context(skippable_sources=value)


def test_run_context_copies_and_round_trips_skippable_sources() -> None:
    sources = ["semantic_scholar", "openalex"]
    context = _minimal_run_context(
        external_costs_authorized=True,
        skippable_sources=sources,
    )
    sources.append("arxiv")

    payload = context.to_dict()
    payload["skippable_sources"].append("github")

    assert context.skippable_sources == ["semantic_scholar", "openalex"]
    assert RunContext.from_json(context.to_json()) == context


def test_find_feedback_adapter_uses_conservative_recovery_defaults() -> None:
    from copy import deepcopy

    from feedback.feedback_adapter import FindFeedbackAdapter
    from feedback.query_builder import build_experience_query

    request = _minimal_find_stage_request(project_id="project-001")
    request_before = deepcopy(request.to_dict())

    context = FindFeedbackAdapter().adapt(
        request,
        build_experience_query(request),
        [],
    )

    assert context.external_costs_authorized is False
    assert context.skippable_sources == []
    assert context.requested_parameters == request.requested_parameters
    assert context.effective_parameters == request.requested_parameters
    assert request.to_dict() == request_before


EVIDENCE_OBSERVED_AT = datetime(2026, 9, 10, 9, 0, tzinfo=timezone.utc)


def _minimal_evidence_fact(**overrides: object) -> EvidenceFact:
    values: dict[str, object] = {
        "code": "find.candidate_count",
        "value": 5,
        "source_contract_id": "snapshot-001",
        "source_field": "counts.candidates",
        "producer": "find-progress-observer",
        "run_id": "find-001",
        "observed_at": EVIDENCE_OBSERVED_AT,
    }
    values.update(overrides)
    return EvidenceFact(**values)  # type: ignore[arg-type]


def _minimal_evidence_definition(**overrides: object) -> EvidenceDefinition:
    values: dict[str, object] = {
        "code": "find.candidate_count",
        "value_type": "integer",
        "intended_producer": "find-progress-observer",
        "description": "Number of candidates actually observed by Find.",
    }
    values.update(overrides)
    return EvidenceDefinition(**values)  # type: ignore[arg-type]


def _minimal_evidence_collection_rule(
    **overrides: object,
) -> EvidenceCollectionRule:
    values: dict[str, object] = {
        "rule_id": "find-candidate-count-from-progress",
        "evidence_code": "find.candidate_count",
        "source_field": "counts.candidates",
        "collector": "progress_snapshot_field",
        "collector_parameters": {},
        "implementation_status": "ready",
    }
    values.update(overrides)
    return EvidenceCollectionRule(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "value",
    [
        5,
        5.5,
        True,
        "scoring",
        [1, 2, 3],
        {"phase": "scoring", "counts": [1, 2]},
    ],
)
def test_evidence_fact_accepts_actual_json_values(value: object) -> None:
    fact = _minimal_evidence_fact(value=value)

    assert fact.value == value
    assert fact.observed_at.tzinfo is not None
    assert fact.schema_version == "feedback.evidence_fact.v1"


def test_evidence_fact_accepts_explicit_current_schema_version() -> None:
    fact = _minimal_evidence_fact(
        schema_version="feedback.evidence_fact.v1",
    )

    assert fact.schema_version == "feedback.evidence_fact.v1"


@pytest.mark.parametrize(
    "field_name",
    ["code", "source_contract_id", "source_field", "producer", "run_id"],
)
@pytest.mark.parametrize("value", ["", "   "])
def test_evidence_fact_rejects_blank_identity_fields(
    field_name: str,
    value: str,
) -> None:
    with pytest.raises(ValueError, match=rf"EvidenceFact\.{field_name}"):
        _minimal_evidence_fact(**{field_name: value})


@pytest.mark.parametrize(
    "value",
    [
        None,
        object(),
        float("nan"),
        float("inf"),
        {"nested": [1, float("nan")]},
        {"nested": {"number": float("-inf")}},
    ],
)
def test_evidence_fact_rejects_missing_or_invalid_json_values(value: object) -> None:
    with pytest.raises(ValueError, match=r"EvidenceFact\.value"):
        _minimal_evidence_fact(value=value)


@pytest.mark.parametrize(
    "value",
    ["Bearer credential", {"token": "credential"}],
)
def test_evidence_fact_rejects_obvious_secrets(value: object) -> None:
    with pytest.raises(ValueError, match=r"EvidenceFact\.value"):
        _minimal_evidence_fact(value=value)


@pytest.mark.parametrize(
    "observed_at",
    [datetime(2026, 9, 10, 9, 0), "2026-09-10T09:00:00Z"],
)
def test_evidence_fact_rejects_invalid_observed_at(observed_at: object) -> None:
    with pytest.raises(ValueError, match=r"EvidenceFact\.observed_at"):
        _minimal_evidence_fact(observed_at=observed_at)


def test_evidence_fact_rejects_wrong_schema() -> None:
    with pytest.raises(ValueError, match=r"EvidenceFact\.schema_version"):
        _minimal_evidence_fact(schema_version="feedback.evidence_fact.v2")


@pytest.mark.parametrize(
    "value_type",
    ["boolean", "integer", "number", "string", "array", "object"],
)
def test_evidence_definition_accepts_closed_value_types(value_type: str) -> None:
    definition = _minimal_evidence_definition(value_type=value_type)

    assert definition.value_type == value_type
    assert definition.schema_version == "feedback.evidence_definition.v1"


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("code", " "),
        ("value_type", ""),
        ("value_type", "unknown"),
        ("value_type", "null"),
        ("intended_producer", " "),
        ("description", ""),
    ],
)
def test_evidence_definition_rejects_invalid_required_fields(
    field_name: str,
    value: str,
) -> None:
    with pytest.raises(ValueError, match=rf"EvidenceDefinition\.{field_name}"):
        _minimal_evidence_definition(**{field_name: value})


def test_evidence_definition_rejects_secrets_and_wrong_schema() -> None:
    with pytest.raises(ValueError, match=r"EvidenceDefinition\.description"):
        _minimal_evidence_definition(description="Use Bearer credential")
    with pytest.raises(ValueError, match=r"EvidenceDefinition\.schema_version"):
        _minimal_evidence_definition(
            schema_version="feedback.evidence_definition.v2",
        )


@pytest.mark.parametrize(
    "implementation_status",
    ["ready", "needs_instrumentation", "unsupported"],
)
@pytest.mark.parametrize(
    "collector_parameters",
    [{}, {"path": ["scoring", "queue_depth"], "options": {"strict": True}}],
)
def test_evidence_collection_rule_accepts_inert_supported_shapes(
    implementation_status: str,
    collector_parameters: dict[str, object],
) -> None:
    rule = _minimal_evidence_collection_rule(
        implementation_status=implementation_status,
        collector_parameters=collector_parameters,
    )

    assert rule.implementation_status == implementation_status
    assert rule.collector_parameters == collector_parameters
    assert rule.schema_version == "feedback.evidence_collection_rule.v1"


@pytest.mark.parametrize(
    "field_name",
    ["rule_id", "evidence_code", "source_field", "collector"],
)
def test_evidence_collection_rule_rejects_blank_identity_fields(
    field_name: str,
) -> None:
    with pytest.raises(ValueError, match=rf"EvidenceCollectionRule\.{field_name}"):
        _minimal_evidence_collection_rule(**{field_name: " "})


@pytest.mark.parametrize(
    "collector_parameters",
    [
        [],
        {"unsupported": object()},
        {"number": float("nan")},
        {"number": float("inf")},
        {"nested": [1, float("-inf")]},
        {"authorization": "credential"},
    ],
)
def test_evidence_collection_rule_rejects_unsafe_parameters(
    collector_parameters: object,
) -> None:
    with pytest.raises(
        ValueError,
        match=r"EvidenceCollectionRule\.collector_parameters",
    ):
        _minimal_evidence_collection_rule(
            collector_parameters=collector_parameters,
        )


@pytest.mark.parametrize(
    "implementation_status",
    ["", "proposed", "active", "confirmed"],
)
def test_evidence_collection_rule_rejects_other_status_domains(
    implementation_status: str,
) -> None:
    with pytest.raises(
        ValueError,
        match=r"EvidenceCollectionRule\.implementation_status",
    ):
        _minimal_evidence_collection_rule(
            implementation_status=implementation_status,
        )


def test_evidence_collection_rule_rejects_wrong_schema() -> None:
    with pytest.raises(ValueError, match=r"EvidenceCollectionRule\.schema_version"):
        _minimal_evidence_collection_rule(
            schema_version="feedback.evidence_collection_rule.v2",
        )


def test_evidence_contract_field_sets_exclude_duplicate_semantics() -> None:
    assert {item.name for item in fields(EvidenceFact)} == {
        "code",
        "value",
        "source_contract_id",
        "source_field",
        "producer",
        "run_id",
        "observed_at",
        "schema_version",
    }
    assert {item.name for item in fields(EvidenceDefinition)} == {
        "code",
        "value_type",
        "intended_producer",
        "description",
        "schema_version",
    }
    assert {item.name for item in fields(EvidenceCollectionRule)} == {
        "rule_id",
        "evidence_code",
        "source_field",
        "collector",
        "collector_parameters",
        "implementation_status",
        "schema_version",
    }


def test_observed_anomaly_kind_is_not_a_root_cause_code() -> None:
    doc = " ".join((inspect.getdoc(Anomaly) or "").casefold().split())

    assert "observed anomaly classification" in doc
    assert "not a root-cause code" in doc


def test_evidence_reference_kind_is_distinct_from_observed_fact_code() -> None:
    doc = " ".join((inspect.getdoc(EvidenceRef) or "").casefold().split())
    reference_fields = {item.name for item in fields(EvidenceRef)}
    fact_fields = {item.name for item in fields(EvidenceFact)}

    assert "classifies the reference" in doc
    assert "not a machine-readable observed fact code" in doc
    assert not {"code", "value"} & reference_fields
    assert {"code", "value"} <= fact_fields
    assert "kind" not in fact_fields


def test_validation_check_code_is_distinct_from_observed_fact_code() -> None:
    check_doc = " ".join((inspect.getdoc(ValidationCheck) or "").casefold().split())
    fact_doc = " ".join((inspect.getdoc(EvidenceFact) or "").casefold().split())
    check = ValidationCheck(
        code="result_exists",
        status=ValidationStatus.PASS,
        required=True,
        message="Result exists",
        expected=True,
        actual=True,
    )
    fact = _minimal_evidence_fact(code="find.result_exists", value=True)

    assert "identifies one validation check" in check_doc
    assert "identifies one reusable observed fact" in fact_doc
    assert type(check) is not type(fact)
    assert {"expected", "actual"} <= {item.name for item in fields(check)}
    assert "value" in {item.name for item in fields(fact)}
    with pytest.raises(ValueError):
        EvidenceFact.from_dict(check.to_dict())
    with pytest.raises(ValueError):
        ValidationCheck.from_dict(fact.to_dict())


def test_evidence_fact_code_describes_observation_not_root_cause() -> None:
    doc = " ".join((inspect.getdoc(EvidenceFact) or "").casefold().split())
    fact_fields = {item.name for item in fields(EvidenceFact)}

    assert "what was observed" in doc
    assert "not why the anomaly occurred" in doc
    assert not {
        "root_cause_code",
        "root_cause_status",
        "confidence",
        "hypotheses",
    } & fact_fields


@pytest.mark.parametrize("code", ["progress_stalled", "validation", "result_exists"])
def test_evidence_fact_semantic_boundary_is_not_a_string_blacklist(code: str) -> None:
    assert _minimal_evidence_fact(code=code).code == code
