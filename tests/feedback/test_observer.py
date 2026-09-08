from __future__ import annotations

from datetime import datetime, timedelta, timezone
import inspect
import json
from pathlib import Path
from typing import get_type_hints

import pytest

import feedback
import feedback.observer as observer_module
from feedback import (
    ArtifactRef,
    ExecutionHandle,
    ExperienceQuery,
    Observer,
    ProgressSnapshot,
    ProgressStatus,
    RecoveryAction,
    RiskLevel,
    RunContext,
)
from feedback.observer import FileProgressObserver


NOW = datetime(2026, 9, 5, 10, 0, 10, tzinfo=timezone.utc)
RUN_ID = "find_001"


def _make_run_context(tmp_path: Path, **overrides: object) -> RunContext:
    values: dict[str, object] = {
        "context_id": "ctx-observer",
        "attempt_index": 0,
        "project_id": "project-observer",
        "request_source": "cli",
        "created_at": NOW - timedelta(minutes=1),
        "producer": "observer-tests",
        "producer_version": "1.0",
        "research_topic": "Reliable Find observation",
        "selection_snapshot_path": str(tmp_path / "selection.json"),
        "selection": {"include_arxiv": True},
        "command_redacted": ["python", "modules/finding/main.py"],
        "working_directory": str(tmp_path),
        "python_executable": "/opt/python",
        "config_snapshot_path": str(tmp_path / "find.config.json"),
        "input_snapshot_path": str(tmp_path / "input.json"),
        "requested_parameters": {"abstract_scoring_max_workers": 2},
        "effective_parameters": {"abstract_scoring_max_workers": 1},
        "expected_artifacts": [
            ArtifactRef(role="result", path="final/find_results.json", required=True)
        ],
        "startup_grace_seconds": 30,
        "stall_suspect_seconds": 60,
        "stall_confirm_seconds": 120,
        "recovery_budget": 1,
        "allowed_recovery_actions": [RecoveryAction.RETRY_NEW_RUN],
        "approval_risk_threshold": RiskLevel.MEDIUM,
        "validation_policy_version": "find.validation.v1",
        "experience_query": ExperienceQuery(limit=5, project_id="project-observer"),
    }
    values.update(overrides)
    return RunContext(**values)  # type: ignore[arg-type]


def _make_execution_handle(
    tmp_path: Path,
    *,
    context_id: str = "ctx-observer",
    run_id: str | None = RUN_ID,
    run_dir: Path | None = None,
    started_at: datetime | None = None,
    process_alive: bool = True,
    exit_code: int | None = None,
) -> ExecutionHandle:
    if run_id is None:
        bound_run_dir = None
    else:
        bound_run_dir = run_dir or (tmp_path / "runs" / run_id)
    return ExecutionHandle(
        context_id=context_id,
        pid=7312,
        started_at=started_at or (NOW - timedelta(seconds=10)),
        process_alive=process_alive,
        stdout_path=str(tmp_path / "find.stdout.log"),
        stderr_path=str(tmp_path / "find.stderr.log"),
        run_id=run_id,
        run_dir=str(bound_run_dir) if bound_run_dir is not None else None,
        exit_code=exit_code,
    )


def _progress_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "run_id": RUN_ID,
        "updated_at": "2026-09-05T10:00:00Z",
        "phase": "abstract_scoring",
        "counts": {
            "raw_title_index": 20,
            "abstract_scored_papers": 2,
        },
        "live_progress": {
            "phase": "abstract_scoring",
            "current": 2,
            "total": 5,
            "percent": 40,
            "message": "Scoring abstracts",
        },
        "source_status": [],
    }
    payload.update(overrides)
    return payload


def _write_progress(
    run_dir: Path,
    payload: dict[str, object],
    *,
    preferred: bool = True,
) -> Path:
    path = run_dir / "logs" / "find_progress.json" if preferred else run_dir / "find_progress.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _artifact(snapshot: ProgressSnapshot, role: str) -> ArtifactRef:
    return next(item for item in snapshot.artifact_observations if item.role == role)


