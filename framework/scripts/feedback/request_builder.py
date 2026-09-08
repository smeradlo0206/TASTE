"""Build the standardized pre-Find request from resolved Framework values."""

from __future__ import annotations

from collections.abc import Mapping

from .contracts import FindStageRequest


def build_find_stage_request(
    *,
    request_source: str,
    research_topic: str,
    selection: Mapping[str, object],
    config_path: str,
    requested_parameters: Mapping[str, object],
    working_directory: str,
    project_id: str | None = None,
    force_new_find: bool = False,
    restart_full_cycle: bool = False,
    human_approved_new_find: bool = False,
    approval_reason: str | None = None,
) -> FindStageRequest:
    """Translate already-resolved Framework inputs into one Find request contract."""

    return FindStageRequest(
        request_source=request_source,
        research_topic=research_topic,
        selection=selection,
        config_path=config_path,
        requested_parameters=requested_parameters,
        working_directory=working_directory,
        project_id=project_id,
        force_new_find=force_new_find,
        restart_full_cycle=restart_full_cycle,
        human_approved_new_find=human_approved_new_find,
        approval_reason=approval_reason,
    )
