"""Build fixed before-stage experience queries from Find requests."""

from __future__ import annotations

from .contracts import (
    Anomaly,
    ExperienceQuery,
    FindStageRequest,
    RecoveryExperienceQuery,
    RunContext,
)


def build_experience_query(
    request: FindStageRequest,
    *,
    required_context_tags: list[str] | None = None,
    limit: int = 5,
) -> ExperienceQuery:
    """Map one standardized Find request to the fixed V0 experience filters."""

    return ExperienceQuery(
        project_id=request.project_id,
        case_types=["normal", "technical", "preference"],
        outcomes=["success", "recovered"],
        required_context_tags=[] if required_context_tags is None else required_context_tags,
        verified_only=True,
        include_deprecated=False,
        limit=limit,
    )


def build_recovery_experience_query(
    anomaly: Anomaly,
    run_context: RunContext,
    *,
    limit: int = 10,
) -> RecoveryExperienceQuery:
    """Map one anomaly and its run context to fixed recovery filters."""

    if not isinstance(anomaly, Anomaly):
        raise TypeError("anomaly must be an Anomaly")
    if not isinstance(run_context, RunContext):
        raise TypeError("run_context must be a RunContext")

    return RecoveryExperienceQuery(
        anomaly_kind=anomaly.kind,
        project_id=run_context.project_id,
        environment_fingerprint=run_context.environment_fingerprint,
        required_context_tags=list(
            run_context.experience_query.required_context_tags
        ),
        verified_only=True,
        include_deprecated=False,
        limit=limit,
    )
