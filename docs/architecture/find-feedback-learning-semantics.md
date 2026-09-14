# Find Feedback Learning Semantics

## 1. Document status and scope

- Status: **normative and authoritative** for the Find Feedback learning loop.
- Effective date: 2026-09-12.
- Scope: component responsibilities and the boundary between observed evidence,
  recovery proposals, validated recovery results, recording, and persistence.
- This document does not reinterpret or overwrite evidence from historical runs.
- When a historical handoff or diagram conflicts with this document, this
  document controls the learning-loop semantics. Current implementation status
  is stated separately from target and future responsibilities below.

This document records both the Stage 0 responsibility freeze and subsequent
pre-implementation semantic decisions. The three foundational evidence contracts
and the three v2 runtime carrier fields now exist. Observer, Validator, and
AnomalyBuilder do not yet produce or propagate `EvidenceFact`, and recording is
not connected to production.

## 2. Normative target data flow

```text
Observer / Validator
    -> AnomalyBuilder -> Anomaly
    -> Controller queries RecoveryExperienceStore
       -> reliable experience found: validate it
       -> no reliable experience: call RecoveryAdvisor
          -> untrusted RecoveryProposal
    -> Controller validates inputs/proposal and creates RecoveryDecision
    -> approval Gate when required
    -> Supervisor temporarily configures accepted evidence rules
       for the recovery run only (future implementation)
    -> Recovery Find
    -> recovery Observer / Validator
    -> Validator PASS and ready_for_read=True
    -> ExperienceRecorder -> ExperienceCase
    -> ExperienceCaseWriter -> Store
```

The temporary-rule step, concrete Recorder, Writer, Store write support, and
production recording connection are target or future behavior. They are not
implemented by this Stage 0 change.

## 3. Component responsibilities

| Component | Frozen target responsibility | Explicit boundary | Current status |
|---|---|---|---|
| Observer | Observe one real run and return structured process/progress facts. | Does not decide or execute recovery and does not invent unobserved facts. | Implemented for current Find observation. |
| Validator | Deterministically validate one bound completed run and return `ValidationResult`. | Does not propose root causes or recovery and does not turn requested evidence into observed evidence. | Implemented for current Find result validation. |
| AnomalyBuilder / Anomaly | Organize only supplied Observer/Validator facts, `EvidenceRef` values, and detection results into the initial anomaly. | Does not read new evidence, call Advisor, execute recovery, or absorb later hypotheses into the initial anomaly. | Builder and contract are implemented. |
| Controller | Receive `Anomaly`, `RunContext`, and `SupervisorState`; query the injected `RecoveryExperienceStore`; safely match verified cases; call the injected `RecoveryAdvisor` only when no reliable case applies; validate the untrusted proposal; produce a budget-, risk-, allowed-action-, and approval-constrained `RecoveryDecision`. | Does not execute recovery, mutate inputs, write Store history, publish Find results, call Observer/Validator/AnomalyBuilder, or bypass the Gate. | Implemented for current contracts and supported recovery decisions. |
| Advisor | Produce an untrusted `RecoveryProposal`. In future it may propose candidate root-cause hypotheses, supporting evidence references, new evidence definitions, evidence collection rules, and a recovery approach. | Does not confirm a root cause, mutate the initial Anomaly, configure Observer/Validator directly, decide or approve recovery, execute recovery, or write Store data. | Proposal production exists; the future evidence/root-cause vocabulary is not present in the contract. |
| Supervisor | Coordinate Observer, Validator, AnomalyBuilder, Controller, and Gate. In future, accept Controller-approved evidence rules, scope them to a recovery run, remove them afterwards, and permit Recorder/Writer only after recovery PASS. | Temporary rules must not affect the initial run, other runs, or persistent defaults. | Core coordination exists. Temporary evidence-rule registration and publication are not implemented. |
| Recorder | Organize only supplied structured contracts from one validated recovery and create one `ExperienceCase`. | Does not query/write Store data, call an LLM, read artifacts, execute recovery, mutate inputs, or reconsider the recovery strategy. | `ExperienceRecorder` Protocol exists; no concrete `FindExperienceRecorder` exists. |
| Writer | Persist a complete `ExperienceCase`; provide idempotency, conflict detection, a bounded cross-process lock, re-read under lock, a same-directory temporary file, and atomic replacement. | Does not create cases, analyze anomalies, modify recovery policy, or change project configuration. A write failure must report that experience was not saved and must not invalidate a successful Find. | Not implemented. |
| Store | Be the final persistent location for validated recovery cases and support query semantics. Future case contracts may carry candidate causes, evidence definitions/rules, actually observed recovery evidence, the executed action, and validation outcome. | The current JSON-array format remains unchanged in Stage 0. Querying and writing remain separate interface responsibilities. | `JsonExperienceStore` is currently read-only and implements existing queries. |

