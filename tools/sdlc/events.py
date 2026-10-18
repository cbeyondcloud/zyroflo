"""The per-run audit trail — T446a: what happened, when, in a chain nobody can edit.

`.sdlc/runs/<run-id>/events.jsonl` is append-only and hash-chained with the SAME
`entry_hash` the approval ledger uses: editing, inserting or removing a line
breaks every hash after it, and `verify` names where. It is committed with the
run folder, so git is the transport and CI's append-only walk covers it.

**It is written by the code paths that already ACT, never by a second collection
path** - `state.create` (`run_opened`), `state.advance` (`loop_back`,
`gate_waiting`, `run_closed`) and `approve.record` (`gate_decided`) - so it is
the account of the events, not a parallel one that can disagree with them.
Gate decisions stay in `approvals.jsonl`; an event POINTS at the decision by its
entry hash instead of copying it.

**No free text reaches an event.** A note, a prompt, a model's answer or a
command line can carry anything - a key a runner echoed, a customer's data - so
events carry ids, names, statuses, timings and hashes only. Every core field is a
value this module chose, never one a caller typed. Declared extension fields
(`ext`, Z1) are caller-supplied but shape-checked against their declaration;
the framework that registers them owns what it wires in (see the Z1 block).

T446b adds `step_started`/`step_finished` (timings, tokens, cost) from the
driver; T446c aggregates and exports. This row is the record they write into.
"""
from __future__ import annotations

import json
import math
import re
import sys
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.sdlc import state as ledger  # noqa: E402

SCHEMA_VERSION = 1
EVENTS = "events.jsonl"

RUN_OPENED, STEP_STARTED, STEP_FINISHED = "run_opened", "step_started", "step_finished"
GATE_WAITING, GATE_DECIDED = "gate_waiting", "gate_decided"
LOOP_BACK, RUN_CLOSED = "loop_back", "run_closed"
#: Every event type, in lifecycle order - the schema's enum and the one list a
#: writer may choose from.
TYPES = (RUN_OPENED, STEP_STARTED, STEP_FINISHED, GATE_WAITING, GATE_DECIDED,
         LOOP_BACK, RUN_CLOSED)

#: The fields an event may carry beyond the common ones, per type. A writer
#: passing anything else is refused: the schema is the contract a dashboard
#: reads, and a field that appears unannounced is a column nobody documented.
FIELDS = {
    RUN_OPENED: ("kind", "project", "owner", "criticality"),
    STEP_STARTED: ("attempt", "actor"),
    STEP_FINISHED: ("attempt", "started_at", "ended_at", "duration_ms", "actor",
                    "runner", "provider", "model", "protocol", "tokens", "cost",
                    "exit_code", "outcome", "artifacts", "forced"),
    GATE_WAITING: ("gate", "hard"),
    GATE_DECIDED: ("gate", "decision", "approver", "ledger_hash", "waited_ms",
                   "forced"),
    LOOP_BACK: ("from", "to"),
    RUN_CLOSED: ("status",),
}
#: On every event.
COMMON = ("schema_version", "run_id", "seq", "at", "event", "step", "step_name",
          "prev", "hash")

#: Every field's JSON type - the schema (`schema.py`) is DERIVED from this, so a
#: field added above without a type here fails the schema build by name.
#: `null` where a value can honestly be unknown: an unmeasured number is null
#: with a reason elsewhere, never 0 (the metrics rule).
FIELD_TYPES = {
    "schema_version": "integer", "run_id": "string", "seq": "integer",
    "at": "string", "event": "string", "step": "string", "step_name": "string",
    "prev": "string", "hash": "string",
    "kind": "string", "project": "string", "owner": "string",
    "criticality": "string", "attempt": "integer", "started_at": "string",
    "ended_at": "string", "duration_ms": ["integer", "null"], "actor": "string",
    "runner": ["string", "null"], "provider": ["string", "null"],
    "model": ["string", "null"], "protocol": ["string", "null"],
    "tokens": "object", "cost": "object", "exit_code": ["integer", "null"],
    "outcome": "string", "artifacts": "object", "forced": "boolean",
    "gate": "string", "hard": ["boolean", "null"], "decision": "string",
    "approver": "string", "ledger_hash": "string",
    "waited_ms": ["integer", "null"], "from": "string", "to": "string",
    "status": "string",
    "ext": "object",
}


