"""Immutable evidence at the independently checked compilation boundaries.

This module records checker *claims*, not semantic proofs. Hash validation establishes
that the artifacts and the recorded dependency graph have not changed. The ``method``
and ``coverage`` fields identify the independent checker and the property it actually
checked; neither a content hash nor successful serialization substitutes for that work.

Artifacts and records live in a content-addressed object store. The manifest is the
only replaceable file. Repeating a stage instance invalidates its old record and every
dependent record. Distinct ``iteration`` values represent separate instances, such as
syndrome rounds or a cooling/scheduling retry, without erasing their earlier evidence.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence

SCHEMA = "qccd.compilation.evidence.v1"
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
CHECK_STATUSES = frozenset(
    {"passed", "failed", "partial", "unsupported", "not_run", "blocked", "not_applicable"}
)


@dataclass(frozen=True)
class StageContract:
    id: str
    name: str
    obligation: str
    permitted_changes: tuple[str, ...]
    conditional: bool = False


STAGES = (
    StageContract("circuit_normalization", "Circuit Normalization",
                  "Preserve source circuit semantics in the supported logical basis.",
                  ("logical gate decomposition", "stable source-operation identifiers")),
    StageContract("dependency_analysis", "Dependency Analysis",
                  "Preserve all quantum, classical, reset, and barrier dependencies.",
                  ("dependency annotations",)),
    StageContract("initial_placement", "Initial Placement",
                  "Establish a complete legal qubit-to-ion mapping and initial configuration.",
                  ("initial mapping", "initial occupancy and chain order")),
    StageContract("interaction_placement", "Interaction Placement",
                  "Assign each interaction to a capable site with sufficient capacity.",
                  ("execution-site annotations", "required-operand annotations")),
    StageContract("transport_synthesis", "Transport Synthesis",
                  "Preserve ion identity and realize interactions through legal physical transitions.",
                  ("movement primitives", "splits and merges", "exchanges", "collective shifts")),
    StageContract("native_gate_lowering", "Native Gate Lowering",
                  "Preserve ideal logical semantics, operands, conditions, angles, and frames.",
                  ("native gate decompositions", "frame updates")),
    StageContract("broadcast_packing", "Broadcast Packing",
                  "Group compatible commands without changing their operations or state transitions.",
                  ("compatible control-cycle grouping",)),
    StageContract("operating_point_binding", "Operating-Point Binding",
                  "Bind commands to implementations supported by the fixed physical model.",
                  ("operating points", "durations", "heating and resource annotations")),
    StageContract("event_scheduling", "Event Scheduling",
                  "Assign times respecting durations, dependencies, resources, and ion trajectories.",
                  ("event start times", "legal overlap")),
    StageContract("thermal_analysis", "Thermal Analysis",
                  "Replay heating and cooling and check thermal budgets at required operations.",
                  ("thermal trace", "thermal acceptance diagnostics")),
    StageContract("cooling_insertion", "Cooling Insertion",
                  "Insert permitted cooling while preserving existing operations and ideal behavior.",
                  ("cooling commands", "cooling dependencies"), conditional=True),
    StageContract("artifact_validation", "Artifact Validation",
                  "Read back the executable and validate its source-to-execution evidence chain.",
                  ("serialization", "source maps", "linked stage certificates")),
)
STAGE_CONTRACTS = MappingProxyType({stage.id: stage for stage in STAGES})
STAGE_IDS = tuple(STAGE_CONTRACTS)


@dataclass(frozen=True)
class StageCheck:
    """An externally computed verdict with explicit verification scope.

    A passed claim must name a method and coverage and have no known limitations.
    Claims lacking that evidence are recorded as partial, never promoted to passed.
    """

    status: str
    method: str
    coverage: tuple[str, ...] = ()
    messages: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()


class StageIntegrityError(ValueError):
    """The saved evidence or its dependency graph is missing or inconsistent."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _digest(value: Any) -> str:
    return sha256(_canonical(value)).hexdigest()


def _snapshot(value: Any) -> Any:
    # A detached JSON value also rejects unsupported/nonfinite values before writing.
    return json.loads(_canonical(value))


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, (tuple, list)) or any(not isinstance(x, str) for x in value):
        raise ValueError("Checker coverage and diagnostics must be strings or lists of strings")
    return [x for x in value if x.strip()]


