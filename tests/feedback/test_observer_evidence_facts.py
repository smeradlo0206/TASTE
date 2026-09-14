from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from feedback import EvidenceFact, ProgressSnapshot, ProgressStatus
from feedback.observer import FileProgressObserver
from test_observer import (
    NOW,
    _make_execution_handle,
    _make_run_context,
    _progress_payload,
    _set_now,
    _write_progress,
)


AUTHORIZED_CODES = {
    "find.seconds_without_progress",
    "find.source_total",
    "find.source_limited",
}


def _facts_by_code(snapshot: ProgressSnapshot) -> dict[str, EvidenceFact]:
    return {fact.code: fact for fact in snapshot.evidence_facts}


def _assert_snapshot_identity(snapshot: ProgressSnapshot, fact: EvidenceFact) -> None:
    assert fact.source_contract_id == snapshot.snapshot_id
    assert fact.producer == snapshot.producer
    assert fact.run_id == snapshot.run_id
    assert fact.observed_at == snapshot.observed_at


def test_valid_empty_source_status_produces_three_observed_facts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "runs" / "find_001"
    _write_progress(run_dir, _progress_payload(source_status=[]))
    _set_now(monkeypatch, NOW)

    snapshot = FileProgressObserver().observe(
        _make_run_context(tmp_path),
        _make_execution_handle(tmp_path, run_dir=run_dir),
    )

    facts = _facts_by_code(snapshot)
    assert set(facts) == AUTHORIZED_CODES
    assert facts["find.seconds_without_progress"].value == 0.0
    assert facts["find.seconds_without_progress"].source_field == "seconds_without_progress"
    assert facts["find.source_total"].value == 0
    assert facts["find.source_total"].source_field == "source_total"
    assert facts["find.source_limited"].value == 0
    assert facts["find.source_limited"].source_field == "source_limited"
    for fact in facts.values():
        _assert_snapshot_identity(snapshot, fact)


@pytest.mark.parametrize(
    "limited_row",
    [
        {"source": "explicit", "ok": False, "limited": True},
        {"source": "rate", "ok": False, "rate_limited": True},
        {"source": "status", "ok": False, "status": "http_429"},
        {"source": "error", "ok": False, "error": "http_429"},
    ],
)
def test_limited_source_fact_uses_existing_mutually_exclusive_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    limited_row: dict[str, object],
) -> None:
    run_dir = tmp_path / "runs" / "find_001"
    _write_progress(run_dir, _progress_payload(source_status=[limited_row]))
    _set_now(monkeypatch, NOW)

    snapshot = FileProgressObserver().observe(
        _make_run_context(tmp_path),
        _make_execution_handle(tmp_path, run_dir=run_dir),
    )

    facts = _facts_by_code(snapshot)
    assert set(facts) == AUTHORIZED_CODES
    assert snapshot.source_total == 1
    assert snapshot.source_limited == 1
    assert snapshot.source_failed == 0
    assert facts["find.source_total"].value == 1
    assert facts["find.source_limited"].value == 1


@pytest.mark.parametrize(
    "source_status",
    [
        pytest.param(None, id="missing"),
        pytest.param({"source": "not-a-list"}, id="not-a-list"),
        pytest.param([{"source": "valid", "ok": True}, "not-an-object"], id="invalid-row"),
    ],
)
def test_unobserved_source_status_does_not_turn_unknown_into_zero_facts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_status: object,
) -> None:
    run_dir = tmp_path / "runs" / "find_001"
    payload = _progress_payload()
    if source_status is None:
        payload.pop("source_status")
    else:
        payload["source_status"] = source_status
    _write_progress(run_dir, payload)
    _set_now(monkeypatch, NOW)

    snapshot = FileProgressObserver().observe(
        _make_run_context(tmp_path),
        _make_execution_handle(tmp_path, run_dir=run_dir),
    )

    facts = _facts_by_code(snapshot)
    assert set(facts) == {"find.seconds_without_progress"}
    assert facts["find.seconds_without_progress"].value == snapshot.seconds_without_progress
    assert all(fact.value is not None for fact in snapshot.evidence_facts)


def test_invalid_current_progress_does_not_republish_previous_source_counts_as_facts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "runs" / "find_001"
    progress_path = _write_progress(
        run_dir,
        _progress_payload(source_status=[{"source": "limited", "limited": True}]),
    )
    context = _make_run_context(tmp_path)
    handle = _make_execution_handle(tmp_path, run_dir=run_dir)
    _set_now(monkeypatch, NOW)
    previous = FileProgressObserver().observe(context, handle)
    progress_path.write_text("not-json", encoding="utf-8")
    _set_now(monkeypatch, NOW + timedelta(seconds=30))

    snapshot = FileProgressObserver().observe(context, handle, previous)

    facts = _facts_by_code(snapshot)
    assert snapshot.progress_parse_ok is False
    assert (snapshot.source_total, snapshot.source_limited) == (1, 1)
    assert set(facts) == {"find.seconds_without_progress"}
    assert facts["find.seconds_without_progress"].value == 30.0


def test_seconds_without_progress_fact_is_recreated_for_each_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "runs" / "find_001"
    _write_progress(run_dir, _progress_payload(source_status=[]))
    context = _make_run_context(tmp_path)
    handle = _make_execution_handle(tmp_path, run_dir=run_dir)
    _set_now(monkeypatch, NOW)
    previous = FileProgressObserver().observe(context, handle)
    previous_fact = _facts_by_code(previous)["find.seconds_without_progress"]
    _set_now(monkeypatch, NOW + timedelta(seconds=45))

    snapshot = FileProgressObserver().observe(context, handle, previous)

    fact = _facts_by_code(snapshot)["find.seconds_without_progress"]
    assert snapshot.status is ProgressStatus.RUNNING
    assert snapshot.seconds_without_progress == 45.0
    assert fact.value == 45.0
    assert fact.source_contract_id != previous_fact.source_contract_id
    assert fact is not previous_fact
    _assert_snapshot_identity(snapshot, fact)


def test_bound_run_without_progress_still_produces_only_observer_timing_fact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_now(monkeypatch, NOW)
    snapshot = FileProgressObserver().observe(
        _make_run_context(tmp_path),
        _make_execution_handle(tmp_path),
    )

    facts = _facts_by_code(snapshot)
    assert snapshot.progress_parse_ok is False
    assert set(facts) == {"find.seconds_without_progress"}
    assert facts["find.seconds_without_progress"].value == snapshot.seconds_without_progress
    _assert_snapshot_identity(snapshot, facts["find.seconds_without_progress"])


def test_unbound_run_does_not_fabricate_fact_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_now(monkeypatch, NOW)

    snapshot = FileProgressObserver().observe(
        _make_run_context(tmp_path),
        _make_execution_handle(tmp_path, run_id=None),
    )

    assert snapshot.run_id == ""
    assert snapshot.evidence_facts == []