class EventError(RuntimeError):
    """An event that cannot be written as the schema says - refused, never
    written half-right into a record CI treats as append-only."""


# ── Z1: declared extension fields, for frameworks built on the harness ───────
#
# A framework that drives the harness may need a few more ids on each event
# (which team, agent and version, tier, policy version). They go in an optional
# `ext` block, `{"<namespace>": {"<field>": value}}`, under these rules:
#
# * DECLARED first (`register_extension`): an undeclared namespace or field is
#   refused like an undeclared core field; a different re-declaration is refused.
# * SHAPE-CHECKED values: strings are id-shaped (letters, digits, ._@:/+=-, no
#   spaces, max 128), numbers are finite and within +/-2**53. The shape keeps
#   prose out; it cannot recognise a secret that has no spaces. So a field may
#   carry its own `pattern` or `enum`, and the REGISTERING FRAMEWORK is
#   responsible for what it wires into each field - this module refuses
#   anything that does not fit the declaration.
# * Validated, then deep-copied, BEFORE anything is written (T465): a refused
#   event writes nothing, and a caller changing its dict afterwards changes
#   nothing.
# * Part of the entry, so the chain hash covers it.
# * Optional: an event without `ext` is byte-for-byte what it was before; the
#   harness's own readers ignore the block. `set_default_ext` lets a framework
#   supply its ids once per process, so events the harness writes itself
#   (`state.create`, `state.advance`, `approve.record`, drive) carry them too.
#
# Where it surfaces: NDJSON exports and the SQLite `events.payload` JSON keep it;
# CSV exports and the typed SQLite columns do not.

#: Registered namespaces -> {field: {"type": ..., optional "pattern", "enum"}}.
EXTENSIONS: dict[str, dict[str, dict]] = {}
#: Optional on every event.
OPTIONAL = ("ext",)
EXT_TYPES = ("string", "integer", "boolean", "number", "string[]")
_NAMESPACE = re.compile(r"[a-z][a-z0-9_-]{0,31}")
_FIELD = re.compile(r"[a-z][a-z0-9_]{0,63}")
#: An id, name, version or hash: no spaces, no prose (`fullmatch`, so no newline).
_ID_VALUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@:/+=-]{0,127}")
_MAX_LIST = 100
_MAX_INT = 2 ** 53
_MAX_EXT_BYTES = 4096
_REGISTRY_LOCK = threading.Lock()
_default_ext: Callable[[str, str], dict | None] | None = None


def register_extension(namespace: str, fields: dict) -> None:
    """Declare an extension namespace and its fields. A field is a type name
    (one of EXT_TYPES) or a mapping {"type": ..., "pattern": ..., "enum": [...]}.
    Registering the same declaration again is a no-op; a different one is
    refused - two writers disagreeing about a field is the undocumented column
    this module exists to prevent."""
    if not isinstance(namespace, str) or not _NAMESPACE.fullmatch(namespace):
        raise EventError(f"extension namespace {namespace!r} must be lowercase letters, "
                         f"digits, '-' or '_' (max 32)")
    if not isinstance(fields, dict) or not fields:
        raise EventError(f"extension {namespace!r} must declare at least one field")
    specs: dict[str, dict] = {}
    for name, spec in dict(fields).items():
        if not isinstance(name, str) or not _FIELD.fullmatch(name):
            raise EventError(f"extension field {namespace}.{name!r} must be lowercase "
                             f"letters, digits or '_' (max 64)")
        specs[name] = _field_spec(f"{namespace}.{name}", spec)
    with _REGISTRY_LOCK:
        if namespace in EXTENSIONS and EXTENSIONS[namespace] != specs:
            raise EventError(f"extension {namespace!r} is already registered with "
                             f"different fields - one declaration per namespace")
        EXTENSIONS[namespace] = specs


def set_default_ext(provider: Callable[[str, str], dict | None] | None) -> None:
    """Supply `ext` for every event this process writes: `provider(event,
    run_id)` returns an ext block (or None). An explicit `ext=` on `append`
    overrides it field by field. None clears it."""
    global _default_ext
    if provider is not None and not callable(provider):
        raise EventError("set_default_ext takes a callable(event, run_id) or None")
    _default_ext = provider