There is no separate `RootCauseMatcher`. Querying and matching recovery
experience are Controller responsibilities. There is no `Curator`,
`ExperienceCurator`, or `RootCauseCurator`: Advisor proposes, Recorder organizes
a validated outcome, and Writer persists the resulting case.

## 4. Observed facts versus proposals

The learning loop uses distinct semantic categories:

1. **Observed evidence** is a value actually produced for the current run by an
   Observer or Validator and represented by existing structured contracts such
   as `EvidenceRef` and `ValidationResult`.
2. **A candidate root cause** is an Advisor hypothesis. It is not confirmed by
   being proposed or by one successful recovery.
3. **A new evidence definition** describes evidence that might be useful to
   observe. It is not an observed value.
4. **An evidence collection rule** describes how a future recovery run might
   collect that evidence. It is not an `EvidenceRef` and not an evidence fact.
5. **An unavailable observation need** records that the desired observation
   cannot currently be implemented. It must remain unavailable rather than be
   replaced by a guessed value.

Advisor must not fabricate values that were not observed in the run. Proposed
definitions or collection rules must never be written into `Anomaly`,
`ValidationResult`, or `ExperienceCase` as though they were measurements from
that run.

`EvidenceFact`, `EvidenceDefinition`, and `EvidenceCollectionRule` are implemented
as foundational data contracts. `ProgressSnapshot`, `ValidationResult`, and
`Anomaly` can carry `EvidenceFact`, but production components are not connected:
Observer and Validator do not yet produce Facts, AnomalyBuilder does not yet
propagate them, and Advisor does not yet produce definitions or rules.

## 5. Source outcome classification

Source outcome counts are mutually exclusive. One source observation contributes
to at most one of `source_limited`, `source_failed`, and `source_ready`, using the
fixed priority:

```text
limited > failed > ready
```

- If `limited=True`, or the observation contains an explicit `http_429` or
  `rate_limited` signal, count it in `source_limited`. It must not also be counted
  in `source_failed` or `source_ready`.
- Otherwise, if `ok=False`, count it in `source_failed`.
- Otherwise, if `ok=True`, count it in `source_ready`.
- A row that satisfies none of these classifications contributes only to the
  total source count.

This is a statistical classification priority only. It does not confirm why a
source was limited. `source_rate_limited` remains a candidate or suspected root
cause and requires Controller validation against actual observed evidence.

## 6. Runtime evidence carrier version boundary

Adding `evidence_facts` to the three runtime evidence carriers requires explicit
v2 schemas:

- `find.progress_snapshot.v2`;
- `find.validation_result.v2`;
- `find.anomaly.v2`.

The v2 forms write `evidence_facts`. When their contract-specific readers receive
v1 input, they migrate it to `evidence_facts=[]`. Migration must not synthesize an
`EvidenceFact` from old `counts`, `actual`, summaries, `EvidenceRef` values, or any
other legacy field. Absence of an observed fact remains absence.

Readers accept only the explicitly supported v1 and v2 forms. Unknown schema
versions are rejected. The migration logic is local to `ProgressSnapshot`,
`ValidationResult`, and `Anomaly`; it does not change `JsonContract` or the
serialization behavior of unrelated contracts.

The v2 carrier contracts and their local v1-to-v2 migrations are implemented.
Production Fact creation and propagation remain unimplemented.

