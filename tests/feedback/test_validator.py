from __future__ import annotations

from datetime import datetime, timezone
import inspect
import json
from pathlib import Path
from typing import get_type_hints

import pytest

import feedback
from feedback import ExecutionHandle, ValidationResult, ValidationStatus
from feedback.interfaces import ResultValidator
from feedback.validator import FindResultValidator


RUN_ID = "find_20260905_120000_000001"
STARTED_AT = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
EXPECTED_CHECK_CODES = [
    "process_terminal",
    "process_exit_code_ok",
    "run_dir_exists",
    "result_exists",
    "result_parseable",
    "result_run_id_matches",
    "reading_candidate_ids_unique",
    "reading_bridge_probe_passed",
]


def _make_handle(run_path: Path, **overrides: object) -> ExecutionHandle:
    values: dict[str, object] = {
        "context_id": "ctx-validator",
        "pid": 7102,
        "started_at": STARTED_AT,
        "process_alive": False,
        "stdout_path": str(run_path / "logs" / "stdout.log"),
        "stderr_path": str(run_path / "logs" / "stderr.log"),
        "run_id": RUN_ID,
        "run_dir": str(run_path),
        "exit_code": 0,
    }
    values.update(overrides)
    return ExecutionHandle(**values)  # type: ignore[arg-type]


def _valid_payload() -> dict[str, object]:
    return {
        "run_id": RUN_ID,
        "screened_ranking": [
            {"doi": "10.1000/A", "title": "Paper A"},
            {"metadata": {"arxiv_id": "2401.00001"}, "title": "Paper B"},
        ],
        "recommendation_target_count": 3,
        "recommendation_actual_count": 2,
        "recommendation_shortfall": 1,
        "strong_recommendation_count": 2,
        "recommendation_quality": {"status": "acceptable"},
    }


def _write_result(
    run_dir: Path,
    payload: object,
    *,
    canonical: bool = True,
) -> Path:
    path = run_dir / "final" / "find_results.json" if canonical else run_dir / "find_results.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _check(result: ValidationResult, code: str):
    return next(check for check in result.checks if check.code == code)


def _run_validation(
    validator: ResultValidator,
    execution_handle: ExecutionHandle,
) -> ValidationResult:
    return validator.validate(execution_handle)


def test_valid_canonical_result_passes_without_modifying_inputs(tmp_path: Path) -> None:
    run_dir = tmp_path / RUN_ID
    result_path = _write_result(run_dir, _valid_payload())
    original_bytes = result_path.read_bytes()
    handle = _make_handle(run_dir)
    original_handle = handle.to_dict()

    result = FindResultValidator().validate(handle)

    assert result.status is ValidationStatus.PASS
    assert result.ready_for_read is True
    assert [check.code for check in result.checks] == EXPECTED_CHECK_CODES
    assert all(check.required for check in result.checks)
    assert all(check.status is ValidationStatus.PASS for check in result.checks)
    assert result.passed_check_count == 8
    assert result.warning_check_count == 0
    assert result.blocked_check_count == 0
    assert result.run_id == RUN_ID
    assert result.validated_run_dir == str(run_dir)
    assert result.producer == "find_result_validator"
    assert result.producer_version == "1.0"
    assert result.policy_version == "find.validation.minimum.v1"
    assert result.validated_at.tzinfo is not None
    assert result.duration_ms >= 0
    assert result.candidate_ids == ["doi:10.1000/a", "arxiv_id:2401.00001"]
    assert result.candidate_digest == "e7a7fd82bafa5900674c9f4c7c27f9e8cf2d39737cf633ef5b473930ee120fcb"
    assert result.downstream_input_preview == {
        "ranking_source": "screened_ranking",
        "candidate_count": 2,
    }
    assert result.recommendation_target_count == 3
    assert result.recommendation_actual_count == 2
    assert result.recommendation_shortfall == 1
    assert result.strong_recommendation_count == 2
    assert result.recommendation_quality_status == "acceptable"
    assert result.bridge_probe_status is ValidationStatus.PASS
    assert result.warnings == []
    assert result.blockers == []
    assert result.failure_codes == []
    assert len(result.input_artifact_refs) == 1
    artifact = result.input_artifact_refs[0]
    assert artifact.role == "result"
    assert artifact.path == str(result_path)
    assert artifact.required is True
    assert artifact.exists is True
    assert artifact.size_bytes == len(original_bytes)
    assert artifact.modified_at is not None and artifact.modified_at.tzinfo is not None
    assert artifact.parse_status == "valid"
    assert artifact.sha256 is None
    assert result_path.read_bytes() == original_bytes
    assert handle.to_dict() == original_handle


