"""Read-only file observer for one existing Find run."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from uuid import uuid4

from .contracts import (
    ArtifactRef,
    EvidenceFact,
    ExecutionHandle,
    ProgressSnapshot,
    ProgressStatus,
    RunContext,
)


_LOG_TAIL_BYTES = 64 * 1024
_PRODUCER = "file_progress_observer"
_PRODUCER_VERSION = "1.0"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _required_for(run_context: RunContext, role: str, path: Path) -> bool:
    for expected in run_context.expected_artifacts:
        if expected.role == role or Path(expected.path).name == path.name:
            return expected.required
    return False


def _select_path(run_dir: Path, preferred: str, fallback: str) -> Path:
    preferred_path = run_dir / preferred
    if preferred_path.exists():
        return preferred_path
    fallback_path = run_dir / fallback
    if fallback_path.exists():
        return fallback_path
    return preferred_path


def _artifact(
    path: Path,
    *,
    role: str,
    required: bool,
    parse_status: str | None,
    observation_errors: list[str],
) -> ArtifactRef:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return ArtifactRef(
            role=role,
            path=str(path),
            required=required,
            exists=False,
            parse_status=parse_status,
        )
    except OSError:
        observation_errors.append(f"{role} file metadata could not be read")
        return ArtifactRef(
            role=role,
            path=str(path),
            required=required,
            exists=False,
            parse_status=parse_status,
        )
    return ArtifactRef(
        role=role,
        path=str(path),
        required=required,
        exists=True,
        size_bytes=stat.st_size,
        modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
        parse_status=parse_status,
    )


def _read_json_object(
    path: Path,
    *,
    role: str,
    required: bool,
    observation_errors: list[str],
) -> tuple[ArtifactRef, dict[str, object] | None]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        observation_errors.append(f"{role} file is missing")
        return (
            _artifact(
                path,
                role=role,
                required=required,
                parse_status="missing",
                observation_errors=observation_errors,
            ),
            None,
        )
    except (OSError, UnicodeError):
        observation_errors.append(f"{role} file could not be read")
        return (
            _artifact(
                path,
                role=role,
                required=required,
                parse_status="invalid",
                observation_errors=observation_errors,
            ),
            None,
        )

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        observation_errors.append(f"{role} JSON could not be parsed")
        return (
            _artifact(
                path,
                role=role,
                required=required,
                parse_status="invalid",
                observation_errors=observation_errors,
            ),
            None,
        )
    if not isinstance(payload, dict):
        observation_errors.append(f"{role} JSON must contain an object")
        return (
            _artifact(
                path,
                role=role,
                required=required,
                parse_status="invalid",
                observation_errors=observation_errors,
            ),
            None,
        )
    return (
        _artifact(
            path,
            role=role,
            required=required,
            parse_status="valid",
            observation_errors=observation_errors,
        ),
        payload,
    )


def _read_tail(path: Path, *, role: str, observation_errors: list[str]) -> str:
    try:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - _LOG_TAIL_BYTES))
            return stream.read(_LOG_TAIL_BYTES).decode("utf-8", errors="replace")
    except FileNotFoundError:
        return ""
    except OSError:
        observation_errors.append(f"{role} file tail could not be read")
        return ""


def _parse_datetime(
    value: object,
    *,
    field_name: str,
    observation_errors: list[str],
) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        observation_errors.append(f"{field_name} must be a timezone-aware timestamp")
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        observation_errors.append(f"{field_name} must be a timezone-aware timestamp")
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        observation_errors.append(f"{field_name} must be a timezone-aware timestamp")
        return None
    return parsed


def _optional_non_negative_int(
    values: dict[str, object],
    field_name: str,
    *,
    observation_errors: list[str],
) -> int | None:
    if field_name not in values:
        return None
    value = values[field_name]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        observation_errors.append(f"live_progress.{field_name} must be a non-negative integer")
        return None
    return value


def _parse_counts(
    value: object,
    *,
    observation_errors: list[str],
) -> dict[str, int]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        observation_errors.append("counts must be an object")
        return {}
    counts: dict[str, int] = {}
    for name, count in value.items():
        if not isinstance(name, str):
            observation_errors.append("counts contains a non-string key")
            continue
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            observation_errors.append(f"counts.{name} must be a non-negative integer")
            continue
        counts[name] = count
    return counts


def _parse_source_status(
    value: object,
    *,
    observation_errors: list[str],
) -> tuple[int, int, int, int]:
    if value is None:
        return 0, 0, 0, 0
    if not isinstance(value, list):
        observation_errors.append("source_status must be a list")
        return 0, 0, 0, 0

    total = ready = limited = failed = 0
    for index, row in enumerate(value):
        if not isinstance(row, dict):
            observation_errors.append(f"source_status[{index}] must be an object")
            continue
        total += 1
        error = row.get("error")
        status = str(row.get("status") or "").strip().lower()
        error_code = str(error or "").strip().lower()
        is_limited = (
            row.get("limited") is True
            or row.get("rate_limited") is True
            or status in {"limited", "rate_limited", "http_429"}
            or error_code in {"rate_limited", "http_429"}
        )
        is_failed = (
            (error is not None and bool(str(error).strip()))
            or row.get("failed") is True
            or row.get("ok") is False
            or status in {"failed", "failure", "error"}
        )
        if is_limited:
            limited += 1
        elif is_failed:
            failed += 1
        elif row.get("ok") is True:
            ready += 1
    return total, ready, limited, failed


def _collect_signals(text: str) -> tuple[set[str], set[str]]:
    lowered = text.lower()
    signals: set[str] = set()
    source_signals: set[str] = set()
    if re.search(
        r"(?:\bhttp\b|https?://|too many requests).{0,80}\b429\b|"
        r"\b429\b.{0,80}(?:\bhttp\b|https?://|too many requests)",
        lowered,
    ):
        source_signals.add("http_429")
    if "rate limit" in lowered or "rate-limit" in lowered:
        source_signals.add("rate_limited")
    if re.search(r"\btimed out\b|\btimeout\b", lowered):
        signals.add("timeout")
    if "traceback (most recent call last)" in lowered:
        signals.add("traceback")
    return signals, source_signals


def _meaningful_progress_changed(
    previous_snapshot: ProgressSnapshot,
    *,
    run_id: str,
    phase: str,
    current: int | None,
    total: int | None,
    percent: int | None,
    counts: dict[str, int],
    progress_updated_at: datetime | None,
    result_exists: bool,
) -> bool:
    return any(
        (
            previous_snapshot.run_id != run_id,
            previous_snapshot.phase != phase,
            previous_snapshot.current != current,
            previous_snapshot.total != total,
            previous_snapshot.percent != percent,
            previous_snapshot.counts != counts,
            previous_snapshot.progress_updated_at != progress_updated_at,
            previous_snapshot.result_exists != result_exists,
        )
    )


def _status_for(
    run_context: RunContext,
    execution_handle: ExecutionHandle,
    previous_snapshot: ProgressSnapshot | None,
    *,
    cancel_requested: bool,
    run_bound: bool,
    progress_parse_ok: bool,
    elapsed_seconds: float,
    seconds_without_progress: float,
    result_exists: bool,
) -> tuple[ProgressStatus, str]:
    if cancel_requested and not execution_handle.process_alive:
        return ProgressStatus.CANCELLED, "Find stopped after cancellation was requested"
    if not execution_handle.process_alive:
        if execution_handle.exit_code is None:
            return ProgressStatus.UNKNOWN, "Find is not alive and its exit code is unavailable"
        if execution_handle.exit_code != 0:
            return ProgressStatus.FAILED, "Find exited with a non-zero exit code"
        if result_exists:
            return ProgressStatus.COMPLETED, "Find exited successfully"
        return ProgressStatus.COMPLETED, "Find exited successfully; result is not yet observed"

    if not run_bound:
        if elapsed_seconds < run_context.startup_grace_seconds:
            return ProgressStatus.STARTING, "Find run is not yet bound"
        return ProgressStatus.UNKNOWN, "Find run is not bound after the startup grace period"

    previous_progress_available = (
        previous_snapshot is not None and previous_snapshot.progress_parse_ok
    )
    if not progress_parse_ok and not previous_progress_available:
        if elapsed_seconds < run_context.startup_grace_seconds:
            return ProgressStatus.STARTING, "Waiting for valid progress within startup grace period"
        return ProgressStatus.UNKNOWN, "Progress is unavailable after the startup grace period"

    if seconds_without_progress >= run_context.stall_confirm_seconds:
        return ProgressStatus.STALLED, "No meaningful progress reached the confirmed stall threshold"
    if seconds_without_progress >= run_context.stall_suspect_seconds:
        return ProgressStatus.SUSPECTED_STALL, "No meaningful progress reached the suspected stall threshold"
    if progress_parse_ok:
        return ProgressStatus.RUNNING, "Valid Find progress was observed"
    return ProgressStatus.RUNNING, "Using the last known progress after an observation error"


class FileProgressObserver:
    """Create one read-only ProgressSnapshot from an existing Find run."""

    def observe(
        self,
        run_context: RunContext,
        execution_handle: ExecutionHandle,
        previous_snapshot: ProgressSnapshot | None = None,
        *,
        cancel_requested: bool = False,
    ) -> ProgressSnapshot:
        if not isinstance(run_context, RunContext):
            raise TypeError("run_context must be a RunContext")
        if not isinstance(execution_handle, ExecutionHandle):
            raise TypeError("execution_handle must be an ExecutionHandle")
        if previous_snapshot is not None and not isinstance(previous_snapshot, ProgressSnapshot):
            raise TypeError("previous_snapshot must be a ProgressSnapshot or None")
        if not isinstance(cancel_requested, bool):
            raise TypeError("cancel_requested must be a bool")
        if run_context.context_id != execution_handle.context_id:
            raise ValueError("RunContext context_id does not match ExecutionHandle context_id")
        if (
            execution_handle.run_id
            and previous_snapshot is not None
            and previous_snapshot.run_id
            and previous_snapshot.run_id != execution_handle.run_id
        ):
            raise ValueError("previous snapshot run_id does not match ExecutionHandle run_id")

        observed_at = _utc_now()
        elapsed_seconds = max(
            0.0,
            (observed_at - execution_handle.started_at).total_seconds(),
        )
        observation_errors: list[str] = []
        signals: set[str] = set()
        source_signals: set[str] = set()

        stdout_path = Path(execution_handle.stdout_path)
        stderr_path = Path(execution_handle.stderr_path)
        stdout_artifact = _artifact(
            stdout_path,
            role="stdout",
            required=False,
            parse_status="not_applicable",
            observation_errors=observation_errors,
        )
        stderr_artifact = _artifact(
            stderr_path,
            role="stderr",
            required=False,
            parse_status="not_applicable",
            observation_errors=observation_errors,
        )
        for path, role in ((stdout_path, "stdout"), (stderr_path, "stderr")):
            log_signals, log_source_signals = _collect_signals(
                _read_tail(path, role=role, observation_errors=observation_errors)
            )
            signals.update(log_signals)
            source_signals.update(log_source_signals)

        run_bound = bool(execution_handle.run_id and execution_handle.run_dir)
        sequence = 0 if previous_snapshot is None else previous_snapshot.sequence + 1
        run_id = execution_handle.run_id or ""
        phase = "starting" if not run_bound else "unknown"
        raw_phase: str | None = None
        counts: dict[str, int] = {}
        current: int | None = None
        total: int | None = None
        percent: int | None = None
        message: str | None = None
        run_started_at: datetime | None = None
        progress_updated_at: datetime | None = None
        source_total = source_ready = source_limited = source_failed = 0
        source_status_observed = False
        progress_parse_ok = False
        result_exists = False
        result_size_bytes: int | None = None
        source_status_exists = False

        if not run_bound:
            observation_errors.append("Find run is not yet bound")
            progress_artifact = ArtifactRef(
                role="progress",
                path="logs/find_progress.json",
                required=_required_for(run_context, "progress", Path("logs/find_progress.json")),
                exists=False,
                parse_status="missing",
            )
            result_artifact = ArtifactRef(
                role="result",
                path="final/find_results.json",
                required=_required_for(run_context, "result", Path("final/find_results.json")),
                exists=False,
                parse_status="missing",
            )
            source_status_artifact = ArtifactRef(
                role="source_status",
                path="reports/source_status.md",
                required=_required_for(
                    run_context,
                    "source_status",
                    Path("reports/source_status.md"),
                ),
                exists=False,
                parse_status="not_applicable",
            )
        else:
            run_dir = Path(execution_handle.run_dir or "")
            progress_path = _select_path(run_dir, "logs/find_progress.json", "find_progress.json")
            result_path = _select_path(run_dir, "final/find_results.json", "find_results.json")
            source_status_path = _select_path(
                run_dir,
                "reports/source_status.md",
                "source_status.md",
            )
            progress_artifact, payload = _read_json_object(
                progress_path,
                role="progress",
                required=_required_for(run_context, "progress", progress_path),
                observation_errors=observation_errors,
            )
            result_artifact, _ = _read_json_object(
                result_path,
                role="result",
                required=_required_for(run_context, "result", result_path),
                observation_errors=observation_errors,
            )
            source_status_artifact = _artifact(
                source_status_path,
                role="source_status",
                required=_required_for(run_context, "source_status", source_status_path),
                parse_status="not_applicable",
                observation_errors=observation_errors,
            )
            result_exists = result_artifact.exists is True
            result_size_bytes = result_artifact.size_bytes if result_exists else None
            source_status_exists = source_status_artifact.exists is True
            if source_status_exists:
                _, markdown_source_signals = _collect_signals(
                    _read_tail(
                        source_status_path,
                        role="source_status",
                        observation_errors=observation_errors,
                    )
                )
                source_signals.update(markdown_source_signals)

            if payload is not None:
                payload_run_id = payload.get("run_id")
                if not isinstance(payload_run_id, str) or not payload_run_id:
                    observation_errors.append("progress run_id is missing or invalid")
                elif payload_run_id != execution_handle.run_id:
                    raise ValueError("progress run_id does not match ExecutionHandle run_id")
                else:
                    progress_parse_ok = True
                    run_id = payload_run_id
                    raw_phase_value = payload.get("phase")
                    if isinstance(raw_phase_value, str) and raw_phase_value:
                        raw_phase = raw_phase_value
                        phase = raw_phase_value
                    else:
                        observation_errors.append("phase is missing or invalid")
                    counts = _parse_counts(payload.get("counts"), observation_errors=observation_errors)
                    live_progress = payload.get("live_progress")
                    if live_progress is None:
                        live_progress_values: dict[str, object] = {}
                    elif isinstance(live_progress, dict):
                        live_progress_values = live_progress
                    else:
                        observation_errors.append("live_progress must be an object")
                        live_progress_values = {}
                    current = _optional_non_negative_int(
                        live_progress_values,
                        "current",
                        observation_errors=observation_errors,
                    )
                    total = _optional_non_negative_int(
                        live_progress_values,
                        "total",
                        observation_errors=observation_errors,
                    )
                    if current is not None and total is not None and current > total:
                        observation_errors.append("live_progress.current must not exceed total")
                        current = None
                    percent = _optional_non_negative_int(
                        live_progress_values,
                        "percent",
                        observation_errors=observation_errors,
                    )
                    if percent is not None and percent > 100:
                        observation_errors.append("live_progress.percent must be between 0 and 100")
                        percent = None
                    message_value = live_progress_values.get("message")
                    if message_value is not None:
                        if isinstance(message_value, str):
                            message = message_value
                        else:
                            observation_errors.append("live_progress.message must be a string")
                    progress_updated_at = _parse_datetime(
                        payload.get("updated_at"),
                        field_name="updated_at",
                        observation_errors=observation_errors,
                    )
                    explicit_run_started_at = payload.get("run_started_at")
                    if explicit_run_started_at is None:
                        explicit_run_started_at = payload.get("started_at")
                    run_started_at = _parse_datetime(
                        explicit_run_started_at,
                        field_name="run_started_at",
                        observation_errors=observation_errors,
                    )
                    source_status = payload.get("source_status")
                    source_status_observed = (
                        "source_status" in payload
                        and isinstance(source_status, list)
                        and all(isinstance(row, dict) for row in source_status)
                    )
                    source_total, source_ready, source_limited, source_failed = _parse_source_status(
                        source_status,
                        observation_errors=observation_errors,
                    )

            if not progress_parse_ok and previous_snapshot is not None:
                run_id = previous_snapshot.run_id or run_id
                phase = previous_snapshot.phase
                raw_phase = previous_snapshot.raw_phase
                counts = dict(previous_snapshot.counts)
                current = previous_snapshot.current
                total = previous_snapshot.total
                percent = previous_snapshot.percent
                message = previous_snapshot.message
                run_started_at = previous_snapshot.run_started_at
                progress_updated_at = previous_snapshot.progress_updated_at
                source_total = previous_snapshot.source_total
                source_ready = previous_snapshot.source_ready
                source_limited = previous_snapshot.source_limited
                source_failed = previous_snapshot.source_failed

        if previous_snapshot is None:
            if progress_parse_ok:
                last_meaningful_change_at = observed_at
                seconds_without_progress = 0.0
            else:
                last_meaningful_change_at = None
                seconds_without_progress = elapsed_seconds
        else:
            changed = _meaningful_progress_changed(
                previous_snapshot,
                run_id=run_id,
                phase=phase,
                current=current,
                total=total,
                percent=percent,
                counts=counts,
                progress_updated_at=progress_updated_at,
                result_exists=result_exists,
            )
            if changed:
                last_meaningful_change_at = observed_at
                seconds_without_progress = 0.0
            else:
                last_meaningful_change_at = (
                    previous_snapshot.last_meaningful_change_at
                    or previous_snapshot.observed_at
                )
                seconds_without_progress = max(
                    0.0,
                    (observed_at - last_meaningful_change_at).total_seconds(),
                )

        status, status_reason = _status_for(
            run_context,
            execution_handle,
            previous_snapshot,
            cancel_requested=cancel_requested,
            run_bound=run_bound,
            progress_parse_ok=progress_parse_ok,
            elapsed_seconds=elapsed_seconds,
            seconds_without_progress=seconds_without_progress,
            result_exists=result_exists,
        )
        snapshot_id = f"snapshot-{uuid4().hex}"
        evidence_facts: list[EvidenceFact] = []
        if run_id:
            evidence_facts.append(
                EvidenceFact(
                    code="find.seconds_without_progress",
                    value=seconds_without_progress,
                    source_contract_id=snapshot_id,
                    source_field="seconds_without_progress",
                    producer=_PRODUCER,
                    run_id=run_id,
                    observed_at=observed_at,
                )
            )
            if source_status_observed:
                evidence_facts.extend(
                    [
                        EvidenceFact(
                            code="find.source_total",
                            value=source_total,
                            source_contract_id=snapshot_id,
                            source_field="source_total",
                            producer=_PRODUCER,
                            run_id=run_id,
                            observed_at=observed_at,
                        ),
                        EvidenceFact(
                            code="find.source_limited",
                            value=source_limited,
                            source_contract_id=snapshot_id,
                            source_field="source_limited",
                            producer=_PRODUCER,
                            run_id=run_id,
                            observed_at=observed_at,
                        ),
                    ]
                )
        return ProgressSnapshot(
            snapshot_id=snapshot_id,
            run_id=run_id,
            created_at=observed_at,
            observed_at=observed_at,
            sequence=sequence,
            producer=_PRODUCER,
            producer_version=_PRODUCER_VERSION,
            status=status,
            phase=phase,
            raw_phase=raw_phase,
            current=current,
            total=total,
            percent=percent,
            message=message,
            run_started_at=run_started_at,
            progress_updated_at=progress_updated_at,
            last_meaningful_change_at=last_meaningful_change_at,
            phase_elapsed_seconds=None,
            counts=counts,
            elapsed_seconds=elapsed_seconds,
            seconds_without_progress=seconds_without_progress,
            process_alive=execution_handle.process_alive,
            cancel_requested=cancel_requested,
            pid=execution_handle.pid,
            process_started_at=execution_handle.started_at,
            exit_code=execution_handle.exit_code,
            termination_signal=None,
            artifact_observations=[
                progress_artifact,
                result_artifact,
                source_status_artifact,
                stdout_artifact,
                stderr_artifact,
            ],
            progress_parse_ok=progress_parse_ok,
            result_exists=result_exists,
            result_size_bytes=result_size_bytes,
            source_status_exists=source_status_exists,
            source_total=source_total,
            source_ready=source_ready,
            source_limited=source_limited,
            source_failed=source_failed,
            status_reason=status_reason,
            signals=sorted(signals),
            source_signals=sorted(source_signals),
            observation_errors=observation_errors,
            evidence_refs=[],
            evidence_facts=evidence_facts,
        )