def _check(value: StageCheck | Mapping[str, Any], stage_id: str) -> dict[str, Any]:
    raw = asdict(value) if isinstance(value, StageCheck) else dict(value)
    status = raw.get("status")
    if status not in CHECK_STATUSES:
        raise ValueError(f"Unknown checker status: {status!r}")
    method = raw.get("method", "")
    if not isinstance(method, str):
        raise ValueError("Checker method must be a string")
    coverage = _strings(raw.get("coverage", ()))
    messages = _strings(raw.get("messages", raw.get("diagnostics", ())))
    limitations = _strings(raw.get("limitations", ()))
    if status == "not_applicable" and not STAGE_CONTRACTS[stage_id].conditional:
        raise ValueError(f"{stage_id} is not a conditional stage")
    if status in {"passed", "not_applicable"}:
        if not method.strip() or not coverage or limitations:
            status = "partial"
            messages.append("Claim is partial: a named checker, explicit coverage, and no "
                            "known limitations are required for acceptance.")
    return {"status": status, "method": method, "coverage": coverage,
            "messages": messages, "limitations": limitations}


def _instance(stage_id: str, iteration: Mapping[str, Any] | None) -> str:
    return stage_id if iteration is None else stage_id + ":" + _digest(iteration)


def _invalidated(instance: str, active: Mapping[str, str],
                 records: Mapping[str, dict[str, Any]]) -> set[str]:
    old = active.get(instance)
    invalid = {old} if old is not None else set()
    while True:
        descendants = {ref for ref in active.values()
                       if any(parent in invalid for parent in records[ref]["predecessors"])}
        expanded = invalid | descendants
        if expanded == invalid:
            return invalid
        invalid = expanded


def _summary(active: Mapping[str, str], records: Mapping[str, dict[str, Any]],
             conditional: Mapping[str, str], required: Sequence[str]) -> dict[str, Any]:
    present = {records[ref]["stage_id"] for ref in active.values()}
    missing = [stage for stage in STAGE_IDS if stage not in present and stage not in conditional]
    scope_missing = [stage for stage in required if stage not in present and stage not in conditional]
    checks = [records[ref]["check"]["status"] for ref in active.values()]

    def status(absent: Sequence[str]) -> str:
        if "failed" in checks:
            return "failed"
        if absent or any(s in {"unsupported", "not_run", "blocked"} for s in checks):
            return "incomplete"
        if "partial" in checks:
            return "partial"
        return "passed"

    return {"status": status(missing), "scope_status": status(scope_missing),
            "missing_stages": missing, "missing_required_stages": scope_missing,
            "full_pipeline": not missing}