def _set_now(monkeypatch: pytest.MonkeyPatch, value: datetime) -> None:
    monkeypatch.setattr(observer_module, "_utc_now", lambda: value)


def test_file_progress_observer_matches_protocol_and_public_import() -> None:
    from feedback import FileProgressObserver as PublicFileProgressObserver

    assert PublicFileProgressObserver is FileProgressObserver
    assert "FileProgressObserver" in feedback.__all__
    assert inspect.signature(FileProgressObserver.observe) == inspect.signature(Observer.observe)
    assert get_type_hints(FileProgressObserver.observe) == get_type_hints(Observer.observe)


def test_unbound_run_returns_starting_without_scanning_run_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decoy = tmp_path / ".runtime" / "runs" / "find_decoy"
    _write_progress(decoy, _progress_payload(run_id="find_decoy"))
    stdout_path = tmp_path / "find.stdout.log"
    stdout_path.write_text("starting\n", encoding="utf-8")
    context = _make_run_context(tmp_path)
    handle = _make_execution_handle(tmp_path, run_id=None)
    _set_now(monkeypatch, handle.started_at + timedelta(seconds=5))

    snapshot = FileProgressObserver().observe(context, handle)

    assert snapshot.status is ProgressStatus.STARTING
    assert snapshot.phase == "starting"
    assert snapshot.run_id == ""
    assert snapshot.progress_parse_ok is False
    assert snapshot.result_exists is False
    assert snapshot.source_status_exists is False
    assert _artifact(snapshot, "stdout").exists is True
    assert _artifact(snapshot, "progress").path != str(decoy / "logs" / "find_progress.json")
    assert "not yet bound" in snapshot.status_reason


def test_valid_progress_uses_preferred_paths_and_maps_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "runs" / RUN_ID
    preferred = _write_progress(run_dir, _progress_payload())
    _write_progress(
        run_dir,
        _progress_payload(run_id="find_wrong", phase="wrong_fallback"),
        preferred=False,
    )
    context = _make_run_context(tmp_path)
    handle = _make_execution_handle(tmp_path, run_dir=run_dir)
    _set_now(monkeypatch, NOW)

    snapshot = FileProgressObserver().observe(context, handle)

    assert snapshot.status is ProgressStatus.RUNNING
    assert snapshot.run_id == RUN_ID
    assert snapshot.phase == "abstract_scoring"
    assert snapshot.raw_phase == "abstract_scoring"
    assert (snapshot.current, snapshot.total, snapshot.percent) == (2, 5, 40)
    assert snapshot.message == "Scoring abstracts"
    assert snapshot.counts == {
        "raw_title_index": 20,
        "abstract_scored_papers": 2,
    }
    assert snapshot.progress_updated_at == datetime(2026, 9, 5, 10, 0, tzinfo=timezone.utc)
    assert snapshot.progress_parse_ok is True
    assert _artifact(snapshot, "progress").path == str(preferred)
    assert _artifact(snapshot, "progress").parse_status == "valid"


def test_source_status_counts_use_failure_limited_ready_priority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "runs" / RUN_ID
    rows = [
        {"source": "ready", "ok": True},
        {"source": "limited", "ok": True, "limited": True},
        {"source": "failed", "ok": True, "limited": True, "error": "rate limited"},
        {"source": "unknown"},
        "invalid-row",
    ]
    _write_progress(run_dir, _progress_payload(source_status=rows))
    _set_now(monkeypatch, NOW)

    snapshot = FileProgressObserver().observe(
        _make_run_context(tmp_path),
        _make_execution_handle(tmp_path, run_dir=run_dir),
    )

    assert snapshot.source_total == 4
    assert snapshot.source_ready == 1
    assert snapshot.source_limited == 1
    assert snapshot.source_failed == 1
    assert snapshot.source_ready + snapshot.source_limited + snapshot.source_failed <= snapshot.source_total
    assert any("source_status[4]" in item for item in snapshot.observation_errors)


