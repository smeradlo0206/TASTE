"""Read-only minimum validation for one completed Find run."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import stat as stat_module
from time import perf_counter
from uuid import uuid4

from .contracts import (
    ArtifactRef,
    ExecutionHandle,
    ValidationCheck,
    ValidationResult,
    ValidationStatus,
)


_PRODUCER = "find_result_validator"
_PRODUCER_VERSION = "1.0"
_POLICY_VERSION = "find.validation.minimum.v1"
_RANKING_KEYS = (
    "screened_ranking",
    "final_ranking",
    "ranked_papers",
    "evaluated_candidates",
    "strong_recommendations",
    "recommendations",
    "read_candidates",
    "articles",
    "input_articles",
    "papers",
)
_IDENTITY_KEYS = (
    "doi",
    "arxiv_id",
    "biorxiv_doi",
    "paper_id",
    "id",
    "url",
    "pdf_url",
)
_TITLE_KEYS = ("title", "paper_title", "name")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _check(
    code: str,
    passed: bool,
    message: str,
    *,
    expected: object | None = None,
    actual: object | None = None,
) -> ValidationCheck:
    return ValidationCheck(
        code=code,
        status=ValidationStatus.PASS if passed else ValidationStatus.BLOCK,
        required=True,
        message=message,
        expected=expected,
        actual=actual,
    )


def _path_exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return True


def _select_result_path(run_dir: Path) -> Path:
    canonical = run_dir / "final" / "find_results.json"
    if _path_exists(canonical):
        return canonical
    fallback = run_dir / "find_results.json"
    if _path_exists(fallback):
        return fallback
    return canonical


def _missing_artifact(path: Path) -> ArtifactRef:
    return ArtifactRef(
        role="result",
        path=str(path),
        required=True,
        exists=False,
        parse_status="missing",
    )


def _read_result(
    path: Path,
) -> tuple[ArtifactRef, dict[str, object] | None, bool, str | None]:
    try:
        file_stat = path.stat()
    except FileNotFoundError:
        return _missing_artifact(path), None, False, "Result file is missing"
    except OSError:
        return (
            ArtifactRef(
                role="result",
                path=str(path),
                required=True,
                exists=True,
                parse_status="invalid",
            ),
            None,
            True,
            "Result file metadata could not be read",
        )

    modified_at = datetime.fromtimestamp(file_stat.st_mtime, tz=timezone.utc)
    if not stat_module.S_ISREG(file_stat.st_mode):
        return (
            ArtifactRef(
                role="result",
                path=str(path),
                required=True,
                exists=True,
                size_bytes=file_stat.st_size,
                modified_at=modified_at,
                parse_status="invalid",
            ),
            None,
            False,
            "Result path is not a file",
        )

    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _missing_artifact(path), None, False, "Result file is missing"
    except (OSError, UnicodeError):
        return (
            ArtifactRef(
                role="result",
                path=str(path),
                required=True,
                exists=True,
                size_bytes=file_stat.st_size,
                modified_at=modified_at,
                parse_status="invalid",
            ),
            None,
            True,
            "Result file could not be read as UTF-8",
        )

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        payload = None
    if not isinstance(payload, dict):
        return (
            ArtifactRef(
                role="result",
                path=str(path),
                required=True,
                exists=True,
                size_bytes=file_stat.st_size,
                modified_at=modified_at,
                parse_status="invalid",
            ),
            None,
            True,
            "Result JSON must contain an object",
        )
    return (
        ArtifactRef(
            role="result",
            path=str(path),
            required=True,
            exists=True,
            size_bytes=file_stat.st_size,
            modified_at=modified_at,
            parse_status="valid",
        ),
        payload,
        True,
        None,
    )


def _number(row: dict[str, object], *keys: str) -> float:
    for key in keys:
        try:
            value = row.get(key)
            if value not in (None, ""):
                return float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
    return 0.0


def _fallback_rank_key(row: dict[str, object]) -> tuple[float, float, float, str]:
    explicit_rank = _number(row, "final_rank", "recommendation_rank", "rank")
    rank_order = -explicit_rank if explicit_rank > 0 else float("-inf")
    title = next((str(row.get(key) or "").strip() for key in _TITLE_KEYS if row.get(key)), "")
    return (
        rank_order,
        _number(row, "fit_score", "recommendation_score", "score"),
        _number(row, "diversity_score", "quality_score"),
        title.lower(),
    )


def _ranking_rows(payload: dict[str, object]) -> tuple[list[dict[str, object]], str]:
    for key in _RANKING_KEYS:
        value = payload.get(key)
        if not isinstance(value, list) or not value:
            continue
        rows = [row for row in value if isinstance(row, dict)]
        if not rows:
            continue
        if key == "evaluated_candidates":
            rows = sorted(rows, key=_fallback_rank_key, reverse=True)
        return rows, key
    return [], ""


def _candidate_identity(row: dict[str, object]) -> str:
    metadata_value = row.get("metadata")
    metadata = metadata_value if isinstance(metadata_value, dict) else {}
    for key in _IDENTITY_KEYS:
        value = str(row.get(key) or metadata.get(key) or "").strip().lower()
        if value:
            return f"{key}:{value}"
    for key in _TITLE_KEYS:
        title = str(row.get(key) or "").strip()
        if not title:
            continue
        normalized = re.sub(r"\W+", " ", title.lower()).strip()
        if normalized:
            return f"title:{normalized}"
    return ""


def _candidate_ids(
    rows: list[dict[str, object]],
) -> tuple[list[str], int, int]:
    identities: list[str] = []
    seen: set[str] = set()
    missing_count = 0
    duplicate_count = 0
    for row in rows:
        identity = _candidate_identity(row)
        if not identity:
            missing_count += 1
            continue
        if identity in seen:
            duplicate_count += 1
            continue
        seen.add(identity)
        identities.append(identity)
    return identities, missing_count, duplicate_count


def _candidate_digest(candidate_ids: list[str]) -> str:
    payload = json.dumps(candidate_ids, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _non_negative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _recommendation_statistics(
    payload: dict[str, object] | None,
) -> tuple[int, int, int, int, str]:
    values = payload or {}
    strong = _non_negative_int(values.get("strong_recommendation_count"))
    if strong is None:
        rows = values.get("strong_recommendations")
        strong = sum(isinstance(row, dict) for row in rows) if isinstance(rows, list) else 0

    actual = _non_negative_int(values.get("recommendation_actual_count"))
    if actual is None:
        actual = strong
    target = _non_negative_int(values.get("recommendation_target_count"))
    if target is None:
        target = 0
    shortfall = _non_negative_int(values.get("recommendation_shortfall"))
    if shortfall is None:
        shortfall = max(target - actual, 0)

    quality = values.get("recommendation_quality")
    nested_status = quality.get("status") if isinstance(quality, dict) else None
    top_level_status = values.get("recommendation_quality_status")
    if isinstance(nested_status, str) and nested_status.strip():
        quality_status = nested_status.strip()
    elif isinstance(top_level_status, str) and top_level_status.strip():
        quality_status = top_level_status.strip()
    else:
        quality_status = "unknown"
    return target, actual, shortfall, strong, quality_status


class FindResultValidator:
    """Validate one completed Find run without modifying its artifacts."""

    def validate(
        self,
        execution_handle: ExecutionHandle,
    ) -> ValidationResult:
        if not isinstance(execution_handle, ExecutionHandle):
            raise TypeError("execution_handle must be an ExecutionHandle")
        if execution_handle.process_alive:
            raise RuntimeError("cannot validate a Find process that is still running")
        if execution_handle.run_id is None or execution_handle.run_dir is None:
            raise RuntimeError("cannot validate a Find run that is not bound")

        started = perf_counter()
        created_at = _utc_now()
        run_dir = Path(execution_handle.run_dir)
        try:
            run_dir_exists = run_dir.is_dir()
        except OSError:
            run_dir_exists = False

        result_path = _select_result_path(run_dir)
        artifact, payload, result_exists, read_error = _read_result(result_path)
        result_parseable = payload is not None
        payload_run_id = payload.get("run_id") if payload is not None else None
        run_id_matches = bool(
            isinstance(payload_run_id, str)
            and payload_run_id.strip()
            and payload_run_id == execution_handle.run_id
        )

        rows, ranking_source = _ranking_rows(payload or {})
        candidate_ids, missing_identity_count, duplicate_identity_count = _candidate_ids(rows)
        identities_unique = missing_identity_count == 0 and duplicate_identity_count == 0
        bridge_probe_passed = bool(
            result_parseable
            and run_id_matches
            and candidate_ids
            and identities_unique
        )

        checks = [
            _check(
                "process_terminal",
                True,
                "Find process is terminal",
                expected=False,
                actual=execution_handle.process_alive,
            ),
            _check(
                "process_exit_code_ok",
                execution_handle.exit_code == 0,
                "Find process exit code is zero" if execution_handle.exit_code == 0 else "Find process exit code is missing or non-zero",
                expected=0,
                actual=execution_handle.exit_code,
            ),
            _check(
                "run_dir_exists",
                run_dir_exists,
                "Find run directory exists" if run_dir_exists else "Find run directory is missing or is not a directory",
                expected=True,
                actual=run_dir_exists,
            ),
            _check(
                "result_exists",
                result_exists,
                "Find result file exists" if result_exists else "Find result file is missing",
                expected=True,
                actual=result_exists,
            ),
            _check(
                "result_parseable",
                result_parseable,
                "Find result is a JSON object" if result_parseable else (read_error or "Find result is not a JSON object"),
                expected="valid JSON object",
                actual=artifact.parse_status,
            ),
            _check(
                "result_run_id_matches",
                run_id_matches,
                "Find result run ID matches" if run_id_matches else "Find result run ID is missing or does not match",
                expected="execution_handle.run_id",
                actual="matching" if run_id_matches else "missing_or_mismatch",
            ),
            _check(
                "reading_candidate_ids_unique",
                identities_unique,
                "Candidate identities are unique" if identities_unique else "Candidate identity is missing or duplicated",
                expected={"missing": 0, "duplicates": 0},
                actual={
                    "missing": missing_identity_count,
                    "duplicates": duplicate_identity_count,
                },
            ),
            _check(
                "reading_bridge_probe_passed",
                bridge_probe_passed,
                "Find result has a Bridge-compatible candidate ranking" if bridge_probe_passed else "Find result is not minimally compatible with the Reading bridge",
                expected=True,
                actual=bridge_probe_passed,
            ),
        ]

        blocked_checks = [
            check for check in checks if check.status is ValidationStatus.BLOCK
        ]
        blockers = [check.message for check in blocked_checks]
        failure_codes = [check.code for check in blocked_checks]
        bridge_probe_errors: list[str] = []
        if not result_parseable:
            bridge_probe_errors.append("Result JSON is unavailable or invalid")
        if result_parseable and not run_id_matches:
            bridge_probe_errors.append("Result run ID is missing or does not match")
        if not candidate_ids:
            bridge_probe_errors.append("No identifiable ranked candidates are available")
        if missing_identity_count:
            bridge_probe_errors.append("One or more ranked candidates have no reliable identity")
        if duplicate_identity_count:
            bridge_probe_errors.append("One or more ranked candidate identities are duplicated")

        status = ValidationStatus.BLOCK if blocked_checks else ValidationStatus.PASS
        target, actual, shortfall, strong, quality = _recommendation_statistics(payload)
        validated_at = _utc_now()
        duration_ms = max(0, int((perf_counter() - started) * 1000))
        return ValidationResult(
            validation_id=f"validation-{uuid4().hex}",
            run_id=execution_handle.run_id,
            created_at=created_at,
            validated_at=validated_at,
            validated_run_dir=execution_handle.run_dir,
            producer=_PRODUCER,
            producer_version=_PRODUCER_VERSION,
            policy_version=_POLICY_VERSION,
            duration_ms=duration_ms,
            status=status,
            ready_for_read=status is ValidationStatus.PASS,
            summary=(
                "Find result passed minimum validation"
                if status is ValidationStatus.PASS
                else "Find result is blocked by minimum validation"
            ),
            checks=checks,
            passed_check_count=sum(
                check.status is ValidationStatus.PASS for check in checks
            ),
            warning_check_count=0,
            blocked_check_count=len(blocked_checks),
            recommendation_target_count=target,
            recommendation_actual_count=actual,
            recommendation_shortfall=shortfall,
            strong_recommendation_count=strong,
            recommendation_quality_status=quality,
            candidate_ids=candidate_ids,
            candidate_digest=_candidate_digest(candidate_ids),
            bridge_probe_status=(
                ValidationStatus.PASS if bridge_probe_passed else ValidationStatus.BLOCK
            ),
            input_artifact_refs=[artifact],
            bridge_probe_errors=bridge_probe_errors,
            downstream_input_preview=(
                {
                    "ranking_source": ranking_source,
                    "candidate_count": len(candidate_ids),
                }
                if bridge_probe_passed
                else None
            ),
            warnings=[],
            blockers=blockers,
            failure_codes=failure_codes,
            evidence_refs=[],
        )