class StageRecorder:
    """Record independent checker results without mutating previous evidence.

    ``context`` should contain the fixed architecture, hardware rules, physical model,
    source/compiler identity, and policy values (or their content digests). Its exact
    immutable snapshot is bound into every record. Callers choose the content; this
    generic store does not infer missing model or rule inputs.
    """

    def __init__(self, root: str | Path, context: Mapping[str, Any], *,
                 required_stage_ids: Sequence[str] | None = None):
        self.root = Path(root)
        self.objects = self.root / "objects"
        self.objects.mkdir(parents=True, exist_ok=True)
        self._records: dict[str, dict[str, Any]] = {}
        self._order: list[str] = []
        self._active: dict[str, str] = {}
        self._required = tuple(STAGE_IDS if required_stage_ids is None else required_stage_ids)
        _validate_required(self._required)
        self.context_sha256 = self._put(dict(context))

    def _put(self, value: Any) -> str:
        payload = _canonical(value)
        digest = sha256(payload).hexdigest()
        path = self.objects / (digest + ".json")
        try:
            with path.open("xb") as handle:
                handle.write(payload)
        except FileExistsError:
            if path.read_bytes() != payload:
                raise StageIntegrityError(f"Immutable object was changed: {digest}") from None
        return digest

    def record(self, stage_id: str, input_artifact: Any, output_artifact: Any,
               witness: Any, check: StageCheck | Mapping[str, Any], *,
               iteration: Mapping[str, Any] | None = None,
               predecessors: Sequence[str] | None = None) -> dict[str, Any]:
        if stage_id not in STAGE_CONTRACTS:
            raise ValueError(f"Unknown compilation stage: {stage_id}")
        if iteration is not None and not isinstance(iteration, Mapping):
            raise ValueError("iteration must be a JSON mapping or None")
        iteration = _snapshot(dict(iteration)) if iteration is not None else None
        instance = _instance(stage_id, iteration)
        invalid = _invalidated(instance, self._active, self._records)
        remaining = {key: ref for key, ref in self._active.items() if ref not in invalid}
        if predecessors is None:
            predecessors = [ref for ref in reversed(self._order) if ref in remaining.values()][:1]
        parents = list(predecessors)
        if len(set(parents)) != len(parents) or any(ref not in remaining.values() for ref in parents):
            raise ValueError("Predecessors must be distinct currently active record digests")
        body = {
            "schema": SCHEMA, "sequence": len(self._order),
            "previous_record": self._order[-1] if self._order else None,
            "context_sha256": self.context_sha256,
            "stage_id": stage_id, "name": STAGE_CONTRACTS[stage_id].name,
            "instance": instance, "iteration": iteration, "predecessors": parents,
            "invalidated": sorted(invalid),
            "input_sha256": self._put(input_artifact),
            "output_sha256": self._put(output_artifact),
            "witness_sha256": self._put(witness), "check": _check(check, stage_id),
        }
        digest = self._put(body)
        self._records[digest] = body
        self._order.append(digest)
        self._active = remaining
        self._active[instance] = digest
        # Return a detached copy: caller edits cannot alter recorder state.
        return {**_snapshot(body), "sha256": digest}

    def finish(self, manifest_name: str = "manifest.json", *,
               conditional_stages: Mapping[str, str] | None = None) -> dict[str, Any]:
        """Save a manifest; only explicitly conditional stages can be omitted.

        ``conditional_stages={"cooling_insertion": "thermal replay is safe"}`` is an
        explicit declaration by the pipeline controller. Its justification should be
        supported by the recorded thermal checker. A partial required-stage scope may
        have ``scope_status='passed'`` but can never make ``status='passed'`` for an
        incomplete pipeline.
        """
        if Path(manifest_name).name != manifest_name or not manifest_name.endswith(".json"):
            raise ValueError("manifest_name must be a JSON file name within the evidence directory")
        conditional = dict(conditional_stages or {})
        _validate_conditional(conditional)
        body = {
            "schema": SCHEMA, "context_sha256": self.context_sha256,
            "contracts_sha256": self._put([asdict(stage) for stage in STAGES]),
            "records": self._order.copy(), "active": self._active.copy(),
            "required_stage_ids": list(self._required), "conditional_stages": conditional,
            **_summary(self._active, self._records, conditional, self._required),
            "integrity_scope": "Hashes establish artifact integrity, not semantic correctness.",
        }
        manifest = {**body, "manifest_sha256": _digest(body)}
        target = self.root / manifest_name
        with tempfile.NamedTemporaryFile(dir=self.root, suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(_canonical(manifest))
        try:
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        verify_manifest(target)
        return _snapshot(manifest)


def _validate_required(required: Sequence[str]) -> None:
    if len(set(required)) != len(required) or any(stage not in STAGE_CONTRACTS for stage in required):
        raise ValueError("required_stage_ids must contain distinct known stage IDs")


def _validate_conditional(conditional: Mapping[str, str]) -> None:
    for stage, reason in conditional.items():
        if stage not in STAGE_CONTRACTS or not STAGE_CONTRACTS[stage].conditional:
            raise ValueError(f"Cannot omit mandatory stage: {stage}")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("Conditional stage omission requires an explicit reason")


def _read_json(path: Path) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise StageIntegrityError(f"Duplicate JSON key in {path.name}: {key}")
            result[key] = value
        return result

    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs,
                          parse_constant=lambda value: (_ for _ in ()).throw(
                              StageIntegrityError(f"Nonfinite JSON value: {value}")))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StageIntegrityError(f"Cannot read evidence file {path.name}: {exc}") from exc


def verify_manifest(path: str | Path, *, expected_context: Mapping[str, Any] | None = None,
                    expected_manifest_sha256: str | None = None) -> dict[str, Any]:
    """Check saved integrity and return the *reported* checker status.

    This does not rerun semantic checkers. ``expected_manifest_sha256`` anchors the
    manifest to a trusted external digest; without one, coordinated replacement of
    an entire evidence bundle cannot be detected by a self-contained hash chain.
    """
    path = Path(path)
    return verify_evidence(
        _read_json(path),
        lambda ref: _read_json(path.parent / "objects" / (ref + ".json")),
        expected_context=expected_context, expected_manifest_sha256=expected_manifest_sha256,
    )