## 7. ExperienceCase v2 root-cause semantics

`find.experience_case.v2` has one long-lived root-cause value and one status:

```text
root_cause: str | None
root_cause_status: unknown | suspected | confirmed
```

- With `unknown`, `root_cause` may be `None`.
- With `suspected`, `root_cause` stores the candidate root cause when one is
  available.
- With `confirmed`, `root_cause` is required.
- A recovery PASS may produce at most `suspected`; it never promotes a cause to
  `confirmed` by itself.

For v1 migration, `confirmed_root_cause` maps to `root_cause` when present.
`confirmed_root_cause` is v1 input compatibility only and must not appear in v2
output. When a v1 case has `root_cause_status=suspected` but no concrete cause
text, migration must not invent `root_cause`; the value remains `None`.

`candidate_root_cause` and `confirmed_root_cause` must not be introduced as two
parallel v2 fields.

## 8. Applicability and EvidenceMatchCondition

`find.experience_case.v2` separates human guidance from machine conditions:

```text
applicability_notes: list[str]
applicability_conditions: list[EvidenceMatchCondition]
```

When reading v1, the old `list[str] applicability_conditions` is moved verbatim
to `applicability_notes`, and structured `applicability_conditions=[]`. Migration
must not parse natural-language notes into machine conditions. Natural-language
notes are never an automatic recovery basis.

One `EvidenceMatchCondition` has exactly these semantic fields:

- `evidence_code`;
- `operator`;
- `baseline_source`;
- `baseline_ref`;
- `baseline_value`.

The first operator set is closed to `eq`, `gte`, and `lte`. The first
`baseline_source` set is closed to `literal`, `run_context`, and `fact`.
Nullability and cross-field requirements among `baseline_ref` and
`baseline_value` remain to be fixed before the contract implementation batch.

The contract describes an inert condition only. It does not execute matching,
authorize recovery, or evaluate evidence. Controller remains the sole owner of
experience query and matching; no `RootCauseMatcher` or synonymous component is
introduced. There is no second `match_conditions` field.

## 9. First root-cause, evidence, and baseline vocabulary

The first deterministic matching vocabulary keeps four semantic layers separate:

- `Anomaly.kind` is a symptom classification. It supports coarse screening only
  and must not match an experience by itself.
- `EvidenceFact` is an actually observed value produced for one real run. It is
  neither a diagnosis nor a requested future observation.
- A baseline defines what an `EvidenceFact` value is compared with. A baseline
  may be a literal, a `RunContext` field, or another observed Fact, but it is not
  itself the observed value or the root cause.
- A root cause is a diagnostic conclusion supported by the required Facts and
  baselines. It is not an alias for `Anomaly.kind` or
  `seconds_without_progress`.

Controller calls the Store and Controller owns deterministic experience
matching. `Anomaly.kind` may reduce the candidate case set, but the required
Fact conditions must also match. Advisor is called only when no reliable
experience matches; its root-cause output remains an untrusted candidate until
Controller validates it. Recovery PASS proves that the recovery method worked
for that bounded run. It does not automatically confirm the root cause.

### 9.1 First Fact codes

These are the only Fact names frozen for the first matching batch. Each maps to
an existing field actually produced through `FileProgressObserver` and
`ProgressSnapshot`; this table does not connect production Fact creation.

| Fact code | Existing source field | Frozen meaning |
|---|---|---|
| `find.raw_title_index_papers` | `counts.raw_title_index_papers` | Pipeline-reported raw title-index volume, not the final candidate count. |
| `find.title_score_input_papers` | `counts.title_score_input_papers` | Pipeline-reported title-score input volume; it does not prove every row received a valid LLM score. |
| `find.evaluated_candidates` | `counts.evaluated_candidates` | Count of deduplicated rows present in the pipeline's evaluated-candidate collection; it does not prove final scoring completeness. |
| `find.llm_scored_candidates` | `counts.llm_scored_candidates` | Count of candidates with the pipeline's valid final LLM abstract-evaluation marker; it is not proof that all candidates were evaluated. |
| `find.source_total` | `source_total` | Number of valid source-status rows observed. |
| `find.source_ready` | `source_ready` | Number classified ready under the mutually exclusive source priority. |
| `find.source_limited` | `source_limited` | Number classified limited under the mutually exclusive source priority. |
| `find.source_failed` | `source_failed` | Number classified failed after excluding limited rows. |
| `find.seconds_without_progress` | `seconds_without_progress` | Elapsed seconds since the Observer's last meaningful progress change. |

