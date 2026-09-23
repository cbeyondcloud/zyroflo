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
events carry ids, names, statuses, timings and hashes only. Every field is a
value this module chose, never one a caller typed.

T446b adds `step_started`/`step_finished` (timings, tokens, cost) from the
driver; T446c aggregates and exports. This row is the record they write into.
"""
from __future__ import annotations

import json
import sys
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
    RUN_OPENED: ("kind",),
    STEP_STARTED: ("attempt",),
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
    "kind": "string", "attempt": "integer", "started_at": "string",
    "ended_at": "string", "duration_ms": ["integer", "null"], "actor": "string",
    "runner": ["string", "null"], "provider": ["string", "null"],
    "model": ["string", "null"], "protocol": ["string", "null"],
    "tokens": "object", "cost": "object", "exit_code": ["integer", "null"],
    "outcome": "string", "artifacts": "object", "forced": "boolean",
    "gate": "string", "hard": ["boolean", "null"], "decision": "string",
    "approver": "string", "ledger_hash": "string",
    "waited_ms": ["integer", "null"], "from": "string", "to": "string",
    "status": "string",
}


class EventError(RuntimeError):
    """An event that cannot be written as the schema says - refused, never
    written half-right into a record CI treats as append-only."""


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
    for n, line in enumerate(path.read_text(encoding="utf-8",
                                            errors="replace").splitlines(), 1):
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


def append(target: Path, run_id: str, event: str, phase: str = "",
           at: str | None = None, **fields) -> dict:
    """Write one event at the end of the run's chain, and return it. `at` lets
    a caller that measured an interval against a timestamp stamp the event
    with THAT timestamp - one clock, not two."""
    if event not in TYPES:
        raise EventError(f"unknown event {event!r} - one of {', '.join(TYPES)}")
    if extra := [k for k in fields if k not in FIELDS[event]]:
        raise EventError(f"{event} does not carry {', '.join(sorted(extra))} - "
                         f"its fields are {', '.join(FIELDS[event]) or 'the common ones'}")
    chain = read(target, run_id)
    if any("_unreadable" in e for e in chain):
        raise EventError(f"{EVENTS} for run {run_id} has an unreadable line - "
                         f"refusing to append past a break nobody can verify")
    entry = {"schema_version": SCHEMA_VERSION, "run_id": run_id,
             "seq": len(chain) + 1,
             "at": at or now(),
             "event": event, "step": phase, "step_name": _step_name(phase) if phase else "",
             **fields,
             "prev": chain[-1].get("hash", "") if chain else ""}
    entry["hash"] = _hash(entry)
    path = events_path(target, run_id)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    return entry


def verify(target: Path, run_id: str) -> list[str]:
    """Every break in the chain, named by position. Empty = intact."""
    problems: list[str] = []
    prev = ""
    for i, entry in enumerate(read(target, run_id), 1):
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
