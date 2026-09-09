from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import get_type_hints

import pytest

import feedback
import feedback.advisor as advisor_module
from integrations.web_llm import LLMClient
from feedback import (
    Anomaly,
    ArtifactRef,
    EvidenceRef,
    ExperienceCase,
    ExperienceQuery,
    LLMRecoveryAdvisor,
    ParameterChange,
    RecoveryAction,
    RecoveryAdvisor,
    RecoveryDecision,
    RecoveryProposal,
    RiskLevel,
    RunContext,
    SupervisorState,
    SupervisorStatus,
    ValidationStatus,
)


NOW = datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc)


class FakeLLMClient:
    def __init__(self, response: object | Exception) -> None:
        self.response = response
        self.prompts: list[str] = []
        self.calls: list[dict[str, bool]] = []
        self.api_key = "test-api-key-must-not-appear"

    def chat(
        self,
        prompt: str,
        *,
        omit_max_tokens: bool = False,
        allow_reasoning_fallback: bool = True,
    ) -> object:
        self.prompts.append(prompt)
        self.calls.append(
            {
                "omit_max_tokens": omit_max_tokens,
                "allow_reasoning_fallback": allow_reasoning_fallback,
            }
        )
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class FakeSDKResponse:
    pass


class FakeErrorResponse:
    error = "provider returned an error-shaped response"


def test_existing_llm_client_success_boundary_is_text() -> None:
    assert get_type_hints(LLMClient.chat)["return"] is str


def _run_context(**overrides: object) -> RunContext:
    values: dict[str, object] = {
        "context_id": "ctx-advisor",
        "attempt_index": 1,
        "project_id": "project-advisor",
        "request_source": "web",
        "created_at": NOW,
        "producer": "advisor-tests",
        "producer_version": "1.0",
        "research_topic": "Reliable research agents",
        "selection_snapshot_path": "/runtime/selection.json",
        "selection": {"include_arxiv": True},
        "command_redacted": ["python", "modules/finding/main.py"],
        "working_directory": "/workspace",
        "python_executable": "/usr/bin/python",
        "config_snapshot_path": "/runtime/find.config.json",
        "input_snapshot_path": "/runtime/input.json",
        "requested_parameters": {"abstract_scoring_max_workers": 2},
        "effective_parameters": {
            "abstract_scoring_max_workers": 1,
            "runtime_tuning": {"ABSTRACT_SCORING_MAX_WORKERS": "1"},
        },
        "expected_artifacts": [
            ArtifactRef(role="result", path="find_results.json", required=True)
        ],
        "startup_grace_seconds": 30,
        "stall_suspect_seconds": 60,
        "stall_confirm_seconds": 120,
        "recovery_budget": 1,
        "allowed_recovery_actions": [
            RecoveryAction.NO_ACTION,
            RecoveryAction.RETRY_NEW_RUN,
            RecoveryAction.RETRY_WITH_PARAMETER_CHANGE,
            RecoveryAction.SKIP_OPTIONAL_SOURCE,
            RecoveryAction.STOP_AND_REPORT,
        ],
        "approval_risk_threshold": RiskLevel.MEDIUM,
        "validation_policy_version": "find.validation.v1",
        "experience_query": ExperienceQuery(limit=5),
        "environment_fingerprint": "environment-advisor",
    }
    values.update(overrides)
    return RunContext(**values)  # type: ignore[arg-type]


def _anomaly(**overrides: object) -> Anomaly:
    values: dict[str, object] = {
        "anomaly_id": "anomaly-advisor",
        "run_id": "find-advisor",
        "created_at": NOW,
        "detected_at": NOW,
        "updated_at": NOW,
        "producer": "advisor-tests",
        "producer_version": "1.0",
        "stage": "find",
        "kind": "progress_stalled",
        "blocking": True,
        "confidence": 0.9,
        "detected_by": ["observer"],
        "supervisor_state_revision": 1,
        "symptoms": ["Scoring progress stopped"],
        "evidence_refs": [
            EvidenceRef(
                kind="snapshot",
                summary="Scoring progress did not advance",
                contract_id="snapshot-advisor",
            ),
            EvidenceRef(
                kind="validation",
                summary="Result is not ready",
                contract_id="validation-advisor",
            ),
        ],
        "root_cause_status": "suspected",
        "affected_phase": "scoring",
        "downstream_impact": "Read cannot start",
        "partial_results_usable": False,
        "recovery_eligible": True,
        "retryable_signal": True,
        "fingerprint": "progress-stalled-advisor",
        "occurrence_count": 1,
    }
    values.update(overrides)
    return Anomaly(**values)  # type: ignore[arg-type]


