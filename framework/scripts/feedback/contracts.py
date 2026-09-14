"""Stable enums and JSON conversion primitives for Find Feedback contracts."""

from __future__ import annotations

from collections.abc import Mapping as MappingABC
from copy import deepcopy
from dataclasses import MISSING, dataclass, field, fields, is_dataclass
from datetime import datetime
from enum import Enum
import json
from math import isfinite
from types import UnionType
from typing import Mapping, TypeVar, Union, get_args, get_origin, get_type_hints


ContractType = TypeVar("ContractType", bound="JsonContract")


def _error(contract_name: str, field_path: str, message: str) -> ValueError:
    path = f"{contract_name}.{field_path}" if field_path else contract_name
    return ValueError(f"{path} {message}")


def _child_path(parent: str, child: str) -> str:
    return f"{parent}.{child}" if parent else child


def _require_aware_datetime(value: datetime, *, contract_name: str, field_path: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise _error(contract_name, field_path, "must be a timezone-aware datetime")
    return value


def _to_json_value(value: object, *, contract_name: str, field_path: str) -> object:
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise _error(contract_name, field_path, "must be a finite JSON number")
        return value
    if isinstance(value, datetime):
        return _require_aware_datetime(value, contract_name=contract_name, field_path=field_path).isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: _to_json_value(
                getattr(value, item.name),
                contract_name=contract_name,
                field_path=_child_path(field_path, item.name),
            )
            for item in fields(value)
        }
    if isinstance(value, (list, tuple)):
        return [
            _to_json_value(item, contract_name=contract_name, field_path=f"{field_path}[{index}]")
            for index, item in enumerate(value)
        ]
    if isinstance(value, MappingABC):
        result: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise _error(contract_name, field_path, "must use string keys")
            result[key] = _to_json_value(
                item,
                contract_name=contract_name,
                field_path=_child_path(field_path, key),
            )
        return result
    raise _error(contract_name, field_path, f"is not JSON-compatible ({type(value).__name__})")


def _from_json_value(
    value: object,
    expected_type: object,
    *,
    contract_name: str,
    field_path: str,
) -> object:
    if expected_type is object:
        return _to_json_value(value, contract_name=contract_name, field_path=field_path)

    origin = get_origin(expected_type)
    arguments = get_args(expected_type)
    if origin in (Union, UnionType):
        if value is None and type(None) in arguments:
            return None
        for candidate in arguments:
            if candidate is type(None):
                continue
            try:
                return _from_json_value(
                    value,
                    candidate,
                    contract_name=contract_name,
                    field_path=field_path,
                )
            except (TypeError, ValueError):
                continue
        raise _error(contract_name, field_path, "does not match an allowed type")

    if origin is list:
        if not isinstance(value, list):
            raise _error(contract_name, field_path, "must be a list")
        item_type = arguments[0] if arguments else object
        return [
            _from_json_value(
                item,
                item_type,
                contract_name=contract_name,
                field_path=f"{field_path}[{index}]",
            )
            for index, item in enumerate(value)
        ]

    if origin in (dict, Mapping, MappingABC):
        if not isinstance(value, MappingABC):
            raise _error(contract_name, field_path, "must be an object")
        key_type, item_type = arguments if len(arguments) == 2 else (str, object)
        if key_type is not str:
            raise _error(contract_name, field_path, "must use string keys")
        result: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise _error(contract_name, field_path, "must use string keys")
            result[key] = _from_json_value(
                item,
                item_type,
                contract_name=contract_name,
                field_path=_child_path(field_path, key),
            )
        return result

    if expected_type is datetime:
        if not isinstance(value, str):
            raise _error(contract_name, field_path, "must be an ISO 8601 datetime string")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise _error(contract_name, field_path, "must be an ISO 8601 datetime string") from exc
        return _require_aware_datetime(parsed, contract_name=contract_name, field_path=field_path)

    if isinstance(expected_type, type) and issubclass(expected_type, Enum):
        try:
            return expected_type(value)
        except ValueError as exc:
            raise _error(contract_name, field_path, f"has invalid value {value!r}") from exc

    if isinstance(expected_type, type) and is_dataclass(expected_type):
        if not isinstance(value, MappingABC):
            raise _error(contract_name, field_path, "must be an object")
        return _from_dataclass(
            expected_type,
            value,
            contract_name=contract_name,
            field_path=field_path,
        )

    if expected_type in (str, int, float, bool):
        if type(value) is not expected_type:
            raise _error(contract_name, field_path, f"must be a {expected_type.__name__}")
        return value

    raise _error(contract_name, field_path, f"uses unsupported type {expected_type!r}")


def _from_dataclass(
    contract_type: type[object],
    data: Mapping[str, object],
    *,
    contract_name: str,
    field_path: str = "",
) -> object:
    contract_fields = {item.name: item for item in fields(contract_type)}
    for name in data:
        if not isinstance(name, str) or name not in contract_fields:
            raise _error(contract_name, _child_path(field_path, str(name)), "is unknown")
    for item in contract_fields.values():
        if item.name not in data and item.default is MISSING and item.default_factory is MISSING:
            raise _error(contract_name, _child_path(field_path, item.name), "is required")

    hints = get_type_hints(contract_type)
    values: dict[str, object] = {}
    for name, item in contract_fields.items():
        if name not in data:
            continue
        values[name] = _from_json_value(
            data[name],
            hints.get(name, item.type),
            contract_name=contract_name,
            field_path=_child_path(field_path, name),
        )
    try:
        return contract_type(**values)
    except TypeError as exc:
        raise _error(contract_name, field_path, str(exc)) from exc


class JsonContract:
    """Small conversion base for future feedback dataclasses."""

    def to_dict(self) -> dict[str, object]:
        if not is_dataclass(self):
            raise TypeError(f"{type(self).__name__} must be a dataclass")
        contract_name = type(self).__name__
        return {
            item.name: _to_json_value(
                getattr(self, item.name),
                contract_name=contract_name,
                field_path=item.name,
            )
            for item in fields(self)
        }

    @classmethod
    def from_dict(cls: type[ContractType], data: Mapping[str, object]) -> ContractType:
        if not is_dataclass(cls):
            raise TypeError(f"{cls.__name__} must be a dataclass")
        if not isinstance(data, MappingABC):
            raise _error(cls.__name__, "", "must be an object")
        return _from_dataclass(cls, data, contract_name=cls.__name__)  # type: ignore[return-value]

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    @classmethod
    def from_json(cls: type[ContractType], payload: str) -> ContractType:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise _error(cls.__name__, "", "contains invalid JSON") from exc
        return cls.from_dict(data)


class ProgressStatus(str, Enum):
    STARTING = "starting"
    RUNNING = "running"
    SUSPECTED_STALL = "suspected_stall"
    STALLED = "stalled"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class ValidationStatus(str, Enum):
    PASS = "pass"
    WARNING = "warning"
    BLOCK = "block"


class RecoveryAction(str, Enum):
    NO_ACTION = "no_action"
    RETRY_NEW_RUN = "retry_new_run"
    RETRY_WITH_PARAMETER_CHANGE = "retry_with_parameter_change"
    SKIP_OPTIONAL_SOURCE = "skip_optional_source"
    REQUEST_APPROVAL = "request_approval"
    STOP_AND_REPORT = "stop_and_report"


class SupervisorStatus(str, Enum):
    IDLE = "idle"
    PREPARING = "preparing"
    RUNNING = "running"
    VALIDATING = "validating"
    DIAGNOSING = "diagnosing"
    DECIDING = "deciding"
    AWAITING_APPROVAL = "awaiting_approval"
    RECOVERING = "recovering"
    GATING = "gating"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SupervisorEventType(str, Enum):
    START = "start"
    PREPARED = "prepared"
    PREPARE_FAILED = "prepare_failed"
    MONITOR_TICK = "monitor_tick"
    PROCESS_EXITED = "process_exited"
    STALL_DETECTED = "stall_detected"
    VALIDATION_PASSED = "validation_passed"
    VALIDATION_BLOCKED = "validation_blocked"
    ANOMALY_DETECTED = "anomaly_detected"
    ANOMALY_READY = "anomaly_ready"
    DIAGNOSIS_FAILED = "diagnosis_failed"
    RETRY_DECIDED = "retry_decided"
    APPROVAL_REQUIRED = "approval_required"
    STOP_DECIDED = "stop_decided"
    APPROVED = "approved"
    REJECTED = "rejected"
    NEW_RUN_STARTED = "new_run_started"
    RECOVERY_FAILED = "recovery_failed"
    GATE_ALLOWED = "gate_allowed"
    GATE_BLOCKED_RECOVERABLE = "gate_blocked_recoverable"
    GATE_BLOCKED_FINAL = "gate_blocked_final"
    CANCEL_REQUESTED = "cancel_requested"
    FATAL_ERROR = "fatal_error"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


def _require_string(value: object, *, contract_name: str, field_path: str) -> str:
    if not isinstance(value, str):
        raise _error(contract_name, field_path, "must be a string")
    return value


def _optional_string(value: object | None, *, contract_name: str, field_path: str) -> str | None:
    if value is None:
        return None
    return _require_string(value, contract_name=contract_name, field_path=field_path)


def _require_bool(value: object, *, contract_name: str, field_path: str) -> bool:
    if not isinstance(value, bool):
        raise _error(contract_name, field_path, "must be a boolean")
    return value


def _copy_json_compatible(
    value: object | None,
    *,
    contract_name: str,
    field_path: str,
) -> object | None:
    _to_json_value(value, contract_name=contract_name, field_path=field_path)
    if isinstance(value, MappingABC):
        return dict(value)
    return value


def _contains_secret(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in ("sk-", "sk_", "bearer ", "api_key=", "apikey="))


@dataclass
class ArtifactRef(JsonContract):
    """A file artifact observed or expected during a feedback run."""

    role: str
    path: str
    required: bool
    exists: bool | None = None
    size_bytes: int | None = None
    modified_at: datetime | None = None
    sha256: str | None = None
    parse_status: str | None = None

    def __post_init__(self) -> None:
        contract_name = type(self).__name__
        _require_string(self.role, contract_name=contract_name, field_path="role")
        _require_string(self.path, contract_name=contract_name, field_path="path")
        _require_bool(self.required, contract_name=contract_name, field_path="required")
        if self.exists is not None:
            _require_bool(self.exists, contract_name=contract_name, field_path="exists")
        if self.size_bytes is not None:
            if isinstance(self.size_bytes, bool) or not isinstance(self.size_bytes, int):
                raise _error(contract_name, "size_bytes", "must be an integer or null")
            if self.size_bytes < 0:
                raise _error(contract_name, "size_bytes", "must be greater than or equal to 0")
        if self.modified_at is not None:
            _require_aware_datetime(
                self.modified_at,
                contract_name=contract_name,
                field_path="modified_at",
            )
        _optional_string(self.sha256, contract_name=contract_name, field_path="sha256")
        _optional_string(self.parse_status, contract_name=contract_name, field_path="parse_status")


@dataclass
class EvidenceRef(JsonContract):
    """A compact reference to evidence associated with a contract outcome.

    ``kind`` classifies the reference itself, such as log, artifact, or
    validation evidence. It is not a machine-readable observed fact code.
    """

    kind: str
    summary: str
    path: str | None = None
    contract_id: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    sha256: str | None = None

    def __post_init__(self) -> None:
        contract_name = type(self).__name__
        _require_string(self.kind, contract_name=contract_name, field_path="kind")
        _require_string(self.summary, contract_name=contract_name, field_path="summary")
        if _contains_secret(self.summary):
            raise _error(contract_name, "summary", "must not contain a secret")
        _optional_string(self.path, contract_name=contract_name, field_path="path")
        _optional_string(self.contract_id, contract_name=contract_name, field_path="contract_id")
        _optional_string(self.sha256, contract_name=contract_name, field_path="sha256")
        if self.line_start is not None:
            if isinstance(self.line_start, bool) or not isinstance(self.line_start, int):
                raise _error(contract_name, "line_start", "must be an integer or null")
            if self.line_start <= 0:
                raise _error(contract_name, "line_start", "must be greater than 0")
        if self.line_end is not None:
            if isinstance(self.line_end, bool) or not isinstance(self.line_end, int):
                raise _error(contract_name, "line_end", "must be an integer or null")
            if self.line_start is not None and self.line_end < self.line_start:
                raise _error(contract_name, "line_end", "must be greater than or equal to line_start")


@dataclass
class ExperienceRef(JsonContract):
    """A reference to one reusable feedback experience case."""

    case_id: str
    verified: bool
    case_type: str
    similarity: float | None = None
    summary: str | None = None

    def __post_init__(self) -> None:
        contract_name = type(self).__name__
        _require_string(self.case_id, contract_name=contract_name, field_path="case_id")
        _require_bool(self.verified, contract_name=contract_name, field_path="verified")
        _require_string(self.case_type, contract_name=contract_name, field_path="case_type")
        if self.case_type not in {"normal", "technical", "preference"}:
            raise _error(contract_name, "case_type", "must be one of normal, technical, preference")
        if self.similarity is not None:
            if isinstance(self.similarity, bool) or not isinstance(self.similarity, (int, float)):
                raise _error(contract_name, "similarity", "must be a number or null")
            if not isfinite(float(self.similarity)) or not 0 <= self.similarity <= 1:
                raise _error(contract_name, "similarity", "must be between 0 and 1")
        _optional_string(self.summary, contract_name=contract_name, field_path="summary")