There is no separate `abstract_scored_count` Fact. The existing
`abstract_scored_papers` alias must not be renamed to imply that all evaluation
completed. `find.seconds_without_progress` is a timing observation, not a root
cause, and no threshold comparison may turn its code into a diagnosis.

### 9.2 Frozen root cause: source access degraded

The first batch freezes exactly one candidate root cause:

```text
root_cause_code: source_access_degraded
```

Possible corresponding `Anomaly.kind` values include `progress_stalled`,
`process_exited_nonzero`, `empty_recommendations`, `recommendation_shortfall`,
and `source_integrity_blocked`. These values only perform coarse screening and
none is sufficient to select this root cause.

The root cause has two separate minimum matching branches. An ExperienceCase
records one complete branch; it does not depend on undefined OR semantics inside
one `applicability_conditions` list.

Limited branch required Fact codes and conditions:

1. `find.source_total` with `operator: gte`, `baseline_source: literal`, and
   `baseline_value: 1`;
2. `find.source_limited` with `operator: gte`, `baseline_source: literal`, and
   `baseline_value: 1`.

Failed branch required Fact codes and conditions:

1. `find.source_total` with `operator: gte`, `baseline_source: literal`, and
   `baseline_value: 1`;
2. `find.source_failed` with `operator: gte`, `baseline_source: literal`, and
   `baseline_value: 1`.

Optional supporting Fact codes are `find.source_ready`,
`find.raw_title_index_papers`, `find.evaluated_candidates`, and
`find.llm_scored_candidates`. They may describe impact or remaining capacity but
cannot replace either branch's required Facts.

If a required Fact is missing, has another run identity, lacks a real source
contract, or does not satisfy one complete branch, the experience is not matched
and the root cause remains unknown. Controller may then call Advisor. It must not
manufacture a zero, infer a Fact from text, or fall back to `Anomaly.kind` alone.

A `source_access_degraded` match must receive approval before recovery. Aggregate
counts do not identify a safely skippable source, and a match alone does not
authorize source removal, parameter changes, or an unbounded retry.

### 9.3 Candidate volume overload is not yet matchable

`candidate_volume_overload` is not frozen as a deterministic root cause. Existing
fields report raw title volume, several processing-stage counts, and configured
limits, but no existing observed field reliably records both:

- eligible unique candidates before the scoring limit; and
- whether that limit actually truncated the run.

Consequently, equality with a configured limit, a large raw title index, fewer
LLM-scored candidates, or a long `seconds_without_progress` value cannot diagnose
candidate overload. Deterministic matching must return unknown, after which
Controller may then call Advisor. This batch does not create a general root-cause
catalog, a second matching contract, a `RootCauseMatcher`, or a Curator. There is
no `RootCauseMatcher` and no Curator in this flow.

## 10. Initial Anomaly immutability

The initial `Anomaly` is a snapshot of Observer/Validator evidence available at
the time it is built. It cannot contain evidence that has not yet been observed.

Advisor hypotheses, requested evidence, approval metadata, recovery-run facts,
and the outcome of recovery do not rewrite the initial Anomaly. Components must
pass detached contract values and preserve the original anomaly semantics
through Advisor, approval, and recovery processing.

If a recovery run observes additional evidence, that evidence belongs to the
recovery run and a future recorded case. It does not retroactively become part
of the initial observation.

## 11. Meaning of recovery PASS

A recovery is successful only when all of the following hold:

