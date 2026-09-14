"""Minimal component interfaces for Find Feedback."""

from __future__ import annotations

from typing import Protocol

from .contracts import (
    Anomaly,
    ExperienceCase,
    ExperienceQuery,
    ExecutionHandle,
    FindStageRequest,
    ProgressSnapshot,
    RecoveryDecision,
    RecoveryExperienceQuery,
    RecoveryProposal,
    RunContext,
    SupervisorState,
    ValidationResult,
)


class ExperienceStore(Protocol):
    """Read-only query interface for validated Find experience cases."""

    def search_cases(
        self,
        query: ExperienceQuery,
    ) -> list[ExperienceCase]:
        """Return experience cases matching the supplied query."""
        ...


class RecoveryExperienceStore(Protocol):
    """Read-only lookup for verified Find recovery experience."""

    def search_recovery_cases(
        self,
        query: RecoveryExperienceQuery,
    ) -> list[ExperienceCase]:
        ...


class FeedbackAdapter(Protocol):
    """Build one pre-launch RunContext from a Find request and matched experiences."""

    def adapt(
        self,
        request: FindStageRequest,
        experience_query: ExperienceQuery,
        experiences: list[ExperienceCase],
    ) -> RunContext:
        ...


class Executor(Protocol):
    """Starts one Find process from a prepared RunContext.

    ``execute`` accepts only the prepared RunContext and returns an
    ExecutionHandle whose context_id equals the input context_id. Implementations
    return immediately after successfully starting Find and obtaining a PID; they
    do not wait for completion. If startup fails, they raise RuntimeError instead
    of returning None or a placeholder handle. Initial handles may have run_id and
    run_dir set to None until Find creates and exposes its real run directory.
    """

    def execute(
        self,
        run_context: RunContext,
    ) -> ExecutionHandle:
        """Start Find and return immediately after successful process launch.

        The returned ExecutionHandle must reference the input RunContext through
        the same context_id. If the process cannot be started, the implementation
        raises RuntimeError and does not return a placeholder handle.
        """
        ...


class Observer(Protocol):
    """Produce one read-only point-in-time observation of a Find run.

    Each call returns one snapshot. An Observer does not start, wait for,
    terminate, or retry Find; own a polling loop; or modify its input contracts.
    Concrete implementations will provide file reading and status evaluation.
    """

    def observe(
        self,
        run_context: RunContext,
        execution_handle: ExecutionHandle,
        previous_snapshot: ProgressSnapshot | None = None,
        *,
        cancel_requested: bool = False,
    ) -> ProgressSnapshot:
        ...


class ResultValidator(Protocol):
    """Validate the artifacts of one completed and bound Find run.

    Concrete implementations use the ExecutionHandle to locate the run and
    return a ValidationResult. This interface does not start, stop, retry, or
    modify Find.
    """

    def validate(
        self,
        execution_handle: ExecutionHandle,
    ) -> ValidationResult:
        ...


class AnomalyBuilder(Protocol):
    """Build at most one structured anomaly from observation or validation evidence.

    Each call only organizes supplied evidence and returns one primary anomaly
    or None. Concrete implementations require at least one input and require
    non-empty, matching run IDs when both inputs are supplied. The builder does
    not read files, control processes, recover runs, or modify its inputs.
    """

    def build(
        self,
        *,
        progress_snapshot: ProgressSnapshot | None = None,
        validation_result: ValidationResult | None = None,
    ) -> Anomaly | None:
        ...


class RecoveryController(Protocol):
    """Choose one recovery decision without executing it.

    The FeedbackSupervisor supplies the AnomalyBuilder output, immutable
    RunContext, and current SupervisorState. An implementation may query an
    injected RecoveryExperienceStore for verified recovery experience and may
    call an injected RecoveryAdvisor when no reliable experience applies. It
    must treat every advisor result as an untrusted RecoveryProposal and
    validate it before returning a RecoveryDecision.

    A controller does not execute recovery, does not persist history, does not
    modify its inputs, and does not call Observer, ResultValidator, or
    AnomalyBuilder. It also does not control processes, publish Find results,
    or bypass an approval gate.

    Concrete implementations are responsible for checking run identity,
    allowed actions, remaining budget, and approval requirements. The returned
    decision identifies the input anomaly and run, while NO_ACTION represents
    an explicit decision not to recover.
    """

    def decide(
        self,
        *,
        anomaly: Anomaly,
        run_context: RunContext,
        supervisor_state: SupervisorState,
    ) -> RecoveryDecision:
        ...


class RecoveryApprovalGate(Protocol):
    """Resolve one recovery approval choice without executing the decision."""

    def resolve(
        self,
        *,
        decision: RecoveryDecision,
        approved: bool | None = None,
        approved_by: str | None = None,
        reason: str | None = None,
    ) -> RecoveryDecision:
        ...


class RecoveryAdvisor(Protocol):
    """Produce one untrusted RecoveryProposal for controller validation.

    An advisor does not make the final recovery decision, does not execute
    recovery, does not approve recovery, does not write an experience store,
    and does not modify the Anomaly or other supplied contracts.
    """

    def propose(
        self,
        *,
        anomaly: Anomaly,
        run_context: RunContext,
        supervisor_state: SupervisorState,
        matched_experiences: list[ExperienceCase],
    ) -> RecoveryProposal:
        ...


class ExperienceRecorder(Protocol):
    """Convert one verified recovery outcome into an experience case.

    A recorder organizes the supplied structured contracts and only produces
    an ExperienceCase. It does not query or write an experience store, does not
    call an LLM, does not read artifacts, does not execute recovery, does not
    modify its inputs, and does not reconsider the selected recovery strategy.
    """

    def record(
        self,
        *,
        run_context: RunContext,
        anomaly: Anomaly,
        recovery_decision: RecoveryDecision,
        validation_before: ValidationResult,
        recovery_execution_handle: ExecutionHandle,
        validation_after: ValidationResult,
    ) -> ExperienceCase:
        ...