@dataclass
class ParameterChange(JsonContract):
    """A single proposed or applied JSON-compatible parameter change."""

    name: str
    after: object
    reason: str
    before: object | None = None
    source_case_id: str | None = None

    def __post_init__(self) -> None:
        contract_name = type(self).__name__
        _require_string(self.name, contract_name=contract_name, field_path="name")
        _require_string(self.reason, contract_name=contract_name, field_path="reason")
        _optional_string(self.source_case_id, contract_name=contract_name, field_path="source_case_id")
        self.before = _copy_json_compatible(
            self.before,
            contract_name=contract_name,
            field_path="before",
        )
        self.after = _copy_json_compatible(
            self.after,
            contract_name=contract_name,
            field_path="after",
        )


@dataclass
class ValidationCheck(JsonContract):
    """One deterministic validation result and its supporting evidence.

    ``code`` identifies one validation check. It is distinct from the code of
    a reusable observed fact.
    """

    code: str
    status: ValidationStatus
    required: bool
    message: str
    expected: object | None = None
    actual: object | None = None
    evidence_refs: list[EvidenceRef] = field(default_factory=list)

    def __post_init__(self) -> None:
        contract_name = type(self).__name__
        _require_string(self.code, contract_name=contract_name, field_path="code")
        if not isinstance(self.status, ValidationStatus):
            raise _error(contract_name, "status", "must be a ValidationStatus")
        _require_bool(self.required, contract_name=contract_name, field_path="required")
        _require_string(self.message, contract_name=contract_name, field_path="message")
        self.expected = _copy_json_compatible(
            self.expected,
            contract_name=contract_name,
            field_path="expected",
        )
        self.actual = _copy_json_compatible(
            self.actual,
            contract_name=contract_name,
            field_path="actual",
        )
        if not isinstance(self.evidence_refs, list):
            raise _error(contract_name, "evidence_refs", "must be a list")
        self.evidence_refs = list(self.evidence_refs)
        for index, evidence in enumerate(self.evidence_refs):
            if not isinstance(evidence, EvidenceRef):
                raise _error(contract_name, f"evidence_refs[{index}]", "must be an EvidenceRef")


@dataclass
class SupervisorEvent(JsonContract):
    """One ordered event emitted by the feedback supervisor."""

    sequence: int
    occurred_at: datetime
    event_type: SupervisorEventType
    message: str
    contract_id: str | None = None

    def __post_init__(self) -> None:
        contract_name = type(self).__name__
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int):
            raise _error(contract_name, "sequence", "must be an integer")
        if self.sequence < 0:
            raise _error(contract_name, "sequence", "must be greater than or equal to 0")
        _require_aware_datetime(
            self.occurred_at,
            contract_name=contract_name,
            field_path="occurred_at",
        )
        if not isinstance(self.event_type, SupervisorEventType):
            raise _error(contract_name, "event_type", "must be a SupervisorEventType")
        _require_string(self.message, contract_name=contract_name, field_path="message")
        _optional_string(self.contract_id, contract_name=contract_name, field_path="contract_id")


def _require_mapping(
    value: object,
    *,
    contract_name: str,
    field_path: str,
) -> dict[str, object]:
    if not isinstance(value, MappingABC):
        raise _error(contract_name, field_path, "must be a mapping")
    copied = deepcopy(dict(value))
    _to_json_value(copied, contract_name=contract_name, field_path=field_path)
    return copied


def _require_list(
    value: object,
    *,
    contract_name: str,
    field_path: str,
) -> list[object]:
    if not isinstance(value, list):
        raise _error(contract_name, field_path, "must be a list")
    return list(value)


def _require_non_empty_string(
    value: object,
    *,
    contract_name: str,
    field_path: str,
) -> str:
    string_value = _require_string(value, contract_name=contract_name, field_path=field_path)
    if not string_value.strip():
        raise _error(contract_name, field_path, "must not be empty")
    return string_value


def _reject_secret_in_value(
    value: object,
    *,
    contract_name: str,
    field_path: str,
) -> None:
    if isinstance(value, str):
        if _contains_secret(value):
            raise _error(contract_name, field_path, "must not contain a secret")
        return
    if isinstance(value, MappingABC):
        for key, nested_value in value.items():
            key_path = _child_path(field_path, str(key))
            if isinstance(key, str) and key.lower() in {
                "api_key",
                "apikey",
                "authorization",
                "access_token",
                "token",
            }:
                raise _error(contract_name, key_path, "must not contain a secret")
            _reject_secret_in_value(
                nested_value,
                contract_name=contract_name,
                field_path=key_path,
            )
        return
    if isinstance(value, (list, tuple)):
        for index, nested_value in enumerate(value):
            _reject_secret_in_value(
                nested_value,
                contract_name=contract_name,
                field_path=f"{field_path}[{index}]",
            )


@dataclass(kw_only=True)
class EvidenceFact(JsonContract):
    """One machine-readable fact actually observed by a feedback producer.

    ``code`` identifies one reusable observed fact: what was observed, not why
    the anomaly occurred. It is not an Anomaly kind, EvidenceRef kind,
    ValidationCheck code, or root-cause code.
    """

    code: str
    value: object
    source_contract_id: str
    source_field: str
    producer: str
    run_id: str
    observed_at: datetime
    schema_version: str = "feedback.evidence_fact.v1"

    def __post_init__(self) -> None:
        contract_name = type(self).__name__
        for field_name in (
            "code",
            "source_contract_id",
            "source_field",
            "producer",
            "run_id",
        ):
            _require_non_empty_string(
                getattr(self, field_name),
                contract_name=contract_name,
                field_path=field_name,
            )
        if self.value is None:
            raise _error(contract_name, "value", "must not be null")
        self.value = _to_json_value(
            self.value,
            contract_name=contract_name,
            field_path="value",
        )
        _reject_secret_in_value(
            self.value,
            contract_name=contract_name,
            field_path="value",
        )
        if not isinstance(self.observed_at, datetime):
            raise _error(
                contract_name,
                "observed_at",
                "must be a timezone-aware datetime",
            )
        _require_aware_datetime(
            self.observed_at,
            contract_name=contract_name,
            field_path="observed_at",
        )
        if self.schema_version != "feedback.evidence_fact.v1":
            raise _error(
                contract_name,
                "schema_version",
                "must equal feedback.evidence_fact.v1",
            )


def _normalize_evidence_facts(
    value: object,
    *,
    contract_name: str,
    field_path: str = "evidence_facts",
) -> list[EvidenceFact]:
    items = _require_list(
        value,
        contract_name=contract_name,
        field_path=field_path,
    )
    normalized: list[EvidenceFact] = []
    first_by_identity: dict[tuple[str, str, str], EvidenceFact] = {}
    for index, item in enumerate(items):
        item_path = f"{field_path}[{index}]"
        if not isinstance(item, EvidenceFact):
            raise _error(contract_name, item_path, "must be an EvidenceFact")
        copied = deepcopy(item)
        identity = (
            copied.code,
            copied.source_contract_id,
            copied.source_field,
        )
        first = first_by_identity.get(identity)
        if first is not None:
            if copied != first:
                raise _error(
                    contract_name,
                    item_path,
                    "conflicts with an earlier EvidenceFact of the same identity",
                )
            continue
        first_by_identity[identity] = copied
        normalized.append(copied)
    return normalized


def _migrate_runtime_evidence_carrier_payload(
    data: Mapping[str, object],
    *,
    contract_name: str,
    v1_schema: str,
    v2_schema: str,
) -> Mapping[str, object]:
    if not isinstance(data, MappingABC):
        raise _error(contract_name, "", "must be an object")
    schema_version = data.get("schema_version")
    if schema_version == v1_schema:
        if "evidence_facts" in data:
            raise _error(contract_name, "evidence_facts", "is unknown")
        migrated = dict(data)
        migrated["schema_version"] = v2_schema
        migrated["evidence_facts"] = []
        return migrated
    if schema_version not in {None, v2_schema}:
        raise _error(
            contract_name,
            "schema_version",
            f"must equal {v1_schema} or {v2_schema}",
        )
    return data


@dataclass(kw_only=True)
class EvidenceDefinition(JsonContract):
    """Define one evidence value that a producer may observe in a future run.

    A definition describes what should be observed. It never contains an
    observed value and is not itself evidence.
    """

    code: str
    value_type: str
    intended_producer: str
    description: str
    schema_version: str = "feedback.evidence_definition.v1"

    def __post_init__(self) -> None:
        contract_name = type(self).__name__
        for field_name in (
            "code",
            "value_type",
            "intended_producer",
            "description",
        ):
            _require_non_empty_string(
                getattr(self, field_name),
                contract_name=contract_name,
                field_path=field_name,
            )
        if self.value_type not in {
            "boolean",
            "integer",
            "number",
            "string",
            "array",
            "object",
        }:
            raise _error(
                contract_name,
                "value_type",
                "must be one of boolean, integer, number, string, array, object",
            )
        _reject_secret_in_value(
            self.description,
            contract_name=contract_name,
            field_path="description",
        )
        if self.schema_version != "feedback.evidence_definition.v1":
            raise _error(
                contract_name,
                "schema_version",
                "must equal feedback.evidence_definition.v1",
            )


@dataclass(kw_only=True)
class EvidenceCollectionRule(JsonContract):
    """Describe an inert proposal for collecting one defined evidence value.

    The contract validates data shape only. It does not authorize or execute
    the collector.
    """

    rule_id: str
    evidence_code: str
    source_field: str
    collector: str
    collector_parameters: Mapping[str, object]
    implementation_status: str
    schema_version: str = "feedback.evidence_collection_rule.v1"

    def __post_init__(self) -> None:
        contract_name = type(self).__name__
        for field_name in (
            "rule_id",
            "evidence_code",
            "source_field",
            "collector",
            "implementation_status",
        ):
            _require_non_empty_string(
                getattr(self, field_name),
                contract_name=contract_name,
                field_path=field_name,
            )
        if self.implementation_status not in {
            "ready",
            "needs_instrumentation",
            "unsupported",
        }:
            raise _error(
                contract_name,
                "implementation_status",
                "must be one of ready, needs_instrumentation, unsupported",
            )
        copied_parameters = _require_mapping(
            self.collector_parameters,
            contract_name=contract_name,
            field_path="collector_parameters",
        )
        self.collector_parameters = _to_json_value(
            copied_parameters,
            contract_name=contract_name,
            field_path="collector_parameters",
        )  # type: ignore[assignment]
        _reject_secret_in_value(
            self.collector_parameters,
            contract_name=contract_name,
            field_path="collector_parameters",
        )
        if self.schema_version != "feedback.evidence_collection_rule.v1":
            raise _error(
                contract_name,
                "schema_version",
                "must equal feedback.evidence_collection_rule.v1",
            )


@dataclass(kw_only=True)
class EvidenceMatchCondition(JsonContract):
    """Describe one inert comparison used by future experience matching.

    The contract validates condition data only. It does not evaluate evidence,
    authorize recovery, or execute a matcher.
    """

    evidence_code: str
    operator: str
    baseline_source: str
    baseline_ref: str | None
    baseline_value: object | None
    schema_version: str = "feedback.evidence_match_condition.v1"

    def __post_init__(self) -> None:
        contract_name = type(self).__name__
        for field_name in ("evidence_code", "operator", "baseline_source"):
            _require_non_empty_string(
                getattr(self, field_name),
                contract_name=contract_name,
                field_path=field_name,
            )
        if self.operator not in {"eq", "gte", "lte"}:
            raise _error(
                contract_name,
                "operator",
                "must be one of eq, gte, lte",
            )
        if self.baseline_source not in {"literal", "run_context", "fact"}:
            raise _error(
                contract_name,
                "baseline_source",
                "must be one of literal, run_context, fact",
            )
        if self.schema_version != "feedback.evidence_match_condition.v1":
            raise _error(
                contract_name,
                "schema_version",
                "must equal feedback.evidence_match_condition.v1",
            )

        self.baseline_ref = _optional_string(
            self.baseline_ref,
            contract_name=contract_name,
            field_path="baseline_ref",
        )
        if self.baseline_source == "literal":
            if self.baseline_ref is not None:
                raise _error(
                    contract_name,
                    "baseline_ref",
                    "must be null when baseline_source is literal",
                )
            if self.baseline_value is None:
                raise _error(
                    contract_name,
                    "baseline_value",
                    "must not be null when baseline_source is literal",
                )
            self.baseline_value = _to_json_value(
                self.baseline_value,
                contract_name=contract_name,
                field_path="baseline_value",
            )
            _reject_secret_in_value(
                self.baseline_value,
                contract_name=contract_name,
                field_path="baseline_value",
            )
            if self.operator in {"gte", "lte"} and (
                isinstance(self.baseline_value, bool)
                or not isinstance(self.baseline_value, (int, float))
            ):
                raise _error(
                    contract_name,
                    "baseline_value",
                    "must be a finite non-boolean number for gte or lte",
                )
        else:
            _require_non_empty_string(
                self.baseline_ref,
                contract_name=contract_name,
                field_path="baseline_ref",
            )
            if self.baseline_value is not None:
                raise _error(
                    contract_name,
                    "baseline_value",
                    "must be null when baseline_source is run_context or fact",
                )


