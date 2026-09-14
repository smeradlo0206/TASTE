#!/usr/bin/env python3
from __future__ import annotations

import argparse
import atexit
import codecs
import datetime as dt
import hashlib
import json
import os
import shutil
import signal
import select
import subprocess
import time
import sys
from pathlib import Path
from typing import Callable

from project.project_paths import ROOT, build_paths, conda_executable, management_python

from runtime.taste_pythonpath import ensure_taste_pythonpath
ensure_taste_pythonpath(ROOT)
from policies.source_selection import canonical_source_selection, normalize_source_selection
from project.project_paths import build_paths as _build_project_paths
from feedback import ExecutionHandle, RunContext, SubprocessFindExecutor

DEFAULT_ENV = os.environ.get("FIND_ENV_NAME") or os.environ.get("CONDA_ENV_NAME", "")
DEFAULT_CORE_VENUE_IDS = ["openreview_iclr_2026", "openreview_neurips", "dblp_icml", "dblp_kdd"]
DEFAULT_LOCAL_LLM_CONFIG_PATH = ROOT / "modules" / "finding" / "config" / "llm.local.json"
_FIND_LOG_CHUNK_SIZE = 64 * 1024
_FIND_LOG_POLL_INTERVAL_SECONDS = 0.05
_FIND_MONITOR_INTERVAL_SECONDS = 1.0
_FIND_TERMINATE_GRACE_SECONDS = 10
_FIND_TERMINATE_POLL_INTERVAL_SECONDS = 0.05
_FIND_DRIVER_PROCESS_GROUP_ENV = "TASTE_FIND_DRIVER_PROCESS_GROUP"
_FIND_RUN_EVENT_PREFIX = "TASTE_FIND_EVENT "
_FIND_RUN_EVENT_BUFFER_LIMIT = 64 * 1024


class _FindRunBindingParser:
    """Bind one ExecutionHandle from complete structured Find log lines."""

    def __init__(self, runs_dir: Path) -> None:
        self._runs_dir = Path(runs_dir).expanduser().resolve()
        self._buffer = ""

    def consume(self, text: str, *, execution_handle: ExecutionHandle) -> bool:
        """Consume one text chunk and report whether a candidate was rejected."""
        rejected = False
        combined = self._buffer + text
        self._buffer = ""
        for segment in combined.splitlines(keepends=True):
            if segment.endswith(("\n", "\r")):
                rejected = self._consume_line(
                    segment.rstrip("\r\n"),
                    execution_handle,
                ) or rejected
            else:
                self._buffer = segment
        if len(self._buffer) > _FIND_RUN_EVENT_BUFFER_LIMIT:
            rejected = self._buffer.startswith(_FIND_RUN_EVENT_PREFIX) or rejected
            self._buffer = ""
        return rejected

    def _consume_line(
        self,
        line: str,
        execution_handle: ExecutionHandle,
    ) -> bool:
        if not line.startswith(_FIND_RUN_EVENT_PREFIX):
            return False
        try:
            payload = json.loads(line.removeprefix(_FIND_RUN_EVENT_PREFIX))
        except (TypeError, json.JSONDecodeError):
            return True
        if not isinstance(payload, dict) or payload.get("event") != "find_run_created":
            return True
        run_id = payload.get("run_id")
        run_dir_value = payload.get("run_dir")
        if (
            not isinstance(run_id, str)
            or not run_id.strip()
            or not isinstance(run_dir_value, str)
            or not run_dir_value.strip()
        ):
            return True
        run_dir = Path(run_dir_value).expanduser()
        if not run_dir.is_absolute():
            return True
        try:
            resolved_run_dir = run_dir.resolve(strict=True)
        except OSError:
            return True
        if (
            not resolved_run_dir.is_dir()
            or resolved_run_dir.name != run_id
            or not resolved_run_dir.is_relative_to(self._runs_dir)
        ):
            return True
        bound_identity = (execution_handle.run_id, execution_handle.run_dir)
        candidate_identity = (run_id, str(resolved_run_dir))
        if bound_identity == (None, None):
            execution_handle.run_id, execution_handle.run_dir = candidate_identity
            return False
        if bound_identity == candidate_identity:
            return False
        return True


def _start_find_with_executor(
    run_context: RunContext,
) -> tuple[SubprocessFindExecutor, ExecutionHandle, subprocess.Popen[bytes]]:
    """Start the one Find child that the generated driver will supervise."""
    executor = SubprocessFindExecutor()
    execution_handle = executor.execute(run_context)
    process = executor.get_process(execution_handle)
    return executor, execution_handle, process


def _read_execution_log_chunk(
    stream: object,
    decoder: codecs.IncrementalDecoder,
    emit: Callable[[str], None],
    captured: list[str] | None,
    *,
    final: bool = False,
) -> bool:
    """Forward one bounded log chunk and optionally retain stdout for JSON."""
    data = stream.read(_FIND_LOG_CHUNK_SIZE)  # type: ignore[attr-defined]
    if not data:
        if final:
            text = decoder.decode(b"", final=True)
            if text:
                emit(text)
                if captured is not None:
                    captured.append(text)
        return False
    text = decoder.decode(data, final=False)
    if text:
        emit(text)
        if captured is not None:
            captured.append(text)
    return True


def _posix_process_table() -> dict[int, tuple[int, int, str]]:
    """Return PID, parent PID, process group, and state from Linux procfs."""
    if os.name != "posix":
        return {}
    processes: dict[int, tuple[int, int, str]] = {}
    try:
        proc_entries = Path("/proc").iterdir()
    except OSError:
        return processes
    for entry in proc_entries:
        if not entry.name.isdigit():
            continue
        try:
            raw_stat = (entry / "stat").read_text(encoding="utf-8")
            _, fields_text = raw_stat.rsplit(") ", 1)
            fields = fields_text.split()
            if len(fields) < 3:
                continue
            processes[int(entry.name)] = (int(fields[1]), int(fields[2]), fields[0])
        except (OSError, ValueError):
            continue
    return processes


def _find_process_tree_pids(root_pid: int) -> set[int]:
    """Collect only descendants of the Find PID known to this driver."""
    processes = _posix_process_table()
    owned = {root_pid}
    pending = [root_pid]
    while pending:
        parent_pid = pending.pop()
        for pid, (candidate_parent_pid, _, _) in processes.items():
            if candidate_parent_pid == parent_pid and pid not in owned:
                owned.add(pid)
                pending.append(pid)
    return owned


def _driver_process_group_pids() -> set[int]:
    """Return other members only when this process owns a dedicated session."""
    if os.name != "posix":
        return set()
    if os.environ.get(_FIND_DRIVER_PROCESS_GROUP_ENV) != "1":
        return set()
    driver_pid = os.getpid()
    try:
        if os.getpgrp() != driver_pid or os.getsid(driver_pid) != driver_pid:
            return set()
    except OSError:
        return set()
    return {
        pid
        for pid, (_, process_group, state) in _posix_process_table().items()
        if process_group == driver_pid and state != "Z" and pid != driver_pid
    }


def _owned_find_process_pids(process: subprocess.Popen[bytes]) -> set[int]:
    """Resolve the direct Find child plus only processes owned by this driver."""
    owned = _driver_process_group_pids()
    if process.poll() is None:
        owned.update(_find_process_tree_pids(process.pid))
    owned.discard(os.getpid())
    return {pid for pid in owned if pid > 0}


def _live_posix_pids(process_ids: set[int]) -> set[int]:
    """Treat vanished and zombie processes as no longer running."""
    processes = _posix_process_table()
    return {
        pid
        for pid in process_ids
        if pid in processes and processes[pid][2] != "Z"
    }


def _wait_for_posix_pids_to_exit(process_ids: set[int], timeout: float) -> set[int]:
    """Wait a bounded interval for the explicitly owned processes to exit."""
    deadline = time.monotonic() + timeout
    remaining = _live_posix_pids(process_ids)
    while remaining and time.monotonic() < deadline:
        time.sleep(_FIND_TERMINATE_POLL_INTERVAL_SECONDS)
        remaining = _live_posix_pids(remaining)
    return remaining


def _signal_posix_pids(process_ids: set[int], signal_number: int) -> None:
    """Signal exact, already-owned PIDs without process-name matching."""
    for pid in process_ids:
        try:
            os.kill(pid, signal_number)
        except (ProcessLookupError, PermissionError):
            continue