def _supervisor_state(
    run_context: RunContext,
    anomaly: Anomaly,
    **overrides: object,
) -> SupervisorState:
    values: dict[str, object] = {
        "supervisor_id": "supervisor-advisor",
        "project_id": run_context.project_id,
        "root_run_id": anomaly.run_id,
        "status": SupervisorStatus.DECIDING,
        "state_revision": 1,
        "created_at": NOW,
        "updated_at": NOW,
        "heartbeat_at": NOW,
        "producer": "advisor-tests",
        "producer_version": "1.0",
        "process_alive": False,
        "cancel_requested": False,
        "recovery_attempts": 0,
        "recovery_budget_total": 1,
        "recovery_budget_remaining": 1,
        "awaiting_approval": False,
        "gate_evaluated": False,
        "allow_read": False,
        "gate_reason": "Recovery advice is pending",
        "terminal": False,
        "event_sequence": 0,
        "state_path": "/runtime/supervisor.json",
        "run_context_id": run_context.context_id,
        "active_run_id": anomaly.run_id,
        "active_anomaly_id": anomaly.anomaly_id,
    }
    values.update(overrides)
    return SupervisorState(**values)  # type: ignore[arg-type]


def _experience_case(**overrides: object) -> ExperienceCase:
    values: dict[str, object] = {
        "case_id": "case-advisor",
        "case_type": "technical",
        "created_at": NOW,
        "updated_at": NOW,
        "producer": "advisor-tests",
        "producer_version": "1.0",
        "verified": True,
        "deprecated": False,
        "context_id": "ctx-history",
        "root_run_id": "find-history-root",
        "final_run_id": "find-history-final",
        "root_cause_status": "suspected",
        "evidence_refs": [EvidenceRef(kind="validation", summary="Recovered")],
        "attempt_count": 1,
        "outcome": "recovered",
        "validation_after_id": "validation-history",
        "validation_after_status": ValidationStatus.PASS,
        "ready_for_read_after": True,
        "risk_level": RiskLevel.LOW,
        "confidence": 0.8,
        "matched_count": 2,
        "applied_count": 2,
        "successful_application_count": 2,
        "project_id": "project-advisor",
        "environment_fingerprint": "environment-advisor",
        "context_tags": ["source:arxiv"],
        "anomaly_id": "anomaly-history",
        "anomaly_kind": "progress_stalled",
        "anomaly_fingerprint": "progress-stalled-history",
        "recovery_action": RecoveryAction.RETRY_WITH_PARAMETER_CHANGE,
        "parameter_changes": [
            ParameterChange(
                name="abstract_scoring_max_workers",
                before=2,
                after=1,
                reason="Reduce concurrency",
            )
        ],
    }
    values.update(overrides)
    return ExperienceCase(**values)  # type: ignore[arg-type]


def _response(**overrides: object) -> str:
    payload: dict[str, object] = {
        "proposed_action": "no_action",
        "reason": "No safe candidate change is indicated",
        "parameter_changes": {},
        "target_sources": [],
        "evidence_ref_ids": ["evidence-0"],
        "confidence": 0.7,
        "risk_level": "low",
    }
    payload.update(overrides)
    return json.dumps(payload)


def _call(
    advisor: RecoveryAdvisor,
    *,
    anomaly: Anomaly | None = None,
    run_context: RunContext | None = None,
    supervisor_state: SupervisorState | None = None,
    matched_experiences: list[ExperienceCase] | None = None,
) -> RecoveryProposal:
    actual_anomaly = anomaly or _anomaly()
    actual_context = run_context or _run_context()
    actual_state = supervisor_state or _supervisor_state(
        actual_context,
        actual_anomaly,
    )
    return advisor.propose(
        anomaly=actual_anomaly,
        run_context=actual_context,
        supervisor_state=actual_state,
        matched_experiences=(
            [_experience_case()]
            if matched_experiences is None
            else matched_experiences
        ),
    )