@dataclass(kw_only=True)
class FindStageRequest(JsonContract):
    """Standardized Framework request for the Find Feedback pre-stage chain.

    It is created before experience lookup and parameter adjustment. It contains only
    raw inputs needed to query experience, adjust parameters, and start Find;
    ``project_id`` is optional Framework metadata. This contract contains neither
    Adapter results, Web job IDs, nor actual Find execution facts or outputs.
    """

    request_source: str
    research_topic: str
    selection: Mapping[str, object]
    config_path: str
    requested_parameters: Mapping[str, object]
    working_directory: str
    schema_version: str = "find.stage_request.v1"
    stage: str = "find"
    project_id: str | None = None
    force_new_find: bool = False
    restart_full_cycle: bool = False
    human_approved_new_find: bool = False
    approval_reason: str | None = None

    def __post_init__(self) -> None:
        contract_name = type(self).__name__
        for field_name in (
            "request_source",
            "research_topic",
            "config_path",
            "working_directory",
        ):
            _require_non_empty_string(
                getattr(self, field_name),
                contract_name=contract_name,
                field_path=field_name,
            )
        for field_name in ("project_id", "approval_reason"):
            value = getattr(self, field_name)
            if value is not None:
                _require_non_empty_string(
                    value,
                    contract_name=contract_name,
                    field_path=field_name,
                )

        if self.schema_version != "find.stage_request.v1":
            raise _error(contract_name, "schema_version", "must equal find.stage_request.v1")
        if self.stage != "find":
            raise _error(contract_name, "stage", "must equal find")
        if self.request_source not in {"web", "cli", "full_cycle"}:
            raise _error(contract_name, "request_source", "must be one of web, cli, full_cycle")
        for field_name in (
            "force_new_find",
            "restart_full_cycle",
            "human_approved_new_find",
        ):
            _require_bool(
                getattr(self, field_name),
                contract_name=contract_name,
                field_path=field_name,
            )

        self.selection = _require_mapping(
            self.selection,
            contract_name=contract_name,
            field_path="selection",
        )
        self.requested_parameters = _require_mapping(
            self.requested_parameters,
            contract_name=contract_name,
            field_path="requested_parameters",
        )
        _reject_secret_in_value(
            self.selection,
            contract_name=contract_name,
            field_path="selection",
        )
        _reject_secret_in_value(
            self.requested_parameters,
            contract_name=contract_name,
            field_path="requested_parameters",
        )


@dataclass(kw_only=True)
class ExperienceQuery(JsonContract):
    """Fixed before-stage filters passed to a future Experience Store.

    This contract describes a query but does not perform one. Empty ``case_types``
    and ``outcomes`` disable their respective filters, while ``project_id=None``
    disables project filtering. Every ``required_context_tags`` value must be present
    in a matching case's context tags. It contains no run identity, fingerprints,
    semantic similarity inputs, or Recovery-specific conditions.
    """

    limit: int
    schema_version: str = "find.experience_query.v1"
    stage: str = "find"
    project_id: str | None = None
    case_types: list[str] = field(default_factory=list)
    outcomes: list[str] = field(default_factory=list)
    required_context_tags: list[str] = field(default_factory=list)
    verified_only: bool = True
    include_deprecated: bool = False

    def __post_init__(self) -> None:
        contract_name = type(self).__name__
        if self.schema_version != "find.experience_query.v1":
            raise _error(contract_name, "schema_version", "must equal find.experience_query.v1")
        if self.stage != "find":
            raise _error(contract_name, "stage", "must equal find")
        if isinstance(self.limit, bool) or not isinstance(self.limit, int):
            raise _error(contract_name, "limit", "must be an integer")
        if not 1 <= self.limit <= 100:
            raise _error(contract_name, "limit", "must be between 1 and 100")
        if self.project_id is not None:
            _require_non_empty_string(
                self.project_id,
                contract_name=contract_name,
                field_path="project_id",
            )
        self.case_types = _copy_string_list(
            self.case_types,
            contract_name=contract_name,
            field_path="case_types",
        )
        for index, case_type in enumerate(self.case_types):
            if case_type not in {"normal", "technical", "preference"}:
                raise _error(
                    contract_name,
                    f"case_types[{index}]",
                    "must be one of normal, technical, preference",
                )
        self.outcomes = _copy_string_list(
            self.outcomes,
            contract_name=contract_name,
            field_path="outcomes",
        )
        for index, outcome in enumerate(self.outcomes):
            if outcome not in {"success", "recovered", "failed", "partial", "cancelled"}:
                raise _error(
                    contract_name,
                    f"outcomes[{index}]",
                    "must be one of success, recovered, failed, partial, cancelled",
                )
        self.required_context_tags = _copy_string_list(
            self.required_context_tags,
            contract_name=contract_name,
            field_path="required_context_tags",
        )
        for index, context_tag in enumerate(self.required_context_tags):
            _require_non_empty_string(
                context_tag,
                contract_name=contract_name,
                field_path=f"required_context_tags[{index}]",
            )
        _require_bool(self.verified_only, contract_name=contract_name, field_path="verified_only")
        _require_bool(
            self.include_deprecated,
            contract_name=contract_name,
            field_path="include_deprecated",
        )


@dataclass(kw_only=True)
class RecoveryExperienceQuery(JsonContract):
    """Fixed filters for future recovery-stage experience lookup.

    ``anomaly_kind`` identifies the structured Find anomaly to match. Context
    tags are supplied by a future Recovery Query Builder from existing run
    context; this contract validates and stores them but does not infer them.
    Recovery queries are restricted to verified, non-deprecated cases.
    """

    anomaly_kind: str
    limit: int
    schema_version: str = "find.recovery_experience_query.v1"
    stage: str = "find"
    project_id: str | None = None
    environment_fingerprint: str | None = None
    required_context_tags: list[str] = field(default_factory=list)
    verified_only: bool = True
    include_deprecated: bool = False

    def __post_init__(self) -> None:
        contract_name = type(self).__name__
        if self.schema_version != "find.recovery_experience_query.v1":
            raise _error(
                contract_name,
                "schema_version",
                "must equal find.recovery_experience_query.v1",
            )
        if self.stage != "find":
            raise _error(contract_name, "stage", "must equal find")

        _require_non_empty_string(
            self.anomaly_kind,
            contract_name=contract_name,
            field_path="anomaly_kind",
        )
        if self.anomaly_kind not in SUPPORTED_FIND_ANOMALY_KINDS:
            raise _error(
                contract_name,
                "anomaly_kind",
                "is not a supported Find anomaly kind",
            )
        if isinstance(self.limit, bool) or not isinstance(self.limit, int):
            raise _error(contract_name, "limit", "must be an integer")
        if not 1 <= self.limit <= 100:
            raise _error(contract_name, "limit", "must be between 1 and 100")

        for field_name in ("project_id", "environment_fingerprint"):
            value = getattr(self, field_name)
            if value is not None:
                _require_non_empty_string(
                    value,
                    contract_name=contract_name,
                    field_path=field_name,
                )

        self.required_context_tags = _copy_string_list(
            self.required_context_tags,
            contract_name=contract_name,
            field_path="required_context_tags",
        )
        for index, context_tag in enumerate(self.required_context_tags):
            _require_non_empty_string(
                context_tag,
                contract_name=contract_name,
                field_path=f"required_context_tags[{index}]",
            )

        _require_bool(
            self.verified_only,
            contract_name=contract_name,
            field_path="verified_only",
        )
        if self.verified_only is not True:
            raise _error(contract_name, "verified_only", "must be true")
        _require_bool(
            self.include_deprecated,
            contract_name=contract_name,
            field_path="include_deprecated",
        )
        if self.include_deprecated is not False:
            raise _error(contract_name, "include_deprecated", "must be false")


@dataclass(kw_only=True)
class RunContext(JsonContract):
    """Immutable-by-convention Find launch plan captured before execution starts.

    Real run IDs, run directories, process facts, actual output locations, and final
    Find results are generated only after the Executor starts Find.

    ``config_snapshot_path`` is the redacted ``find.config.json`` passed to
    ``--config-json`` and carries Find configuration plus source selection.
    ``input_snapshot_path`` is the ``input.json`` passed to ``--input-json`` and
    carries research topic, interest, profile, and arXiv queries.
    ``selection_snapshot_path`` is a separate selection record for audit; it is
    neither ``input_snapshot_path`` nor a ``--input-json`` argument.
    ``command_redacted`` is the pre-launch, redacted command preview only.
    """

    context_id: str
    attempt_index: int
    project_id: str | None
    request_source: str
    created_at: datetime
    producer: str
    producer_version: str
    research_topic: str
    selection_snapshot_path: str
    selection: Mapping[str, object]
    command_redacted: list[str]
    working_directory: str
    python_executable: str
    config_snapshot_path: str
    input_snapshot_path: str
    requested_parameters: Mapping[str, object]
    effective_parameters: Mapping[str, object]
    expected_artifacts: list[ArtifactRef]
    startup_grace_seconds: int
    stall_suspect_seconds: int
    stall_confirm_seconds: int
    recovery_budget: int
    allowed_recovery_actions: list[RecoveryAction]
    approval_risk_threshold: RiskLevel
    validation_policy_version: str
    experience_query: ExperienceQuery
    external_costs_authorized: bool = False
    skippable_sources: list[str] = field(default_factory=list)
    schema_version: str = "find.run_context.v1"
    stage: str = "find"
    researcher_profile_path: str | None = None
    researcher_profile_fingerprint: str | None = None
    entrypoint: str = "modules/finding/main.py"
    action: str = "find"
    conda_env: str | None = None
    model_id: str | None = None
    environment_fingerprint: str | None = None
    parameter_changes: list[ParameterChange] = field(default_factory=list)
    matched_experience_refs: list[ExperienceRef] = field(default_factory=list)
    applied_experience_refs: list[ExperienceRef] = field(default_factory=list)
    experience_parameter_changes: list[ParameterChange] = field(default_factory=list)

    def __post_init__(self) -> None:
        contract_name = type(self).__name__
        for field_name in (
            "context_id",
            "request_source",
            "producer",
            "producer_version",
            "research_topic",
            "selection_snapshot_path",
            "working_directory",
            "python_executable",
            "config_snapshot_path",
            "input_snapshot_path",
            "validation_policy_version",
        ):
            _require_non_empty_string(
                getattr(self, field_name),
                contract_name=contract_name,
                field_path=field_name,
            )
        if self.project_id is not None:
            _require_non_empty_string(
                self.project_id,
                contract_name=contract_name,
                field_path="project_id",
            )

        for field_name in (
            "researcher_profile_path",
            "researcher_profile_fingerprint",
            "conda_env",
            "model_id",
            "environment_fingerprint",
        ):
            _optional_string(getattr(self, field_name), contract_name=contract_name, field_path=field_name)

        if not isinstance(self.experience_query, ExperienceQuery):
            raise _error(
                contract_name,
                "experience_query",
                "must be an ExperienceQuery",
            )

        if self.schema_version != "find.run_context.v1":
            raise _error(contract_name, "schema_version", "must equal find.run_context.v1")
        if self.stage != "find":
            raise _error(contract_name, "stage", "must equal find")
        if self.entrypoint != "modules/finding/main.py":
            raise _error(contract_name, "entrypoint", "must equal modules/finding/main.py")
        if self.action != "find":
            raise _error(contract_name, "action", "must equal find")
        if self.request_source not in {"web", "cli", "full_cycle"}:
            raise _error(contract_name, "request_source", "must be one of web, cli, full_cycle")
        _require_aware_datetime(self.created_at, contract_name=contract_name, field_path="created_at")

        if isinstance(self.attempt_index, bool) or not isinstance(self.attempt_index, int):
            raise _error(contract_name, "attempt_index", "must be an integer")
        if self.attempt_index < 0:
            raise _error(contract_name, "attempt_index", "must be greater than or equal to 0")

        self.selection = _require_mapping(self.selection, contract_name=contract_name, field_path="selection")
        self.requested_parameters = _require_mapping(
            self.requested_parameters,
            contract_name=contract_name,
            field_path="requested_parameters",
        )
        self.effective_parameters = _require_mapping(
            self.effective_parameters,
            contract_name=contract_name,
            field_path="effective_parameters",
        )
        _reject_secret_in_value(
            self.requested_parameters,
            contract_name=contract_name,
            field_path="requested_parameters",
        )
        _reject_secret_in_value(
            self.effective_parameters,
            contract_name=contract_name,
            field_path="effective_parameters",
        )

        command_items = _require_list(
            self.command_redacted,
            contract_name=contract_name,
            field_path="command_redacted",
        )
        self.command_redacted = []
        for index, command_item in enumerate(command_items):
            command = _require_non_empty_string(
                command_item,
                contract_name=contract_name,
                field_path=f"command_redacted[{index}]",
            )
            _reject_secret_in_value(
                command,
                contract_name=contract_name,
                field_path=f"command_redacted[{index}]",
            )
            self.command_redacted.append(command)

        if isinstance(self.startup_grace_seconds, bool) or not isinstance(self.startup_grace_seconds, int):
            raise _error(contract_name, "startup_grace_seconds", "must be an integer")
        if self.startup_grace_seconds <= 0:
            raise _error(contract_name, "startup_grace_seconds", "must be greater than 0")
        if isinstance(self.stall_suspect_seconds, bool) or not isinstance(self.stall_suspect_seconds, int):
            raise _error(contract_name, "stall_suspect_seconds", "must be an integer")
        if isinstance(self.stall_confirm_seconds, bool) or not isinstance(self.stall_confirm_seconds, int):
            raise _error(contract_name, "stall_confirm_seconds", "must be an integer")
        if not self.startup_grace_seconds < self.stall_suspect_seconds < self.stall_confirm_seconds:
            raise _error(
                contract_name,
                "stall_confirm_seconds",
                "must satisfy startup_grace_seconds < stall_suspect_seconds < stall_confirm_seconds",
            )
        if isinstance(self.recovery_budget, bool) or not isinstance(self.recovery_budget, int):
            raise _error(contract_name, "recovery_budget", "must be an integer")
        if self.recovery_budget < 0:
            raise _error(contract_name, "recovery_budget", "must be greater than or equal to 0")
        if not isinstance(self.approval_risk_threshold, RiskLevel):
            raise _error(contract_name, "approval_risk_threshold", "must be a RiskLevel")
        _require_bool(
            self.external_costs_authorized,
            contract_name=contract_name,
            field_path="external_costs_authorized",
        )
        self.skippable_sources = _copy_string_list(
            self.skippable_sources,
            contract_name=contract_name,
            field_path="skippable_sources",
        )
        for index, source in enumerate(self.skippable_sources):
            _require_non_empty_string(
                source,
                contract_name=contract_name,
                field_path=f"skippable_sources[{index}]",
            )
        if len(self.skippable_sources) != len(set(self.skippable_sources)):
            raise _error(
                contract_name,
                "skippable_sources",
                "must not contain duplicates",
            )

        self.expected_artifacts = self._copy_typed_list(
            self.expected_artifacts,
            ArtifactRef,
            "expected_artifacts",
        )
        self.parameter_changes = self._copy_typed_list(
            self.parameter_changes,
            ParameterChange,
            "parameter_changes",
        )
        self.experience_parameter_changes = self._copy_typed_list(
            self.experience_parameter_changes,
            ParameterChange,
            "experience_parameter_changes",
        )
        self.matched_experience_refs = self._copy_typed_list(
            self.matched_experience_refs,
            ExperienceRef,
            "matched_experience_refs",
        )
        self.applied_experience_refs = self._copy_typed_list(
            self.applied_experience_refs,
            ExperienceRef,
            "applied_experience_refs",
        )
        self.allowed_recovery_actions = self._copy_typed_list(
            self.allowed_recovery_actions,
            RecoveryAction,
            "allowed_recovery_actions",
        )
        for index, change in enumerate(
            [*self.parameter_changes, *self.experience_parameter_changes]
        ):
            _reject_secret_in_value(
                change.before,
                contract_name=contract_name,
                field_path=f"parameter_changes[{index}].before",
            )
            _reject_secret_in_value(
                change.after,
                contract_name=contract_name,
                field_path=f"parameter_changes[{index}].after",
            )

        matched_case_ids = {reference.case_id for reference in self.matched_experience_refs}
        for index, reference in enumerate(self.applied_experience_refs):
            if reference.case_id not in matched_case_ids:
                raise _error(
                    contract_name,
                    f"applied_experience_refs[{index}]",
                    "must reference a case in matched_experience_refs",
                )

    def _copy_typed_list(
        self,
        value: object,
        expected_type: type[object],
        field_path: str,
    ) -> list[object]:
        contract_name = type(self).__name__
        copied = _require_list(value, contract_name=contract_name, field_path=field_path)
        for index, item in enumerate(copied):
            if not isinstance(item, expected_type):
                raise _error(
                    contract_name,
                    f"{field_path}[{index}]",
                    f"must be a {expected_type.__name__}",
                )
        return copied


