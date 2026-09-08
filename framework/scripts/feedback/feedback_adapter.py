"""Baseline Find Feedback adapter for runs without matched experience."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import sys
from uuid import uuid4

from .contracts import (
    ArtifactRef,
    ExperienceCase,
    ExperienceQuery,
    ExperienceRef,
    FindStageRequest,
    ParameterChange,
    RecoveryAction,
    RiskLevel,
    RunContext,
)


_SAFE_INTEGER_PARAMETERS = frozenset(
    {
        "abstract_scoring_max_workers",
        "abstract_scoring_batch_size",
        "abstract_scoring_timeout_sec",
        "arxiv_timeout_sec",
    }
)

_RUNTIME_TUNING_KEYS_BY_PARAMETER = {
    "abstract_scoring_max_workers": (
        "ABSTRACT_SCORING_MAX_WORKERS",
        "ABSTRACT_SCORING_WORKER_CAP",
    ),
    "abstract_scoring_batch_size": (
        "ABSTRACT_SCORING_BATCH_SIZE",
        "ABSTRACT_SCORING_MAX_BATCH_SIZE",
    ),
    "abstract_scoring_timeout_sec": ("ABSTRACT_SCORING_TIMEOUT_SEC",),
    "arxiv_timeout_sec": ("ARXIV_TIMEOUT_SEC",),
}


def _sync_runtime_tuning(
    effective_parameters: dict[str, object],
    parameter_name: str,
    value: int,
) -> None:
    runtime_tuning_keys = _RUNTIME_TUNING_KEYS_BY_PARAMETER[parameter_name]
    existing_runtime_tuning = effective_parameters.get("runtime_tuning")
    if existing_runtime_tuning is None:
        runtime_tuning: dict[str, object] = {}
    elif isinstance(existing_runtime_tuning, Mapping):
        runtime_tuning = dict(existing_runtime_tuning)
    else:
        return

    for runtime_tuning_key in runtime_tuning_keys:
        runtime_tuning[runtime_tuning_key] = str(value)
    effective_parameters["runtime_tuning"] = runtime_tuning


class FindFeedbackAdapter:
    """Build a pre-launch RunContext with low-risk parameter experience."""

    def adapt(
        self,
        request: FindStageRequest,
        experience_query: ExperienceQuery,
        experiences: list[ExperienceCase],
    ) -> RunContext:
        if not isinstance(request, FindStageRequest):
            raise TypeError("request must be a FindStageRequest")
        if not isinstance(experience_query, ExperienceQuery):
            raise TypeError("experience_query must be an ExperienceQuery")
        if not isinstance(experiences, list):
            raise TypeError("experiences must be a list of ExperienceCase objects")
        for index, experience in enumerate(experiences):
            if not isinstance(experience, ExperienceCase):
                raise TypeError(f"experiences[{index}] must be an ExperienceCase")

        config_snapshot_path = Path(request.config_path)
        input_snapshot_path = config_snapshot_path.with_name("input.json")
        selection_snapshot_path = config_snapshot_path.with_name("selection.json")
        requested_parameters = deepcopy(request.requested_parameters)
        effective_parameters = deepcopy(requested_parameters)
        matched_experience_refs = [
            ExperienceRef(
                case_id=experience.case_id,
                verified=experience.verified,
                case_type=experience.case_type,
                similarity=None,
                summary=None,
            )
            for experience in experiences
        ]
        applied_experience_refs: list[ExperienceRef] = []
        applied_parameter_changes: list[ParameterChange] = []
        applied_parameter_names: set[str] = set()

        for experience, experience_ref in zip(
            experiences,
            matched_experience_refs,
            strict=True,
        ):
            if not (
                experience.verified
                and not experience.deprecated
                and experience.outcome in {"success", "recovered"}
                and experience.risk_level is RiskLevel.LOW
                and experience.parameter_changes
            ):
                continue

            case_applied = False
            for change in experience.parameter_changes:
                if change.name not in _SAFE_INTEGER_PARAMETERS:
                    continue
                if change.name in applied_parameter_names:
                    continue
                if change.name not in effective_parameters:
                    continue
                if type(change.after) is not int or change.after <= 0:
                    continue

                current_value = effective_parameters[change.name]
                if change.before is not None and change.before != current_value:
                    continue
                if change.after == current_value:
                    continue

                effective_parameters[change.name] = change.after
                _sync_runtime_tuning(
                    effective_parameters,
                    change.name,
                    change.after,
                )
                applied_parameter_names.add(change.name)
                applied_parameter_changes.append(deepcopy(change))
                case_applied = True

            if case_applied:
                applied_experience_refs.append(deepcopy(experience_ref))

        python_executable = sys.executable

        return RunContext(
            context_id=f"ctx-{uuid4().hex}",
            attempt_index=0,
            project_id=request.project_id,
            request_source=request.request_source,
            created_at=datetime.now(timezone.utc),
            producer="find-feedback-adapter",
            producer_version="v0",
            research_topic=request.research_topic,
            selection_snapshot_path=str(selection_snapshot_path),
            selection=deepcopy(request.selection),
            command_redacted=[
                python_executable,
                "modules/finding/main.py",
                "--action",
                "find",
                "--config-json",
                str(config_snapshot_path),
                "--input-json",
                str(input_snapshot_path),
            ],
            working_directory=request.working_directory,
            python_executable=python_executable,
            config_snapshot_path=str(config_snapshot_path),
            input_snapshot_path=str(input_snapshot_path),
            requested_parameters=requested_parameters,
            effective_parameters=effective_parameters,
            expected_artifacts=[
                ArtifactRef(role="result", path="find_results.json", required=True)
            ],
            startup_grace_seconds=30,
            stall_suspect_seconds=60,
            stall_confirm_seconds=120,
            recovery_budget=1,
            allowed_recovery_actions=[RecoveryAction.RETRY_NEW_RUN],
            approval_risk_threshold=RiskLevel.MEDIUM,
            validation_policy_version="find.validation.v1",
            experience_query=experience_query,
            parameter_changes=deepcopy(applied_parameter_changes),
            matched_experience_refs=matched_experience_refs,
            applied_experience_refs=applied_experience_refs,
            experience_parameter_changes=deepcopy(applied_parameter_changes),
        )
