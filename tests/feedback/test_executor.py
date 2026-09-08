from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
import time

import pytest

import feedback
import feedback.executor as executor_module
from feedback import (
    ArtifactRef,
    ExecutionHandle,
    Executor,
    ExperienceQuery,
    RecoveryAction,
    RiskLevel,
    RunContext,
    SubprocessFindExecutor,
)


OBSERVED_AT = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


def _make_run_context(tmp_path: Path) -> RunContext:
    working_directory = tmp_path / "workspace"
    entrypoint = working_directory / "modules" / "finding" / "main.py"
    config_path = tmp_path / "input" / "find.config.json"
    input_path = tmp_path / "input" / "input.json"
    entrypoint.parent.mkdir(parents=True)
    config_path.parent.mkdir(parents=True)
    entrypoint.write_text("print('fake find')\n", encoding="utf-8")
    config_path.write_text(
        '{"schema_version": 1, "config": {}, "selection": {}}\n',
        encoding="utf-8",
    )
    input_path.write_text('{"research_topic": "Executor test"}\n', encoding="utf-8")

    return RunContext(
        context_id="ctx-executor-test",
        attempt_index=0,
        project_id="project-001",
        request_source="cli",
        created_at=OBSERVED_AT,
        producer="executor-tests",
        producer_version="1.0",
        research_topic="Executor test",
        selection_snapshot_path=str(tmp_path / "input" / "selection.json"),
        selection={"include_arxiv": True},
        command_redacted=["redacted-python", "modules/finding/main.py"],
        working_directory=str(working_directory),
        python_executable=sys.executable,
        config_snapshot_path=str(config_path),
        input_snapshot_path=str(input_path),
        requested_parameters={"abstract_scoring_max_workers": 1},
        effective_parameters={"abstract_scoring_max_workers": 1},
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
        experience_query=ExperienceQuery(limit=1),
    )