def _require_non_negative_int(
    value: object,
    *,
    contract_name: str,
    field_path: str,
    nullable: bool = False,
) -> int | None:
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        expected = "an integer or null" if nullable else "an integer"
        raise _error(contract_name, field_path, f"must be {expected}")
    if value < 0:
        raise _error(contract_name, field_path, "must be greater than or equal to 0")
    return value


def _require_non_negative_number(
    value: object,
    *,
    contract_name: str,
    field_path: str,
    nullable: bool = False,
) -> float | int | None:
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        expected = "a number or null" if nullable else "a number"
        raise _error(contract_name, field_path, f"must be {expected}")
    if not isfinite(float(value)) or value < 0:
        raise _error(contract_name, field_path, "must be a finite number greater than or equal to 0")
    return value


def _copy_string_list(
    value: object,
    *,
    contract_name: str,
    field_path: str,
) -> list[str]:
    items = _require_list(value, contract_name=contract_name, field_path=field_path)
    copied: list[str] = []
    for index, item in enumerate(items):
        copied.append(
            _require_string(
                item,
                contract_name=contract_name,
                field_path=f"{field_path}[{index}]",
            )
        )
    return copied


@dataclass(kw_only=True)
class ExecutionHandle(JsonContract):
    """Process facts returned after an Executor successfully starts Find.

    It links the process to a RunContext and gives future Observer and Supervisor
    components the PID, log locations, and any subsequently discovered Find run
    identity. It does not contain launch inputs, progress, validation, recovery, or
    startup failures.
    """

    context_id: str
    pid: int
    started_at: datetime
    process_alive: bool
    stdout_path: str
    stderr_path: str
    schema_version: str = "find.execution_handle.v1"
    stage: str = "find"
    run_id: str | None = None
    run_dir: str | None = None
    exit_code: int | None = None

    def __post_init__(self) -> None:
        contract_name = type(self).__name__
        if self.schema_version != "find.execution_handle.v1":
            raise _error(contract_name, "schema_version", "must equal find.execution_handle.v1")
        if self.stage != "find":
            raise _error(contract_name, "stage", "must equal find")
        for field_name in ("context_id", "stdout_path", "stderr_path"):
            _require_non_empty_string(
                getattr(self, field_name),
                contract_name=contract_name,
                field_path=field_name,
            )
        if isinstance(self.pid, bool) or not isinstance(self.pid, int) or self.pid <= 0:
            raise _error(contract_name, "pid", "must be an integer greater than 0")
        _require_aware_datetime(self.started_at, contract_name=contract_name, field_path="started_at")
        _require_bool(self.process_alive, contract_name=contract_name, field_path="process_alive")

        if (self.run_id is None) != (self.run_dir is None):
            raise _error(contract_name, "run_id", "and run_dir must both be set or both be null")
        if self.run_id is not None:
            _require_non_empty_string(self.run_id, contract_name=contract_name, field_path="run_id")
            _require_non_empty_string(self.run_dir, contract_name=contract_name, field_path="run_dir")
        if self.exit_code is not None and (
            isinstance(self.exit_code, bool) or not isinstance(self.exit_code, int)
        ):
            raise _error(contract_name, "exit_code", "must be an integer or null")
        if self.process_alive and self.exit_code is not None:
            raise _error(contract_name, "exit_code", "must be null while process_alive is true")