def _field_spec(where: str, spec: object) -> dict:
    if isinstance(spec, str):
        spec = {"type": spec}
    if not isinstance(spec, dict):
        raise EventError(f"extension field {where} must be a type name or a mapping")
    if unknown := sorted(set(spec) - {"type", "pattern", "enum"}):
        raise EventError(f"extension field {where}: unknown keys {', '.join(unknown)}")
    kind = spec.get("type")
    if kind not in EXT_TYPES:
        raise EventError(f"extension field {where} has type {kind!r} - "
                         f"one of {', '.join(EXT_TYPES)}")
    out: dict = {"type": kind}
    if "pattern" in spec:
        pattern = spec["pattern"]
        if kind not in ("string", "string[]") or not isinstance(pattern, str):
            raise EventError(f"extension field {where}: a pattern needs a string type")
        try:
            re.compile(pattern)
        except re.error as error:
            raise EventError(f"extension field {where}: pattern does not compile "
                             f"({error})") from error
        out["pattern"] = pattern
    if "enum" in spec:
        allowed = spec["enum"]
        if not isinstance(allowed, list) or not allowed:
            raise EventError(f"extension field {where}: enum must be a non-empty list")
        scalar = {"type": "string" if kind == "string[]" else kind}
        for value in allowed:
            _check_value(where, scalar, value)
        out["enum"] = list(allowed)
    return out


def _resolve_ext(event: str, run_id: str, ext: object) -> dict | None:
    """The validated, deep-copied ext block for one event, or None for no block."""
    if ext is not None and not isinstance(ext, dict):
        raise EventError("ext must be a mapping of namespace -> fields")
    merged: dict = {}
    if _default_ext is not None:
        base = _default_ext(event, run_id)
        if base is not None and not isinstance(base, dict):
            raise EventError("the default ext provider must return a mapping or None")
        for namespace, values in (base or {}).items():
            merged[namespace] = dict(values) if isinstance(values, dict) else values
    for namespace, values in (ext or {}).items():
        if isinstance(merged.get(namespace), dict) and isinstance(values, dict):
            merged[namespace] = {**merged[namespace], **values}
        else:
            merged[namespace] = values
    if not merged:
        return None
    _check_ext(merged)
    text = json.dumps(merged, ensure_ascii=False, sort_keys=True, allow_nan=False)
    if len(text.encode("utf-8")) > _MAX_EXT_BYTES:
        raise EventError(f"ext is {len(text.encode('utf-8'))} bytes - at most "
                         f"{_MAX_EXT_BYTES}")
    return json.loads(text)  # a deep, plain copy: later changes to the caller's dict do nothing


def _check_ext(ext: dict) -> None:
    for namespace, values in ext.items():
        declared = EXTENSIONS.get(namespace)
        if declared is None:
            raise EventError(f"ext {namespace!r} is not a registered extension - "
                             f"register_extension() it first")
        if not isinstance(values, dict):
            raise EventError(f"ext {namespace!r} must be a mapping of field -> value")
        for name, value in values.items():
            spec = declared.get(name)
            if spec is None:
                raise EventError(f"ext {namespace}.{name} is not declared - its fields "
                                 f"are {', '.join(declared) or 'none'}")
            _check_value(f"{namespace}.{name}", spec, value)


def _check_value(where: str, spec: dict, value: object) -> None:
    kind = spec["type"]
    if kind == "string[]":
        if not isinstance(value, list) or len(value) > _MAX_LIST:
            raise EventError(f"ext {where} must be a list of at most {_MAX_LIST} ids")
        for item in value:
            _check_value(where, {**spec, "type": "string"}, item)
        return
    if kind == "string":
        if not isinstance(value, str) or not _ID_VALUE.fullmatch(value):
            raise EventError(f"ext {where}: {value!r} is not an id (letters, digits and "
                             f"._@:/+=- only, no spaces, max 128) - events carry no free text")
        if "pattern" in spec and not re.fullmatch(spec["pattern"], value):
            raise EventError(f"ext {where}: {value!r} does not match the field's pattern")
    elif kind == "integer":
        if not isinstance(value, int) or isinstance(value, bool) or abs(value) > _MAX_INT:
            raise EventError(f"ext {where} must be an integer within +/-2**53")
    elif kind == "number":
        if (not isinstance(value, (int, float)) or isinstance(value, bool)
                or not math.isfinite(value) or abs(value) > _MAX_INT):
            raise EventError(f"ext {where} must be a finite number within +/-2**53")
    elif kind == "boolean" and not isinstance(value, bool):
        raise EventError(f"ext {where} must be true or false")
    if "enum" in spec and value not in spec["enum"]:
        raise EventError(f"ext {where}: {value!r} is not one of {spec['enum']}")