class _FakeProcess:
    pid = 4321

    def wait(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("Executor must not wait for the child process")

    def communicate(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("Executor must not communicate with the child process")


def _capture_popen(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    captured: dict[str, object] = {"calls": 0}

    def fake_popen(*args: object, **kwargs: object) -> _FakeProcess:
        captured["calls"] += 1
        captured["args"] = args
        captured["kwargs"] = kwargs
        process = _FakeProcess()
        captured["process"] = process
        return process

    monkeypatch.setattr(executor_module.subprocess, "Popen", fake_popen)
    return captured


def test_execute_rejects_non_run_context() -> None:
    with pytest.raises(TypeError, match="run_context must be a RunContext"):
        SubprocessFindExecutor().execute(object())  # type: ignore[arg-type]


def test_execute_builds_command_from_run_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_context = _make_run_context(tmp_path)
    captured = _capture_popen(monkeypatch)

    SubprocessFindExecutor().execute(run_context)

    command = captured["args"][0]
    expected_entrypoint = (
        Path(run_context.working_directory) / run_context.entrypoint
    )
    assert command == [
        run_context.python_executable,
        str(expected_entrypoint),
        "--action",
        run_context.action,
        "--config-json",
        run_context.config_snapshot_path,
        "--input-json",
        run_context.input_snapshot_path,
    ]
    assert captured["kwargs"]["cwd"] == run_context.working_directory
    assert captured["calls"] == 1
    assert "shell" not in captured["kwargs"]
    assert run_context.command_redacted not in captured["args"]


def test_execute_returns_initial_execution_handle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_context = _make_run_context(tmp_path)
    _capture_popen(monkeypatch)

    handle = SubprocessFindExecutor().execute(run_context)

    assert isinstance(handle, ExecutionHandle)
    assert handle.context_id == run_context.context_id
    assert handle.pid == _FakeProcess.pid
    assert handle.started_at.tzinfo is timezone.utc
    assert handle.process_alive is True
    assert handle.stdout_path
    assert handle.stderr_path
    assert handle.run_id is None
    assert handle.run_dir is None
    assert handle.exit_code is None


def test_get_process_returns_the_process_created_by_execute_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_context = _make_run_context(tmp_path)
    captured = _capture_popen(monkeypatch)
    executor = SubprocessFindExecutor()

    handle = executor.execute(run_context)
    process = executor.get_process(handle)

    assert captured["calls"] == 1
    assert process is captured["process"]
    assert executor._pending_processes == {}
    with pytest.raises(RuntimeError, match="no pending Find process"):
        executor.get_process(handle)


def test_get_process_rejects_unknown_or_forged_execution_handles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_context = _make_run_context(tmp_path)
    _capture_popen(monkeypatch)
    executor = SubprocessFindExecutor()
    handle = executor.execute(run_context)
    forged = ExecutionHandle(
        context_id=handle.context_id,
        pid=handle.pid + 1,
        started_at=handle.started_at,
        process_alive=True,
        stdout_path=handle.stdout_path,
        stderr_path=handle.stderr_path,
    )
    unknown = ExecutionHandle(
        context_id="ctx-unknown",
        pid=handle.pid,
        started_at=handle.started_at,
        process_alive=True,
        stdout_path=handle.stdout_path,
        stderr_path=handle.stderr_path,
    )

    with pytest.raises(TypeError, match="execution_handle must be an ExecutionHandle"):
        executor.get_process(object())  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="does not match"):
        executor.get_process(forged)
    with pytest.raises(RuntimeError, match="no pending Find process"):
        executor.get_process(unknown)
    assert executor.get_process(handle).pid == handle.pid


def test_execute_rejects_context_id_awaiting_handoff_without_starting_again(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_context = _make_run_context(tmp_path)
    captured = _capture_popen(monkeypatch)
    executor = SubprocessFindExecutor()

    handle = executor.execute(run_context)
    with pytest.raises(RuntimeError, match="awaiting driver handoff"):
        executor.execute(run_context)

    assert captured["calls"] == 1
    assert executor.get_process(handle).pid == handle.pid


def test_execute_redirects_output_to_files_instead_of_pipe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_context = _make_run_context(tmp_path)
    captured = _capture_popen(monkeypatch)

    handle = SubprocessFindExecutor().execute(run_context)

    stdout_stream = captured["kwargs"]["stdout"]
    stderr_stream = captured["kwargs"]["stderr"]
    log_directory = Path(run_context.config_snapshot_path).parent
    assert stdout_stream is not executor_module.subprocess.PIPE
    assert stderr_stream is not executor_module.subprocess.PIPE
    assert Path(handle.stdout_path) == log_directory / "find.stdout.log"
    assert Path(handle.stderr_path) == log_directory / "find.stderr.log"
    assert Path(stdout_stream.name) == Path(handle.stdout_path)
    assert Path(stderr_stream.name) == Path(handle.stderr_path)


def test_execute_closes_parent_log_handles_after_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_context = _make_run_context(tmp_path)
    captured = _capture_popen(monkeypatch)

    SubprocessFindExecutor().execute(run_context)

    assert captured["kwargs"]["stdout"].closed
    assert captured["kwargs"]["stderr"].closed


def test_execute_closes_log_handles_when_popen_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_context = _make_run_context(tmp_path)
    captured: dict[str, object] = {"calls": 0}

    def fail_popen(*args: object, **kwargs: object) -> None:
        captured["calls"] += 1
        captured["stdout"] = kwargs["stdout"]
        captured["stderr"] = kwargs["stderr"]
        raise OSError("cannot start")

    monkeypatch.setattr(executor_module.subprocess, "Popen", fail_popen)

    executor = SubprocessFindExecutor()
    with pytest.raises(RuntimeError, match="failed to start Find process") as error:
        executor.execute(run_context)

    assert isinstance(error.value.__cause__, OSError)
    assert captured["calls"] == 1
    assert captured["stdout"].closed
    assert captured["stderr"].closed
    assert executor._pending_processes == {}


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    [
        ("working_directory", "missing-workspace", "working directory does not exist"),
        ("python_executable", "missing-python", "python executable does not exist"),
        ("entrypoint", "missing-entrypoint.py", "Find entrypoint does not exist"),
        ("config_snapshot_path", "missing-find.config.json", "config snapshot does not exist"),
        ("input_snapshot_path", "missing-input.json", "input snapshot does not exist"),
    ],
)
def test_execute_rejects_missing_launch_paths_before_popen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field_name: str,
    value: str,
    message: str,
) -> None:
    run_context = _make_run_context(tmp_path)
    if field_name == "working_directory":
        run_context.working_directory = str(tmp_path / value)
    elif field_name == "python_executable":
        run_context.python_executable = str(tmp_path / value)
    elif field_name == "entrypoint":
        run_context.entrypoint = value
    elif field_name == "config_snapshot_path":
        run_context.config_snapshot_path = str(tmp_path / value)
    else:
        run_context.input_snapshot_path = str(tmp_path / value)
    captured = _capture_popen(monkeypatch)

    with pytest.raises(RuntimeError, match=message):
        SubprocessFindExecutor().execute(run_context)

    assert captured["calls"] == 0


def test_execute_does_not_mutate_run_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_context = _make_run_context(tmp_path)
    before = run_context.to_dict()
    _capture_popen(monkeypatch)

    SubprocessFindExecutor().execute(run_context)

    assert run_context.to_dict() == before


def test_execute_writes_local_process_output_without_network(tmp_path: Path) -> None:
    run_context = _make_run_context(tmp_path)
    entrypoint = Path(run_context.working_directory) / run_context.entrypoint
    entrypoint.write_text(
        "import sys\n"
        "print('executor stdout marker', flush=True)\n"
        "print('executor stderr marker', file=sys.stderr, flush=True)\n",
        encoding="utf-8",
    )

    executor = SubprocessFindExecutor()
    handle = executor.execute(run_context)
    process = executor.get_process(handle)
    stdout_path = Path(handle.stdout_path)
    stderr_path = Path(handle.stderr_path)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if (
            stdout_path.exists()
            and stderr_path.exists()
            and "executor stdout marker" in stdout_path.read_text(encoding="utf-8")
            and "executor stderr marker" in stderr_path.read_text(encoding="utf-8")
        ):
            break
        time.sleep(0.05)
    else:
        pytest.fail("local Find test process did not write both log markers")

    assert process.wait(timeout=5) == 0

    assert "executor stdout marker" in stdout_path.read_text(encoding="utf-8")
    assert "executor stderr marker" in stderr_path.read_text(encoding="utf-8")
    assert executor._pending_processes == {}


def test_subprocess_find_executor_is_public_and_matches_protocol() -> None:
    executor: Executor = SubprocessFindExecutor()

    assert isinstance(executor, SubprocessFindExecutor)
    assert feedback.SubprocessFindExecutor is SubprocessFindExecutor
    assert "SubprocessFindExecutor" in feedback.__all__