@dataclass(kw_only=True)
class ProgressSnapshot(JsonContract):
    """One observer-owned, point-in-time view of a Find run."""

    snapshot_id: str
    run_id: str
    created_at: datetime
    observed_at: datetime
    sequence: int
    producer: str
    producer_version: str
    status: ProgressStatus
    phase: str
    counts: Mapping[str, int]
    elapsed_seconds: float
    seconds_without_progress: float
    process_alive: bool
    cancel_requested: bool
    artifact_observations: list[ArtifactRef]
    progress_parse_ok: bool
    result_exists: bool
    source_status_exists: bool
    source_total: int
    source_ready: int
    source_limited: int
    source_failed: int
    status_reason: str
    schema_version: str = "find.progress_snapshot.v2"
    raw_phase: str | None = None
    current: int | None = None
    total: int | None = None
    percent: int | None = None
    message: str | None = None
    run_started_at: datetime | None = None
    progress_updated_at: datetime | None = None
    last_meaningful_change_at: datetime | None = None
    phase_elapsed_seconds: float | None = None
    pid: int | None = None
    process_started_at: datetime | None = None
    exit_code: int | None = None
    termination_signal: str | None = None
    result_size_bytes: int | None = None
    source_signals: list[str] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)
    observation_errors: list[str] = field(default_factory=list)
    evidence_refs: list[EvidenceRef] = field(default_factory=list)
    evidence_facts: list[EvidenceFact] = field(default_factory=list)

    def __post_init__(self) -> None:
        contract_name = type(self).__name__
        for field_name in (
            "snapshot_id",
            "run_id",
            "producer",
            "producer_version",
            "phase",
            "status_reason",
        ):
            _require_string(getattr(self, field_name), contract_name=contract_name, field_path=field_name)
        for field_name in ("raw_phase", "message", "termination_signal"):
            _optional_string(getattr(self, field_name), contract_name=contract_name, field_path=field_name)

        if self.schema_version != "find.progress_snapshot.v2":
            raise _error(contract_name, "schema_version", "must equal find.progress_snapshot.v2")
        if not isinstance(self.status, ProgressStatus):
            raise _error(contract_name, "status", "must be a ProgressStatus")
        for field_name in ("created_at", "observed_at"):
            _require_aware_datetime(
                getattr(self, field_name),
                contract_name=contract_name,
                field_path=field_name,
            )
        for field_name in (
            "run_started_at",
            "progress_updated_at",
            "last_meaningful_change_at",
            "process_started_at",
        ):
            value = getattr(self, field_name)
            if value is not None:
                _require_aware_datetime(value, contract_name=contract_name, field_path=field_name)

        _require_non_negative_int(
            self.sequence,
            contract_name=contract_name,
            field_path="sequence",
        )
        self.current = _require_non_negative_int(
            self.current,
            contract_name=contract_name,
            field_path="current",
            nullable=True,
        )
        self.total = _require_non_negative_int(
            self.total,
            contract_name=contract_name,
            field_path="total",
            nullable=True,
        )
        if self.current is not None and self.total is not None and self.current > self.total:
            raise _error(contract_name, "current", "must be less than or equal to total")
        if self.percent is not None:
            if isinstance(self.percent, bool) or not isinstance(self.percent, int):
                raise _error(contract_name, "percent", "must be an integer or null")
            if not 0 <= self.percent <= 100:
                raise _error(contract_name, "percent", "must be between 0 and 100")

        self.counts = _require_mapping(self.counts, contract_name=contract_name, field_path="counts")
        for count_name, count_value in self.counts.items():
            _require_string(count_name, contract_name=contract_name, field_path=f"counts.{count_name}")
            _require_non_negative_int(
                count_value,
                contract_name=contract_name,
                field_path=f"counts.{count_name}",
            )

        _require_non_negative_number(
            self.elapsed_seconds,
            contract_name=contract_name,
            field_path="elapsed_seconds",
        )
        _require_non_negative_number(
            self.seconds_without_progress,
            contract_name=contract_name,
            field_path="seconds_without_progress",
        )
        self.phase_elapsed_seconds = _require_non_negative_number(
            self.phase_elapsed_seconds,
            contract_name=contract_name,
            field_path="phase_elapsed_seconds",
            nullable=True,
        )

        _require_bool(self.process_alive, contract_name=contract_name, field_path="process_alive")
        _require_bool(self.cancel_requested, contract_name=contract_name, field_path="cancel_requested")
        if self.pid is not None:
            if isinstance(self.pid, bool) or not isinstance(self.pid, int) or self.pid <= 0:
                raise _error(contract_name, "pid", "must be an integer greater than 0 or null")
        if self.exit_code is not None and (isinstance(self.exit_code, bool) or not isinstance(self.exit_code, int)):
            raise _error(contract_name, "exit_code", "must be an integer or null")
        if self.process_alive and self.exit_code is not None:
            raise _error(contract_name, "exit_code", "must be null while process_alive is true")

        _require_bool(self.progress_parse_ok, contract_name=contract_name, field_path="progress_parse_ok")
        _require_bool(self.result_exists, contract_name=contract_name, field_path="result_exists")
        _require_bool(
            self.source_status_exists,
            contract_name=contract_name,
            field_path="source_status_exists",
        )
        self.result_size_bytes = _require_non_negative_int(
            self.result_size_bytes,
            contract_name=contract_name,
            field_path="result_size_bytes",
            nullable=True,
        )

        for field_name in ("source_total", "source_ready", "source_limited", "source_failed"):
            _require_non_negative_int(
                getattr(self, field_name),
                contract_name=contract_name,
                field_path=field_name,
            )

        self.artifact_observations = self._copy_typed_list(
            self.artifact_observations,
            ArtifactRef,
            "artifact_observations",
        )
        self.evidence_refs = self._copy_typed_list(
            self.evidence_refs,
            EvidenceRef,
            "evidence_refs",
        )
        self.evidence_facts = _normalize_evidence_facts(
            self.evidence_facts,
            contract_name=contract_name,
        )
        for index, fact in enumerate(self.evidence_facts):
            for field_name, expected in (
                ("run_id", self.run_id),
                ("producer", self.producer),
                ("source_contract_id", self.snapshot_id),
                ("observed_at", self.observed_at),
            ):
                if getattr(fact, field_name) != expected:
                    raise _error(
                        contract_name,
                        f"evidence_facts[{index}].{field_name}",
                        f"must match {field_name if field_name != 'source_contract_id' else 'snapshot_id'}",
                    )
        self.source_signals = _copy_string_list(
            self.source_signals,
            contract_name=contract_name,
            field_path="source_signals",
        )
        self.signals = _copy_string_list(
            self.signals,
            contract_name=contract_name,
            field_path="signals",
        )
        self.observation_errors = _copy_string_list(
            self.observation_errors,
            contract_name=contract_name,
            field_path="observation_errors",
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> ProgressSnapshot:
        migrated = _migrate_runtime_evidence_carrier_payload(
            data,
            contract_name=cls.__name__,
            v1_schema="find.progress_snapshot.v1",
            v2_schema="find.progress_snapshot.v2",
        )
        return super().from_dict(migrated)

    def _copy_typed_list(
        self,
        value: object,
        expected_type: type[object],
        field_path: str,
    ) -> list[object]:
        contract_name = type(self).__name__
        copied = _require_list(value, contract_name=contract_name, field_path=field_path)
        for index, item in enumerate(copied):
            if not isinstance(item, expected_type):
                raise _error(
                    contract_name,
                    f"{field_path}[{index}]",
                    f"must be a {expected_type.__name__}",
                )
        return copied


SUPPORTED_FIND_VALIDATION_CODES = frozenset(
    {
        "run_dir_exists",
        "manifest_exists",
        "manifest_layout_supported",
        "progress_exists",
        "progress_parseable",
        "progress_run_id_matches",
        "progress_phase_complete",
        "process_terminal",
        "process_exit_code_ok",
        "result_exists",
        "result_parseable",
        "result_run_id_matches",
        "result_created_at_valid",
        "strong_recommendations_present",
        "recommendation_count_sufficient",
        "recommendation_shortfall_acceptable",
        "recommendation_quality_ok",
        "real_abstracts_present",
        "required_user_fields_present",
        "source_status_present",
        "source_integrity_not_blocking",
        "reading_candidate_ids_unique",
        "reading_bridge_probe_passed",
    }
)


@dataclass(kw_only=True)
class ValidationResult(JsonContract):
    """A deterministic validation outcome for one completed Find run directory."""

    validation_id: str
    run_id: str
    created_at: datetime
    validated_at: datetime
    validated_run_dir: str
    producer: str
    producer_version: str
    policy_version: str
    duration_ms: int
    status: ValidationStatus
    ready_for_read: bool
    summary: str
    checks: list[ValidationCheck]
    passed_check_count: int
    warning_check_count: int
    blocked_check_count: int
    recommendation_target_count: int
    recommendation_actual_count: int
    recommendation_shortfall: int
    strong_recommendation_count: int
    recommendation_quality_status: str
    candidate_ids: list[str]
    candidate_digest: str
    bridge_probe_status: ValidationStatus
    input_artifact_refs: list[ArtifactRef]
    schema_version: str = "find.validation_result.v2"
    downstream_stage: str = "read"
    bridge_probe_errors: list[str] = field(default_factory=list)
    downstream_input_preview: Mapping[str, object] | None = None
    warnings: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    failure_codes: list[str] = field(default_factory=list)
    evidence_refs: list[EvidenceRef] = field(default_factory=list)
    evidence_facts: list[EvidenceFact] = field(default_factory=list)

    def __post_init__(self) -> None:
        contract_name = type(self).__name__
        for field_name in (
            "validation_id",
            "run_id",
            "validated_run_dir",
            "producer",
            "producer_version",
            "policy_version",
            "summary",
            "recommendation_quality_status",
            "candidate_digest",
        ):
            _require_string(getattr(self, field_name), contract_name=contract_name, field_path=field_name)
        if self.schema_version != "find.validation_result.v2":
            raise _error(contract_name, "schema_version", "must equal find.validation_result.v2")
        if self.downstream_stage != "read":
            raise _error(contract_name, "downstream_stage", "must equal read")
        _require_aware_datetime(self.created_at, contract_name=contract_name, field_path="created_at")
        _require_aware_datetime(self.validated_at, contract_name=contract_name, field_path="validated_at")
        if not isinstance(self.status, ValidationStatus):
            raise _error(contract_name, "status", "must be a ValidationStatus")
        if not isinstance(self.bridge_probe_status, ValidationStatus):
            raise _error(contract_name, "bridge_probe_status", "must be a ValidationStatus")
        _require_bool(self.ready_for_read, contract_name=contract_name, field_path="ready_for_read")

        for field_name in (
            "duration_ms",
            "passed_check_count",
            "warning_check_count",
            "blocked_check_count",
            "recommendation_target_count",
            "recommendation_actual_count",
            "recommendation_shortfall",
            "strong_recommendation_count",
        ):
            _require_non_negative_int(
                getattr(self, field_name),
                contract_name=contract_name,
                field_path=field_name,
            )

        self.checks = self._copy_typed_list(self.checks, ValidationCheck, "checks")
        if not self.checks:
            raise _error(contract_name, "checks", "must contain at least one check")
        for index, check in enumerate(self.checks):
            if check.code not in SUPPORTED_FIND_VALIDATION_CODES:
                raise _error(
                    contract_name,
                    f"checks[{index}].code",
                    "is not a supported Find validation code",
                )
        calculated_counts = {
            ValidationStatus.PASS: sum(check.status is ValidationStatus.PASS for check in self.checks),
            ValidationStatus.WARNING: sum(check.status is ValidationStatus.WARNING for check in self.checks),
            ValidationStatus.BLOCK: sum(check.status is ValidationStatus.BLOCK for check in self.checks),
        }
        supplied_counts = {
            ValidationStatus.PASS: self.passed_check_count,
            ValidationStatus.WARNING: self.warning_check_count,
            ValidationStatus.BLOCK: self.blocked_check_count,
        }
        for validation_status, supplied_count in supplied_counts.items():
            if supplied_count != calculated_counts[validation_status]:
                raise _error(
                    contract_name,
                    self._count_field_name(validation_status),
                    "must match the checks status count",
                )

        required_block = any(
            check.required and check.status is ValidationStatus.BLOCK for check in self.checks
        )
        if required_block and self.status is not ValidationStatus.BLOCK:
            raise _error(contract_name, "status", "must be block when a required check is block")
        if required_block and self.ready_for_read:
            raise _error(contract_name, "ready_for_read", "must be false when a required check is block")
        if self.ready_for_read and self.status is not ValidationStatus.PASS:
            raise _error(contract_name, "ready_for_read", "requires status pass")

        self.candidate_ids = _copy_string_list(
            self.candidate_ids,
            contract_name=contract_name,
            field_path="candidate_ids",
        )
        if len(set(self.candidate_ids)) != len(self.candidate_ids):
            raise _error(contract_name, "candidate_ids", "must not contain duplicates")
        self.input_artifact_refs = self._copy_typed_list(
            self.input_artifact_refs,
            ArtifactRef,
            "input_artifact_refs",
        )
        self.evidence_refs = self._copy_typed_list(
            self.evidence_refs,
            EvidenceRef,
            "evidence_refs",
        )
        self.evidence_facts = _normalize_evidence_facts(
            self.evidence_facts,
            contract_name=contract_name,
        )
        for index, fact in enumerate(self.evidence_facts):
            for field_name, expected in (
                ("run_id", self.run_id),
                ("producer", self.producer),
                ("source_contract_id", self.validation_id),
                ("observed_at", self.validated_at),
            ):
                if getattr(fact, field_name) != expected:
                    raise _error(
                        contract_name,
                        f"evidence_facts[{index}].{field_name}",
                        f"must match {field_name if field_name != 'source_contract_id' else 'validation_id'}",
                    )
        for field_name in ("bridge_probe_errors", "warnings", "blockers", "failure_codes"):
            setattr(
                self,
                field_name,
                _copy_string_list(
                    getattr(self, field_name),
                    contract_name=contract_name,
                    field_path=field_name,
                ),
            )
        if self.blockers and self.ready_for_read:
            raise _error(contract_name, "ready_for_read", "must be false when blockers are present")
        if self.downstream_input_preview is not None:
            self.downstream_input_preview = _require_mapping(
                self.downstream_input_preview,
                contract_name=contract_name,
                field_path="downstream_input_preview",
            )

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> ValidationResult:
        migrated = _migrate_runtime_evidence_carrier_payload(
            data,
            contract_name=cls.__name__,
            v1_schema="find.validation_result.v1",
            v2_schema="find.validation_result.v2",
        )
        return super().from_dict(migrated)

    def _copy_typed_list(
        self,
        value: object,
        expected_type: type[object],
        field_path: str,
    ) -> list[object]:
        contract_name = type(self).__name__
        copied = _require_list(value, contract_name=contract_name, field_path=field_path)
        for index, item in enumerate(copied):
            if not isinstance(item, expected_type):
                raise _error(
                    contract_name,
                    f"{field_path}[{index}]",
                    f"must be a {expected_type.__name__}",
                )
        return copied

    @staticmethod
    def _count_field_name(status: ValidationStatus) -> str:
        return {
            ValidationStatus.PASS: "passed_check_count",
            ValidationStatus.WARNING: "warning_check_count",
            ValidationStatus.BLOCK: "blocked_check_count",
        }[status]


SUPPORTED_FIND_ANOMALY_KINDS = frozenset(
    {
        "startup_failed",
        "progress_missing",
        "progress_unparseable",
        "progress_stalled",
        "process_exited_nonzero",
        "completion_without_result",
        "result_missing",
        "result_unparseable",
        "run_id_mismatch",
        "empty_recommendations",
        "recommendation_shortfall",
        "source_integrity_blocked",
        "reading_bridge_rejected",
    }
)

DIRECT_ANOMALY_EVIDENCE_KINDS = frozenset({"log", "artifact", "validation"})


def _copy_mapping_list(
    value: object,
    *,
    contract_name: str,
    field_path: str,
) -> list[dict[str, object]]:
    items = _require_list(value, contract_name=contract_name, field_path=field_path)
    copied: list[dict[str, object]] = []
    for index, item in enumerate(items):
        copied.append(
            _require_mapping(
                item,
                contract_name=contract_name,
                field_path=f"{field_path}[{index}]",
            )
        )
    return copied


@dataclass(kw_only=True)
class Anomaly(JsonContract):
    """Evidence-backed description of one observed Find anomaly.

    ``kind`` is an observed anomaly classification, not a root-cause code.
    """

    anomaly_id: str
    run_id: str
    created_at: datetime
    detected_at: datetime
    updated_at: datetime
    producer: str
    producer_version: str
    stage: str
    kind: str
    blocking: bool
    confidence: float
    detected_by: list[str]
    supervisor_state_revision: int
    symptoms: list[str]
    evidence_refs: list[EvidenceRef]
    root_cause_status: str
    affected_phase: str
    downstream_impact: str
    partial_results_usable: bool
    recovery_eligible: bool
    retryable_signal: bool
    fingerprint: str
    occurrence_count: int
    schema_version: str = "find.anomaly.v2"
    progress_snapshot_id: str | None = None
    validation_id: str | None = None
    process_facts: Mapping[str, object] = field(default_factory=dict)
    artifact_facts: Mapping[str, object] = field(default_factory=dict)
    timing_facts: Mapping[str, object] = field(default_factory=dict)
    root_cause: str | None = None
    hypotheses: list[Mapping[str, object]] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    affected_sources: list[str] = field(default_factory=list)
    affected_artifacts: list[str] = field(default_factory=list)
    duplicate_of: str | None = None
    evidence_facts: list[EvidenceFact] = field(default_factory=list)

    def __post_init__(self) -> None:
        contract_name = type(self).__name__
        for field_name in (
            "anomaly_id",
            "run_id",
            "producer",
            "producer_version",
            "stage",
            "kind",
            "root_cause_status",
            "affected_phase",
            "downstream_impact",
            "fingerprint",
        ):
            _require_string(getattr(self, field_name), contract_name=contract_name, field_path=field_name)
        for field_name in ("progress_snapshot_id", "validation_id", "root_cause", "duplicate_of"):
            _optional_string(getattr(self, field_name), contract_name=contract_name, field_path=field_name)
        if self.schema_version != "find.anomaly.v2":
            raise _error(contract_name, "schema_version", "must equal find.anomaly.v2")
        if self.kind not in SUPPORTED_FIND_ANOMALY_KINDS:
            raise _error(contract_name, "kind", "is not a supported Find anomaly kind")
        if self.root_cause_status not in {"unknown", "suspected", "confirmed"}:
            raise _error(contract_name, "root_cause_status", "must be one of unknown, suspected, confirmed")
        for field_name in ("created_at", "detected_at", "updated_at"):
            _require_aware_datetime(
                getattr(self, field_name),
                contract_name=contract_name,
                field_path=field_name,
            )
        for field_name in (
            "blocking",
            "partial_results_usable",
            "recovery_eligible",
            "retryable_signal",
        ):
            _require_bool(getattr(self, field_name), contract_name=contract_name, field_path=field_name)
        if isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float)):
            raise _error(contract_name, "confidence", "must be a number")
        if not isfinite(float(self.confidence)) or not 0 <= self.confidence <= 1:
            raise _error(contract_name, "confidence", "must be between 0 and 1")
        _require_non_negative_int(
            self.supervisor_state_revision,
            contract_name=contract_name,
            field_path="supervisor_state_revision",
        )
        if isinstance(self.occurrence_count, bool) or not isinstance(self.occurrence_count, int):
            raise _error(contract_name, "occurrence_count", "must be an integer")
        if self.occurrence_count < 1:
            raise _error(contract_name, "occurrence_count", "must be greater than or equal to 1")

        self.detected_by = _copy_string_list(
            self.detected_by,
            contract_name=contract_name,
            field_path="detected_by",
        )
        if not self.detected_by:
            raise _error(contract_name, "detected_by", "must contain at least one source")
        self.symptoms = _copy_string_list(
            self.symptoms,
            contract_name=contract_name,
            field_path="symptoms",
        )
        if not self.symptoms:
            raise _error(contract_name, "symptoms", "must contain at least one symptom")
        self.evidence_refs = self._copy_evidence_refs(self.evidence_refs)
        if not self.evidence_refs:
            raise _error(contract_name, "evidence_refs", "must contain at least one evidence reference")
        self.evidence_facts = _normalize_evidence_facts(
            self.evidence_facts,
            contract_name=contract_name,
        )
        allowed_source_contract_ids = {
            source_contract_id
            for source_contract_id in (
                self.progress_snapshot_id,
                self.validation_id,
            )
            if source_contract_id is not None
        }
        for index, fact in enumerate(self.evidence_facts):
            if fact.run_id != self.run_id:
                raise _error(
                    contract_name,
                    f"evidence_facts[{index}].run_id",
                    "must match run_id",
                )
            if fact.source_contract_id not in allowed_source_contract_ids:
                raise _error(
                    contract_name,
                    f"evidence_facts[{index}].source_contract_id",
                    "must match progress_snapshot_id or validation_id",
                )

        self.process_facts = _require_mapping(
            self.process_facts,
            contract_name=contract_name,
            field_path="process_facts",
        )
        self.artifact_facts = _require_mapping(
            self.artifact_facts,
            contract_name=contract_name,
            field_path="artifact_facts",
        )
        self.timing_facts = _require_mapping(
            self.timing_facts,
            contract_name=contract_name,
            field_path="timing_facts",
        )
        self.hypotheses = _copy_mapping_list(
            self.hypotheses,
            contract_name=contract_name,
            field_path="hypotheses",
        )
        for field_name in ("missing_evidence", "affected_sources", "affected_artifacts"):
            setattr(
                self,
                field_name,
                _copy_string_list(
                    getattr(self, field_name),
                    contract_name=contract_name,
                    field_path=field_name,
                ),
            )

        if self.root_cause_status == "confirmed":
            if not self.root_cause:
                raise _error(contract_name, "root_cause", "is required when root_cause_status is confirmed")
            if not any(
                evidence.kind in DIRECT_ANOMALY_EVIDENCE_KINDS for evidence in self.evidence_refs
            ):
                raise _error(
                    contract_name,
                    "evidence_refs",
                    "must include direct evidence when root_cause_status is confirmed",
                )

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Anomaly:
        migrated = _migrate_runtime_evidence_carrier_payload(
            data,
            contract_name=cls.__name__,
            v1_schema="find.anomaly.v1",
            v2_schema="find.anomaly.v2",
        )
        return super().from_dict(migrated)

    def _copy_evidence_refs(self, value: object) -> list[EvidenceRef]:
        contract_name = type(self).__name__
        copied = _require_list(value, contract_name=contract_name, field_path="evidence_refs")
        for index, item in enumerate(copied):
            if not isinstance(item, EvidenceRef):
                raise _error(
                    contract_name,
                    f"evidence_refs[{index}]",
                    "must be an EvidenceRef",
                )
        return copied  # type: ignore[return-value]