def test_root_result_is_used_only_when_canonical_result_is_missing(tmp_path: Path) -> None:
    run_dir = tmp_path / RUN_ID
    result_path = _write_result(run_dir, _valid_payload(), canonical=False)

    result = FindResultValidator().validate(_make_handle(run_dir))

    assert result.status is ValidationStatus.PASS
    assert result.input_artifact_refs[0].path == str(result_path)


def test_canonical_result_takes_priority_over_root_result(tmp_path: Path) -> None:
    run_dir = tmp_path / RUN_ID
    canonical = _write_result(run_dir, _valid_payload())
    _write_result(
        run_dir,
        {"run_id": "find_wrong", "strong_recommendations": [{"id": "wrong"}]},
        canonical=False,
    )

    result = FindResultValidator().validate(_make_handle(run_dir))

    assert result.status is ValidationStatus.PASS
    assert result.input_artifact_refs[0].path == str(canonical)
    assert result.candidate_ids == ["doi:10.1000/a", "arxiv_id:2401.00001"]


def test_validate_rejects_non_execution_handle() -> None:
    with pytest.raises(TypeError, match="execution_handle must be an ExecutionHandle"):
        FindResultValidator().validate(object())  # type: ignore[arg-type]


def test_validate_rejects_a_live_process_before_reading_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_read(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("result file must not be read")

    monkeypatch.setattr(Path, "read_text", fail_if_read)
    handle = _make_handle(tmp_path / RUN_ID, process_alive=True, exit_code=None)

    with pytest.raises(RuntimeError, match="still running"):
        FindResultValidator().validate(handle)


def test_validate_rejects_an_unbound_run_without_scanning(tmp_path: Path) -> None:
    handle = _make_handle(tmp_path / RUN_ID, run_id=None, run_dir=None)

    with pytest.raises(RuntimeError, match="not bound"):
        FindResultValidator().validate(handle)


def test_nonzero_exit_code_blocks_but_keeps_safe_file_checks(tmp_path: Path) -> None:
    run_dir = tmp_path / RUN_ID
    _write_result(run_dir, _valid_payload())

    result = FindResultValidator().validate(_make_handle(run_dir, exit_code=7))

    assert result.status is ValidationStatus.BLOCK
    assert result.ready_for_read is False
    assert _check(result, "process_exit_code_ok").status is ValidationStatus.BLOCK
    assert _check(result, "result_parseable").status is ValidationStatus.PASS
    assert "process_exit_code_ok" in result.failure_codes


def test_missing_exit_code_blocks(tmp_path: Path) -> None:
    run_dir = tmp_path / RUN_ID
    _write_result(run_dir, _valid_payload())

    result = FindResultValidator().validate(_make_handle(run_dir, exit_code=None))

    assert _check(result, "process_exit_code_ok").status is ValidationStatus.BLOCK


def test_missing_run_directory_returns_block_with_expected_artifact(tmp_path: Path) -> None:
    run_dir = tmp_path / "missing-run"

    result = FindResultValidator().validate(_make_handle(run_dir))

    assert result.status is ValidationStatus.BLOCK
    assert _check(result, "run_dir_exists").status is ValidationStatus.BLOCK
    assert _check(result, "result_exists").status is ValidationStatus.BLOCK
    artifact = result.input_artifact_refs[0]
    assert artifact.path == str(run_dir / "final" / "find_results.json")
    assert artifact.exists is False
    assert artifact.parse_status == "missing"


def test_missing_result_file_returns_block(tmp_path: Path) -> None:
    run_dir = tmp_path / RUN_ID
    run_dir.mkdir()

    result = FindResultValidator().validate(_make_handle(run_dir))

    assert result.status is ValidationStatus.BLOCK
    assert _check(result, "result_exists").status is ValidationStatus.BLOCK
    assert result.input_artifact_refs[0].parse_status == "missing"


@pytest.mark.parametrize(
    "raw",
    ["{broken", "", "[1, 2, 3]"],
    ids=["invalid-json", "empty", "non-object"],
)
def test_unparseable_or_non_object_result_returns_block(tmp_path: Path, raw: str) -> None:
    run_dir = tmp_path / RUN_ID
    result_path = run_dir / "final" / "find_results.json"
    result_path.parent.mkdir(parents=True)
    result_path.write_text(raw, encoding="utf-8")

    result = FindResultValidator().validate(_make_handle(run_dir))

    assert result.status is ValidationStatus.BLOCK
    assert _check(result, "result_parseable").status is ValidationStatus.BLOCK
    assert result.input_artifact_refs[0].parse_status == "invalid"


@pytest.mark.parametrize(
    "payload",
    [
        {"strong_recommendations": [{"id": "paper-1"}]},
        {"run_id": "find_other", "strong_recommendations": [{"id": "paper-1"}]},
    ],
    ids=["missing", "mismatch"],
)
def test_missing_or_mismatched_result_run_id_blocks(
    tmp_path: Path,
    payload: dict[str, object],
) -> None:
    run_dir = tmp_path / RUN_ID
    _write_result(run_dir, payload)

    result = FindResultValidator().validate(_make_handle(run_dir))

    assert result.status is ValidationStatus.BLOCK
    assert _check(result, "result_run_id_matches").status is ValidationStatus.BLOCK
    assert result.run_id == RUN_ID


def test_missing_ranking_candidates_blocks_bridge_probe(tmp_path: Path) -> None:
    run_dir = tmp_path / RUN_ID
    _write_result(run_dir, {"run_id": RUN_ID})

    result = FindResultValidator().validate(_make_handle(run_dir))

    assert result.candidate_ids == []
    assert _check(result, "reading_candidate_ids_unique").status is ValidationStatus.PASS
    assert _check(result, "reading_bridge_probe_passed").status is ValidationStatus.BLOCK
    assert result.bridge_probe_status is ValidationStatus.BLOCK
    assert result.downstream_input_preview is None


def test_candidate_without_identity_or_real_title_blocks(tmp_path: Path) -> None:
    run_dir = tmp_path / RUN_ID
    _write_result(
        run_dir,
        {"run_id": RUN_ID, "strong_recommendations": [{"abstract": "No identity"}]},
    )

    result = FindResultValidator().validate(_make_handle(run_dir))

    assert result.candidate_ids == []
    assert _check(result, "reading_candidate_ids_unique").status is ValidationStatus.BLOCK
    assert _check(result, "reading_bridge_probe_passed").status is ValidationStatus.BLOCK
    assert any("identity" in blocker.lower() for blocker in result.blockers)


def test_duplicate_candidate_identity_blocks_but_result_ids_remain_unique(tmp_path: Path) -> None:
    run_dir = tmp_path / RUN_ID
    _write_result(
        run_dir,
        {
            "run_id": RUN_ID,
            "strong_recommendations": [
                {"id": "Paper-1", "title": "First"},
                {"id": "paper-1", "title": "Duplicate"},
            ],
        },
    )

    result = FindResultValidator().validate(_make_handle(run_dir))

    assert result.status is ValidationStatus.BLOCK
    assert result.candidate_ids == ["id:paper-1"]
    assert _check(result, "reading_candidate_ids_unique").status is ValidationStatus.BLOCK
    assert "reading_candidate_ids_unique" in result.failure_codes
    assert any("duplicate" in blocker.lower() for blocker in result.blockers)


def test_first_nonempty_dictionary_ranking_source_wins(tmp_path: Path) -> None:
    run_dir = tmp_path / RUN_ID
    _write_result(
        run_dir,
        {
            "run_id": RUN_ID,
            "screened_ranking": ["not-a-record"],
            "final_ranking": [{"id": "first-valid"}],
            "strong_recommendations": [{"id": "later"}],
        },
    )

    result = FindResultValidator().validate(_make_handle(run_dir))

    assert result.status is ValidationStatus.PASS
    assert result.candidate_ids == ["id:first-valid"]
    assert result.downstream_input_preview == {
        "ranking_source": "final_ranking",
        "candidate_count": 1,
    }


def test_screened_ranking_precedes_other_valid_sources(tmp_path: Path) -> None:
    run_dir = tmp_path / RUN_ID
    _write_result(
        run_dir,
        {
            "run_id": RUN_ID,
            "screened_ranking": [{"id": "screened"}],
            "final_ranking": [{"id": "final"}],
        },
    )

    result = FindResultValidator().validate(_make_handle(run_dir))

    assert result.candidate_ids == ["id:screened"]
    assert result.downstream_input_preview == {
        "ranking_source": "screened_ranking",
        "candidate_count": 1,
    }


def test_evaluated_candidates_keep_reading_bridge_fallback_order(tmp_path: Path) -> None:
    run_dir = tmp_path / RUN_ID
    _write_result(
        run_dir,
        {
            "run_id": RUN_ID,
            "evaluated_candidates": [
                {"id": "low", "score": 1},
                {"id": "ranked", "rank": 1, "score": 0},
                {"id": "high", "score": 9},
            ],
        },
    )

    result = FindResultValidator().validate(_make_handle(run_dir))

    assert result.candidate_ids == ["id:ranked", "id:high", "id:low"]
    assert result.downstream_input_preview == {
        "ranking_source": "evaluated_candidates",
        "candidate_count": 3,
    }


def test_candidate_identity_uses_metadata_then_normalized_title(tmp_path: Path) -> None:
    run_dir = tmp_path / RUN_ID
    _write_result(
        run_dir,
        {
            "run_id": RUN_ID,
            "ranked_papers": [
                {"metadata": {"doi": " 10.TEST/ABC "}},
                {"paper_title": "  A---B  "},
                42,
            ],
        },
    )

    result = FindResultValidator().validate(_make_handle(run_dir))

    assert result.status is ValidationStatus.PASS
    assert result.candidate_ids == ["doi:10.test/abc", "title:a b"]


def test_valid_recommendation_statistics_are_preserved(tmp_path: Path) -> None:
    run_dir = tmp_path / RUN_ID
    payload = _valid_payload()
    payload["recommendation_quality_status"] = "ignored-top-level"
    _write_result(run_dir, payload)

    result = FindResultValidator().validate(_make_handle(run_dir))

    assert result.recommendation_target_count == 3
    assert result.recommendation_actual_count == 2
    assert result.recommendation_shortfall == 1
    assert result.strong_recommendation_count == 2
    assert result.recommendation_quality_status == "acceptable"


def test_invalid_or_missing_statistics_use_safe_fallbacks(tmp_path: Path) -> None:
    run_dir = tmp_path / RUN_ID
    _write_result(
        run_dir,
        {
            "run_id": RUN_ID,
            "recommendation_target_count": True,
            "recommendation_actual_count": True,
            "recommendation_shortfall": True,
            "strong_recommendation_count": True,
            "recommendation_quality_status": 7,
            "strong_recommendations": [
                {"id": "one"},
                "ignored",
                {"id": "two"},
            ],
        },
    )

    result = FindResultValidator().validate(_make_handle(run_dir))

    assert result.recommendation_target_count == 0
    assert result.recommendation_actual_count == 2
    assert result.recommendation_shortfall == 0
    assert result.strong_recommendation_count == 2
    assert result.recommendation_quality_status == "unknown"


@pytest.mark.parametrize(
    "read_error",
    [
        PermissionError("denied"),
        OSError("local I/O failure"),
        UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid byte"),
    ],
    ids=["permission", "os-error", "unicode"],
)
def test_file_read_errors_return_sanitized_block(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    read_error: Exception,
) -> None:
    run_dir = tmp_path / RUN_ID
    _write_result(run_dir, _valid_payload())

    def fail_read(*_args: object, **_kwargs: object) -> str:
        raise read_error

    monkeypatch.setattr(Path, "read_text", fail_read)

    result = FindResultValidator().validate(_make_handle(run_dir))

    assert result.status is ValidationStatus.BLOCK
    assert _check(result, "result_parseable").status is ValidationStatus.BLOCK
    assert result.input_artifact_refs[0].parse_status == "invalid"
    assert all(str(run_dir) not in blocker for blocker in result.blockers)


def test_file_removed_during_validation_returns_missing_block(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / RUN_ID
    result_path = _write_result(run_dir, _valid_payload())
    original_read_text = Path.read_text

    def remove_then_read(path: Path, *args: object, **kwargs: object) -> str:
        if path == result_path:
            path.unlink()
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", remove_then_read)

    result = FindResultValidator().validate(_make_handle(run_dir))

    assert result.status is ValidationStatus.BLOCK
    assert _check(result, "result_exists").status is ValidationStatus.BLOCK
    assert result.input_artifact_refs[0].exists is False
    assert result.input_artifact_refs[0].parse_status == "missing"


def test_validation_ids_are_unique(tmp_path: Path) -> None:
    run_dir = tmp_path / RUN_ID
    _write_result(run_dir, _valid_payload())
    validator = FindResultValidator()

    first = validator.validate(_make_handle(run_dir))
    second = validator.validate(_make_handle(run_dir))

    assert first.validation_id != second.validation_id


def test_public_import_and_protocol_substitution(tmp_path: Path) -> None:
    from feedback import FindResultValidator as PublicFindResultValidator
    from feedback.validator import FindResultValidator as ModuleFindResultValidator

    assert PublicFindResultValidator is FindResultValidator
    assert ModuleFindResultValidator is FindResultValidator
    assert "FindResultValidator" in feedback.__all__
    signature = inspect.signature(FindResultValidator.validate)
    hints = get_type_hints(FindResultValidator.validate)
    assert list(signature.parameters) == ["self", "execution_handle"]
    assert hints["execution_handle"] is ExecutionHandle
    assert hints["return"] is ValidationResult
    assert {
        name for name in FindResultValidator.__dict__ if not name.startswith("_")
    } == {"validate"}

    run_dir = tmp_path / RUN_ID
    _write_result(run_dir, _valid_payload())
    validator: ResultValidator = FindResultValidator()

    result = _run_validation(validator, _make_handle(run_dir))

    assert isinstance(result, ValidationResult)
    assert result.status is ValidationStatus.PASS