def test_advisor_builds_proposal_with_code_owned_identity(monkeypatch) -> None:
    client = FakeLLMClient(_response())
    advisor: RecoveryAdvisor = LLMRecoveryAdvisor(llm_client=client)
    anomaly = _anomaly()
    run_context = _run_context()
    state = _supervisor_state(run_context, anomaly)
    monkeypatch.setattr(advisor_module, "_new_proposal_id", lambda: "proposal-fixed")
    monkeypatch.setattr(advisor_module, "_utc_now", lambda: NOW)

    proposal = _call(
        advisor,
        anomaly=anomaly,
        run_context=run_context,
        supervisor_state=state,
    )

    assert isinstance(proposal, RecoveryProposal)
    assert not isinstance(proposal, RecoveryDecision)
    assert proposal.proposal_id == "proposal-fixed"
    assert proposal.created_at == NOW
    assert proposal.context_id == run_context.context_id
    assert proposal.run_id == anomaly.run_id
    assert proposal.anomaly_id == anomaly.anomaly_id
    assert proposal.evidence_refs == [anomaly.evidence_refs[0]]
    assert len(client.prompts) == 1


def test_advisor_omits_local_token_limit_and_rejects_reasoning_fallback() -> None:
    client = FakeLLMClient(_response())

    proposal = _call(LLMRecoveryAdvisor(llm_client=client))

    assert isinstance(proposal, RecoveryProposal)
    assert client.calls == [
        {
            "omit_max_tokens": True,
            "allow_reasoning_fallback": False,
        }
    ]


def test_llm_client_omits_max_tokens_when_requested(monkeypatch) -> None:
    from contracts.web_models import AppConfig
    from integrations import web_llm

    payloads: list[dict[str, object]] = []

    class BodyResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self) -> bytes:
            return json.dumps(
                {"choices": [{"message": {"content": '{"ok":true}'}}]}
            ).encode("utf-8")

    def fake_urlopen(request, **_kwargs):
        payloads.append(json.loads(request.data.decode("utf-8")))
        return BodyResponse()

    monkeypatch.setenv("LLM_RETRIES", "1")
    monkeypatch.setattr(web_llm.urllib.request, "urlopen", fake_urlopen)
    client = web_llm.LLMClient(
        AppConfig(
            provider="openai",
            base_url="https://llm.example.test/v1",
            api_key="test-key",
            model="test-model",
        ),
        role="find",
    )

    result = client.chat(
        "Return JSON only.",
        omit_max_tokens=True,
        allow_reasoning_fallback=False,
    )
    default_result = client.chat(
        "Return JSON only.",
        allow_reasoning_fallback=False,
    )

    assert result == '{"ok":true}'
    assert default_result == '{"ok":true}'
    assert len(payloads) == 2
    assert "max_tokens" not in payloads[0]
    assert "max_output_tokens" not in payloads[0]
    assert payloads[1]["max_tokens"] == client.max_tokens


def test_llm_client_strict_path_never_returns_reasoning_content(monkeypatch) -> None:
    from contracts.web_models import AppConfig
    from integrations import web_llm

    calls = 0
    reasoning = '{"must_not":"be_returned"}'

    class BodyResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self) -> bytes:
            return json.dumps(
                {
                    "choices": [
                        {
                            "finish_reason": "length",
                            "message": {
                                "content": "",
                                "reasoning_content": reasoning,
                            },
                        }
                    ]
                }
            ).encode("utf-8")

    def fake_urlopen(_request, **_kwargs):
        nonlocal calls
        calls += 1
        return BodyResponse()

    monkeypatch.setenv("LLM_RETRIES", "1")
    monkeypatch.setattr(web_llm.urllib.request, "urlopen", fake_urlopen)
    client = web_llm.LLMClient(
        AppConfig(
            provider="openai",
            base_url="https://llm.example.test/v1",
            api_key="test-key",
            model="test-model",
        ),
        role="find",
    )

    with pytest.raises(RuntimeError, match="no extractable text") as caught:
        client.chat(
            "Return JSON only.",
            omit_max_tokens=True,
            allow_reasoning_fallback=False,
        )

    assert calls == 2
    assert reasoning not in str(caught.value)