@dataclass(kw_only=True)
class RecoveryProposal(JsonContract):
    """Untrusted candidate recovery advice awaiting controller validation."""

    proposal_id: str
    context_id: str
    run_id: str
    anomaly_id: str
    created_at: datetime
    proposed_action: RecoveryAction
    reason: str
    confidence: float
    risk_level: RiskLevel
    schema_version: str = "find.recovery_proposal.v1"
    parameter_changes: Mapping[str, object] = field(default_factory=dict)
    target_sources: list[str] = field(default_factory=list)
    evidence_refs: list[EvidenceRef] = field(default_factory=list)

    def __post_init__(self) -> None:
        contract_name = type(self).__name__
        for field_name in ("proposal_id", "context_id", "anomaly_id", "reason"):
            _require_non_empty_string(
                getattr(self, field_name),
                contract_name=contract_name,
                field_path=field_name,
            )
        _require_string(
            self.run_id,
            contract_name=contract_name,
            field_path="run_id",
        )
        _require_aware_datetime(
            self.created_at,
            contract_name=contract_name,
            field_path="created_at",
        )
        if self.schema_version != "find.recovery_proposal.v1":
            raise _error(
                contract_name,
                "schema_version",
                "must equal find.recovery_proposal.v1",
            )
        if not isinstance(self.proposed_action, RecoveryAction):
            raise _error(
                contract_name,
                "proposed_action",
                "must be a RecoveryAction",
            )
        allowed_actions = {
            RecoveryAction.NO_ACTION,
            RecoveryAction.RETRY_NEW_RUN,
            RecoveryAction.RETRY_WITH_PARAMETER_CHANGE,
            RecoveryAction.SKIP_OPTIONAL_SOURCE,
            RecoveryAction.STOP_AND_REPORT,
        }
        if self.proposed_action not in allowed_actions:
            raise _error(
                contract_name,
                "proposed_action",
                "is not an action that an advisor may propose",
            )
        if not isinstance(self.risk_level, RiskLevel):
            raise _error(contract_name, "risk_level", "must be a RiskLevel")
        if isinstance(self.confidence, bool) or not isinstance(
            self.confidence,
            (int, float),
        ):
            raise _error(contract_name, "confidence", "must be a number")
        if not isfinite(float(self.confidence)) or not 0 <= self.confidence <= 1:
            raise _error(
                contract_name,
                "confidence",
                "must be between 0 and 1",
            )

        self.parameter_changes = _require_mapping(
            self.parameter_changes,
            contract_name=contract_name,
            field_path="parameter_changes",
        )
        for name in self.parameter_changes:
            _require_non_empty_string(
                name,
                contract_name=contract_name,
                field_path="parameter_changes",
            )
        self.target_sources = _copy_string_list(
            self.target_sources,
            contract_name=contract_name,
            field_path="target_sources",
        )
        for index, source in enumerate(self.target_sources):
            _require_non_empty_string(
                source,
                contract_name=contract_name,
                field_path=f"target_sources[{index}]",
            )
        evidence_items = _require_list(
            self.evidence_refs,
            contract_name=contract_name,
            field_path="evidence_refs",
        )
        self.evidence_refs = []
        for index, evidence in enumerate(evidence_items):
            if not isinstance(evidence, EvidenceRef):
                raise _error(
                    contract_name,
                    f"evidence_refs[{index}]",
                    "must be an EvidenceRef",
                )
            self.evidence_refs.append(evidence)

        if self.proposed_action is RecoveryAction.RETRY_WITH_PARAMETER_CHANGE:
            if not self.parameter_changes:
                raise _error(
                    contract_name,
                    "parameter_changes",
                    "must not be empty for retry_with_parameter_change",
                )
            if self.target_sources:
                raise _error(
                    contract_name,
                    "target_sources",
                    "must be empty for retry_with_parameter_change",
                )
        elif self.proposed_action is RecoveryAction.SKIP_OPTIONAL_SOURCE:
            if self.parameter_changes:
                raise _error(
                    contract_name,
                    "parameter_changes",
                    "must be empty for skip_optional_source",
                )
            if not self.target_sources:
                raise _error(
                    contract_name,
                    "target_sources",
                    "must not be empty for skip_optional_source",
                )
        else:
            if self.parameter_changes:
                raise _error(
                    contract_name,
                    "parameter_changes",
                    "must be empty for this action",
                )
            if self.target_sources:
                raise _error(
                    contract_name,
                    "target_sources",
                    "must be empty for this action",
                )


@dataclass(kw_only=True)
class RecoveryDecision(JsonContract):
    """A controlled recovery decision for one Anomaly, before any execution."""

    decision_id: str
    run_id: str
    anomaly_id: str
    created_at: datetime
    decided_at: datetime
    producer: str
    producer_version: str
    action: RecoveryAction
    reason: str
    risk_level: RiskLevel
    executable: bool
    new_run_required: bool
    exploratory: bool
    requires_approval: bool
    approval_status: str
    attempt_index: int
    budget_before: int
    budget_cost: int
    budget_after: int
    max_same_action_attempts: int
    verification_policy: str
    required_post_checks: list[str]
    success_definition: str
    stop_if_failed: bool
    schema_version: str = "find.recovery_decision.v1"
    proposal_id: str | None = None
    proposed_action: RecoveryAction | None = None
    target_phase: str | None = None
    target_sources: list[str] = field(default_factory=list)
    action_parameters: Mapping[str, object] = field(default_factory=dict)
    parameter_changes: list[ParameterChange] = field(default_factory=list)
    preserve_artifacts: list[ArtifactRef] = field(default_factory=list)
    proposed_new_run_id: str | None = None
    command_preview_redacted: list[str] = field(default_factory=list)
    matched_playbook_id: str | None = None
    matched_experience_refs: list[ExperienceRef] = field(default_factory=list)
    evidence_of_previous_success: list[EvidenceRef] = field(default_factory=list)
    approval_reason: str | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None
    preconditions: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        contract_name = type(self).__name__
        for field_name in (
            "decision_id",
            "run_id",
            "anomaly_id",
            "producer",
            "producer_version",
            "reason",
            "approval_status",
            "verification_policy",
            "success_definition",
        ):
            _require_string(getattr(self, field_name), contract_name=contract_name, field_path=field_name)
        for field_name in (
            "target_phase",
            "proposed_new_run_id",
            "matched_playbook_id",
            "approval_reason",
            "approved_by",
        ):
            _optional_string(getattr(self, field_name), contract_name=contract_name, field_path=field_name)
        if self.schema_version != "find.recovery_decision.v1":
            raise _error(contract_name, "schema_version", "must equal find.recovery_decision.v1")
        for field_name in ("created_at", "decided_at"):
            _require_aware_datetime(
                getattr(self, field_name),
                contract_name=contract_name,
                field_path=field_name,
            )
        if self.approved_at is not None:
            _require_aware_datetime(
                self.approved_at,
                contract_name=contract_name,
                field_path="approved_at",
            )
        if not isinstance(self.action, RecoveryAction):
            raise _error(contract_name, "action", "must be a RecoveryAction")
        if not isinstance(self.risk_level, RiskLevel):
            raise _error(contract_name, "risk_level", "must be a RiskLevel")
        if self.proposal_id is not None:
            _require_non_empty_string(
                self.proposal_id,
                contract_name=contract_name,
                field_path="proposal_id",
            )
        if self.proposed_action is not None and not isinstance(
            self.proposed_action,
            RecoveryAction,
        ):
            raise _error(
                contract_name,
                "proposed_action",
                "must be a RecoveryAction or null",
            )
        if self.proposed_action is RecoveryAction.REQUEST_APPROVAL:
            raise _error(
                contract_name,
                "proposed_action",
                "must not be request_approval",
            )
        if self.proposal_id is not None:
            if self.action is not RecoveryAction.REQUEST_APPROVAL:
                raise _error(
                    contract_name,
                    "proposal_id",
                    "requires action to be request_approval",
                )
            if self.proposed_action is None:
                raise _error(
                    contract_name,
                    "proposed_action",
                    "is required when proposal_id is present",
                )
        if (
            self.proposed_action is not None
            and self.action is not RecoveryAction.REQUEST_APPROVAL
        ):
            raise _error(
                contract_name,
                "proposed_action",
                "requires action to be request_approval",
            )
        for field_name in (
            "executable",
            "new_run_required",
            "exploratory",
            "requires_approval",
            "stop_if_failed",
        ):
            _require_bool(getattr(self, field_name), contract_name=contract_name, field_path=field_name)
        if self.approval_status not in {"not_required", "pending", "approved", "rejected"}:
            raise _error(
                contract_name,
                "approval_status",
                "must be one of not_required, pending, approved, rejected",
            )
        if self.requires_approval and self.approval_status == "not_required":
            raise _error(contract_name, "approval_status", "cannot be not_required when approval is required")
        if not self.requires_approval and self.approval_status != "not_required":
            raise _error(contract_name, "approval_status", "must be not_required when approval is not required")
        if self.risk_level is RiskLevel.HIGH and self.approval_status != "approved" and self.executable:
            raise _error(contract_name, "executable", "must be false for unapproved high-risk recovery")

        if isinstance(self.attempt_index, bool) or not isinstance(self.attempt_index, int):
            raise _error(contract_name, "attempt_index", "must be an integer")
        if self.attempt_index < 1:
            raise _error(contract_name, "attempt_index", "must be greater than or equal to 1")
        for field_name in (
            "budget_before",
            "budget_cost",
            "budget_after",
            "max_same_action_attempts",
        ):
            _require_non_negative_int(
                getattr(self, field_name),
                contract_name=contract_name,
                field_path=field_name,
            )
        retry_actions = {
            RecoveryAction.RETRY_NEW_RUN,
            RecoveryAction.RETRY_WITH_PARAMETER_CHANGE,
        }
        if self.executable and self.action in retry_actions and self.budget_cost > self.budget_before:
            raise _error(contract_name, "budget_cost", "exceeds the available recovery budget")
        if self.budget_after != self.budget_before - self.budget_cost:
            raise _error(contract_name, "budget_after", "must equal budget_before minus budget_cost")

        if self.action in retry_actions and not self.new_run_required:
            raise _error(contract_name, "new_run_required", "must be true for retry actions")
        if self.executable and self.action in retry_actions and not self.proposed_new_run_id:
            raise _error(contract_name, "proposed_new_run_id", "is required for executable retry actions")
        if self.action is RecoveryAction.STOP_AND_REPORT and self.parameter_changes:
            raise _error(contract_name, "parameter_changes", "must be empty for stop_and_report")
        if self.action is RecoveryAction.NO_ACTION:
            if self.new_run_required:
                raise _error(contract_name, "new_run_required", "must be false for no_action")
            if self.budget_cost != 0:
                raise _error(contract_name, "budget_cost", "must be 0 for no_action")

        self.target_sources = _copy_string_list(
            self.target_sources,
            contract_name=contract_name,
            field_path="target_sources",
        )
        self.command_preview_redacted = _copy_string_list(
            self.command_preview_redacted,
            contract_name=contract_name,
            field_path="command_preview_redacted",
        )
        for index, command_item in enumerate(self.command_preview_redacted):
            _reject_secret_in_value(
                command_item,
                contract_name=contract_name,
                field_path=f"command_preview_redacted[{index}]",
            )
        self.preconditions = _copy_string_list(
            self.preconditions,
            contract_name=contract_name,
            field_path="preconditions",
        )
        self.required_post_checks = _copy_string_list(
            self.required_post_checks,
            contract_name=contract_name,
            field_path="required_post_checks",
        )
        self.action_parameters = _require_mapping(
            self.action_parameters,
            contract_name=contract_name,
            field_path="action_parameters",
        )
        self.parameter_changes = self._copy_typed_list(
            self.parameter_changes,
            ParameterChange,
            "parameter_changes",
        )
        self.preserve_artifacts = self._copy_typed_list(
            self.preserve_artifacts,
            ArtifactRef,
            "preserve_artifacts",
        )
        self.matched_experience_refs = self._copy_typed_list(
            self.matched_experience_refs,
            ExperienceRef,
            "matched_experience_refs",
        )
        self.evidence_of_previous_success = self._copy_typed_list(
            self.evidence_of_previous_success,
            EvidenceRef,
            "evidence_of_previous_success",
        )

    def _copy_typed_list(
        self,
        value: object,
        expected_type: type[object],
        field_path: str,
    ) -> list[object]:
        contract_name = type(self).__name__
        copied = _require_list(value, contract_name=contract_name, field_path=field_path)
        for index, item in enumerate(copied):
            if not isinstance(item, expected_type):
                raise _error(
                    contract_name,
                    f"{field_path}[{index}]",
                    f"must be a {expected_type.__name__}",
                )
        return copied


