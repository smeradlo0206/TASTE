"""Concrete subprocess launcher for a prepared Find run."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import subprocess
from threading import Lock

from .contracts import ExecutionHandle, RunContext


class SubprocessFindExecutor:
    """Start Find from a RunContext and return its initial process facts."""

    def __init__(self) -> None:
        self._handoff_lock = Lock()
        self._pending_processes: dict[
            str,
            tuple[ExecutionHandle, subprocess.Popen[bytes]],
        ] = {}

    def execute(self, run_context: RunContext) -> ExecutionHandle:
        """Start Find through the stable Executor contract."""
        if not isinstance(run_context, RunContext):
            raise TypeError("run_context must be a RunContext")

        with self._handoff_lock:
            if run_context.context_id in self._pending_processes:
                raise RuntimeError(
                    "a Find process for this context_id is awaiting driver handoff"
                )
            execution_handle, process = self._start_process(run_context)
            self._pending_processes[run_context.context_id] = (
                execution_handle,
                process,
            )
            return execution_handle

    def get_process(
        self,
        execution_handle: ExecutionHandle,
    ) -> subprocess.Popen[bytes]:
        """Transfer the process created by ``execute`` to its current driver."""
        if not isinstance(execution_handle, ExecutionHandle):
            raise TypeError("execution_handle must be an ExecutionHandle")

        with self._handoff_lock:
            pending = self._pending_processes.get(execution_handle.context_id)
            if pending is None:
                raise RuntimeError("no pending Find process exists for this ExecutionHandle")
            expected_handle, process = pending
            if (
                expected_handle is not execution_handle
                or expected_handle.context_id != execution_handle.context_id
                or expected_handle.pid != execution_handle.pid
                or process.pid != execution_handle.pid
            ):
                raise RuntimeError("ExecutionHandle does not match the pending Find process")
            del self._pending_processes[execution_handle.context_id]
            return process

    def _start_process(
        self,
        run_context: RunContext,
    ) -> tuple[ExecutionHandle, subprocess.Popen[bytes]]:
        """Perform the one concrete Find launch used by ``execute``."""

        working_directory = Path(run_context.working_directory)
        if not working_directory.is_dir():
            raise RuntimeError("working directory does not exist")

        python_executable = Path(run_context.python_executable)
        if not python_executable.is_file():
            raise RuntimeError("python executable does not exist")

        entrypoint = Path(run_context.entrypoint)
        if not entrypoint.is_absolute():
            entrypoint = working_directory / entrypoint
        if not entrypoint.is_file():
            raise RuntimeError("Find entrypoint does not exist")

        config_snapshot_path = Path(run_context.config_snapshot_path)
        if not config_snapshot_path.is_file():
            raise RuntimeError("config snapshot does not exist")

        input_snapshot_path = Path(run_context.input_snapshot_path)
        if not input_snapshot_path.is_file():
            raise RuntimeError("input snapshot does not exist")

        command = [
            run_context.python_executable,
            str(entrypoint),
            "--action",
            run_context.action,
            "--config-json",
            run_context.config_snapshot_path,
            "--input-json",
            run_context.input_snapshot_path,
        ]
        stdout_path = config_snapshot_path.parent / "find.stdout.log"
        stderr_path = config_snapshot_path.parent / "find.stderr.log"
        started_at = datetime.now(timezone.utc)

        try:
            with stdout_path.open("wb") as stdout_stream, stderr_path.open(
                "wb"
            ) as stderr_stream:
                try:
                    process = subprocess.Popen(
                        command,
                        cwd=str(working_directory),
                        stdout=stdout_stream,
                        stderr=stderr_stream,
                    )
                except OSError as exc:
                    raise RuntimeError("failed to start Find process") from exc
        except OSError as exc:
            raise RuntimeError("failed to prepare Find log files") from exc

        execution_handle = ExecutionHandle(
            context_id=run_context.context_id,
            pid=process.pid,
            started_at=started_at,
            process_alive=True,
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
            run_id=None,
            run_dir=None,
            exit_code=None,
        )
        return execution_handle, process