def test_meaningful_progress_increments_sequence_and_resets_stall_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "runs" / RUN_ID
    progress_path = _write_progress(run_dir, _progress_payload())
    context = _make_run_context(tmp_path)
    handle = _make_execution_handle(tmp_path, run_dir=run_dir)
    _set_now(monkeypatch, NOW)
    previous = FileProgressObserver().observe(context, handle)
    changed = _progress_payload(
        updated_at="2026-09-05T10:00:20Z",
        counts={"raw_title_index": 20, "abstract_scored_papers": 3},
        live_progress={
            "phase": "abstract_scoring",
            "current": 3,
            "total": 5,
            "percent": 60,
            "message": "Scoring abstracts",
        },
    )
    progress_path.write_text(json.dumps(changed), encoding="utf-8")
    later = NOW + timedelta(seconds=20)
    _set_now(monkeypatch, later)

    snapshot = FileProgressObserver().observe(context, handle, previous)

    assert snapshot.sequence == 1
    assert snapshot.current == 3
    assert snapshot.seconds_without_progress == 0
    assert snapshot.last_meaningful_change_at == later


@pytest.mark.parametrize(
    ("seconds", "expected_status"),
    [
        (60, ProgressStatus.SUSPECTED_STALL),
        (120, ProgressStatus.STALLED),
    ],
)
def test_unchanged_progress_reaches_stall_thresholds_without_sleep(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seconds: int,
    expected_status: ProgressStatus,
) -> None:
    run_dir = tmp_path / "runs" / RUN_ID
    _write_progress(run_dir, _progress_payload())
    context = _make_run_context(tmp_path)
    handle = _make_execution_handle(tmp_path, run_dir=run_dir)
    _set_now(monkeypatch, NOW)
    previous = FileProgressObserver().observe(context, handle)
    _set_now(monkeypatch, NOW + timedelta(seconds=seconds))

    snapshot = FileProgressObserver().observe(context, handle, previous)

    assert snapshot.status is expected_status
    assert snapshot.seconds_without_progress == seconds
    assert snapshot.last_meaningful_change_at == NOW


@pytest.mark.parametrize(
    ("process_alive", "exit_code", "cancel_requested", "expected_status"),
    [
        (False, 0, False, ProgressStatus.COMPLETED),
        (False, 7, False, ProgressStatus.FAILED),
        (False, 0, True, ProgressStatus.CANCELLED),
        (False, None, False, ProgressStatus.UNKNOWN),
    ],
)
def test_finished_process_statuses_follow_execution_handle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_alive: bool,
    exit_code: int | None,
    cancel_requested: bool,
    expected_status: ProgressStatus,
) -> None:
    run_dir = tmp_path / "runs" / RUN_ID
    _write_progress(run_dir, _progress_payload())
    result_path = run_dir / "final" / "find_results.json"
    result_path.parent.mkdir(parents=True)
    result_path.write_text(json.dumps({"run_id": RUN_ID}), encoding="utf-8")
    context = _make_run_context(tmp_path)
    handle = _make_execution_handle(
        tmp_path,
        run_dir=run_dir,
        process_alive=process_alive,
        exit_code=exit_code,
    )
    _set_now(monkeypatch, NOW)

    snapshot = FileProgressObserver().observe(
        context,
        handle,
        cancel_requested=cancel_requested,
    )

    assert snapshot.status is expected_status
    assert snapshot.process_alive is process_alive
    assert snapshot.exit_code == exit_code
    assert snapshot.result_exists is True
    assert snapshot.result_size_bytes == result_path.stat().st_size
    assert _artifact(snapshot, "result").parse_status == "valid"


def test_live_cancel_request_does_not_claim_process_is_cancelled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "runs" / RUN_ID
    _write_progress(run_dir, _progress_payload())
    _set_now(monkeypatch, NOW)

    snapshot = FileProgressObserver().observe(
        _make_run_context(tmp_path),
        _make_execution_handle(tmp_path, run_dir=run_dir),
        cancel_requested=True,
    )

    assert snapshot.cancel_requested is True
    assert snapshot.status is ProgressStatus.RUNNING