def _stop_find_process(process: subprocess.Popen[bytes]) -> int | None:
    """Stop the direct Find child and only its driver-owned descendants."""
    if os.name != "posix":
        try:
            return_code = process.poll()
            if return_code is not None:
                return process.wait()
            process.terminate()
            try:
                return process.wait(timeout=_FIND_TERMINATE_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                process.kill()
                return process.wait()
        except Exception:
            return process.poll()

    try:
        process_ids = _owned_find_process_pids(process)
        _signal_posix_pids(process_ids, signal.SIGTERM)
        remaining = _wait_for_posix_pids_to_exit(
            process_ids,
            _FIND_TERMINATE_GRACE_SECONDS,
        )
        if remaining:
            _signal_posix_pids(remaining, signal.SIGKILL)
            _wait_for_posix_pids_to_exit(
                remaining,
                _FIND_TERMINATE_GRACE_SECONDS,
            )
        try:
            return process.wait(timeout=_FIND_TERMINATE_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            return process.wait()
    except Exception:
        return process.poll()


def _register_find_process_exit_cleanup(process: subprocess.Popen[bytes]) -> None:
    """Ensure an unhandled generated-driver failure cannot orphan this Find tree."""
    atexit.register(_stop_find_process, process)


def _consume_execution_logs(
    process: subprocess.Popen[bytes],
    execution_handle: ExecutionHandle,
    emit: Callable[[str], None],
    *,
    poll_interval: float = _FIND_LOG_POLL_INTERVAL_SECONDS,
    on_monitor_tick: Callable[[ExecutionHandle], None] | None = None,
) -> str:
    """Forward split Find logs, wait for completion, and retain stdout only."""
    stdout_parts: list[str] = []
    stdout_decoder = codecs.getincrementaldecoder("utf-8")("replace")
    stderr_decoder = codecs.getincrementaldecoder("utf-8")("replace")
    next_monitor_tick = time.monotonic()
    try:
        with Path(execution_handle.stdout_path).open("rb") as stdout_stream, Path(
            execution_handle.stderr_path
        ).open("rb") as stderr_stream:
            while True:
                _read_execution_log_chunk(
                    stdout_stream,
                    stdout_decoder,
                    emit,
                    stdout_parts,
                )
                _read_execution_log_chunk(
                    stderr_stream,
                    stderr_decoder,
                    emit,
                    None,
                )
                if process.poll() is not None:
                    exit_code = process.wait()
                    if exit_code != 0:
                        _stop_find_process(process)
                    while _read_execution_log_chunk(
                        stdout_stream,
                        stdout_decoder,
                        emit,
                        stdout_parts,
                    ):
                        pass
                    while _read_execution_log_chunk(
                        stderr_stream,
                        stderr_decoder,
                        emit,
                        None,
                    ):
                        pass
                    _read_execution_log_chunk(
                        stdout_stream,
                        stdout_decoder,
                        emit,
                        stdout_parts,
                        final=True,
                    )
                    _read_execution_log_chunk(
                        stderr_stream,
                        stderr_decoder,
                        emit,
                        None,
                        final=True,
                    )
                    execution_handle.process_alive = False
                    execution_handle.exit_code = exit_code
                    return "".join(stdout_parts)
                if (
                    on_monitor_tick is not None
                    and time.monotonic() >= next_monitor_tick
                ):
                    try:
                        on_monitor_tick(execution_handle)
                    except Exception as error:
                        emit(
                            "[framework] Feedback monitor callback failed: "
                            + type(error).__name__
                            + "\n"
                        )
                    next_monitor_tick = time.monotonic() + _FIND_MONITOR_INTERVAL_SECONDS
                time.sleep(poll_interval)
    except BaseException:
        exit_code = _stop_find_process(process)
        if exit_code is not None:
            execution_handle.process_alive = False
            execution_handle.exit_code = exit_code
        raise


def _extract_json_tail(text: str) -> dict[str, object]:
    for index in range(len(text) - 1, -1, -1):
        if text[index] != "{":
            continue
        candidate = text[index:].strip()
        try:
            payload = json.loads(candidate)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def _parse_find_cli_result(
    stdout_output: str,
    finding_module: Path,
) -> tuple[str, Path, dict[str, object]]:
    """Resolve only this Find invocation's result from its stdout payload."""
    cli_payload = _extract_json_tail(stdout_output)
    run_id = str(cli_payload.get("run_id") or "")
    run_dir_text = str(cli_payload.get("run_dir") or "")
    if not run_id or not run_dir_text:
        raise RuntimeError("Finding CLI did not return run_id/run_dir")
    directory = Path(run_dir_text)
    if not directory.is_absolute():
        directory = finding_module / directory
    result_path = directory / "find_results.json"
    if not result_path.exists():
        raise RuntimeError("Finding CLI completed but find_results.json is missing: " + str(directory))
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("Finding CLI produced an unreadable find_results.json: " + str(directory)) from exc
    if not isinstance(result, dict):
        raise RuntimeError("Finding CLI produced a non-object find_results.json: " + str(directory))
    return run_id, directory, result


def _terminate_driver_process_group(proc: subprocess.Popen[str]) -> None:
    """Stop the outer driver group, which includes its inherited Find child."""
    if proc.poll() is not None:
        proc.wait()
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except Exception:
        proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            proc.kill()
        proc.wait()


def _local_llm_config_path() -> Path:
    raw = os.environ.get("FINDING_LLM_CONFIG", "").strip()
    return Path(raw).expanduser() if raw else DEFAULT_LOCAL_LLM_CONFIG_PATH


def _load_local_llm_config() -> dict:
    path = _local_llm_config_path()
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


DRIVER_TEMPLATE = r'''
from __future__ import annotations
import json
import os
import shutil
import sys
import time
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

root = Path({root_json})
framework_scripts = root / "framework" / "scripts"
if str(framework_scripts) not in sys.path:
    sys.path.insert(0, str(framework_scripts))
from runtime.taste_pythonpath import ensure_taste_pythonpath
ensure_taste_pythonpath(root)
os.environ["WORKFLOW_RUNTIME_DIR"] = os.environ.get("FINDING_RUNTIME_DIR") or str(root / "modules" / "finding" / ".runtime")

from orchestration.run_frontend import (
    _FindRunBindingParser,
    _consume_execution_logs,
    _parse_find_cli_result,
    _register_find_process_exit_cleanup,
    _start_find_with_executor,
)
from project.project_paths import build_paths, load_project_config
from bridges.reading_bridge import update_project_read_default_after_find
from bridges.sync_outputs import adopt_taste_find_run
from contracts.web_models import AppConfig
from integrations.web_llm import LLMClient
from feedback import (
    FeedbackSupervisor,
    FindFeedbackAdapter,
    FindAnomalyBuilder,
    FindRecoveryApprovalGate,
    FindRecoveryController,
    FindResultValidator,
    FileProgressObserver,
    ExecutionHandle,
    JsonExperienceStore,
    LLMRecoveryAdvisor,
    RecoveryAction,
    RecoveryDecision,
    RunContext,
    SupervisorState,
    SupervisorStatus,
    ValidationStatus,
    build_experience_query,
    build_find_stage_request,
)
from feedback.feedback_adapter import (
    _SAFE_INTEGER_PARAMETERS,
    _sync_runtime_tuning,
)

DEFAULT_CORE_VENUE_IDS = {core_venue_ids_json}
project = {project_json}
max_papers = {max_papers}
max_ideas = {max_ideas}
repair_rounds = {repair_rounds}
include_arxiv = {include_arxiv}
include_huggingface = {include_huggingface}
include_github = {include_github}
use_venues = {use_venues}
source_selection = {source_selection_json}
api_mode = {api_mode_json}
request_source = {request_source_json}
force_new_find = {force_new_find}
restart_full_cycle = {restart_full_cycle}
human_approved_new_find = {human_approved_new_find}
approval_reason = {approval_reason_json}
web_job_id = {web_job_id_json}
paths = build_paths(project)
internal_output_dir_raw = os.environ.get("TASTE_INTERNAL_FIND_OUTPUT_DIR", "").strip()
internal_output_dir = Path(internal_output_dir_raw).expanduser() if internal_output_dir_raw else None
publish_outputs = internal_output_dir is None
finding_module = root / "modules" / "finding"
finding_entrypoint = finding_module / "main.py"
module_find_config_path = finding_module / "config" / "find.config.json"
project_find_config_path = paths.root / "config" / "finding.json"
def read_json_file(path):
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {{}}
    return payload if isinstance(payload, dict) else {{}}

def write_json_file(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

def split_find_config_payload(payload):
    if not isinstance(payload, dict):
        return {{}}, {{}}
    if "config" in payload or "selection" in payload:
        config_payload = payload.get("config") if isinstance(payload.get("config"), dict) else {{}}
        selection_payload = payload.get("selection") if isinstance(payload.get("selection"), dict) else {{}}
        config_payload = dict(config_payload)
    else:
        config_payload = dict(payload)
        selection_payload = {{}}
    embedded_selection = config_payload.pop("default_find_selection", None)
    if isinstance(embedded_selection, dict) and not selection_payload:
        selection_payload = embedded_selection
    return config_payload, dict(selection_payload)

def ensure_project_find_config():
    if not project_find_config_path.exists():
        payload = read_json_file(module_find_config_path)
        if not payload:
            payload = {{"schema_version": 1, "config": {{}}, "selection": dict(source_selection)}}
        write_json_file(project_find_config_path, payload)
    return project_find_config_path

project_find_config_source = ensure_project_find_config()
project_find_config_payload = read_json_file(project_find_config_source)
finding_cfg, configured_selection = split_find_config_payload(project_find_config_payload)
legacy_finding_cfg = {{}}

def local_llm_config_path():
    raw = os.environ.get("FINDING_LLM_CONFIG", "").strip()
    return Path(raw).expanduser() if raw else finding_module / "config" / "llm.local.json"

local_llm_path = local_llm_config_path()
if not os.environ.get("FINDING_LLM_CONFIG", "").strip() and local_llm_path.exists():
    os.environ["FINDING_LLM_CONFIG"] = str(local_llm_path)
local_llm = read_json_file(local_llm_path)
input_dir_raw = os.environ.get("TASTE_FIND_INPUT_DIR", "").strip()
input_dir = Path(input_dir_raw).expanduser() if input_dir_raw else paths.root / "tmp" / "finding" / "input"
input_dir.mkdir(parents=True, exist_ok=True)
cfg = load_project_config(project)
legacy_finding_cfg = cfg.get("finding", {{}}) if isinstance(cfg.get("finding", {{}}), dict) else {{}}
for _key, _value in legacy_finding_cfg.items():
    if _key not in finding_cfg and _key not in {{"api_key", "email", "llm_roles", "provider", "base_url", "model", "temperature", "default_find_selection"}}:
        finding_cfg[_key] = _value
api_key_env = os.environ.get("LLM_API_KEY_ENV") or "OPENAI_API_KEY"
api_key = (os.environ.get(api_key_env, "") if api_key_env else "") or os.environ.get("LLM_API_KEY", "") or local_llm.get("api_key", "")
api_base = os.environ.get("LLM_API_BASE") or local_llm.get("base_url") or "https://api.openai.com/v1"
model = os.environ.get("LLM_MODEL") or local_llm.get("model") or "mock-model"
provider = os.environ.get("LLM_PROVIDER") or local_llm.get("provider") or "mock"
if not (api_base and model and api_key):
    provider = "mock"

def env_int(name, default):
    raw = os.environ.get(name, "")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 0
    return value if value > 0 else int(default)

research_goal = "\n".join([str(cfg.get("topic", "")), str(cfg.get("user_prompt", "")), ", ".join(cfg.get("queries", []))])
configured_topic = str(finding_cfg.get("research_topic") or cfg.get("topic") or research_goal).strip()

topic_queries = []
extra_queries = []
for raw in os.environ.get("EXTRA_QUERIES", "").splitlines():
    raw = raw.strip()
    if not raw:
        continue
    try:
        decoded = json.loads(raw)
    except Exception:
        decoded = raw
    if isinstance(decoded, list):
        extra_queries.extend(str(item).strip() for item in decoded if str(item).strip())
    elif isinstance(decoded, str) and decoded.strip():
        extra_queries.extend(item.strip() for item in decoded.replace(";", "\n").split("\n") if item.strip())
if os.environ.get("EXTRA_QUERY", "").strip():
    extra_queries.extend(item.strip() for item in os.environ.get("EXTRA_QUERY", "").replace(";", "\n").split("\n") if item.strip())
for item in extra_queries:
    if item and item not in topic_queries:
        topic_queries.append(item)
for item in cfg.get("queries", []):
    if isinstance(item, str) and item.strip() and item.strip() not in topic_queries:
        topic_queries.append(item.strip())

deep_survey = {deep_survey}
fast_mode = {fast_mode}
DEFAULT_ARXIV_WINDOW_DAYS = 180
literature_cfg = cfg.get("literature", {{}}) if isinstance(cfg.get("literature", {{}}), dict) else {{}}
for _key, _value in finding_cfg.items():
    if _key not in literature_cfg and _key not in {{"api_key", "email", "llm_roles"}}:
        literature_cfg[_key] = _value

def config_positive_int(name, default):
    try:
        value = int(literature_cfg.get(name) or 0)
    except (TypeError, ValueError):
        value = 0
    return value if value > 0 else int(default)

def config_nonnegative_int(name, default=0):
    try:
        return max(0, int(literature_cfg.get(name) if literature_cfg.get(name) is not None else default))
    except (TypeError, ValueError):
        return max(0, int(default))

arxiv_default_window_days = DEFAULT_ARXIV_WINDOW_DAYS if deep_survey else literature_cfg.get("arxiv_window_days", DEFAULT_ARXIV_WINDOW_DAYS) or DEFAULT_ARXIV_WINDOW_DAYS
arxiv_window_days = env_int("WINDOW_DAYS", arxiv_default_window_days)
nonvenue_fetch_limit = config_positive_int("nonvenue_fetch_limit", 5000)
venue_scan_limit = config_nonnegative_int("venue_title_scan_limit", 0)
if str(os.environ.get("VENUE_TITLE_SCAN_LIMIT") or "").strip():
    try:
        venue_scan_limit = max(0, int(os.environ["VENUE_TITLE_SCAN_LIMIT"]))
    except (TypeError, ValueError):
        pass
title_abstract_scoring_limit = config_positive_int("title_abstract_scoring_limit", 1000)
arxiv_max_queries = env_int("ARXIV_MAX_QUERIES", config_positive_int("arxiv_max_queries", 3))
arxiv_timeout_sec = env_int("ARXIV_TIMEOUT_SEC", config_positive_int("arxiv_timeout_sec", 45 if deep_survey else 15))
arxiv_candidate_limit = env_int("ARXIV_LLM_CANDIDATE_LIMIT", config_positive_int("arxiv_llm_candidate_limit", 0))
arxiv_per_category = env_int("ARXIV_LLM_CANDIDATES_PER_CATEGORY", config_positive_int("arxiv_llm_candidates_per_category", 0))
biorxiv_candidate_limit = env_int("BIORXIV_LLM_CANDIDATE_LIMIT", config_positive_int("biorxiv_llm_candidate_limit", 0))
biorxiv_per_category = env_int("BIORXIV_LLM_CANDIDATES_PER_CATEGORY", config_positive_int("biorxiv_llm_candidates_per_category", 0))
nature_candidate_limit = env_int("NATURE_CANDIDATE_LIMIT", config_positive_int("nature_candidate_limit", 200))
science_candidate_limit = env_int("SCIENCE_CANDIDATE_LIMIT", config_positive_int("science_candidate_limit", 200))
abstract_scoring_max_workers = env_int("ABSTRACT_SCORING_MAX_WORKERS", config_positive_int("abstract_scoring_max_workers", 10))
abstract_scoring_batch_size = env_int("ABSTRACT_SCORING_BATCH_SIZE", config_positive_int("abstract_scoring_batch_size", 10))
abstract_scoring_timeout_sec = env_int("ABSTRACT_SCORING_TIMEOUT_SEC", config_positive_int("abstract_scoring_timeout_sec", 180))

runtime_tuning = dict(literature_cfg.get("runtime_tuning") or {{}}) if isinstance(literature_cfg.get("runtime_tuning"), dict) else {{}}
def runtime_default(name, default=None):
    raw = os.environ.get(name, "")
    if str(raw).strip():
        runtime_tuning[name] = raw
    elif name not in runtime_tuning and default is not None:
        runtime_tuning[name] = default

runtime_keys = [
    "ARXIV_FULL_SCAN",
    "ARXIV_MAX_QUERIES",
    "ARXIV_TIMEOUT_SEC",
    "ABSTRACT_SCORING_BATCH_SIZE",
    "ABSTRACT_SCORING_MAX_BATCH_SIZE",
    "ABSTRACT_SCORING_MAX_TOKENS",
    "SINGLE_ABSTRACT_SCORING_MAX_TOKENS",
    "ABSTRACT_SCORING_LLM_RETRIES",
    "ABSTRACT_SCORING_WALL_TIMEOUT_SEC",
    "ABSTRACT_SCORING_MAX_WORKERS",
    "ABSTRACT_SCORING_WORKER_CAP",
    "ABSTRACT_SCORING_TIMEOUT_SEC",
    "OMITTED_ITEM_RETRY_ATTEMPTS",
    "USE_LLM_TITLE_FILTER",
    "LARGE_TITLE_POOL_THRESHOLD",
]
for key in runtime_keys:
    runtime_default(key)
runtime_tuning["ABSTRACT_SCORING_BATCH_SIZE"] = str(abstract_scoring_batch_size)
runtime_tuning["ABSTRACT_SCORING_MAX_BATCH_SIZE"] = str(max(1, abstract_scoring_batch_size))
runtime_tuning["ABSTRACT_SCORING_MAX_WORKERS"] = str(abstract_scoring_max_workers)
runtime_tuning["ABSTRACT_SCORING_WORKER_CAP"] = str(max(1, abstract_scoring_max_workers))
runtime_tuning["ABSTRACT_SCORING_TIMEOUT_SEC"] = str(abstract_scoring_timeout_sec)
if deep_survey:
    runtime_default("VENUE_TITLE_SCAN_LIMIT", str(venue_scan_limit))
    runtime_default("ARXIV_FULL_SCAN", "0")
    runtime_default("ARXIV_MAX_QUERIES", "3")
    runtime_default("ARXIV_TIMEOUT_SEC", "45")
    runtime_default("ABSTRACT_SCORING_BATCH_SIZE", "10")
    runtime_default("ABSTRACT_SCORING_MAX_BATCH_SIZE", "10")
    runtime_default("ABSTRACT_SCORING_MAX_TOKENS", "12000")
    runtime_default("SINGLE_ABSTRACT_SCORING_MAX_TOKENS", "3000")
    runtime_default("ABSTRACT_SCORING_LLM_RETRIES", "2")
    runtime_default("ABSTRACT_SCORING_WALL_TIMEOUT_SEC", "180")
    runtime_default("ABSTRACT_SCORING_MAX_WORKERS", "6")
    runtime_default("ABSTRACT_SCORING_WORKER_CAP", "6")
    runtime_default("ABSTRACT_SCORING_TIMEOUT_SEC", "180")
    runtime_default("OMITTED_ITEM_RETRY_ATTEMPTS", "2")
    runtime_default("LARGE_TITLE_POOL_THRESHOLD", "800")
if os.environ.get("DISABLE_LLM_TITLE_FILTER", "0").lower() in {{"1", "true", "yes", "on"}}:
    runtime_tuning["USE_LLM_TITLE_FILTER"] = "0"
elif os.environ.get("FORCE_LLM_TITLE_FILTER", "0").lower() in {{"1", "true", "yes", "on"}}:
    runtime_tuning["USE_LLM_TITLE_FILTER"] = "1"
elif deep_survey:
    runtime_default("USE_LLM_TITLE_FILTER", "1")

# Structured Find settings are the public configuration contract. Replace
# stale auto-generated runtime_tuning values instead of letting an older run
# silently override the current Web values.
runtime_tuning["ARXIV_FULL_SCAN"] = str(os.environ.get("ARXIV_FULL_SCAN") or "0")
runtime_tuning["ARXIV_MAX_QUERIES"] = str(arxiv_max_queries)
runtime_tuning["ARXIV_TIMEOUT_SEC"] = str(arxiv_timeout_sec)

year = date.today().year
venue_ids = list(source_selection.get("venue_ids") or [])
if not use_venues:
    venue_ids = []
years = []
for item in source_selection.get("years") or [year]:
    try:
        years.append(int(item))
    except Exception:
        pass
if not years:
    years = [year]

project_interest = str(finding_cfg.get("research_interest") or cfg.get("research_interest") or cfg.get("user_prompt") or research_goal).strip()
project_profile = str(finding_cfg.get("researcher_profile") or cfg.get("researcher_profile") or "").strip()
researcher_profile = project_profile[:18000]

config_payload = {{
    "research_topic": configured_topic,
    "research_interest": project_interest or configured_topic,
    "researcher_profile": researcher_profile,
    "provider": provider,
    "base_url": api_base,
    "api_key": "",
    "model": model,
    "temperature": float(os.environ.get("LLM_TEMPERATURE") or local_llm.get("temperature", 0.2) or 0.2),
    "nonvenue_fetch_limit": nonvenue_fetch_limit,
    "max_recommended_papers": max_papers,
    "max_ideas": max_ideas,
    "venue_title_scan_limit": venue_scan_limit,
    "title_abstract_scoring_limit": title_abstract_scoring_limit,
    "arxiv_max_queries": arxiv_max_queries,
    "arxiv_timeout_sec": arxiv_timeout_sec,
    "arxiv_llm_candidate_limit": arxiv_candidate_limit,
    "arxiv_llm_candidates_per_category": arxiv_per_category,
    "biorxiv_llm_candidate_limit": biorxiv_candidate_limit,
    "biorxiv_llm_candidates_per_category": biorxiv_per_category,
    "llm_concurrency": env_int("LLM_CONCURRENCY", config_positive_int("llm_concurrency", 10)),
    "abstract_scoring_max_workers": abstract_scoring_max_workers,
    "abstract_scoring_batch_size": abstract_scoring_batch_size,
    "abstract_scoring_timeout_sec": abstract_scoring_timeout_sec,
    "arxiv_categories": literature_cfg.get("arxiv_categories") if isinstance(literature_cfg.get("arxiv_categories"), list) else [],
    "arxiv_queries": topic_queries,
    "github_languages": literature_cfg.get("github_languages") if isinstance(literature_cfg.get("github_languages"), list) else ["python", "all"],
    "github_since": str(literature_cfg.get("github_since") or "monthly"),
    "arxiv_start_date": str(literature_cfg.get("arxiv_start_date") or (date.today() - timedelta(days=arxiv_window_days)).isoformat()),
    "arxiv_end_date": str(literature_cfg.get("arxiv_end_date") or date.today().isoformat()),
    "biorxiv_categories": literature_cfg.get("biorxiv_categories") if isinstance(literature_cfg.get("biorxiv_categories"), list) else [],
    "biorxiv_start_date": str(literature_cfg.get("biorxiv_start_date") or ""),
    "biorxiv_end_date": str(literature_cfg.get("biorxiv_end_date") or ""),
    "nature_journals": literature_cfg.get("nature_journals") if isinstance(literature_cfg.get("nature_journals"), list) else ["nature", "natmachintell", "natcomputsci", "nmeth", "ncomms"],
    "nature_article_types": literature_cfg.get("nature_article_types") if isinstance(literature_cfg.get("nature_article_types"), list) else ["article"],
    "nature_start_date": str(literature_cfg.get("nature_start_date") or ""),
    "nature_end_date": str(literature_cfg.get("nature_end_date") or ""),
    "nature_candidate_limit": nature_candidate_limit,
    "science_journals": literature_cfg.get("science_journals") if isinstance(literature_cfg.get("science_journals"), list) else ["science", "sciadv"],
    "science_article_types": literature_cfg.get("science_article_types") if isinstance(literature_cfg.get("science_article_types"), list) else ["Research Article"],
    "science_start_date": str(literature_cfg.get("science_start_date") or ""),
    "science_end_date": str(literature_cfg.get("science_end_date") or ""),
    "science_candidate_limit": science_candidate_limit,
    "runtime_tuning": runtime_tuning,
}}
selection_payload = dict(source_selection)
selection_payload.update({{
    "venue_ids": venue_ids,
    "years": years,
    "venue_years": [
        pair for pair in source_selection.get("venue_years", [])
        if isinstance(pair, dict) and str(pair.get("venue_id") or "") in set(venue_ids)
    ] if venue_ids else [],
    "include_arxiv": bool(include_arxiv),
    "include_huggingface": bool(include_huggingface),
    "include_github": bool(include_github),
    "include_biorxiv": bool(source_selection.get("include_biorxiv")),
    "include_nature": bool(source_selection.get("include_nature")),
    "include_science": bool(source_selection.get("include_science")),
}})
if venue_ids and not selection_payload.get("venue_years"):
    selection_payload["venue_years"] = [
        {{"venue_id": venue_id, "year": int(year_value)}}
        for venue_id in venue_ids
        for year_value in years
    ]

input_payload = {{
    "research_topic": configured_topic,
    "research_interest": project_interest or configured_topic,
    "researcher_profile": researcher_profile,
    "arxiv_queries": topic_queries,
}}
find_input_fields = {{"research_topic", "research_interest", "researcher_profile", "arxiv_queries"}}
find_llm_fields = {{"provider", "base_url", "api_key", "model", "temperature", "llm_roles"}}
find_config_payload = {{
    key: value
    for key, value in config_payload.items()
    if key not in find_input_fields and key not in find_llm_fields and key not in {{"default_find_selection", "email"}}
}}
find_config_path = input_dir / "find.config.json"
input_path = input_dir / "input.json"
config_path = input_dir / "config.json"
selection_path = input_dir / "selection.json"
write_json_file(input_path, input_payload)
config_path.write_text(json.dumps(config_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
selection_path.write_text(json.dumps(selection_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

find_stage_request = build_find_stage_request(
    request_source=request_source,
    project_id=project or None,
    research_topic=configured_topic,
    selection=selection_payload,
    config_path=str(find_config_path),
    requested_parameters=find_config_payload,
    working_directory=str(root),
    force_new_find=force_new_find,
    restart_full_cycle=restart_full_cycle,
    human_approved_new_find=human_approved_new_find,
    approval_reason=approval_reason or None,
)
experience_query = build_experience_query(
    find_stage_request,
    required_context_tags=[],
    limit=5,
)
experience_store = JsonExperienceStore(
    root / ".runtime" / "feedback" / "experience_cases.json"
)
matched_experiences = experience_store.search_cases(experience_query)
run_context = FindFeedbackAdapter().adapt(
    find_stage_request,
    experience_query,
    matched_experiences,
)
runtime_find_config = {{
    "schema_version": 1,
    "config": dict(run_context.effective_parameters),
    "selection": selection_payload,
}}
write_json_file(find_config_path, runtime_find_config)

_RECOVERY_APPROVAL_WAIT_SECONDS = 900.0

def _recovery_approval_paths():
    if request_source != "web" or not web_job_id:
        return None
    if (
        Path(web_job_id).name != web_job_id
        or web_job_id in {{".", ".."}}
        or not all(character.isalnum() or character in "_.-" for character in web_job_id)
    ):
        raise ValueError("Web job ID is not safe for recovery approval")
    project_root = paths.root.resolve()
    control_root = (project_root / "tmp" / "feedback_recovery").resolve()
    if project_root not in control_root.parents:
        raise ValueError("Recovery approval path is outside the project")
    control_dir = (control_root / web_job_id).resolve()
    if control_dir.parent != control_root:
        raise ValueError("Recovery approval path is outside the control directory")
    return control_dir / "pending.json", control_dir / "resolution.json"

def _atomic_write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + "." + str(os.getpid()) + ".tmp")
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)

def _cleanup_recovery_approval_files():
    approval_paths = _recovery_approval_paths()
    if approval_paths is None:
        return
    pending_path, resolution_path = approval_paths
    pending_path.unlink(missing_ok=True)
    resolution_path.unlink(missing_ok=True)
    try:
        pending_path.parent.rmdir()
    except OSError:
        pass

def _pending_recovery_summary(decision):
    return {{
        "decision_id": decision.decision_id,
        "run_id": decision.run_id,
        "anomaly_id": decision.anomaly_id,
        "proposed_action": decision.proposed_action.value,
        "risk_level": decision.risk_level.value,
        "reason": decision.reason,
        "parameter_changes": [change.to_dict() for change in decision.parameter_changes],
        "target_sources": list(decision.target_sources),
        "approval_status": decision.approval_status,
    }}

def _wait_for_recovery_resolution(decision):
    approval_paths = _recovery_approval_paths()
    if approval_paths is None:
        print(
            "[framework] Pending recovery requires an active Web job; recovery was not executed",
            flush=True,
        )
        return None
    pending_path, resolution_path = approval_paths
    _atomic_write_json(pending_path, _pending_recovery_summary(decision))
    print(
        "TASTE_FEEDBACK_RECOVERY "
        + json.dumps(
            {{"status": "awaiting_approval", "decision_id": decision.decision_id}},
            sort_keys=True,
            separators=(",", ":"),
        ),
        flush=True,
    )
    deadline = time.monotonic() + _RECOVERY_APPROVAL_WAIT_SECONDS
    while time.monotonic() < deadline:
        if not resolution_path.is_file():
            time.sleep(0.25)
            continue
        try:
            resolution = json.loads(resolution_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError("Recovery approval resolution is invalid") from error
        required_fields = {{
            "decision_id",
            "approved",
            "approved_by",
            "reason",
            "resolved_at",
        }}
        if not isinstance(resolution, dict) or set(resolution) != required_fields:
            raise RuntimeError("Recovery approval resolution is invalid")
        if resolution.get("decision_id") != decision.decision_id:
            raise RuntimeError("Recovery approval decision identity does not match")
        if type(resolution.get("approved")) is not bool:
            raise RuntimeError("Recovery approval choice is invalid")
        if not isinstance(resolution.get("approved_by"), str) or not isinstance(
            resolution.get("reason"), str
        ):
            raise RuntimeError("Recovery approval metadata is invalid")
        return resolution
    raise RuntimeError("Recovery approval timed out")

_executed_recovery_decision_ids = set()

def _execute_approved_recovery_decision(
    *,
    decision: RecoveryDecision,
    run_context: RunContext,
) -> ExecutionHandle:
    if not isinstance(decision, RecoveryDecision):
        raise TypeError("decision must be a RecoveryDecision")
    if not isinstance(run_context, RunContext):
        raise TypeError("run_context must be a RunContext")

    validated_decision = RecoveryDecision.from_json(decision.to_json())
    validated_context = RunContext.from_json(run_context.to_json())
    if validated_decision.decision_id in _executed_recovery_decision_ids:
        raise RuntimeError("RecoveryDecision has already been executed")
    if not validated_decision.executable:
        raise ValueError("RecoveryDecision must be executable")
    if validated_decision.approval_status not in {{"not_required", "approved"}}:
        raise ValueError("RecoveryDecision approval status is not executable")

    if validated_decision.approval_status == "approved":
        if (
            validated_decision.action is not RecoveryAction.REQUEST_APPROVAL
            or validated_decision.proposed_action is None
        ):
            raise ValueError("approved RecoveryDecision has no proposed action")
        recovery_action = validated_decision.proposed_action
    else:
        if (
            validated_decision.action is RecoveryAction.REQUEST_APPROVAL
            or validated_decision.proposed_action is not None
        ):
            raise ValueError("not_required RecoveryDecision has invalid action data")
        recovery_action = validated_decision.action

    if recovery_action is RecoveryAction.SKIP_OPTIONAL_SOURCE:
        raise ValueError("skip_optional_source recovery is not supported")
    executable_actions = {{
        RecoveryAction.RETRY_NEW_RUN,
        RecoveryAction.RETRY_WITH_PARAMETER_CHANGE,
    }}
    if recovery_action not in executable_actions:
        raise ValueError("RecoveryDecision action is not executable")
    if recovery_action not in validated_context.allowed_recovery_actions:
        raise ValueError("RecoveryDecision action is not allowed by RunContext")
    if not validated_decision.new_run_required:
        raise ValueError("RecoveryDecision must require a new run")
    if not validated_decision.proposed_new_run_id:
        raise ValueError("RecoveryDecision must provide a new run ID")
    if validated_decision.attempt_index != validated_context.attempt_index + 1:
        raise ValueError("RecoveryDecision attempt index is stale")

    proposed_new_run_id = validated_decision.proposed_new_run_id
    if (
        Path(proposed_new_run_id).name != proposed_new_run_id
        or proposed_new_run_id in {{".", ".."}}
    ):
        raise ValueError("RecoveryDecision new run ID is not path-safe")

    effective_parameters = deepcopy(dict(validated_context.effective_parameters))
    decision_changes = deepcopy(validated_decision.parameter_changes)
    if recovery_action is RecoveryAction.RETRY_NEW_RUN:
        if decision_changes or validated_decision.target_sources:
            raise ValueError("retry_new_run must not carry recovery payload")
    else:
        if not decision_changes or validated_decision.target_sources:
            raise ValueError("retry_with_parameter_change has invalid payload")
        changed_names = set()
        for change in decision_changes:
            if change.name in changed_names:
                raise ValueError("RecoveryDecision repeats a parameter change")
            if change.name not in _SAFE_INTEGER_PARAMETERS:
                raise ValueError("RecoveryDecision contains a non-whitelisted parameter")
            if change.name not in effective_parameters:
                raise ValueError("RecoveryDecision parameter is absent from RunContext")
            current_value = effective_parameters[change.name]
            if change.before is not None and change.before != current_value:
                raise ValueError("RecoveryDecision parameter before value is stale")
            if type(change.after) is not int or change.after <= 0:
                raise ValueError("RecoveryDecision parameter after value is invalid")
            effective_parameters[change.name] = change.after
            _sync_runtime_tuning(
                effective_parameters,
                change.name,
                change.after,
            )
            changed_names.add(change.name)

    def read_required_json_object(snapshot_path, snapshot_name):
        try:
            payload = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(
                "Recovery run " + snapshot_name + " snapshot is unavailable"
            ) from error
        if not isinstance(payload, dict):
            raise RuntimeError(
                "Recovery run " + snapshot_name + " snapshot must be an object"
            )
        return payload

    input_payload = read_required_json_object(
        validated_context.input_snapshot_path,
        "input",
    )
    selection_payload = read_required_json_object(
        validated_context.selection_snapshot_path,
        "selection",
    )
    if selection_payload != dict(validated_context.selection):
        raise ValueError("RunContext selection does not match its snapshot")

    recovery_input_dir = (
        Path(validated_context.config_snapshot_path).parent
        / "recovery"
        / proposed_new_run_id
    )
    recovery_input_dir.mkdir(parents=True, exist_ok=False)
    recovery_config_path = recovery_input_dir / "find.config.json"
    recovery_input_path = recovery_input_dir / "input.json"
    recovery_selection_path = recovery_input_dir / "selection.json"
    write_json_file(
        recovery_config_path,
        {{
            "schema_version": 1,
            "config": effective_parameters,
            "selection": selection_payload,
        }},
    )
    write_json_file(recovery_input_path, input_payload)
    write_json_file(recovery_selection_path, selection_payload)

    recovery_run_context = replace(
        validated_context,
        attempt_index=validated_context.attempt_index + 1,
        created_at=datetime.now(timezone.utc),
        selection_snapshot_path=str(recovery_selection_path),
        selection=deepcopy(selection_payload),
        command_redacted=[
            validated_context.python_executable,
            validated_context.entrypoint,
            "--action",
            validated_context.action,
            "--config-json",
            str(recovery_config_path),
            "--input-json",
            str(recovery_input_path),
        ],
        config_snapshot_path=str(recovery_config_path),
        input_snapshot_path=str(recovery_input_path),
        effective_parameters=deepcopy(effective_parameters),
        parameter_changes=deepcopy(
            list(validated_context.parameter_changes) + decision_changes
        ),
    )
    _executed_recovery_decision_ids.add(validated_decision.decision_id)
    recovery_supervisor = _build_recovery_supervisor(
        decision=validated_decision,
        recovery_run_context=recovery_run_context,
    )
    execution_handle, _ = _run_supervised_find_attempt(
        attempt_run_context=recovery_run_context,
        attempt_supervisor=recovery_supervisor,
    )
    if execution_handle.exit_code != 0:
        raise RuntimeError("Recovery Find process failed")
    if recovery_supervisor.trace_summary["validation_status"] != "pass":
        raise RuntimeError("Recovery Find result validation failed")
    return execution_handle

recovery_advisor = None
try:
    recovery_llm_payload = dict(local_llm)
    recovery_llm_payload.update(
        {{
            "provider": provider,
            "base_url": api_base,
            "api_key": api_key,
            "model": model,
            "temperature": config_payload["temperature"],
        }}
    )
    recovery_llm_config = AppConfig(**recovery_llm_payload)
    recovery_llm_client = LLMClient(recovery_llm_config, role="find")
    if recovery_llm_client.enabled:
        recovery_advisor = LLMRecoveryAdvisor(llm_client=recovery_llm_client)
except Exception:
    recovery_advisor = None
    print("[framework] Recovery Advisor unavailable", flush=True)

supervisor_now = datetime.now(timezone.utc)
initial_supervisor_state = SupervisorState(
    supervisor_id="supervisor-" + run_context.context_id,
    project_id=run_context.project_id,
    root_run_id=None,
    status=SupervisorStatus.PREPARING,
    state_revision=0,
    created_at=supervisor_now,
    updated_at=supervisor_now,
    heartbeat_at=supervisor_now,
    producer="framework-find-driver",
    producer_version="v0",
    process_alive=False,
    cancel_requested=False,
    recovery_attempts=0,
    recovery_budget_total=run_context.recovery_budget,
    recovery_budget_remaining=run_context.recovery_budget,
    awaiting_approval=False,
    gate_evaluated=False,
    allow_read=False,
    gate_reason="Preparing Find supervision",
    terminal=False,
    event_sequence=0,
    state_path=str(input_dir / "feedback-supervisor-state.json"),
    run_context_id=run_context.context_id,
)
feedback_observer = FileProgressObserver()
feedback_validator = FindResultValidator()
feedback_anomaly_builder = FindAnomalyBuilder()
feedback_recovery_controller = FindRecoveryController(
    experience_store=experience_store,
    recovery_advisor=recovery_advisor,
)
feedback_approval_gate = FindRecoveryApprovalGate()
supervisor = FeedbackSupervisor(
    observer=feedback_observer,
    result_validator=feedback_validator,
    anomaly_builder=feedback_anomaly_builder,
    recovery_controller=feedback_recovery_controller,
    initial_state=initial_supervisor_state,
    approval_gate=feedback_approval_gate,
)
feedback_decisions = []
feedback_monitor_started_logged = False
feedback_summary_logged = False

def _report_feedback_trace_failure():
    try:
        print("[framework] Feedback trace emission failed", flush=True)
    except Exception:
        pass

def _emit_feedback_monitor_started_once():
    global feedback_monitor_started_logged
    if feedback_monitor_started_logged:
        return
    feedback_monitor_started_logged = True
    try:
        payload = {{"event": "monitor_started"}}
        print(
            "TASTE_FEEDBACK_TRACE "
            + json.dumps(payload, sort_keys=True, separators=(",", ":")),
            flush=True,
        )
    except Exception:
        _report_feedback_trace_failure()

def _emit_feedback_summary_once():
    global feedback_summary_logged
    if feedback_summary_logged:
        return
    feedback_summary_logged = True
    try:
        summary = supervisor.trace_summary
        payload = {{
            "event": "summary",
            "monitor_calls": summary["monitor_calls"],
            "observer_status": summary["observer_status"],
            "validation_status": summary["validation_status"],
            "anomaly_kind": summary["anomaly_kind"],
            "controller_called": summary["controller_called"],
            "recovery_decision_action": summary["recovery_decision_action"],
        }}
        print(
            "TASTE_FEEDBACK_TRACE "
            + json.dumps(payload, sort_keys=True, separators=(",", ":")),
            flush=True,
        )
    except Exception:
        _report_feedback_trace_failure()

find_runtime_dir = Path(
    os.environ.get("FINDING_RUNTIME_DIR") or finding_module / ".runtime"
).expanduser()

def _run_supervised_find_attempt(*, attempt_run_context, attempt_supervisor):
    binding_parser = _FindRunBindingParser(find_runtime_dir / "runs")
    terminal_feedback_called = False

    def emit_find_log(text):
        try:
            binding_rejected = binding_parser.consume(
                text,
                execution_handle=execution_handle,
            )
        except Exception:
            binding_rejected = True
        if binding_rejected:
            print("[framework] Find run binding event rejected", flush=True)
        print(text, end="", flush=True)

    def on_feedback_monitor_tick(handle):
        decision = attempt_supervisor.on_monitor_tick(
            run_context=attempt_run_context,
            execution_handle=handle,
        )
        if attempt_supervisor is supervisor:
            _emit_feedback_monitor_started_once()
        if decision is not None:
            feedback_decisions.append(decision)

    def notify_feedback_process_exited():
        try:
            decision = attempt_supervisor.on_process_exited(
                run_context=attempt_run_context,
                execution_handle=execution_handle,
            )
        except RuntimeError:
            decision = None
            print("[framework] Feedback Supervisor terminal call failed", flush=True)
        if decision is not None:
            feedback_decisions.append(decision)

    executor, execution_handle, process = _start_find_with_executor(attempt_run_context)
    _register_find_process_exit_cleanup(process)
    stdout_output = _consume_execution_logs(
        process,
        execution_handle,
        emit_find_log,
        on_monitor_tick=on_feedback_monitor_tick,
    )
    returncode = execution_handle.exit_code

    if execution_handle.run_id and execution_handle.run_dir:
        notify_feedback_process_exited()
        terminal_feedback_called = True
        if attempt_supervisor is supervisor:
            _emit_feedback_summary_once()

    if returncode == 0:
        run_id, directory, _ = _parse_find_cli_result(
            stdout_output,
            finding_module,
        )
        if execution_handle.run_id and execution_handle.run_dir:
            if (
                execution_handle.run_id != run_id
                or Path(execution_handle.run_dir).resolve() != directory.resolve()
            ):
                raise RuntimeError(
                    "Finding CLI run identity conflicts with the bound Find run"
                )
        else:
            execution_handle.run_id = run_id
            execution_handle.run_dir = str(directory)
    elif not terminal_feedback_called:
        print("[framework] Find exited before run identity was bound", flush=True)
        if attempt_supervisor is supervisor:
            _emit_feedback_summary_once()

    if execution_handle.run_id and not terminal_feedback_called:
        notify_feedback_process_exited()
        if attempt_supervisor is supervisor:
            _emit_feedback_summary_once()
    return execution_handle, stdout_output

def _build_recovery_supervisor(*, decision, recovery_run_context):
    parent_state = supervisor.state
    now = datetime.now(timezone.utc)
    recovery_state = replace(
        parent_state,
        supervisor_id=parent_state.supervisor_id + "-recovery-" + str(decision.attempt_index),
        status=SupervisorStatus.RECOVERING,
        state_revision=parent_state.state_revision + 1,
        updated_at=now,
        heartbeat_at=now,
        process_alive=False,
        recovery_attempts=parent_state.recovery_attempts + 1,
        recovery_budget_remaining=decision.budget_after,
        awaiting_approval=False,
        gate_evaluated=False,
        allow_read=False,
        gate_reason="Validating one bounded recovery Find",
        terminal=False,
        final_status=None,
        run_context_id=recovery_run_context.context_id,
        active_run_id=None,
        latest_progress_snapshot_id=None,
        latest_validation_id=None,
        pending_approval_decision_id=None,
        active_pid=None,
        process_started_at=None,
        exit_code=None,
        gate_validation_id=None,
        terminal_reason=None,
        last_error=None,
    )
    return FeedbackSupervisor(
        observer=feedback_observer,
        result_validator=feedback_validator,
        anomaly_builder=feedback_anomaly_builder,
        recovery_controller=feedback_recovery_controller,
        initial_state=recovery_state,
        approval_gate=feedback_approval_gate,
    )

def _consume_recovery_decision(*, decision, run_context, supervisor):
    if decision is None:
        return None
    if decision.action in {{RecoveryAction.NO_ACTION, RecoveryAction.STOP_AND_REPORT}}:
        return None
    resolved = decision
    if decision.approval_status == "pending":
        if not decision.requires_approval or decision.executable:
            raise RuntimeError("Pending RecoveryDecision state is invalid")
        try:
            resolution = _wait_for_recovery_resolution(decision)
            if resolution is None:
                return None
            resolved = supervisor.resolve_recovery_approval(
                decision_id=resolution["decision_id"],
                approved=resolution["approved"],
                approved_by=resolution["approved_by"] or None,
                reason=resolution["reason"],
            )
        finally:
            _cleanup_recovery_approval_files()
    if resolved.approval_status == "rejected":
        return None
    if resolved.approval_status not in {{"not_required", "approved"}}:
        raise RuntimeError("RecoveryDecision approval state is invalid")
    if not resolved.executable:
        raise RuntimeError("RecoveryDecision is not executable")
    return _execute_approved_recovery_decision(
        decision=resolved,
        run_context=run_context,
    )

print("[framework] Finding public CLI input: " + str(find_config_path) + " / " + str(input_path), flush=True)
initial_decision_count = len(feedback_decisions)
execution_handle, stdout_output = _run_supervised_find_attempt(
    attempt_run_context=run_context,
    attempt_supervisor=supervisor,
)
decision = (
    feedback_decisions[initial_decision_count]
    if len(feedback_decisions) > initial_decision_count
    else None
)
recovery_execution_handle = _consume_recovery_decision(
    decision=decision,
    run_context=run_context,
    supervisor=supervisor,
)
if recovery_execution_handle is not None:
    execution_handle = recovery_execution_handle
    stdout_output = Path(execution_handle.stdout_path).read_text(
        encoding="utf-8",
        errors="replace",
    )

returncode = execution_handle.exit_code
if returncode != 0:
    raise SystemExit(returncode)
selected_validation = feedback_validator.validate(execution_handle)
if (
    selected_validation.status is not ValidationStatus.PASS
    or selected_validation.ready_for_read is not True
):
    raise RuntimeError("Find result validation failed; failed run was not published")
run_id, directory, result = _parse_find_cli_result(stdout_output, finding_module)
if (
    execution_handle.run_id != run_id
    or not execution_handle.run_dir
    or Path(execution_handle.run_dir).resolve() != directory.resolve()
):
    raise RuntimeError("Finding CLI run identity conflicts with the bound Find run")
out_dir = internal_output_dir if internal_output_dir is not None else paths.planning / "finding"
out_dir.mkdir(parents=True, exist_ok=True)

STANDARD_FIND_ARTIFACTS = [
    "find.md", "source_status.md", "hf.md", "github.md",
    "find_results.json", "find_progress.json", "manifest.json", "selection.json",
    "venue_health_report.json", "category_scan_report.json", "title_filter_report.json",
    "arxiv_raw.json", "arxiv_prefiltered.json", "biorxiv.md", "biorxiv_raw.json",
    "biorxiv_prefiltered.json", "nature.md", "nature_raw.json", "nature_prefiltered.json",
    "science.md", "science_raw.json", "science_prefiltered.json",
]

def _copy_find_artifacts(target_dir):
    copied = []
    target_dir.mkdir(parents=True, exist_ok=True)
    for name in STANDARD_FIND_ARTIFACTS:
        source = directory / name
        if source.exists():
            shutil.copyfile(source, target_dir / name)
            copied.append(name)
    return copied

def _safe_int(value, default=0):
    try:
        if value is None or value == "":
            return default
        return int(float(str(value).strip()))
    except Exception:
        return default


def _survey_stats_from_find(find_result):
    category_scan_rows = find_result.get("category_scan_report", []) if isinstance(find_result, dict) else []
    title_filter_rows = find_result.get("title_filter_report", []) if isinstance(find_result, dict) else []
    source_rows = find_result.get("source_status", []) if isinstance(find_result, dict) else []
    venue_rows = find_result.get("venue_health_report", []) if isinstance(find_result, dict) else []
    arxiv_row = next((row for row in source_rows if isinstance(row, dict) and row.get("source") == "arxiv"), {{}})
    raw_count = len(find_result.get("raw_title_index", [])) if isinstance(find_result, dict) else 0
    if not raw_count:
        raw_count = sum(_safe_int((row if isinstance(row, dict) else {{}}).get("corpus_count") or (row if isinstance(row, dict) else {{}}).get("sample_count") or (row if isinstance(row, dict) else {{}}).get("raw_title_index_count"), 0) for row in venue_rows)
    if not raw_count:
        raw_count = sum(_safe_int((row if isinstance(row, dict) else {{}}).get("raw_title_index_count"), 0) for row in source_rows)
    evaluated = find_result.get("evaluated_candidates", []) if isinstance(find_result, dict) else []
    llm_scored = sum(1 for row in evaluated if isinstance(row, dict) and str(row.get("reason_source") or "") == "llm abstract evaluation")
    return {{
        "deep_survey": deep_survey,
        "raw_title_index_papers": raw_count,
        "venue_total_papers_available": raw_count,
        "venue_corpus_audited_papers": raw_count,
        "category_corpus_audited_papers": sum(_safe_int(row.get("corpus_audit_papers") or row.get("total_papers"), 0) for row in category_scan_rows if isinstance(row, dict)),
        "venue_category_selected_papers": sum(_safe_int(row.get("selected_category_papers"), 0) for row in category_scan_rows if isinstance(row, dict)),
        "venue_title_filter_input_papers": sum(_safe_int(row.get("title_filter_input_papers"), 0) for row in title_filter_rows if isinstance(row, dict)),
        "venue_final_title_candidates": sum(_safe_int(row.get("final_title_candidates"), 0) for row in title_filter_rows if isinstance(row, dict)),
        "venue_detail_fetched_candidates": len(evaluated),
        "venue_evaluated_candidates": len(evaluated),
        "llm_scored_candidates": llm_scored,
        "full_venue_corpus_audit": bool(raw_count),
        "llm_scoring_policy": "Full venue corpus is audited; category/title-screened candidates are batch-scored by LLM for efficiency.",
        "venue_read_candidates": len(find_result.get("strong_recommendations", []) or find_result.get("articles", [])) if isinstance(find_result, dict) else 0,
        "strong_recommendations": len(find_result.get("strong_recommendations", []) or find_result.get("articles", [])) if isinstance(find_result, dict) else 0,
        "category_scan_reports": len(category_scan_rows),
        "title_filter_reports": len(title_filter_rows),
        "arxiv_raw_count": len(find_result.get("arxiv_raw", [])) if isinstance(find_result, dict) else 0,
        "arxiv_prefiltered_count": len(find_result.get("arxiv_prefiltered", [])) if isinstance(find_result, dict) else 0,
        "arxiv_pages_fetched": arxiv_row.get("pages_fetched", 0) if isinstance(arxiv_row, dict) else 0,
        "arxiv_full_scan": arxiv_row.get("full_scan", False) if isinstance(arxiv_row, dict) else False,
        "arxiv_deduped_count": arxiv_row.get("deduped_count", 0) if isinstance(arxiv_row, dict) else 0,
    }}

def _write_frontend_state(stage, find_result, read_result=None, idea_result=None, plan_result=None):
    stats = _survey_stats_from_find(find_result)
    payload = {{
        "project": project,
        "repo_root": str(root),
        "taste_run_id": run_id,
        "taste_run_dir": str(directory),
        "output_dir": str(out_dir),
        "internal_literature_survey": not publish_outputs,
        "web_visible": publish_outputs,
        "provider": provider,
        "base_url": api_base,
        "model": model,
        "llm_enabled": provider != "mock",
        "api_mode": api_mode,
        "stage": stage,
        "status": stage,
        "max_papers": max_papers,
        "max_ideas": max_ideas,
        "repair_rounds": repair_rounds,
        "include_arxiv": include_arxiv,
        "include_huggingface": include_huggingface,
        "include_github": include_github,
        "survey_stats": stats,
        "venue_ids": venue_ids,
        "years": years,
        "targeted_queries": extra_queries,
        "topic_queries": topic_queries,
        "arxiv_window_days": arxiv_window_days,
        "survey_policy": {{
            "scope": "all configured literature sources with source-specific retrieval, title scoring, and globally bounded title+abstract scoring",
            "title_scan_limit": venue_scan_limit,
            "title_scan_limit_meaning": "safety cap per venue/year; local databases smaller than this are scanned fully",
            "nonvenue_fetch_limit": nonvenue_fetch_limit,
            "nonvenue_fetch_limit_meaning": "maximum papers retained per enabled arXiv/bioRxiv source, newest publication date first",
            "title_abstract_scoring_limit": title_abstract_scoring_limit,
            "title_abstract_scoring_limit_meaning": "maximum title-scored candidates entering abstract/detail retrieval and final title+abstract LLM scoring",
            "llm_title_filter_policy": "source candidates complete title scoring before the global title-score ranking and final title+abstract scoring",
            "arxiv_window_days": arxiv_window_days,
        }},
        "counts": {{
            "strong_recommendations": len(find_result.get("strong_recommendations", []) or find_result.get("articles", [])) if isinstance(find_result, dict) else 0,
            "evaluated_candidates": len(find_result.get("evaluated_candidates", [])) if isinstance(find_result, dict) else 0,
            "huggingface": len(find_result.get("huggingface", [])) if isinstance(find_result, dict) else 0,
            "github": len(find_result.get("github", [])) if isinstance(find_result, dict) else 0,
            "readings": len((read_result or {{}}).get("readings", [])),
            "ideas": len((idea_result or {{}}).get("ideas", [])),
            "plans": len((plan_result or {{}}).get("plans", [])),
        }},
    }}
    state_path = paths.state / "finding_frontend.json" if publish_outputs else out_dir / "finding_frontend.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    summary = ["# Find Frontend\n\n"]
    for key in ["taste_run_id", "stage", "provider", "api_mode", "model", "output_dir"]:
        summary.append("- " + key + ": " + str(payload.get(key, "")) + "\n")
    if extra_queries:
        summary.append("- targeted_queries: " + json.dumps(extra_queries, ensure_ascii=False) + "\n")
    for key, value in payload["counts"].items():
        summary.append("- " + key + ": " + str(value) + "\n")
    summary.append("\n## Survey Coverage\n")
    for key in ["deep_survey", "venue_total_papers_available", "venue_corpus_audited_papers", "venue_category_selected_papers", "venue_title_filter_input_papers", "venue_final_title_candidates", "venue_detail_fetched_candidates", "llm_scored_candidates", "venue_read_candidates", "arxiv_raw_count", "arxiv_prefiltered_count", "arxiv_pages_fetched"]:
        summary.append("- " + key + ": " + str(stats.get(key)) + "\n")
    summary.append("\n## Survey Policy\n")
    summary.append("- scope: all configured literature sources; publication-venue category signals are source-specific prefilters\n")
    summary.append(f"- title_scan_limit: {{venue_scan_limit}}; safety cap, not a target sample size\n")
    summary.append(f"- nonvenue_fetch_limit: {{nonvenue_fetch_limit}}; maximum papers retained per enabled arXiv/bioRxiv source, newest first\n")
    summary.append(f"- title_abstract_scoring_limit: {{title_abstract_scoring_limit}}; maximum title-scored candidates entering final title+abstract scoring\n")
    summary.append("- scoring order: source retrieval, title LLM scoring, global title-score ranking, then title+abstract LLM scoring\n")
    summary.append("\n## Usage In TASTE\n")
    summary.append("- Find-stage artifacts are written immediately after Find so Claude/experiments can use them while Read/Idea/Plan continues.\n")
    summary.append("- Treat candidates not selected by the final ranking as literature signals only, not paper-claim evidence.\n")
    frontend_md_path = paths.planning / "finding_frontend.md" if publish_outputs else out_dir / "finding_frontend.md"
    frontend_md_path.parent.mkdir(parents=True, exist_ok=True)
    frontend_md_path.write_text("".join(summary), encoding="utf-8")
    return payload

def _taste_article_from_item(row, source):
    title = str(row.get("title") or "").strip()
    if not title:
        return None
    return {{
        "id": str(row.get("id") or row.get("paper_id") or row.get("entry_id") or title)[:120],
        "source": source,
        "title": title,
        "authors": ", ".join(row.get("authors", [])) if isinstance(row.get("authors"), list) else str(row.get("authors", "")),
        "abstract": str(row.get("abstract") or row.get("summary") or row.get("tldr") or row.get("reason") or "")[:6000],
        "url": row.get("url") or row.get("abs_url") or row.get("entry_id") or "",
        "pdf_url": row.get("pdf_url") or "",
        "venue": row.get("venue") or row.get("source") or "TASTE-cache",
        "year": int(str(row.get("year") or row.get("published", "") or date.today().year)[:4]) if str(row.get("year") or row.get("published", "") or date.today().year)[:4].isdigit() else date.today().year,
        "category": row.get("category") or ", ".join(row.get("categories", [])) if isinstance(row.get("categories", []), list) else str(row.get("category", "")),
        "classification_source": row.get("classification_source") or "fallback",
        "fit_score": row.get("fit_score") or row.get("discovery_priority_score") or 7.0,
        "diversity_score": row.get("diversity_score") or 6.0,
        "score": row.get("score") or row.get("discovery_priority_score") or 7.0,
        "reason": row.get("reason") or row.get("taste_reason") or "Recovered from TASTE discovery cache because live TASTE sources were unavailable.",
        "reason_source": row.get("reason_source") or "TASTE discovery cache recovery",
    }}

def _load_backup_articles(limit):
    candidates = []
    # Prefer prior successful TASTE runs because they already match TASTE's schema.
    runs_root = root / "modules" / "finding" / ".runtime" / "runs"
    for find_path in sorted(runs_root.glob("find_*/find_results.json"), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True):
        if run_id in str(find_path):
            continue
        try:
            payload = json.loads(find_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for row in payload.get("articles", []):
            item = _taste_article_from_item(row, "taste_history")
            if item:
                candidates.append(item)
        if len(candidates) >= limit:
            break
    # Then use TASTE's broader discovery cache.
    for path in sorted(paths.discover.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for row in payload.get("items", []) if isinstance(payload, dict) else []:
            item = _taste_article_from_item(row, "discovery_cache")
            if item:
                candidates.append(item)
        if len(candidates) >= limit * 4:
            break
    seen = set()
    unique = []
    for item in candidates:
        key = item["title"].lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
        if len(unique) >= limit:
            break
    return unique

if not result.get("strong_recommendations") and not result.get("articles") and not (
    result.get("screened_ranking")
    or result.get("evaluated_candidates")
    or result.get("title_candidates")
    or result.get("retrieval_candidates")
):
    if os.environ.get("ALLOW_STALE_CACHE_RECOVERY", "0").lower() in {{"1", "true", "yes", "on"}}:
        backup_articles = _load_backup_articles(max_papers)
        if backup_articles:
            print(f"Live sources produced no recommendations; recovered {{len(backup_articles)}} recommendations from TASTE cache", flush=True)
            result["strong_recommendations"] = backup_articles
            result.setdefault("source_status", []).append({{"source": "cache_recovery", "ok": True, "limited": True, "count": len(backup_articles), "message": "Recovered articles from TASTE cache because live sources were empty or rate-limited."}})
            (directory / "find_results.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + chr(10), encoding="utf-8")
            article_lines = ["# Recommended Articles", ""]
            for index, item in enumerate(backup_articles, 1):
                article_lines.extend([
                    f"## {{index}}. {{item.get('title', 'Untitled')}}",
                    "",
                    f"- Source: {{item.get('source', '')}}",
                    f"- Venue: {{item.get('venue', '')}}",
                    f"- URL: {{item.get('url', '')}}",
                    "",
                    str(item.get("abstract") or item.get("reason") or "").strip(),
                    "",
                ])
            recovered_article = chr(10).join(article_lines)
            (directory / "find.md").write_text(recovered_article, encoding="utf-8")
            status_lines = ["# Source Status", "", "## cache_recovery", "", f"- **Status**: ok", f"- **Count**: {{len(backup_articles)}}", "- **Message**: Recovered articles from TASTE cache because live sources were empty or rate-limited.", ""]
            (directory / "source_status.md").write_text(chr(10).join(status_lines), encoding="utf-8")
    else:
        raise RuntimeError("Fresh Find produced no usable candidates; stale TASTE cache recovery is disabled. Fix sources/scoring before continuing.")

if publish_outputs:
    adopt_taste_find_run(paths, {{"taste_run_id": run_id, "taste_run_dir": str(directory)}}, run_id)
    read_default_update = update_project_read_default_after_find(project, result, projects_root=paths.root.parent)
    print("[framework] Read project default: " + json.dumps(read_default_update, ensure_ascii=False), flush=True)
else:
    _copy_find_artifacts(out_dir)

payload = _write_frontend_state("find_completed", result)
print(json.dumps(payload, ensure_ascii=False))
'''


def driver_python_command(args: argparse.Namespace, cfg: dict, driver: Path) -> list[str]:
    env_name = str(getattr(args, "env_name", "") or "").strip()
    if env_name:
        conda = conda_executable(cfg)
        if not conda:
            raise RuntimeError(f"conda not found for --env-name {env_name}; use MANAGEMENT_PYTHON or clear --env-name")
        return [conda, "run", "--no-capture-output", "-n", env_name, "python", str(driver)]

    runtime = cfg.get("runtime", {}) if isinstance(cfg.get("runtime", {}), dict) else {}
    for candidate in [
        os.environ.get("MANAGEMENT_PYTHON", ""),
        runtime.get("management_python"),
        runtime.get("python_executable"),
        cfg.get("python_executable"),
        management_python(),
        sys.executable,
    ]:
        value = str(candidate or "").strip()
        if value and Path(value).expanduser().exists():
            return [str(Path(value).expanduser()), str(driver)]
    return [sys.executable, str(driver)]


def run(cmd: list[str], cwd: Path = ROOT, env: dict[str, str] | None = None, timeout_sec: int = 900, live_log_path: Path | None = None) -> subprocess.CompletedProcess[str]:
    driver_env = dict(os.environ if env is None else env)
    driver_env[_FIND_DRIVER_PROCESS_GROUP_ENV] = "1"
    proc = subprocess.Popen(cmd, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=driver_env, start_new_session=True, bufsize=1)
    started = time.monotonic()
    lines: list[str] = []
    last_heartbeat = 0.0
    if live_log_path:
        live_log_path.parent.mkdir(parents=True, exist_ok=True)
        live_log_path.write_text(f"[frontend] started pid={proc.pid} timeout_sec={timeout_sec}\n", encoding="utf-8")
    assert proc.stdout is not None
    try:
        while True:
            if timeout_sec and time.monotonic() - started > timeout_sec:
                raise subprocess.TimeoutExpired(cmd, timeout_sec, output="".join(lines), stderr="")
            return_code = proc.poll()
            ready, _, _ = select.select([proc.stdout], [], [], 0.2)
            if ready:
                line = proc.stdout.readline()
                if line:
                    clean = redact(line)
                    lines.append(clean)
                    print(clean, end="", flush=True)
                    if live_log_path:
                        with live_log_path.open("a", encoding="utf-8") as handle:
                            handle.write(clean)
                    continue
            if return_code is not None:
                remainder = proc.stdout.read() or ""
                if remainder:
                    clean = redact(remainder)
                    lines.append(clean)
                    print(clean, end="", flush=True)
                    if live_log_path:
                        with live_log_path.open("a", encoding="utf-8") as handle:
                            handle.write(clean)
                return subprocess.CompletedProcess(cmd, return_code, "".join(lines), "")
            if live_log_path and time.monotonic() - last_heartbeat > 30:
                # Keep liveness in process/state metadata. Repeating idle heartbeats
                # drown out the real Find channel/LLM scoring steps in the UI log.
                last_heartbeat = time.monotonic()
            time.sleep(0.2)
    except subprocess.TimeoutExpired as exc:
        _terminate_driver_process_group(proc)
        exc.output = "".join(lines)
        exc.stderr = ""
        raise exc
    except BaseException:
        _terminate_driver_process_group(proc)
        raise


def read_focus_queries(path: str) -> list[str]:
    if not path:
        return []
    focus_path = Path(path)
    if not focus_path.exists():
        return []
    text = focus_path.read_text(encoding="utf-8", errors="ignore")
    values: list[str] = []
    if focus_path.suffix.lower() == ".json":
        try:
            payload = json.loads(text)
        except Exception:
            payload = None
        if isinstance(payload, dict):
            for key in ["queries", "followup_queries", "suggested_followup_queries", "targets"]:
                for item in payload.get(key, []) if isinstance(payload.get(key, []), list) else []:
                    if isinstance(item, str) and item.strip():
                        values.append(item.strip())
                    elif isinstance(item, dict):
                        title = str(item.get("title") or item.get("query") or item.get("name") or "").strip()
                        if title:
                            values.append(title)
        elif isinstance(payload, list):
            for item in payload:
                if isinstance(item, str) and item.strip():
                    values.append(item.strip())
                elif isinstance(item, dict):
                    title = str(item.get("title") or item.get("query") or item.get("name") or "").strip()
                    if title:
                        values.append(title)
    else:
        values.extend(line.strip(" -\t") for line in text.splitlines() if line.strip(" -\t"))
    return values


def merge_extra_queries(args: argparse.Namespace) -> list[str]:
    values = list(args.query or []) + read_focus_queries(args.focus_file)
    seen = set()
    out: list[str] = []
    for value in values:
        text = " ".join(str(value or "").split())
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        out.append(text)
    existing: list[str] = []
    raw = os.environ.get("EXTRA_QUERIES", "").strip()
    if raw:
        try:
            decoded = json.loads(raw)
        except Exception:
            decoded = raw
        if isinstance(decoded, list):
            existing.extend(str(item).strip() for item in decoded if str(item).strip())
        elif isinstance(decoded, str):
            existing.extend(item.strip() for item in decoded.replace(";", "\n").split("\n") if item.strip())
    merged = []
    seen = set()
    for value in existing + out:
        key = value.lower()
        if value and key not in seen:
            seen.add(key)
            merged.append(value)
    if merged:
        os.environ["EXTRA_QUERIES"] = json.dumps(merged, ensure_ascii=False)
    return merged


def write_driver(path: Path, project: str, max_papers: int, max_ideas: int, repair_rounds: int, include_arxiv: bool, include_huggingface: bool, include_github: bool, use_venues: bool, source_selection: dict[str, Any], *, request_source: str = "cli", force_new_find: bool = False, restart_full_cycle: bool = False, human_approved_new_find: bool = False, approval_reason: str = "", web_job_id: str = "", deep_survey: bool = False, fast_mode: bool = False) -> None:
    code = DRIVER_TEMPLATE.format(
        root_json=json.dumps(str(ROOT)),
        taste_root_json=json.dumps(str(ROOT)),
        project_json=json.dumps(project),
        max_papers=max_papers,
        max_ideas=max_ideas,
        repair_rounds=repair_rounds,
        include_arxiv=include_arxiv,
        include_huggingface=include_huggingface,
        include_github=include_github,
        use_venues=use_venues,
        source_selection_json=repr(source_selection),
        api_mode_json=json.dumps(os.environ.get("LLM_API_MODE", "chat_completions")),
        request_source_json=json.dumps(request_source),
        force_new_find=bool(force_new_find),
        restart_full_cycle=bool(restart_full_cycle),
        human_approved_new_find=bool(human_approved_new_find),
        approval_reason_json=json.dumps(approval_reason),
        web_job_id_json=json.dumps(web_job_id),
        core_venue_ids_json=json.dumps(DEFAULT_CORE_VENUE_IDS),
        deep_survey=bool(deep_survey),
        fast_mode=bool(fast_mode),
    )
    path.write_text(code, encoding="utf-8")


def _cleanup_recovery_approval_control_files(
    project_root: Path,
    web_job_id: str,
) -> None:
    job_id = str(web_job_id or "").strip()
    if (
        not job_id
        or Path(job_id).name != job_id
        or job_id in {".", ".."}
        or not all(character.isalnum() or character in "_.-" for character in job_id)
    ):
        return
    resolved_project_root = Path(project_root).resolve()
    control_root = (
        resolved_project_root / "tmp" / "feedback_recovery"
    ).resolve()
    if resolved_project_root not in control_root.parents:
        return
    control_dir = (control_root / job_id).resolve()
    if control_dir.parent != control_root:
        return
    for name in ("pending.json", "resolution.json"):
        (control_dir / name).unlink(missing_ok=True)
    try:
        control_dir.rmdir()
    except OSError:
        pass


def redact(text: str) -> str:
    for key in ["OPENAI_API_KEY", "LLM_API_KEY"]:
        value = os.environ.get(key, "")
        if value:
            text = text.replace(value, "<redacted>")
    local_key = str(_load_local_llm_config().get("api_key") or "")
    if local_key:
        text = text.replace(local_key, "<redacted>")
    return text


def _load_project_config(project: str) -> dict:
    try:
        return __import__("project_paths").load_project_config(project)
    except Exception:
        return {}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 0
    return value if value > 0 else int(default)


def _env_nonnegative_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw in (None, ""):
        return max(0, int(default))
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return max(0, int(default))


def _effective_source_selection(args: argparse.Namespace) -> dict[str, Any]:
    explicit_selection = str(getattr(args, "selection_json", "") or "").strip()
    if explicit_selection:
        selection = normalize_source_selection(json.loads(explicit_selection))
    else:
        project_path = _build_project_paths(args.project).config
        selection = canonical_source_selection(project_config_path=project_path)
    if getattr(args, "skip_venues", False):
        selection["venue_ids"] = []
    if getattr(args, "skip_arxiv", False):
        selection["include_arxiv"] = False
    if getattr(args, "skip_huggingface", False):
        selection["include_huggingface"] = False
    if getattr(args, "skip_github", False):
        selection["include_github"] = False
    return normalize_source_selection(selection)


def _taste_signature(args: argparse.Namespace, extra_queries: list[str]) -> dict:
    cfg = _load_project_config(args.project)
    literature_cfg = cfg.get("literature", {}) if isinstance(cfg.get("literature", {}), dict) else {}
    deep = bool(args.deep_survey)
    wide = bool(args.wide_survey)
    selection = _effective_source_selection(args)
    venues = list(selection.get("venue_ids") or [])
    years = [int(item) for item in selection.get("years") or [dt.date.today().year]]
    window_days = _env_int("WINDOW_DAYS", 180 if deep else int(literature_cfg.get("primary_window_days", 90) or 90))
    topic_queries: list[str] = []
    for item in extra_queries:
        if item and item not in topic_queries:
            topic_queries.append(item)
    for item in cfg.get("queries", []) or []:
        if isinstance(item, str) and item.strip():
            normalized = " ".join(item.split())
            if normalized not in topic_queries:
                topic_queries.append(normalized)
    local_llm = _load_local_llm_config()
    api_key_env = os.environ.get("LLM_API_KEY_ENV") or "OPENAI_API_KEY"
    api_key = (os.environ.get(api_key_env, "") if api_key_env else "") or os.environ.get("LLM_API_KEY", "") or str(local_llm.get("api_key") or "")
    api_base = os.environ.get("LLM_API_BASE") or local_llm.get("base_url") or ""
    model = os.environ.get("LLM_MODEL") or local_llm.get("model") or "mock-model"
    provider = os.environ.get("LLM_PROVIDER") or local_llm.get("provider") or "mock"
    payload = {
        "schema": 1,
        "scoring_policy_version": "quality_bonus_v4_selective_venue",
        "project": args.project,
        "topic": str(cfg.get("topic", "")),
        "user_prompt": str(cfg.get("user_prompt", "")),
        "queries": topic_queries,
        "deep_survey": deep,
        "wide_survey": wide,
        "venues": venues,
        "years": years,
        "include_arxiv": bool(selection.get("include_arxiv")),
        "include_huggingface": bool(selection.get("include_huggingface")),
        "include_github": bool(selection.get("include_github")),
        "arxiv_window_days": window_days,
        "venue_title_scan_limit": _env_nonnegative_int("VENUE_TITLE_SCAN_LIMIT", int(literature_cfg.get("venue_title_scan_limit") or 0)),
        "nonvenue_fetch_limit": int(literature_cfg.get("nonvenue_fetch_limit") or 5000),
        "title_abstract_scoring_limit": int(literature_cfg.get("title_abstract_scoring_limit") or 1000),
        "abstract_scoring_max_workers": _env_int("ABSTRACT_SCORING_MAX_WORKERS", 10),
        "abstract_scoring_batch_size": _env_int("ABSTRACT_SCORING_BATCH_SIZE", 10),
        "max_papers": int(args.max_papers),
        "max_ideas": int(args.max_ideas),
        "provider": provider,
        "model": model,
        "api_mode": os.environ.get("LLM_API_MODE", "chat_completions"),
        "api_base": api_base,
        "api_key_env": api_key_env,
        "llm_config_source": "modules/finding/config/llm.local.json_or_env",
        "llm_key_available": bool(api_key),
        "llm_title_filter": "forced" if os.environ.get("FORCE_LLM_TITLE_FILTER", "0").lower() in {"1", "true", "yes", "on"} else "disabled_for_large_pools",
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["signature"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return payload


def _copy_cached_taste_outputs(project: str, run_dir_path: Path) -> dict:
    paths = build_paths(project)
    out_dir = paths.planning / "finding"
    out_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for name in [
        "find.md", "source_status.md", "hf.md", "github.md",
        "find_results.json", "find_progress.json", "manifest.json", "selection.json",
        "venue_health_report.json", "category_scan_report.json", "title_filter_report.json",
        "arxiv_raw.json", "arxiv_prefiltered.json", "biorxiv_raw.json", "biorxiv_prefiltered.json",
        "nature_raw.json", "nature_prefiltered.json", "science_raw.json", "science_prefiltered.json",
    ]:
        source = run_dir_path / name
        if source.exists():
            shutil.copyfile(source, out_dir / name)
            copied.append(name)
    return {"output_dir": str(out_dir), "copied": copied}


def maybe_reuse_taste_run(args: argparse.Namespace, extra_queries: list[str]) -> dict | None:
    if os.environ.get("ALLOW_FIND_RUN_REUSE", "0").lower() not in {"1", "true", "yes", "on"}:
        return None
    if os.environ.get("FORCE_REFRESH", "0").lower() in {"1", "true", "yes", "on"}:
        return None
    paths = build_paths(args.project)
    reuse_ttl_hours = _env_int("REUSE_TTL_HOURS", 72)
    expected = _taste_signature(args, extra_queries)
    state_path = paths.state / "taste_reuse_signature.json"
    state = {}
    try:
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    except Exception:
        state = {}
    run_dir_text = str(state.get("run_dir") or "")
    run_dir_path = Path(run_dir_text)
    generated_at = str(state.get("generated_at") or "")
    age_ok = True
    if generated_at:
        try:
            created = dt.datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
            age_ok = (dt.datetime.now(dt.timezone.utc) - created).total_seconds() <= reuse_ttl_hours * 3600
        except Exception:
            age_ok = False
    required = ["find_results.json", "find.md"]
    if (
        state.get("signature") == expected.get("signature")
        and age_ok
        and run_dir_path.exists()
        and all((run_dir_path / name).exists() for name in required)
    ):
        copied = _copy_cached_taste_outputs(args.project, run_dir_path)
        payload = {
            "project": args.project,
            "status": "reused",
            "stage": "reused_cached_taste_run",
            "taste_run_id": state.get("run_id"),
            "taste_run_dir": str(run_dir_path),
            "signature": expected,
            "reuse_ttl_hours": reuse_ttl_hours,
            "copied": copied.get("copied", []),
            "reason": "Same literature channels, direction, years/window, budgets, and LLM config; reused TASTE intermediate artifacts instead of rerunning discovery.",
        }
        (paths.state / "finding_frontend.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        (paths.planning / "finding_frontend.md").write_text(
            "# Find Frontend\n\n"
            "- status: reused\n"
            f"- taste_run_id: {payload['taste_run_id']}\n"
            f"- taste_run_dir: {payload['taste_run_dir']}\n"
            "- reason: same survey signature; reused cached TASTE artifacts.\n"
            "\nTargeted or different-direction literature refreshes change the signature and will run TASTE again.\n",
            encoding="utf-8",
        )
        print("Reused cached TASTE run " + str(payload["taste_run_id"]), flush=True)
        print(json.dumps(payload, ensure_ascii=False), flush=True)
        return payload
    return None


def save_taste_reuse_signature(project: str, run_id: str, run_dir_path: Path, args: argparse.Namespace, extra_queries: list[str]) -> None:
    paths = build_paths(project)
    signature = _taste_signature(args, extra_queries)
    payload = {
        **signature,
        "run_id": run_id,
        "run_dir": str(run_dir_path),
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "required_files": ["find_results.json", "find.md"],
    }
    (paths.state / "taste_reuse_signature.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")




def latest_taste_run_hint() -> dict:
    runtime_root = Path(os.environ.get("FINDING_RUNTIME_DIR") or ROOT / "modules" / "finding" / ".runtime").expanduser()
    runs = sorted((runtime_root / "runs").glob("find_*"), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    if not runs:
        return {}
    latest = runs[0]
    files = sorted(child.name for child in latest.iterdir() if child.is_file())
    return {"latest_run_dir": str(latest), "latest_run_files": files}





def write_find_timeout_state(
    project: str,
    *,
    status: str,
    reason: str,
    log_path: Path,
    timeout_sec: int,
    elapsed_sec: float,
    run_dir: Path | None = None,
    output_dir: Path | None = None,
) -> dict:
    paths = build_paths(project)
    out_dir = output_dir or paths.planning / "finding"
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "project": project,
        "status": status,
        "stage": status,
        "timeout_sec": timeout_sec,
        "elapsed_sec": round(elapsed_sec, 3),
        "reason": reason,
        "log_path": str(log_path),
        "taste_run_dir": str(run_dir) if run_dir else "",
        "output_dir": str(out_dir),
        "guardrail": "Find timeout records must not create fallback papers, readings, ideas, or plans. Rerun Find or rebuild downstream via reading --action current_find_research_plan only after real Find artifacts exist.",
    }
    state_path = paths.state / "finding_frontend.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_lines = [
        "# Find Frontend",
        "",
        f"- status: {status}",
        f"- timeout_sec: {timeout_sec}",
        f"- elapsed_sec: {elapsed_sec:.3f}",
        f"- output_dir: {out_dir}",
        f"- log_path: {log_path}",
        "",
        "No fallback scientific artifacts were generated. Use real `find_results.json` plus `reading --action current_find_research_plan` before downstream stages.",
    ]
    (paths.planning / "finding_frontend.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    (out_dir / "finding_frontend_timeout.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload

def main() -> int:
    parser = argparse.ArgumentParser(description="Run the configured Find route and sync real Find artifacts into the project.")
    parser.add_argument("--project", required=True)
    parser.add_argument("--web-job-id", default="", help=argparse.SUPPRESS)
    parser.add_argument("--request-source", choices=["web", "cli", "full_cycle"], default="", help=argparse.SUPPRESS)
    parser.add_argument("--force-new-find", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--restart-full-cycle", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--human-approved-new-find", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--approval-reason", default="", help=argparse.SUPPRESS)
    parser.add_argument("--env-name", default=DEFAULT_ENV)
    parser.add_argument("--max-papers", type=int, default=20)
    parser.add_argument("--max-ideas", type=int, default=6)
    parser.add_argument("--repair-rounds", type=int, default=3)
    parser.add_argument("--skip-arxiv", action="store_true")
    parser.add_argument("--skip-huggingface", action="store_true")
    parser.add_argument("--skip-github", action="store_true")
    parser.add_argument("--skip-venues", action="store_true", help="Skip slow venue title-index sources and use arXiv/HF/GitHub only.")
    parser.add_argument("--selection-json", default="", help="Explicit source selection JSON supplied by Framework for this Find request.")
    parser.add_argument("--timeout-sec", type=int, default=0, help="Optional overall Find timeout; 0 disables the overall timeout.")
    parser.add_argument("--fast-mode", action="store_true", help="Use conservative budgets and skip slower external sources so initialization cannot dominate the loop.")
    parser.add_argument("--deep-survey", action="store_true", help="Use TASTE focused deep survey mode: full venue-corpus audit, category prefiltering, and screened-candidate LLM scoring.")
    parser.add_argument("--wide-survey", action="store_true", help="Allow broader venue/year scope. Default follows the project canonical source selection.")
    parser.add_argument("--query", action="append", default=[], help="Targeted query supplied by project agent; appended to project literature queries.")
    parser.add_argument("--focus-file", default="", help="Optional JSON/Markdown/TXT file with targeted queries or paper titles.")
    parser.add_argument("--internal-output-dir", default="", help="Run Find into this internal directory without publishing to the web-facing project artifacts.")
    args = parser.parse_args()
    if os.environ.get("DISABLE_NEW_FIND", "0").lower() in {"1", "true", "yes", "on"}:
        paths = build_paths(args.project)
        existing_find = paths.planning / "finding" / "find_results.json"
        payload = {
            "project": args.project,
            "status": "existing_find_reused_record_only",
            "stage": "existing_find_reused_record_only",
            "existing_find_results": str(existing_find),
            "existing_find_available": existing_find.exists(),
            "guardrail": "Record-only/existing-literature mode was explicitly requested; this invocation reuses canonical project literature artifacts instead of launching TASTE.",
        }
        try:
            data = json.loads(existing_find.read_text(encoding="utf-8")) if existing_find.exists() else {}
            if isinstance(data, dict):
                payload["taste_run_id"] = data.get("run_id", "")
        except Exception as exc:
            payload["existing_find_error"] = str(exc)[:300]
        (paths.state / "finding_frontend.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        (paths.planning / "finding_frontend.md").write_text(
            "# Find Frontend\n\n"
            "- status: existing_find_reused_record_only\n"
            f"- existing_find_results: {existing_find}\n"
            "- guardrail: existing-literature mode was explicitly requested; no TASTE run was launched by this invocation.\n",
            encoding="utf-8",
        )
        print(json.dumps(payload, ensure_ascii=False), flush=True)
        return 0
    extra_queries = merge_extra_queries(args)
    internal_output_dir = Path(args.internal_output_dir).expanduser() if str(args.internal_output_dir or "").strip() else None
    if internal_output_dir is not None:
        internal_output_dir.mkdir(parents=True, exist_ok=True)

    if args.deep_survey:
        if os.environ.get("REFRESH_LOCAL_DB", "0").lower() in {"1", "true", "yes", "on"}:
            refresh_cmd = [
                sys.executable,
                str(ROOT / "modules" / "finding" / "main.py"),
                "--action",
                "local_database",
                "--if-missing",
            ]
            years = os.environ.get("YEARS", "").strip()
            if years:
                refresh_cmd.extend(["--years", years])
            venues = os.environ.get("LOCAL_DB_VENUES", "").strip()
            if venues:
                refresh_cmd.extend(["--venues", venues])
            db_update_timeout = int(os.environ.get("DB_UPDATE_TIMEOUT_SEC", "1800")) + 60
            if args.timeout_sec > 0:
                db_update_timeout = min(args.timeout_sec, db_update_timeout)
            subprocess.run(refresh_cmd, cwd=ROOT, text=True, capture_output=True, timeout=db_update_timeout)

    if args.fast_mode:
        args.max_papers = min(args.max_papers, 3)
        args.max_ideas = min(args.max_ideas, 2)
        args.repair_rounds = min(args.repair_rounds, 1)
        args.skip_huggingface = True
        args.skip_github = True
        args.skip_venues = True

    if not (ROOT / "framework" / "scripts" / "main.py").is_file():
        print(f"missing framework entrypoint: {ROOT / 'framework' / 'scripts' / 'main.py'}", file=sys.stderr)
        return 2
    source_selection = _effective_source_selection(args)
    reuse_payload = None if internal_output_dir is not None else maybe_reuse_taste_run(args, extra_queries)
    if reuse_payload:
        return 0
    paths = build_paths(args.project)
    cfg = _load_project_config(args.project)
    run_token = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S_%f") + f"_{os.getpid()}"
    tmp_dir = paths.root / "tmp" / "finding" / run_token
    tmp_dir.mkdir(parents=True, exist_ok=True)
    driver = tmp_dir / "run_driver.py"
    write_driver(
        driver,
        args.project,
        args.max_papers,
        args.max_ideas,
        args.repair_rounds,
        bool(source_selection.get("include_arxiv")),
        bool(source_selection.get("include_huggingface")),
        bool(source_selection.get("include_github")),
        bool(source_selection.get("venue_ids")),
        source_selection,
        request_source=args.request_source or ("web" if args.web_job_id else "cli"),
        force_new_find=bool(args.force_new_find),
        restart_full_cycle=bool(args.restart_full_cycle),
        human_approved_new_find=bool(args.human_approved_new_find),
        approval_reason=str(args.approval_reason or "").strip(),
        web_job_id=str(args.web_job_id or "").strip(),
        deep_survey=bool(args.deep_survey),
        fast_mode=bool(args.fast_mode),
    )
    if extra_queries:
        targeted_path = (internal_output_dir / "taste_targeted_queries.json") if internal_output_dir is not None else paths.state / "taste_targeted_queries.json"
        try:
            existing_targeted = json.loads(targeted_path.read_text(encoding="utf-8")) if targeted_path.exists() else {}
        except Exception:
            existing_targeted = {}
        if not isinstance(existing_targeted, dict):
            existing_targeted = {}
        existing_targeted.update({"project": args.project, "queries": extra_queries, "updated_by": "run_frontend"})
        targeted_path.write_text(json.dumps(existing_targeted, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    log_path = (internal_output_dir / "finding_frontend.log") if internal_output_dir is not None else paths.logs / "finding_frontend.log"
    start = time.time()
    run_env = os.environ.copy()
    run_env["WORKFLOW_RUNTIME_DIR"] = run_env.get("FINDING_RUNTIME_DIR") or str(ROOT / "modules" / "finding" / ".runtime")
    run_env["TASTE_FIND_INPUT_DIR"] = str(tmp_dir / "input")
    if internal_output_dir is not None:
        run_env["TASTE_INTERNAL_FIND_OUTPUT_DIR"] = str(internal_output_dir)
    local_llm_path = _local_llm_config_path()
    if not run_env.get("FINDING_LLM_CONFIG") and local_llm_path.exists():
        run_env["FINDING_LLM_CONFIG"] = str(local_llm_path)
    local_llm = _load_local_llm_config()
    api_key_env = run_env.get("LLM_API_KEY_ENV") or "OPENAI_API_KEY"
    api_key = (run_env.get(api_key_env, "") if api_key_env else "") or run_env.get("LLM_API_KEY", "") or str(local_llm.get("api_key") or "")
    if api_key_env:
        run_env["LLM_API_KEY_ENV"] = str(api_key_env)
    for env_key, local_key in [("LLM_API_BASE", "base_url"), ("LLM_MODEL", "model"), ("LLM_PROVIDER", "provider")]:
        if not run_env.get(env_key) and local_llm.get(local_key):
            run_env[env_key] = str(local_llm.get(local_key))
    try:
        driver_cmd = driver_python_command(args, cfg, driver)
    except RuntimeError as exc:
        log_path.write_text(str(exc) + "\n", encoding="utf-8")
        print(str(exc), file=sys.stderr)
        return 2
    try:
        proc = run(driver_cmd, cwd=ROOT, env=run_env, timeout_sec=args.timeout_sec, live_log_path=log_path)
    except subprocess.TimeoutExpired as exc:
        elapsed = time.time() - start
        stdout = redact(exc.stdout or "") if isinstance(exc.stdout, str) else ""
        stderr = redact(exc.stderr or "") if isinstance(exc.stderr, str) else ""
        log_path.write_text(stdout + "\n--- STDERR ---\n" + stderr + f"\n--- TIMEOUT ---\ntimeout_sec={args.timeout_sec}\nelapsed_sec={elapsed:.3f}\n", encoding="utf-8")
        try:
            driver.unlink()
        except FileNotFoundError:
            pass
        fallback_reason = f"Find timed out after {args.timeout_sec}s before producing a complete usable result."
        if internal_output_dir is not None:
            timeout_payload = {
                "project": args.project,
                "status": "internal_find_timeout",
                "stage": "internal_find_timeout",
                "timeout_sec": args.timeout_sec,
                "elapsed_sec": round(elapsed, 3),
                "output_dir": str(internal_output_dir),
                "reason": fallback_reason,
                "web_visible": False,
                "internal_literature_survey": True,
                "log_path": str(log_path),
            }
            (internal_output_dir / "finding_frontend.json").write_text(json.dumps(timeout_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            (internal_output_dir / "finding_frontend.md").write_text("# Internal literature survey\n\n- status: internal_find_timeout\n- web_visible: False\n", encoding="utf-8")
            print(json.dumps(timeout_payload, ensure_ascii=False))
            return 124
        run_hint = latest_taste_run_hint()
        latest_dir = Path(run_hint.get("latest_run_dir", "")) if run_hint else Path("")
        if latest_dir.exists() and (latest_dir / "find_results.json").exists():
            out_dir = paths.planning / "finding"
            out_dir.mkdir(parents=True, exist_ok=True)
            for name in ["find.md", "find_results.json", "selection.json", "source_status.md", "venue_health_report.json", "category_scan_report.json", "title_filter_report.json", "arxiv_raw.json", "arxiv_prefiltered.json", "hf.md", "github.md"]:
                source = latest_dir / name
                if source.exists():
                    shutil.copyfile(source, out_dir / name)
            payload = write_find_timeout_state(
                args.project,
                status="find_artifacts_copied_after_timeout",
                reason=fallback_reason,
                log_path=log_path,
                timeout_sec=args.timeout_sec,
                elapsed_sec=elapsed,
                run_dir=latest_dir,
                output_dir=out_dir,
            )
            print(json.dumps(payload, ensure_ascii=False))
            print(f"native frontend timed out after {args.timeout_sec}s; copied real Find artifacts only.", file=sys.stderr)
            return 0
        else:
            payload = write_find_timeout_state(
                args.project,
                status="blocked_find_timeout_no_usable_artifacts",
                reason=fallback_reason,
                log_path=log_path,
                timeout_sec=args.timeout_sec,
                elapsed_sec=elapsed,
                run_dir=None,
                output_dir=paths.planning / "finding",
            )
            print(json.dumps(payload, ensure_ascii=False))
            print(f"native frontend timed out after {args.timeout_sec}s before usable Find; no fallback artifacts were written.", file=sys.stderr)
            return 124
    finally:
        _cleanup_recovery_approval_control_files(paths.root, args.web_job_id)
    log_path.write_text(redact(proc.stdout) + "\n--- STDERR ---\n" + redact(proc.stderr), encoding="utf-8")
    try:
        driver.unlink()
    except FileNotFoundError:
        pass
    try:
        hint = latest_taste_run_hint()
        run_dir = Path(str(hint.get("latest_run_dir") or ""))
        if os.environ.get("ALLOW_FIND_RUN_REUSE", "0").lower() in {"1", "true", "yes", "on"} and internal_output_dir is None and proc.returncode == 0 and run_dir.exists() and (run_dir / "find_results.json").exists():
            run_id = run_dir.name
            save_taste_reuse_signature(args.project, run_id, run_dir, args, extra_queries)
    except Exception as exc:
        print(f"TASTE reuse signature save failed: {exc}", file=sys.stderr)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
