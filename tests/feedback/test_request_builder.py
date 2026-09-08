from __future__ import annotations

import feedback
from feedback import FindStageRequest, build_find_stage_request


def test_builder_maps_resolved_framework_find_values() -> None:
    request = build_find_stage_request(
        request_source="web",
        project_id="demo",
        research_topic="Reliable research agents",
        selection={
            "venue_ids": ["dblp_icml"],
            "years": [2026],
            "include_arxiv": True,
        },
        config_path="/workspace/projects/demo/tmp/finding/input/find.config.json",
        requested_parameters={
            "max_recommended_papers": 7,
            "max_ideas": 3,
            "arxiv_queries": ["reliable research agents"],
        },
        working_directory="/workspace",
        force_new_find=True,
        restart_full_cycle=True,
        human_approved_new_find=True,
        approval_reason="The user approved a fresh Find run.",
    )

    assert isinstance(request, FindStageRequest)
    assert request.project_id == "demo"
    assert request.research_topic == "Reliable research agents"
    assert request.selection["venue_ids"] == ["dblp_icml"]
    assert request.requested_parameters["max_recommended_papers"] == 7
    assert request.config_path.endswith("/input/find.config.json")
    assert request.working_directory == "/workspace"
    assert request.force_new_find is True
    assert request.restart_full_cycle is True
    assert request.human_approved_new_find is True
    assert request.approval_reason == "The user approved a fresh Find run."


def test_builder_uses_contract_validation() -> None:
    try:
        build_find_stage_request(
            request_source="web",
            project_id="demo",
            research_topic="Reliable research agents",
            selection={"include_arxiv": True},
            config_path="/workspace/find.config.json",
            requested_parameters={"api_key": "must-not-be-recorded"},
            working_directory="/workspace",
        )
    except ValueError as exc:
        assert "api_key" in str(exc)
    else:
        raise AssertionError("FindStageRequest validation must reject secrets")


def test_builder_is_available_from_stable_feedback_import() -> None:
    assert feedback.build_find_stage_request is build_find_stage_request
    assert "build_find_stage_request" in feedback.__all__