def test_missing_progress_is_reported_without_raising(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "runs" / RUN_ID
    run_dir.mkdir(parents=True)
    context = _make_run_context(tmp_path)
    handle = _make_execution_handle(tmp_path, run_dir=run_dir)
    _set_now(monkeypatch, handle.started_at + timedelta(seconds=5))

    snapshot = FileProgressObserver().observe(context, handle)

    assert snapshot.status is ProgressStatus.STARTING
    assert snapshot.progress_parse_ok is False
    assert _artifact(snapshot, "progress").parse_status == "missing"
    assert any("progress file is missing" in item for item in snapshot.observation_errors)


def test_invalid_progress_json_degrades_to_unknown_after_startup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "runs" / RUN_ID
    progress_path = run_dir / "logs" / "find_progress.json"
    progress_path.parent.mkdir(parents=True)
    progress_path.write_text('{"run_id":', encoding="utf-8")
    context = _make_run_context(tmp_path)
    handle = _make_execution_handle(
        tmp_path,
        run_dir=run_dir,
        started_at=NOW - timedelta(seconds=40),
    )
    _set_now(monkeypatch, NOW)

    snapshot = FileProgressObserver().observe(context, handle)

    assert snapshot.status is ProgressStatus.UNKNOWN
    assert snapshot.progress_parse_ok is False
    assert _artifact(snapshot, "progress").parse_status == "invalid"
    assert snapshot.observation_errors


def test_invalid_progress_fields_are_ignored_without_fabricating_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "runs" / RUN_ID
    _write_progress(
        run_dir,
        _progress_payload(
            updated_at="2026-09-05T10:00:00",
            counts={"valid": 2, "bool": True, "negative": -1, "text": "3"},
            live_progress={
                "current": 8,
                "total": 5,
                "percent": 120,
                "message": 42,
            },
        ),
    )
    _set_now(monkeypatch, NOW)

    snapshot = FileProgressObserver().observe(
        _make_run_context(tmp_path),
        _make_execution_handle(tmp_path, run_dir=run_dir),
    )

    assert snapshot.counts == {"valid": 2}
    assert snapshot.current is None
    assert snapshot.total == 5
    assert snapshot.percent is None
    assert snapshot.message is None
    assert snapshot.progress_updated_at is None
    assert len(snapshot.observation_errors) >= 5


def test_broken_progress_preserves_last_known_values_without_new_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "runs" / RUN_ID
    progress_path = _write_progress(run_dir, _progress_payload())
    context = _make_run_context(tmp_path)
    handle = _make_execution_handle(tmp_path, run_dir=run_dir)
    _set_now(monkeypatch, NOW)
    previous = FileProgressObserver().observe(context, handle)
    progress_path.write_text("not-json", encoding="utf-8")
    _set_now(monkeypatch, NOW + timedelta(seconds=60))

    snapshot = FileProgressObserver().observe(context, handle, previous)

    assert snapshot.sequence == 1
    assert snapshot.progress_parse_ok is False
    assert snapshot.phase == previous.phase
    assert snapshot.counts == previous.counts
    assert snapshot.current == previous.current
    assert snapshot.message == previous.message
    assert snapshot.status is ProgressStatus.SUSPECTED_STALL
    assert snapshot.seconds_without_progress == 60


def test_progress_run_id_mismatch_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "runs" / RUN_ID
    _write_progress(run_dir, _progress_payload(run_id="find_other"))
    _set_now(monkeypatch, NOW)

    with pytest.raises(ValueError, match="progress run_id does not match"):
        FileProgressObserver().observe(
            _make_run_context(tmp_path),
            _make_execution_handle(tmp_path, run_dir=run_dir),
        )


def test_fallback_artifacts_and_log_tail_signals_are_observed_read_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "runs" / RUN_ID
    progress_path = _write_progress(run_dir, _progress_payload(), preferred=False)
    result_path = run_dir / "find_results.json"
    result_path.write_text(json.dumps({"run_id": RUN_ID}), encoding="utf-8")
    source_path = run_dir / "source_status.md"
    source_path.write_text("HTTP 429 Too Many Requests; rate-limited", encoding="utf-8")
    stdout_path = tmp_path / "find.stdout.log"
    stderr_path = tmp_path / "find.stderr.log"
    stdout_path.write_bytes(("Traceback (most recent call last)\n" + "x" * 70000).encode())
    stderr_path.write_text(
        "HTTP 429 Too Many Requests\nrequest timed out\n",
        encoding="utf-8",
    )
    files = (progress_path, result_path, source_path, stdout_path, stderr_path)
    before = {path: path.read_bytes() for path in files}
    _set_now(monkeypatch, NOW)

    snapshot = FileProgressObserver().observe(
        _make_run_context(tmp_path),
        _make_execution_handle(tmp_path, run_dir=run_dir),
    )

    assert _artifact(snapshot, "progress").path == str(progress_path)
    assert _artifact(snapshot, "result").path == str(result_path)
    assert _artifact(snapshot, "source_status").path == str(source_path)
    assert snapshot.source_status_exists is True
    assert "http_429" in snapshot.source_signals
    assert "rate_limited" in snapshot.source_signals
    assert "timeout" in snapshot.signals
    assert "traceback" not in snapshot.signals
    assert all(path.read_bytes() == before[path] for path in files)
    assert not any("Too Many Requests" in item for item in snapshot.observation_errors)


@pytest.mark.parametrize(
    ("argument", "value", "message"),
    [
        ("run_context", object(), "run_context must be a RunContext"),
        ("execution_handle", object(), "execution_handle must be an ExecutionHandle"),
        ("previous_snapshot", object(), "previous_snapshot must be a ProgressSnapshot or None"),
        ("cancel_requested", 1, "cancel_requested must be a bool"),
    ],
)
def test_observer_rejects_invalid_input_types(
    tmp_path: Path,
    argument: str,
    value: object,
    message: str,
) -> None:
    arguments: dict[str, object] = {
        "run_context": _make_run_context(tmp_path),
        "execution_handle": _make_execution_handle(tmp_path),
        "previous_snapshot": None,
        "cancel_requested": False,
    }
    arguments[argument] = value

    with pytest.raises(TypeError, match=message):
        FileProgressObserver().observe(**arguments)  # type: ignore[arg-type]


def test_observer_rejects_context_and_previous_run_conflicts(tmp_path: Path) -> None:
    context = _make_run_context(tmp_path)
    wrong_context_handle = _make_execution_handle(tmp_path, context_id="ctx-other")

    with pytest.raises(ValueError, match="context_id does not match"):
        FileProgressObserver().observe(context, wrong_context_handle)

    handle = _make_execution_handle(tmp_path)
    previous = ProgressSnapshot(
        snapshot_id="snapshot-other",
        run_id="find_other",
        created_at=NOW,
        observed_at=NOW,
        sequence=0,
        producer="observer-tests",
        producer_version="1.0",
        status=ProgressStatus.RUNNING,
        phase="finding",
        counts={},
        elapsed_seconds=0,
        seconds_without_progress=0,
        process_alive=True,
        cancel_requested=False,
        artifact_observations=[],
        progress_parse_ok=True,
        result_exists=False,
        source_status_exists=False,
        source_total=0,
        source_ready=0,
        source_limited=0,
        source_failed=0,
        status_reason="test",
    )
    with pytest.raises(ValueError, match="previous snapshot run_id does not match"):
        FileProgressObserver().observe(context, handle, previous)


def test_observe_does_not_modify_input_contracts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "runs" / RUN_ID
    _write_progress(run_dir, _progress_payload())
    context = _make_run_context(tmp_path)
    handle = _make_execution_handle(tmp_path, run_dir=run_dir)
    _set_now(monkeypatch, NOW)
    previous = FileProgressObserver().observe(context, handle)
    before = (context.to_json(), handle.to_json(), previous.to_json())
    _set_now(monkeypatch, NOW + timedelta(seconds=1))

    FileProgressObserver().observe(context, handle, previous)

    assert (context.to_json(), handle.to_json(), previous.to_json()) == before
