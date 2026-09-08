"""Minimal in-memory coordination for Find feedback components."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone

from .contracts import (
    Anomaly,
    ExecutionHandle,
    ProgressSnapshot,
    RecoveryAction,
    RecoveryDecision,
    RunContext,
    SupervisorState,
    SupervisorStatus,
    ValidationResult,
)
from .interfaces import AnomalyBuilder, Observer, RecoveryController, ResultValidator


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class FeedbackSupervisor:
    """Coordinate one observation or terminal-validation call at a time."""

    def __init__(
        self,
        *,
        observer: Observer,
        result_validator: ResultValidator,
        anomaly_builder: AnomalyBuilder,
        recovery_controller: RecoveryController,
        initial_state: SupervisorState,
    ) -> None:
        if not isinstance(initial_state, SupervisorState):
            raise TypeError("initial_state must be a SupervisorState")
        self._observer = observer
        self._result_validator = result_validator
        self._anomaly_builder = anomaly_builder
        self._recovery_controller = recovery_controller
        self._state = deepcopy(initial_state)
        self._previous_snapshot: ProgressSnapshot | None = None
        self._last_validation_result: ValidationResult | None = None
        self._last_anomaly: Anomaly | None = None
        self._last_decision: RecoveryDecision | None = None
        self._handled_anomalies: set[str] = set()

    @property
    def state(self) -> SupervisorState:
        """Return a detached view of the current in-memory state."""
        return deepcopy(self._state)

    def on_monitor_tick(
        self,
        *,
        run_context: RunContext,
        execution_handle: ExecutionHandle,
        cancel_requested: bool = False,
    ) -> RecoveryDecision | None:
        """Observe and classify one running-process tick without deciding recovery."""
        self._validate_common_inputs(run_context, execution_handle)
        if type(cancel_requested) is not bool:
            raise TypeError("cancel_requested must be a bool")

        try:
            snapshot = self._observer.observe(
                deepcopy(run_context),
                deepcopy(execution_handle),
                deepcopy(self._previous_snapshot),
                cancel_requested=cancel_requested,
            )
            if not isinstance(snapshot, ProgressSnapshot):
                raise TypeError("Observer must return a ProgressSnapshot")
        except Exception as error:
            raise RuntimeError("Feedback Supervisor observer call failed") from error

        self._previous_snapshot = deepcopy(snapshot)
        now = _utc_now()
        self._state = replace(
            self._state,
            state_revision=self._state.state_revision + 1,
            updated_at=now,
            heartbeat_at=now,
            cancel_requested=cancel_requested,
            latest_progress_snapshot_id=snapshot.snapshot_id,
        )

        anomaly = self._build_anomaly(progress_snapshot=snapshot)
        self._last_anomaly = deepcopy(anomaly)
        return None

    def on_process_exited(
        self,
        *,
        run_context: RunContext,
        execution_handle: ExecutionHandle,
    ) -> RecoveryDecision | None:
        """Validate one precisely bound terminal run and optionally decide once."""
        self._validate_common_inputs(run_context, execution_handle)
        if execution_handle.process_alive:
            raise ValueError("execution_handle process is still running")
        if execution_handle.exit_code is None:
            raise ValueError("execution_handle exit_code must be set")
        if execution_handle.run_id is None or execution_handle.run_dir is None:
            raise ValueError("execution_handle run_id and run_dir must be bound")

        try:
            validation = self._result_validator.validate(deepcopy(execution_handle))
            if not isinstance(validation, ValidationResult):
                raise TypeError("ResultValidator must return a ValidationResult")
            if validation.run_id != execution_handle.run_id:
                raise ValueError("ValidationResult run_id does not match ExecutionHandle")
        except Exception as error:
            raise RuntimeError("Feedback Supervisor validator call failed") from error

        self._last_validation_result = deepcopy(validation)
        now = _utc_now()
        self._state = replace(
            self._state,
            status=SupervisorStatus.VALIDATING,
            state_revision=self._state.state_revision + 1,
            updated_at=now,
            heartbeat_at=now,
            root_run_id=execution_handle.run_id,
            run_context_id=run_context.context_id,
            active_run_id=execution_handle.run_id,
            latest_validation_id=validation.validation_id,
            process_alive=False,
            active_pid=execution_handle.pid,
            process_started_at=execution_handle.started_at,
            exit_code=execution_handle.exit_code,
        )

        matching_snapshot = self._matching_snapshot(validation)
        anomaly = self._build_anomaly(
            progress_snapshot=matching_snapshot,
            validation_result=validation,
        )
        self._last_anomaly = deepcopy(anomaly)
        if anomaly is None:
            return None

        anomaly_key = self._anomaly_key(anomaly)
        if (
            anomaly_key in self._handled_anomalies
            or self._state.active_recovery_decision_id is not None
        ):
            return None

        deciding_state = replace(
            self._state,
            status=SupervisorStatus.DECIDING,
            state_revision=self._state.state_revision + 1,
            updated_at=_utc_now(),
            active_anomaly_id=anomaly.anomaly_id,
        )
        try:
            decision = self._recovery_controller.decide(
                anomaly=deepcopy(anomaly),
                run_context=deepcopy(run_context),
                supervisor_state=deepcopy(deciding_state),
            )
            if not isinstance(decision, RecoveryDecision):
                raise TypeError("RecoveryController must return a RecoveryDecision")
            if decision.run_id != anomaly.run_id or decision.anomaly_id != anomaly.anomaly_id:
                raise ValueError("RecoveryDecision identity does not match Anomaly")
        except Exception as error:
            raise RuntimeError(
                "Feedback Supervisor recovery controller call failed"
            ) from error

        self._handled_anomalies.add(anomaly_key)
        self._last_decision = deepcopy(decision)
        active_decision_id = (
            None
            if decision.action is RecoveryAction.NO_ACTION
            else decision.decision_id
        )
        self._state = replace(
            deciding_state,
            state_revision=deciding_state.state_revision + 1,
            updated_at=_utc_now(),
            active_recovery_decision_id=active_decision_id,
        )
        return deepcopy(decision)

    @staticmethod
    def _validate_common_inputs(
        run_context: RunContext,
        execution_handle: ExecutionHandle,
    ) -> None:
        if not isinstance(run_context, RunContext):
            raise TypeError("run_context must be a RunContext")
        if not isinstance(execution_handle, ExecutionHandle):
            raise TypeError("execution_handle must be an ExecutionHandle")
        if run_context.context_id != execution_handle.context_id:
            raise ValueError("run_context and execution_handle context_id must match")

    def _build_anomaly(
        self,
        *,
        progress_snapshot: ProgressSnapshot | None = None,
        validation_result: ValidationResult | None = None,
    ) -> Anomaly | None:
        try:
            anomaly = self._anomaly_builder.build(
                progress_snapshot=deepcopy(progress_snapshot),
                validation_result=deepcopy(validation_result),
            )
            if anomaly is not None and not isinstance(anomaly, Anomaly):
                raise TypeError("AnomalyBuilder must return an Anomaly or None")
            return anomaly
        except Exception as error:
            raise RuntimeError(
                "Feedback Supervisor anomaly builder call failed"
            ) from error

    def _matching_snapshot(
        self,
        validation_result: ValidationResult,
    ) -> ProgressSnapshot | None:
        snapshot = self._previous_snapshot
        if (
            snapshot is not None
            and snapshot.run_id
            and validation_result.run_id
            and snapshot.run_id == validation_result.run_id
        ):
            return deepcopy(snapshot)
        return None

    @staticmethod
    def _anomaly_key(anomaly: Anomaly) -> str:
        identity = anomaly.fingerprint or anomaly.anomaly_id
        return f"{anomaly.run_id}:{identity}"