def events_path(target: Path, run_id: str) -> Path:
    return ledger.run_dir(target, run_id) / EVENTS


def _hash(entry: dict) -> str:
    # The approval ledger's own function, imported at CALL time: `approve`
    # writes `gate_decided` through this module, so a module-level import
    # would be a cycle. One implementation of the chain, not two.
    from tools.sdlc import approve
    return approve.entry_hash(entry)


def read(target: Path, run_id: str) -> list[dict]:
    """Every event in file order; an unreadable line is a marker, never skipped."""
    path = events_path(target, run_id)
    if not path.is_file():
        return []
    out: list[dict] = []
    # On the newline the writer joins with, never `splitlines()` (T635): see `approve`.
    for n, line in enumerate(path.read_text(encoding="utf-8",
                                            errors="replace").split("\n"), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            out.append({"_unreadable": n})
            continue
        out.append(row if isinstance(row, dict) else {"_unreadable": n})
    return out


def _step_name(phase: str) -> str:
    from tools.sdlc import stage
    try:
        return stage.by_phase(phase).name
    except stage.StageError:
        return ""


def now() -> str:
    """An event timestamp: UTC ISO-8601, to the millisecond."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _break_at(chain: list[dict]) -> int | None:
    """The line number of the first line nobody can read, or None when the
    chain can take another event. THE predicate - `append` and
    `require_appendable` both ask it, so the two cannot disagree."""
    return next((e["_unreadable"] for e in chain if "_unreadable" in e), None)


def require_appendable(target: Path, run_id: str) -> None:
    """Refuse NOW a run whose chain `append` would refuse later (T465).

    Every operation that records an event makes its own write first -
    `state.advance` saves the move, `approve.record` appends the approval -
    and `append` checks the chain only when it writes. So a refusal from there
    arrived AFTER the write it was meant to go with: the command said
    "refused" while the move, or the approval, stood, and each retry of a
    refused approval recorded another one. Callers ask this before their
    first write, which is what lets the refusal say nothing was written."""
    line = _break_at(read(target, run_id))
    if line is not None:
        raise EventError(f"{EVENTS} for run {run_id}: line {line} is not readable JSON "
                         f"- refused before anything was written; repair that line "
                         f"(a committed run has it in git) and retry")


def append(target: Path, run_id: str, event: str, phase: str = "",
           at: str | None = None, ext: dict | None = None, **fields) -> dict:
    """Write one event at the end of the run's chain, and return it. `at` lets
    a caller that measured an interval against a timestamp stamp the event
    with THAT timestamp - one clock, not two. `ext` carries declared
    extension fields (Z1); it is validated before anything is written."""
    if event not in TYPES:
        raise EventError(f"unknown event {event!r} - one of {', '.join(TYPES)}")
    if extra := [k for k in fields if k not in FIELDS[event]]:
        raise EventError(f"{event} does not carry {', '.join(sorted(extra))} - "
                         f"its fields are {', '.join(FIELDS[event]) or 'the common ones'}")
    resolved = _resolve_ext(event, run_id, ext)  # validated before anything is written
    if resolved is not None:
        fields["ext"] = resolved
    chain = read(target, run_id)
    if _break_at(chain) is not None:
        raise EventError(f"{EVENTS} for run {run_id} has an unreadable line - "
                         f"refusing to append past a break nobody can verify")
    entry = {"schema_version": SCHEMA_VERSION, "run_id": run_id,
             "seq": len(chain) + 1,
             "at": at or now(),
             "event": event, "step": phase, "step_name": _step_name(phase) if phase else "",
             **fields,
             "prev": chain[-1].get("hash", "") if chain else ""}
    entry["hash"] = _hash(entry)
    ledger.append_record(events_path(target, run_id),
                         json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    return entry


def verify(target: Path, run_id: str) -> list[str]:
    """Every break in the chain, named by position. Empty = intact."""
    problems: list[str] = []
    prev = ""
    chain = read(target, run_id)
    for i, entry in enumerate(chain, 1):
        if "_unreadable" in entry:
            return problems + [f"{EVENTS} line {entry['_unreadable']}: not readable "
                               f"JSON - the chain cannot be walked past it"]
        what = entry.get("event", "?")
        if entry.get("prev", "") != prev:
            problems.append(f"{EVENTS} event {i} ({what}): links to "
                            f"{entry.get('prev', '')!r}, the previous event hashes to "
                            f"{prev!r} - an event was edited, inserted or removed")
        if entry.get("hash") != _hash(entry):
            problems.append(f"{EVENTS} event {i} ({what}): its recorded hash does "
                            f"not match its contents - the line was edited")
        if entry.get("seq") != i:
            problems.append(f"{EVENTS} event {i} ({what}): seq {entry.get('seq')!r} "
                            f"- an event was inserted or removed")
        prev = entry.get("hash", "")
    # T473: whose trail this is. An intact chain copied from another run folder
    # passed every check above, crediting that run's decisions, approvers and
    # waits to this one - the approvals ledger already refused the same copy.
    claimed = sorted({str(e.get("run_id")) for e in chain if e.get("run_id") != run_id})
    if claimed:
        problems.append(f"{EVENTS}: its events claim run_id "
                        f"{', '.join(repr(c) for c in claimed)} - a trail moved between "
                        f"run folders proves nothing about either")
    return problems


def last(target: Path, run_id: str, event: str, **match) -> dict | None:
    """The most recent event of a type whose fields match - e.g. the
    `gate_waiting` a decision answers, for its `waited_ms`."""
    for entry in reversed(read(target, run_id)):
        if entry.get("event") == event and all(entry.get(k) == v for k, v in match.items()):
            return entry
    return None


def millis_between(start: str, end: str) -> int | None:
    """Whole milliseconds from one event's `at` to another's, or None when
    either cannot be read - never 0 for a time nobody could measure."""
    try:
        a, b = datetime.fromisoformat(start), datetime.fromisoformat(end)
    except (TypeError, ValueError):
        return None
    return int((b - a).total_seconds() * 1000)


# ── T446b: what a step cost, as REPORTED - never guessed ─────────────────────

#: The environment variable a spawned runner may write usage into (one JSON
#: line per model call: provider, model, input_tokens, output_tokens,
#: cached_tokens) - RUNNER_CONTRACT.md sec. 1.
USAGE_ENV = "SDLC_USAGE_FILE"


def usage_path(target: Path, run_id: str, phase: str, attempt: int) -> Path:
    """Where one attempt of one step reports its usage, inside the run folder."""
    return ledger.run_dir(target, run_id) / "drive" / f"{phase}.{attempt}.usage.jsonl"


def tokens_from(path: Path, source: str = "reported") -> dict:
    """The step's token record. A runner that wrote nothing is `unavailable`
    WITH a reason - never zero, because zero is a measurement and this is the
    absence of one (the metrics rule, carried into the audit trail)."""
    rows = []
    if path.is_file():
        # On the newline the runner joins with, never `splitlines()`: a record holding U+2028
        # read as no usage at all (T635).
        for line in path.read_text(encoding="utf-8", errors="replace").split("\n"):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    if not rows:
        return {"source": "unavailable", "reason": "runner reported no usage"}

    def total(key: str) -> int | None:
        vals = [r.get(key) for r in rows]
        return sum(v for v in vals if isinstance(v, int)) if any(
            isinstance(v, int) for v in vals) else None

    inp, out, cached = total("input_tokens"), total("output_tokens"), total("cached_tokens")
    return {"source": source, "calls": len(rows), "input": inp, "output": out,
            "cached": cached,
            "total": (inp or 0) + (out or 0) if inp is not None or out is not None else None}


def cost_from(tokens: dict, table: dict | None) -> dict:
    """An estimate ONLY where the catalog declares a price - provider prices
    change, and a built-in price would be a confident lie."""
    if tokens.get("source") in (None, "unavailable"):
        return {"source": "unpriced", "reason": "no token counts to price"}
    pin = (table or {}).get("price_in_per_mtok")
    pout = (table or {}).get("price_out_per_mtok")
    if not isinstance(pin, (int, float)) or not isinstance(pout, (int, float)):
        return {"source": "unpriced", "reason": "no price declared in the catalog"}
    amount = ((tokens.get("input") or 0) * pin + (tokens.get("output") or 0) * pout) / 1e6
    return {"source": "priced", "amount": round(amount, 6),
            "currency": str((table or {}).get("price_currency", "unspecified"))}
