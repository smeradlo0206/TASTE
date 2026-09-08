"""Deterministic ExperienceCase selection for the future local store."""

from __future__ import annotations

import json
from pathlib import Path

from .contracts import (
    ExperienceCase,
    ExperienceQuery,
    RecoveryExperienceQuery,
    ValidationStatus,
)


def _normalized_tags(tags: list[str]) -> set[str]:
    return {tag.strip().lower() for tag in tags}


def select_experience_cases(
    cases: list[ExperienceCase],
    query: ExperienceQuery,
) -> list[ExperienceCase]:
    """Return cases matching fixed V0 filters without mutating the inputs."""

    if not isinstance(cases, list):
        raise TypeError("cases must be a list of ExperienceCase objects")
    for index, case in enumerate(cases):
        if not isinstance(case, ExperienceCase):
            raise TypeError(f"cases[{index}] must be an ExperienceCase")
    if not isinstance(query, ExperienceQuery):
        raise TypeError("query must be an ExperienceQuery")

    required_tags = _normalized_tags(query.required_context_tags)
    selected: list[ExperienceCase] = []
    for case in cases:
        if case.stage != query.stage:
            continue
        if query.project_id is not None and case.project_id not in {
            None,
            query.project_id,
        }:
            continue
        if query.case_types and case.case_type not in query.case_types:
            continue
        if query.outcomes and case.outcome not in query.outcomes:
            continue
        if query.verified_only and not case.verified:
            continue
        if not query.include_deprecated and case.deprecated:
            continue
        if required_tags and not required_tags.issubset(
            _normalized_tags(case.context_tags)
        ):
            continue
        selected.append(case)

    selected.sort(key=lambda case: case.case_id)
    selected.sort(key=lambda case: case.updated_at, reverse=True)
    return selected[: query.limit]


class JsonExperienceStore:
    """Read validated experience cases from one JSON array without writing it."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def _load_cases(self) -> list[ExperienceCase]:
        if not self.path.exists():
            return []

        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("experience store root must be a JSON array")

        cases: list[ExperienceCase] = []
        for index, item in enumerate(payload):
            if not isinstance(item, dict):
                raise ValueError(f"cases[{index}] must be a JSON object")
            try:
                case = ExperienceCase.from_dict(item)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"cases[{index}] is invalid: {exc}") from exc
            cases.append(case)
        return cases

    def search_cases(self, query: ExperienceQuery) -> list[ExperienceCase]:
        cases = self._load_cases()
        return select_experience_cases(cases, query)

    def search_recovery_cases(
        self,
        query: RecoveryExperienceQuery,
    ) -> list[ExperienceCase]:
        """Return verified recovery cases matching fixed recovery filters."""

        if not isinstance(query, RecoveryExperienceQuery):
            raise TypeError("query must be a RecoveryExperienceQuery")

        required_tags = _normalized_tags(query.required_context_tags)
        selected: list[ExperienceCase] = []
        for case in self._load_cases():
            if case.stage != query.stage:
                continue
            if case.verified is not True or case.deprecated is not False:
                continue
            if case.case_type != "technical" or case.outcome != "recovered":
                continue
            if case.validation_after_status is not ValidationStatus.PASS:
                continue
            if case.ready_for_read_after is not True:
                continue
            if case.anomaly_kind != query.anomaly_kind:
                continue
            if case.recovery_action is None:
                continue
            if case.successful_application_count <= 0:
                continue

            if query.project_id is None:
                if case.project_id is not None:
                    continue
            elif case.project_id not in {None, query.project_id}:
                continue

            if query.environment_fingerprint is None:
                if case.environment_fingerprint is not None:
                    continue
            elif case.environment_fingerprint not in {
                None,
                query.environment_fingerprint,
            }:
                continue

            if required_tags and not required_tags.issubset(
                _normalized_tags(case.context_tags)
            ):
                continue
            selected.append(case)

        selected.sort(key=lambda case: case.case_id)
        selected.sort(key=lambda case: case.updated_at, reverse=True)
        selected.sort(key=lambda case: case.confidence, reverse=True)
        selected.sort(
            key=lambda case: case.successful_application_count,
            reverse=True,
        )
        selected.sort(
            key=lambda case: (
                query.environment_fingerprint is not None
                and case.environment_fingerprint == query.environment_fingerprint
            ),
            reverse=True,
        )
        selected.sort(
            key=lambda case: (
                query.project_id is not None
                and case.project_id == query.project_id
            ),
            reverse=True,
        )
        return selected[: query.limit]