def test_advisor_summarizes_inputs_without_mutation_or_sensitive_content() -> None:
    client = FakeLLMClient(_response())
    advisor = LLMRecoveryAdvisor(llm_client=client)
    anomaly = _anomaly(
        symptoms=["Authorization token secret-value was rejected"],
    )
    run_context = _run_context()
    run_context.effective_parameters["api_key"] = "test-api-key"
    run_context.effective_parameters["nested"] = {
        "password": "password-value",
        "safe": "visible",
    }
    state = _supervisor_state(run_context, anomaly)
    experiences = [_experience_case()]
    before = tuple(
        deepcopy(value.to_dict())
        for value in (anomaly, run_context, state, experiences[0])
    )

    advisor.propose(
        anomaly=anomaly,
        run_context=run_context,
        supervisor_state=state,
        matched_experiences=experiences,
    )

    prompt = client.prompts[0]
    assert "case-advisor" in prompt
    assert "abstract_scoring_max_workers" in prompt
    assert "visible" in prompt
    for secret in (
        "test-api-key-must-not-appear",
        "test-api-key",
        "secret-value",
        "password-value",
    ):
        assert secret not in prompt
    assert "stdout" not in prompt.lower()
    assert "stderr" not in prompt.lower()
    assert "no markdown" in prompt.lower()
    assert "no explanation" in prompt.lower()
    assert "no unknown fields" in prompt.lower()
    assert tuple(
        value.to_dict() for value in (anomaly, run_context, state, experiences[0])
    ) == before


def test_advisor_maps_only_existing_evidence_and_rejects_duplicate_ids() -> None:
    anomaly = _anomaly()
    known_client = FakeLLMClient(
        _response(evidence_ref_ids=["evidence-1", "evidence-0"])
    )

    proposal = _call(LLMRecoveryAdvisor(llm_client=known_client), anomaly=anomaly)

    assert proposal.evidence_refs == [
        anomaly.evidence_refs[1],
        anomaly.evidence_refs[0],
    ]

    for evidence_ids, message in [
        (["evidence-unknown"], "unknown anomaly evidence"),
        (["evidence-0", "evidence-0"], "invalid recovery proposal"),
    ]:
        client = FakeLLMClient(_response(evidence_ref_ids=evidence_ids))
        with pytest.raises(RuntimeError, match=message) as caught:
            _call(LLMRecoveryAdvisor(llm_client=client))
        assert caught.value.__cause__ is not None
        assert len(client.prompts) == 1


@pytest.mark.parametrize(
    "response",
    [
        "",
        "   \n\t",
        None,
    ],
)
def test_advisor_rejects_empty_response_without_retry(response: object) -> None:
    client = FakeLLMClient(response)

    with pytest.raises(RuntimeError, match="returned an empty response") as caught:
        _call(LLMRecoveryAdvisor(llm_client=client))

    assert caught.value.__cause__ is not None
    assert len(client.prompts) == 1


@pytest.mark.parametrize(
    "response",
    [FakeSDKResponse(), FakeErrorResponse()],
)
def test_advisor_rejects_unexpected_response_object_without_retry(
    response: object,
) -> None:
    client = FakeLLMClient(response)

    with pytest.raises(RuntimeError, match="response extraction failed") as caught:
        _call(LLMRecoveryAdvisor(llm_client=client))

    assert isinstance(caught.value.__cause__, TypeError)
    assert len(client.prompts) == 1


@pytest.mark.parametrize(
    "response",
    [
        "not json",
        "explanation " + _response(),
        _response() + " trailing explanation",
        "```json\n" + _response() + "\n```",
    ],
)
def test_advisor_rejects_non_strict_json_without_retry(response: str) -> None:
    client = FakeLLMClient(response)

    with pytest.raises(RuntimeError, match="returned invalid JSON") as caught:
        _call(LLMRecoveryAdvisor(llm_client=client))

    assert isinstance(caught.value.__cause__, json.JSONDecodeError)
    assert response not in str(caught.value)
    assert len(client.prompts) == 1


def test_advisor_rejects_json_array_as_invalid_proposal_without_retry() -> None:
    client = FakeLLMClient("[]")

    with pytest.raises(RuntimeError, match="invalid recovery proposal") as caught:
        _call(LLMRecoveryAdvisor(llm_client=client))

    assert caught.value.__cause__ is not None
    assert len(client.prompts) == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"proposed_action": "unsupported"},
        {"proposed_action": "request_approval"},
        {"confidence": 2},
        {"risk_level": "critical"},
        {
            "proposed_action": "retry_with_parameter_change",
            "parameter_changes": {},
        },
        {"proposed_action": "skip_optional_source", "target_sources": []},
        {"proposed_action": "no_action", "target_sources": ["arxiv"]},
    ],
)
def test_advisor_rejects_contract_invalid_proposals_without_retry(
    overrides: dict[str, object],
) -> None:
    client = FakeLLMClient(_response(**overrides))

    with pytest.raises(RuntimeError, match="invalid recovery proposal") as caught:
        _call(LLMRecoveryAdvisor(llm_client=client))

    assert caught.value.__cause__ is not None
    assert len(client.prompts) == 1