@dataclass(kw_only=True)
class ExperienceCase(JsonContract):
    """A normal, technical, or preference case retained for future matching."""

    case_id: str
    case_type: str
    created_at: datetime
    updated_at: datetime
    producer: str
    producer_version: str
    verified: bool
    deprecated: bool
    context_id: str
    root_run_id: str
    final_run_id: str
    root_cause_status: str
    evidence_refs: list[EvidenceRef]
    attempt_count: int
    outcome: str
    validation_after_id: str
    validation_after_status: ValidationStatus
    ready_for_read_after: bool
    risk_level: RiskLevel
    confidence: float
    matched_count: int
    applied_count: int
    successful_application_count: int
    schema_version: str = "find.experience_case.v2"
    stage: str = "find"
    project_id: str | None = None
    environment_fingerprint: str | None = None
    context_tags: list[str] = field(default_factory=list)
    anomaly_id: str | None = None
    anomaly_kind: str | None = None
    anomaly_fingerprint: str | None = None
    root_cause: str | None = None
    decision_id: str | None = None
    recovery_action: RecoveryAction | None = None
    parameter_changes: list[ParameterChange] = field(default_factory=list)
    target_sources: list[str] = field(default_factory=list)
    approval_record: Mapping[str, object] = field(default_factory=dict)
    playbook_id: str | None = None
    execution_started_at: datetime | None = None
    execution_finished_at: datetime | None = None
    exit_code: int | None = None
    duration_seconds: float | None = None
    new_run_ids: list[str] = field(default_factory=list)
    failure_reason: str | None = None
    validation_before_id: str | None = None
    validation_before_status: ValidationStatus | None = None
    resolved_failure_codes: list[str] = field(default_factory=list)
    remaining_failure_codes: list[str] = field(default_factory=list)
    evidence_facts: list[EvidenceFact] = field(default_factory=list)
    applicability_notes: list[str] = field(default_factory=list)
    applicability_conditions: list[EvidenceMatchCondition] = field(default_factory=list)
    recommended_action: RecoveryAction | None = None
    user_feedback: str | None = None
    user_labels: list[str] = field(default_factory=list)
    user_satisfied: bool | None = None
    preference_scope: list[str] = field(default_factory=list)
    last_matched_at: datetime | None = None
    last_applied_at: datetime | None = None

    def __post_init__(self) -> None:
        contract_name = type(self).__name__
        for field_name in (
            "case_id",
            "case_type",
            "producer",
            "producer_version",
            "context_id",
            "root_run_id",
            "final_run_id",
            "root_cause_status",
            "outcome",
            "validation_after_id",
        ):
            _require_string(getattr(self, field_name), contract_name=contract_name, field_path=field_name)
        for field_name in (
            "project_id",
            "environment_fingerprint",
            "anomaly_id",
            "anomaly_kind",
            "anomaly_fingerprint",
            "root_cause",
            "decision_id",
            "playbook_id",
            "failure_reason",
            "validation_before_id",
            "user_feedback",
        ):
            _optional_string(getattr(self, field_name), contract_name=contract_name, field_path=field_name)
        if self.schema_version != "find.experience_case.v2":
            raise _error(contract_name, "schema_version", "must equal find.experience_case.v2")
        if self.stage != "find":
            raise _error(contract_name, "stage", "must equal find")
        if self.case_type not in {"normal", "technical", "preference"}:
            raise _error(contract_name, "case_type", "must be one of normal, technical, preference")
        if self.outcome not in {"success", "recovered", "failed", "partial", "cancelled"}:
            raise _error(contract_name, "outcome", "must be one of success, recovered, failed, partial, cancelled")
        if self.root_cause_status not in {"unknown", "suspected", "confirmed"}:
            raise _error(contract_name, "root_cause_status", "must be one of unknown, suspected, confirmed")
        if self.root_cause is not None:
            _require_non_empty_string(
                self.root_cause,
                contract_name=contract_name,
                field_path="root_cause",
            )
        if self.root_cause_status == "confirmed" and self.root_cause is None:
            raise _error(
                contract_name,
                "root_cause",
                "is required when root_cause_status is confirmed",
            )
        if self.anomaly_kind is not None and self.anomaly_kind not in SUPPORTED_FIND_ANOMALY_KINDS:
            raise _error(contract_name, "anomaly_kind", "is not a supported Find anomaly kind")

        for field_name in ("created_at", "updated_at"):
            _require_aware_datetime(
                getattr(self, field_name),
                contract_name=contract_name,
                field_path=field_name,
            )
        for field_name in (
            "execution_started_at",
            "execution_finished_at",
            "last_matched_at",
            "last_applied_at",
        ):
            value = getattr(self, field_name)
            if value is not None:
                _require_aware_datetime(value, contract_name=contract_name, field_path=field_name)

        for field_name in ("verified", "deprecated", "ready_for_read_after"):
            _require_bool(getattr(self, field_name), contract_name=contract_name, field_path=field_name)
        if self.user_satisfied is not None:
            _require_bool(
                self.user_satisfied,
                contract_name=contract_name,
                field_path="user_satisfied",
            )
        if not isinstance(self.validation_after_status, ValidationStatus):
            raise _error(contract_name, "validation_after_status", "must be a ValidationStatus")
        if self.validation_before_status is not None and not isinstance(
            self.validation_before_status, ValidationStatus
        ):
            raise _error(contract_name, "validation_before_status", "must be a ValidationStatus or null")
        if not isinstance(self.risk_level, RiskLevel):
            raise _error(contract_name, "risk_level", "must be a RiskLevel")
        for field_name in ("recovery_action", "recommended_action"):
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, RecoveryAction):
                raise _error(contract_name, field_name, "must be a RecoveryAction or null")

        _require_non_negative_int(
            self.attempt_count,
            contract_name=contract_name,
            field_path="attempt_count",
        )
        for field_name in ("matched_count", "applied_count", "successful_application_count"):
            _require_non_negative_int(
                getattr(self, field_name),
                contract_name=contract_name,
                field_path=field_name,
            )
        if not self.successful_application_count <= self.applied_count <= self.matched_count:
            raise _error(
                contract_name,
                "successful_application_count",
                "must satisfy successful_application_count <= applied_count <= matched_count",
            )
        self.duration_seconds = _require_non_negative_number(
            self.duration_seconds,
            contract_name=contract_name,
            field_path="duration_seconds",
            nullable=True,
        )
        if self.exit_code is not None and (isinstance(self.exit_code, bool) or not isinstance(self.exit_code, int)):
            raise _error(contract_name, "exit_code", "must be an integer or null")
        if isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float)):
            raise _error(contract_name, "confidence", "must be a number")
        if not isfinite(float(self.confidence)) or not 0 <= self.confidence <= 1:
            raise _error(contract_name, "confidence", "must be between 0 and 1")

        self.context_tags = _copy_string_list(
            self.context_tags,
            contract_name=contract_name,
            field_path="context_tags",
        )
        for index, context_tag in enumerate(self.context_tags):
            _require_non_empty_string(
                context_tag,
                contract_name=contract_name,
                field_path=f"context_tags[{index}]",
            )
        self.new_run_ids = _copy_string_list(
            self.new_run_ids,
            contract_name=contract_name,
            field_path="new_run_ids",
        )
        self.resolved_failure_codes = _copy_string_list(
            self.resolved_failure_codes,
            contract_name=contract_name,
            field_path="resolved_failure_codes",
        )
        self.remaining_failure_codes = _copy_string_list(
            self.remaining_failure_codes,
            contract_name=contract_name,
            field_path="remaining_failure_codes",
        )
        self.applicability_notes = _copy_string_list(
            self.applicability_notes,
            contract_name=contract_name,
            field_path="applicability_notes",
        )
        self.user_labels = _copy_string_list(
            self.user_labels,
            contract_name=contract_name,
            field_path="user_labels",
        )
        self.preference_scope = _copy_string_list(
            self.preference_scope,
            contract_name=contract_name,
            field_path="preference_scope",
        )
        self.evidence_refs = self._copy_typed_list(self.evidence_refs, EvidenceRef, "evidence_refs")
        self.evidence_facts = deepcopy(
            self._copy_typed_list(
                self.evidence_facts,
                EvidenceFact,
                "evidence_facts",
            )
        )
        self.applicability_conditions = deepcopy(
            self._copy_typed_list(
                self.applicability_conditions,
                EvidenceMatchCondition,
                "applicability_conditions",
            )
        )
        self.parameter_changes = self._copy_typed_list(
            self.parameter_changes,
            ParameterChange,
            "parameter_changes",
        )
        self.target_sources = _copy_string_list(
            self.target_sources,
            contract_name=contract_name,
            field_path="target_sources",
        )
        for index, source in enumerate(self.target_sources):
            _require_non_empty_string(
                source,
                contract_name=contract_name,
                field_path=f"target_sources[{index}]",
            )
        if len(self.target_sources) != len(set(self.target_sources)):
            raise _error(contract_name, "target_sources", "must not contain duplicates")
        if self.recovery_action is RecoveryAction.SKIP_OPTIONAL_SOURCE:
            if not self.target_sources:
                raise _error(
                    contract_name,
                    "target_sources",
                    "must be non-empty when recovery_action is skip_optional_source",
                )
        elif self.target_sources:
            raise _error(
                contract_name,
                "target_sources",
                "must be empty unless recovery_action is skip_optional_source",
            )
        self.approval_record = _require_mapping(
            self.approval_record,
            contract_name=contract_name,
            field_path="approval_record",
        )

        if self.case_type == "technical" and not all(
            (self.anomaly_id, self.anomaly_kind, self.anomaly_fingerprint)
        ):
            raise _error(
                contract_name,
                "anomaly_id",
                "anomaly_id, anomaly_kind, and anomaly_fingerprint are required for technical cases",
            )
        if self.case_type == "preference" and not (
            self.user_feedback or self.user_labels or self.user_satisfied is not None
        ):
            raise _error(
                contract_name,
                "user_feedback",
                "preference cases require user_feedback, user_labels, or user_satisfied",
            )
        if self.outcome == "recovered":
            if not self.verified:
                raise _error(contract_name, "verified", "must be true when outcome is recovered")
            if self.validation_after_status is not ValidationStatus.PASS:
                raise _error(
                    contract_name,
                    "validation_after_status",
                    "must be pass when outcome is recovered",
                )
            if not self.ready_for_read_after:
                raise _error(
                    contract_name,
                    "ready_for_read_after",
                    "must be true when outcome is recovered",
                )

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> ExperienceCase:
        if not isinstance(data, MappingABC):
            raise _error(cls.__name__, "", "must be an object")

        schema_version = data.get("schema_version")
        if schema_version == "find.experience_case.v1":
            for field_name in (
                "root_cause",
                "evidence_facts",
                "applicability_notes",
                "candidate_root_cause",
                "root_cause_code",
                "evidence_match_conditions",
                "match_conditions",
            ):
                if field_name in data:
                    raise _error(cls.__name__, field_name, "is unknown")

            migrated = dict(data)
            confirmed_root_cause = migrated.pop("confirmed_root_cause", None)
            legacy_applicable = _copy_string_list(
                migrated.pop("applicability_conditions", []),
                contract_name=cls.__name__,
                field_path="applicability_conditions",
            )
            legacy_non_applicable = _copy_string_list(
                migrated.pop("non_applicable_conditions", []),
                contract_name=cls.__name__,
                field_path="non_applicable_conditions",
            )
            migrated.update(
                {
                    "schema_version": "find.experience_case.v2",
                    "root_cause": confirmed_root_cause,
                    "evidence_facts": [],
                    "applicability_notes": [
                        *legacy_applicable,
                        *(
                            f"non_applicable: {note}"
                            for note in legacy_non_applicable
                        ),
                    ],
                    "applicability_conditions": [],
                }
            )
            return super().from_dict(migrated)

        if schema_version not in {None, "find.experience_case.v2"}:
            raise _error(
                cls.__name__,
                "schema_version",
                "must equal find.experience_case.v1 or find.experience_case.v2",
            )
        return super().from_dict(data)

    def _copy_typed_list(
        self,
        value: object,
        expected_type: type[object],
        field_path: str,
    ) -> list[object]:
        contract_name = type(self).__name__
        copied = _require_list(value, contract_name=contract_name, field_path=field_path)
        for index, item in enumerate(copied):
            if not isinstance(item, expected_type):
                raise _error(
                    contract_name,
                    f"{field_path}[{index}]",
                    f"must be a {expected_type.__name__}",
                )
        return copied