def verify_evidence(manifest: Mapping[str, Any], object_loader: Callable[[str], Any], *,
                    expected_context: Mapping[str, Any] | None = None,
                    expected_manifest_sha256: str | None = None) -> dict[str, Any]:
    """Validate the same evidence from an immutable artifact store or memory.

    ``object_loader`` returns a parsed JSON snapshot for a requested SHA-256 digest.
    This function validates every returned snapshot's digest itself, as well as the
    complete record chain, context, active dependencies, and checker-status summary.
    The loader must preserve JSON number types; workspace-normalized artifacts should
    retain the original snapshot bytes rather than normalizing the snapshot itself.
    Like :func:`verify_manifest`, this does not rerun semantic checkers.
    """
    try:
        body = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
        digest = _digest(body)
        if manifest.get("manifest_sha256") != digest:
            raise StageIntegrityError("Manifest digest mismatch")
        if expected_manifest_sha256 is not None and expected_manifest_sha256 != digest:
            raise StageIntegrityError("Manifest differs from its trusted external digest")
        if manifest["schema"] != SCHEMA:
            raise StageIntegrityError("Unsupported evidence schema")

        def read_object(ref: str) -> Any:
            if not isinstance(ref, str) or not _DIGEST.fullmatch(ref):
                raise StageIntegrityError("Invalid object digest")
            value = object_loader(ref)
            if _digest(value) != ref:
                raise StageIntegrityError(f"Object digest mismatch: {ref}")
            return value

        context = read_object(manifest["context_sha256"])
        if expected_context is not None and _digest(expected_context) != _digest(context):
            raise StageIntegrityError("Evidence is bound to a different compilation context")
        if read_object(manifest["contracts_sha256"]) != _snapshot([asdict(stage) for stage in STAGES]):
            raise StageIntegrityError("Stage contracts differ from the supported registry")
        records: dict[str, dict[str, Any]] = {}
        active: dict[str, str] = {}
        previous = None
        for sequence, ref in enumerate(manifest["records"]):
            if ref in records:
                raise StageIntegrityError("Duplicate record in manifest")
            record = read_object(ref)
            stage_id = record["stage_id"]
            if stage_id not in STAGE_CONTRACTS:
                raise StageIntegrityError(f"Unknown stage: {stage_id}")
            if (record["schema"] != SCHEMA or record["sequence"] != sequence or
                    record["previous_record"] != previous or
                    record["context_sha256"] != manifest["context_sha256"] or
                    record["name"] != STAGE_CONTRACTS[stage_id].name):
                raise StageIntegrityError("Record chain or context mismatch")
            instance = _instance(stage_id, record["iteration"])
            if record["instance"] != instance:
                raise StageIntegrityError("Stage instance mismatch")
            invalid = _invalidated(instance, active, records)
            if record["invalidated"] != sorted(invalid):
                raise StageIntegrityError("Incorrect downstream invalidation")
            active = {key: value for key, value in active.items() if value not in invalid}
            parents = record["predecessors"]
            if (len(set(parents)) != len(parents) or
                    any(parent not in active.values() for parent in parents)):
                raise StageIntegrityError("Missing, stale, or future predecessor")
            for key in ("input_sha256", "output_sha256", "witness_sha256"):
                read_object(record[key])
            if record["check"] != _check(record["check"], stage_id):
                raise StageIntegrityError("Checker claim lacks its required method or coverage")
            records[ref] = record
            active[instance] = ref
            previous = ref
        if active != manifest["active"]:
            raise StageIntegrityError("Active evidence differs from dependency replay")
        conditional = manifest["conditional_stages"]
        required = manifest["required_stage_ids"]
        _validate_conditional(conditional)
        _validate_required(required)
        summary = _summary(active, records, conditional, required)
        if any(manifest.get(key) != value for key, value in summary.items()):
            raise StageIntegrityError("Reported compilation status differs from checker evidence")
        return {"integrity": "passed", "reported_status": summary["status"], **summary,
                "context_sha256": manifest["context_sha256"], "manifest_sha256": digest,
                "record_count": len(records), "active_record_count": len(active),
                "semantic_checkers_rerun": False}
    except StageIntegrityError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise StageIntegrityError(f"Malformed compilation evidence: {exc}") from exc