@pytest.mark.parametrize(
    "payload_update",
    [
        {"reason": None},
        {"unexpected": True},
    ],
)
def test_advisor_rejects_missing_and_unknown_response_fields(
    payload_update: dict[str, object],
) -> None:
    payload = json.loads(_response())
    if payload_update == {"reason": None}:
        payload.pop("reason")
    else:
        payload.update(payload_update)
    client = FakeLLMClient(json.dumps(payload))

    with pytest.raises(RuntimeError, match="invalid recovery proposal") as caught:
        _call(LLMRecoveryAdvisor(llm_client=client))

    assert caught.value.__cause__ is not None
    assert len(client.prompts) == 1


@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError("provider rejected test-api-key-in-provider-error"),
        TimeoutError("transport timed out with test-api-key-in-provider-error"),
    ],
)
def test_advisor_wraps_transport_failure_without_leaking_or_retrying(
    failure: Exception,
) -> None:
    secret = "test-api-key-in-provider-error"
    client = FakeLLMClient(failure)

    with pytest.raises(RuntimeError, match="Recovery Advisor LLM transport failed") as caught:
        _call(LLMRecoveryAdvisor(llm_client=client))

    assert caught.value.__cause__ is failure
    assert secret not in str(caught.value)
    assert len(client.prompts) == 1


@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError(
            "Chat Completions API returned no extractable text; secret response details"
        ),
        json.JSONDecodeError("invalid provider JSON", "sensitive response", 0),
    ],
)
def test_advisor_distinguishes_client_response_extraction_failure(
    failure: Exception,
) -> None:
    client = FakeLLMClient(failure)

    with pytest.raises(RuntimeError, match="response extraction failed") as caught:
        _call(LLMRecoveryAdvisor(llm_client=client))

    assert caught.value.__cause__ is failure
    assert "secret response details" not in str(caught.value)
    assert len(client.prompts) == 1


@pytest.mark.parametrize("argument", ["anomaly", "run_context", "supervisor_state"])
def test_advisor_rejects_invalid_contract_inputs(argument: str) -> None:
    anomaly = _anomaly()
    run_context = _run_context()
    state = _supervisor_state(run_context, anomaly)
    values: dict[str, object] = {
        "anomaly": anomaly,
        "run_context": run_context,
        "supervisor_state": state,
        "matched_experiences": [],
    }
    values[argument] = {}
    advisor = LLMRecoveryAdvisor(llm_client=FakeLLMClient(_response()))

    with pytest.raises(TypeError, match=argument):
        advisor.propose(**values)  # type: ignore[arg-type]


def test_advisor_validates_matched_experience_container_and_items() -> None:
    advisor = LLMRecoveryAdvisor(llm_client=FakeLLMClient(_response()))
    with pytest.raises(TypeError, match="matched_experiences"):
        _call(advisor, matched_experiences=())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match=r"matched_experiences\[0\]"):
        _call(advisor, matched_experiences=[object()])  # type: ignore[list-item]


@pytest.mark.parametrize(
    "state_overrides",
    [
        {"project_id": "other-project"},
        {"run_context_id": "other-context"},
        {"active_run_id": "other-run"},
        {"active_anomaly_id": "other-anomaly"},
    ],
)
def test_advisor_rejects_identity_mismatch(state_overrides: dict[str, object]) -> None:
    anomaly = _anomaly()
    context = _run_context()
    state = _supervisor_state(context, anomaly, **state_overrides)

    with pytest.raises(ValueError, match="identity"):
        _call(
            LLMRecoveryAdvisor(llm_client=FakeLLMClient(_response())),
            anomaly=anomaly,
            run_context=context,
            supervisor_state=state,
        )


def test_advisor_does_not_write_files(tmp_path: Path) -> None:
    before = list(tmp_path.iterdir())

    _call(LLMRecoveryAdvisor(llm_client=FakeLLMClient(_response())))

    assert list(tmp_path.iterdir()) == before


def test_llm_recovery_advisor_has_stable_public_imports() -> None:
    from feedback import LLMRecoveryAdvisor as PublicAdvisor
    from feedback.advisor import LLMRecoveryAdvisor as ModuleAdvisor

    assert PublicAdvisor is advisor_module.LLMRecoveryAdvisor
    assert ModuleAdvisor is advisor_module.LLMRecoveryAdvisor
    assert "LLMRecoveryAdvisor" in feedback.__all__