- `ExecutionHandle.process_alive is False`;
- `ExecutionHandle.exit_code == 0`;
- `ValidationResult.status == ValidationStatus.PASS`;
- `ValidationResult.ready_for_read is True`;
- the ValidationResult `run_id` equals the recovery ExecutionHandle `run_id`;
- the normalized `validated_run_dir` equals the normalized recovery `run_dir`.

These conditions prove that the bounded recovery execution succeeded, that its
method may be recorded as a successful recovery experience, and that candidate
causes or evidence rules are worth later validation.

They do **not** by themselves prove that an Advisor root cause is confirmed,
that a stable causal relationship exists between new evidence and the cause, or
that the same recovery may be applied without limits to other projects. After
one successful recovery, Advisor-originated root-cause semantics remain
candidate or suspected, not confirmed.

## 12. Recorder, Writer, and Store boundary

The Framework owns the composition:

```python
experience_case = recorder.record(...)
experience_store.append_case(experience_case)
```

Recorder creates but does not persist an `ExperienceCase`. Writer accepts a
complete case and performs persistence controls. Store is the final persistent
location; it is not an alternate decision engine.

Recorder must not swallow Writer errors. The Framework must catch an expected
write failure, emit a fixed and sanitized message equivalent to “recovery
succeeded, but experience was not saved”, and continue publishing the recovery
Find that already passed validation. A Store failure must not roll back the Find,
start another Find, or claim that experience was saved.

This section is normative target behavior. Stage 0 does not implement Writer,
`append_case`, or Framework production wiring, and does not change the current
`JsonExperienceStore` JSON-array format or read-only behavior.

## 13. Current implementation status

### Implemented

- Existing contracts including `Anomaly`, `RecoveryProposal`,
  `RecoveryDecision`, `ExperienceCase`, and validation/process contracts.
- Foundational `EvidenceFact`, `EvidenceDefinition`, and
  `EvidenceCollectionRule` contracts, including public exports and contract
  serialization tests. They are not connected to production components.
- `ProgressSnapshot`, `ValidationResult`, and `Anomaly` v2 can carry validated,
  detached `EvidenceFact` values and locally migrate valid v1 payloads to empty
  Fact lists.
- Observer, Validator, AnomalyBuilder, Controller, Advisor, approval Gate, and
  Supervisor behavior for the currently supported fields and recovery flow.
- Read-only `ExperienceStore` and `RecoveryExperienceStore` query interfaces.
- `JsonExperienceStore.search_cases()` and `search_recovery_cases()`.
- The `ExperienceRecorder` Protocol connects existing contracts; there is no
  concrete Recorder implementation.
- Read-only Supervisor access to detached terminal validation and anomaly
  evidence in the current working tree.

### Partially implemented

- Supervisor coordination exists, but has no temporary evidence-rule registry,
  recovery-only rule publication, or rule cleanup lifecycle.
- Advisor can produce the current `RecoveryProposal`, but the proposal contract
  cannot yet express the future root-cause/evidence-rule model described here.
- The Store can query recovery cases but cannot write them.
- Writer and Store write support are not implemented.

### Not implemented

- Observer and Validator production of `EvidenceFact` and AnomalyBuilder
  propagation of upstream Facts.
- `EvidenceMatchCondition` and `ExperienceCase` v2.
- Advisor extensions for new root-cause hypotheses, evidence definitions, or
  evidence collection rules.
- Temporary evidence-rule injection into a recovery run.
- Concrete `FindExperienceRecorder`.
- `ExperienceCaseWriter` and `JsonExperienceStore.append_case()`.
- Recorder/Writer production-loop wiring and end-to-end learning-loop tests.

## 14. Explicit non-goals for this semantic-freeze batch

- No `RootCauseMatcher` or synonymous matching component.
- No `Curator`, `ExperienceCurator`, `RootCauseCurator`, or synonym.
- No new or changed production data contract.
- No production wiring for `EvidenceFact`, `EvidenceDefinition`, or
  `EvidenceCollectionRule`.
- No temporary-rule registration or injection behavior.
- No concrete Recorder implementation.
- No Writer or Store write method.
- No production recovery-flow or publication change.
- No Store format or schema migration implementation in this batch.
- No claim that future target capability is already implemented.