@dataclass(kw_only=True)
class SupervisorState(JsonContract):
    """Current state of the Find Feedback controller for one root run."""

    supervisor_id: str
    project_id: str | None
    root_run_id: str | None
    status: SupervisorStatus
    state_revision: int
    created_at: datetime
    updated_at: datetime
    heartbeat_at: datetime
    producer: str
    producer_version: str
    process_alive: bool
    cancel_requested: bool
    recovery_attempts: int
    recovery_budget_total: int
    recovery_budget_remaining: int
    awaiting_approval: bool
    gate_evaluated: bool
    allow_read: bool
    gate_reason: str
    terminal: bool
    event_sequence: int
    state_path: str
    schema_version: str = "find.supervisor_state.v1"
    run_context_id: str | None = None
    active_run_id: str | None = None
    latest_progress_snapshot_id: str | None = None
    latest_validation_id: str | None = None
    active_anomaly_id: str | None = None
    active_recovery_decision_id: str | None = None
    experience_case_id: str | None = None
    active_pid: int | None = None
    process_started_at: datetime | None = None
    exit_code: int | None = None
    cancel_requested_at: datetime | None = None
    last_recovery_action: RecoveryAction | None = None
    last_action_attempt_count: int = 0
    pending_approval_decision_id: str | None = None
    gate_validation_id: str | None = None
    terminal_reason: str | None = None
    final_status: SupervisorStatus | None = None
    diagnostic_report_path: str | None = None
    last_error: str | None = None
    recent_events: list[SupervisorEvent] = field(default_factory=list)

    def __post_init__(self) -> None:
        contract_name = type(self).__name__
        for field_name in (
            "supervisor_id",
            "producer",
            "producer_version",
            "gate_reason",
            "state_path",
        ):
            _require_string(getattr(self, field_name), contract_name=contract_name, field_path=field_name)
        if self.project_id is not None:
            _require_non_empty_string(
                self.project_id,
                contract_name=contract_name,
                field_path="project_id",
            )
        for field_name in (
            "root_run_id",
            "active_run_id",
            "run_context_id",
            "latest_progress_snapshot_id",
            "latest_validation_id",
            "active_anomaly_id",
            "active_recovery_decision_id",
            "experience_case_id",
            "pending_approval_decision_id",
            "gate_validation_id",
            "terminal_reason",
            "diagnostic_report_path",
            "last_error",
        ):
            _optional_string(getattr(self, field_name), contract_name=contract_name, field_path=field_name)
        if self.root_run_id is not None:
            _require_non_empty_string(
                self.root_run_id,
                contract_name=contract_name,
                field_path="root_run_id",
            )
        if self.schema_version != "find.supervisor_state.v1":
            raise _error(contract_name, "schema_version", "must equal find.supervisor_state.v1")
        if not isinstance(self.status, SupervisorStatus):
            raise _error(contract_name, "status", "must be a SupervisorStatus")
        if self.final_status is not None and not isinstance(self.final_status, SupervisorStatus):
            raise _error(contract_name, "final_status", "must be a SupervisorStatus or null")
        if self.last_recovery_action is not None and not isinstance(
            self.last_recovery_action, RecoveryAction
        ):
            raise _error(contract_name, "last_recovery_action", "must be a RecoveryAction or null")
        _require_non_negative_int(
            self.last_action_attempt_count,
            contract_name=contract_name,
            field_path="last_action_attempt_count",
        )
        if self.last_recovery_action is None and self.last_action_attempt_count != 0:
            raise _error(
                contract_name,
                "last_action_attempt_count",
                "must be 0 when last_recovery_action is null",
            )
        for field_name in ("created_at", "updated_at", "heartbeat_at"):
            _require_aware_datetime(
                getattr(self, field_name),
                contract_name=contract_name,
                field_path=field_name,
            )
        for field_name in ("process_started_at", "cancel_requested_at"):
            value = getattr(self, field_name)
            if value is not None:
                _require_aware_datetime(value, contract_name=contract_name, field_path=field_name)
        for field_name in (
            "process_alive",
            "cancel_requested",
            "awaiting_approval",
            "gate_evaluated",
            "allow_read",
            "terminal",
        ):
            _require_bool(getattr(self, field_name), contract_name=contract_name, field_path=field_name)
        if self.active_pid is not None:
            if isinstance(self.active_pid, bool) or not isinstance(self.active_pid, int) or self.active_pid <= 0:
                raise _error(contract_name, "active_pid", "must be an integer greater than 0 or null")
        if self.exit_code is not None and (isinstance(self.exit_code, bool) or not isinstance(self.exit_code, int)):
            raise _error(contract_name, "exit_code", "must be an integer or null")
        if self.cancel_requested_at is not None and not self.cancel_requested:
            raise _error(
                contract_name,
                "cancel_requested_at",
                "requires cancel_requested to be true",
            )

        for field_name in (
            "state_revision",
            "recovery_attempts",
            "recovery_budget_total",
            "recovery_budget_remaining",
            "event_sequence",
        ):
            _require_non_negative_int(
                getattr(self, field_name),
                contract_name=contract_name,
                field_path=field_name,
            )
        if self.recovery_budget_remaining > self.recovery_budget_total:
            raise _error(
                contract_name,
                "recovery_budget_remaining",
                "must be less than or equal to recovery_budget_total",
            )
        consumed_budget = self.recovery_budget_total - self.recovery_budget_remaining
        if self.recovery_attempts > consumed_budget:
            raise _error(
                contract_name,
                "recovery_attempts",
                "cannot exceed consumed recovery budget",
            )

        nonterminal_statuses = {
            SupervisorStatus.IDLE,
            SupervisorStatus.PREPARING,
            SupervisorStatus.RUNNING,
            SupervisorStatus.VALIDATING,
            SupervisorStatus.DIAGNOSING,
            SupervisorStatus.DECIDING,
            SupervisorStatus.AWAITING_APPROVAL,
            SupervisorStatus.RECOVERING,
            SupervisorStatus.GATING,
        }
        terminal_statuses = {
            SupervisorStatus.COMPLETED,
            SupervisorStatus.BLOCKED,
            SupervisorStatus.FAILED,
            SupervisorStatus.CANCELLED,
        }
        if self.status in nonterminal_statuses:
            if self.terminal:
                raise _error(contract_name, "terminal", "must be false for a nonterminal status")
            if self.final_status is not None:
                raise _error(contract_name, "final_status", "must be null for a nonterminal status")
        if self.status in terminal_statuses and not self.terminal:
            raise _error(contract_name, "terminal", "must be true for a terminal status")
        run_context_required_statuses = {
            SupervisorStatus.RUNNING,
            SupervisorStatus.VALIDATING,
            SupervisorStatus.DIAGNOSING,
            SupervisorStatus.DECIDING,
            SupervisorStatus.AWAITING_APPROVAL,
            SupervisorStatus.RECOVERING,
            SupervisorStatus.GATING,
            SupervisorStatus.COMPLETED,
            SupervisorStatus.BLOCKED,
        }
        if self.status in run_context_required_statuses and not self.run_context_id:
            raise _error(contract_name, "run_context_id", "is required after preparing")

        root_run_required_statuses = {
            SupervisorStatus.RUNNING,
            SupervisorStatus.VALIDATING,
            SupervisorStatus.DIAGNOSING,
            SupervisorStatus.DECIDING,
            SupervisorStatus.AWAITING_APPROVAL,
            SupervisorStatus.RECOVERING,
            SupervisorStatus.GATING,
            SupervisorStatus.COMPLETED,
            SupervisorStatus.BLOCKED,
        }
        if self.status in root_run_required_statuses and not self.root_run_id:
            raise _error(contract_name, "root_run_id", "is required after the first Find run starts")

        if self.status in {SupervisorStatus.IDLE, SupervisorStatus.PREPARING} and self.process_alive:
            raise _error(contract_name, "process_alive", "must be false while idle or preparing")
        if self.status is SupervisorStatus.RUNNING:
            if not self.active_run_id:
                raise _error(contract_name, "active_run_id", "is required when status is running")
            if not self.process_alive:
                raise _error(contract_name, "process_alive", "must be true when status is running")
            if self.active_pid is None and self.process_started_at is None:
                raise _error(
                    contract_name,
                    "active_pid",
                    "or process_started_at is required when status is running",
                )
        if self.status is SupervisorStatus.VALIDATING:
            if not self.active_run_id:
                raise _error(contract_name, "active_run_id", "is required when status is validating")
            if self.process_alive:
                raise _error(contract_name, "process_alive", "must be false when status is validating")
        if self.status is SupervisorStatus.DIAGNOSING:
            if not (self.latest_progress_snapshot_id or self.latest_validation_id):
                raise _error(
                    contract_name,
                    "latest_progress_snapshot_id",
                    "or latest_validation_id is required when status is diagnosing",
                )
        if self.status is SupervisorStatus.DECIDING and not self.active_anomaly_id:
            raise _error(contract_name, "active_anomaly_id", "is required when status is deciding")
        if self.status is SupervisorStatus.AWAITING_APPROVAL:
            if not self.active_anomaly_id:
                raise _error(contract_name, "active_anomaly_id", "is required when status is awaiting_approval")
            if not self.active_recovery_decision_id:
                raise _error(
                    contract_name,
                    "active_recovery_decision_id",
                    "is required when status is awaiting_approval",
                )
            if not self.pending_approval_decision_id:
                raise _error(
                    contract_name,
                    "pending_approval_decision_id",
                    "is required when status is awaiting_approval",
                )
            if not self.awaiting_approval:
                raise _error(contract_name, "awaiting_approval", "must be true when status is awaiting_approval")
        if self.status is SupervisorStatus.RECOVERING:
            if not self.active_anomaly_id:
                raise _error(contract_name, "active_anomaly_id", "is required when status is recovering")
            if not self.active_recovery_decision_id:
                raise _error(
                    contract_name,
                    "active_recovery_decision_id",
                    "is required when status is recovering",
                )
            if self.awaiting_approval:
                raise _error(contract_name, "awaiting_approval", "must be false when status is recovering")
        if self.status is SupervisorStatus.GATING and not self.latest_validation_id:
            raise _error(contract_name, "latest_validation_id", "is required when status is gating")

        if self.status is SupervisorStatus.COMPLETED:
            if self.process_alive:
                raise _error(contract_name, "process_alive", "must be false when status is completed")
            if not self.gate_evaluated:
                raise _error(contract_name, "gate_evaluated", "must be true when status is completed")
            if not self.allow_read:
                raise _error(contract_name, "allow_read", "must be true when status is completed")
            if self.final_status is not SupervisorStatus.COMPLETED:
                raise _error(contract_name, "final_status", "must be completed when status is completed")
        if self.status is SupervisorStatus.BLOCKED:
            if self.process_alive:
                raise _error(contract_name, "process_alive", "must be false when status is blocked")
            if self.allow_read:
                raise _error(contract_name, "allow_read", "must be false when status is blocked")
            if not self.terminal_reason:
                raise _error(contract_name, "terminal_reason", "is required when status is blocked")
            if self.final_status is not SupervisorStatus.BLOCKED:
                raise _error(contract_name, "final_status", "must be blocked when status is blocked")
        if self.status is SupervisorStatus.FAILED:
            if self.process_alive:
                raise _error(contract_name, "process_alive", "must be false when status is failed")
            if self.allow_read:
                raise _error(contract_name, "allow_read", "must be false when status is failed")
            if not (self.last_error or self.terminal_reason):
                raise _error(contract_name, "last_error", "or terminal_reason is required when status is failed")
            if self.final_status is not SupervisorStatus.FAILED:
                raise _error(contract_name, "final_status", "must be failed when status is failed")
        if self.status is SupervisorStatus.CANCELLED:
            if self.process_alive:
                raise _error(contract_name, "process_alive", "must be false when status is cancelled")
            if not self.cancel_requested:
                raise _error(contract_name, "cancel_requested", "must be true when status is cancelled")
            if self.final_status is not SupervisorStatus.CANCELLED:
                raise _error(contract_name, "final_status", "must be cancelled when status is cancelled")

        if self.allow_read:
            if not self.gate_evaluated:
                raise _error(contract_name, "gate_evaluated", "must be true when allow_read is true")
            if not self.gate_validation_id:
                raise _error(contract_name, "gate_validation_id", "is required when allow_read is true")
        if self.awaiting_approval and not self.pending_approval_decision_id:
            raise _error(
                contract_name,
                "pending_approval_decision_id",
                "is required while awaiting_approval is true",
            )
        if self.terminal and self.process_alive:
            raise _error(contract_name, "process_alive", "must be false when terminal is true")

        self.recent_events = self._copy_events(self.recent_events)
        sequences = [event.sequence for event in self.recent_events]
        if sequences != sorted(sequences):
            raise _error(contract_name, "recent_events", "must be sorted by sequence in ascending order")
        if len(set(sequences)) != len(sequences):
            raise _error(contract_name, "recent_events", "must not contain duplicate sequences")

    def _copy_events(self, value: object) -> list[SupervisorEvent]:
        contract_name = type(self).__name__
        copied = _require_list(value, contract_name=contract_name, field_path="recent_events")
        for index, item in enumerate(copied):
            if not isinstance(item, SupervisorEvent):
                raise _error(
                    contract_name,
                    f"recent_events[{index}]",
                    "must be a SupervisorEvent",
                )
        return copied  # type: ignore[return-value]
