"""The run ledger — item 31: one folder per run, one `state.json` inside it.

A run is a folder under `<target>/.sdlc/runs/<run-id>/`, committed with the
code it describes, and moved to `runs/archive/<run-id>/` when it closes —
OpenSpec's change-folder lifecycle, borrowed deliberately (reference plan).
`state.json` is the machine half of the record: which phase the run is in,
whether it is running or waiting for a human, and the hash of every artifact
the run has produced.

**The format is dependency-free, and that is now the only option.** The plan
adopted `pydantic-graph`'s `FileStatePersistence` as the engine that would
write this file, on the recorded ground that it persists plain JSON. Measured
at the pinned version (2.23.0, the pydantic-ai project, floor `>=0.0.19`):
there is no persistence layer in the package at all — no `FileStatePersistence`,
no snapshot type, no `state.json`. The 2.x line kept `Graph`/`BaseNode` and
dropped persistence entirely, so persisting a run is the caller's job. This
module is therefore the format's owner rather than its reader, which is what
the plan already required of it ("the state format is dependency-free and solo
mode reads/writes it with stdlib alone"). Whether a typed engine drives the
transitions later is item 41's question; it will find the file waiting.

**The declaration is gone too, as of 2026-08-25.** `pydantic-graph` outlived
the measurement above — the reason for it was void and it stayed in the `sdlc`
extra anyway, imported by nothing. An unused declaration is not free —
`in-toto-attestation` was the same shape and was the only thing pulling a
`protobuf` with two CVEs at `high` — so it was removed, and an engine that
wants to drive these transitions adds it back deliberately.

Three rules, each load-bearing:

* **A terminal run is terminal.** `closed` and `rejected` refuse further
  transitions. A run that can be quietly re-opened is not a record.
* **Nothing is overwritten in place without a trace.** Every transition
  appends to `history`, so the file answers "how did it get here", not only
  "where is it".
* **An artifact is its hash.** `record_artifact` stores `sha256:...` at the
  moment of recording, so a later edit is visible rather than assumed away —
  the same property item 32's approvals depend on.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Windows is a first-class target: piped stdout is cp1252 and run titles are
# arbitrary text.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SCHEMA_VERSION = 1
HARNESS = Path(__file__).resolve().parents[2]

#: The lifecycle's phases, P0-P11 — the master plan's numbering, unchanged.
PHASES = tuple(f"P{i}" for i in range(12))
#: `awaiting_approval` is the halt the gates produce; `halted` is everything
#: else that stops a run (a failed precondition, an operator's hand).
STATUSES = ("running", "awaiting_approval", "halted", "rejected", "closed")
TERMINAL = ("rejected", "closed")
KINDS = ("requirement", "defect", "deploy-change")

RUNS_REL = Path(".sdlc") / "runs"
ARCHIVE_NAME = "archive"
STATE_NAME = "state.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def runs_dir(target: Path) -> Path:
    return target / RUNS_REL


def archive_dir(target: Path) -> Path:
    return runs_dir(target) / ARCHIVE_NAME


def run_dir(target: Path, run_id: str) -> Path:
    """The run's folder, active or archived. Active wins if both exist, and
    that collision is reported by `list_runs` rather than resolved silently."""
    active = runs_dir(target) / run_id
    if active.is_dir():
        return active
    return archive_dir(target) / run_id


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


#: Field separators for the directory digest. A byte that cannot occur in a
#: path name separates a name from its hash, and a second separates the
#: records — otherwise two files could be named so that their concatenation
#: reads exactly like one differently-named pair.
NUL = bytes([0])
SEP = bytes([10])


def digest_of(path: Path) -> str:
    """One hash for an artifact, whether it is a file or a DIRECTORY.

    P4 produces `40-impl/` — one file per component — and it is the artifact
    G3 stands on, which made it the one output in the lifecycle that item 32's
    "an artifact is its hash" could not cover. A directory hashes over the
    sorted list of its files' relative paths AND their digests, so a renamed
    file, an added one and an edited one are all changes; hashing only the
    contents would let a rename pass as identical.

    An EMPTY directory is refused rather than hashed. A constant digest for
    "nothing here" is the failure this repository keeps finding in its own
    tools: empty reading exactly like clean.
    """
    if path.is_file():
        return sha256_of(path)
    if not path.is_dir():
        raise RuntimeError(f"no artifact at {path} - record what exists, "
                           f"or the hash records nothing")
    entries = sorted(f for f in path.rglob('*') if f.is_file())
    if not entries:
        raise RuntimeError(f"{path} is an empty directory - hashing it would give "
                           f"a stable digest for having produced nothing")
    h = hashlib.sha256()
    for f in entries:
        h.update(f.relative_to(path).as_posix().encode("utf-8"))
        h.update(NUL)
        h.update(sha256_of(f).encode("ascii"))
        h.update(SEP)
    return f"sha256:{h.hexdigest()}"


def harness_version() -> str:
    """The harness commit this run was driven by, so a past run's behaviour is
    attributable to exact rule and prompt versions. Absent history is said, not
    guessed."""
    try:
        proc = subprocess.run(["git", "-C", str(HARNESS), "rev-parse", "HEAD"],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    return proc.stdout.strip() if proc.returncode == 0 else "unknown"


def new_run_id(title: str, now: datetime | None = None) -> str:
    """Sortable by time, readable, and stable for a given (title, second)."""
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")
    digest = hashlib.sha256(title.strip().encode("utf-8")).hexdigest()[:6]
    return f"{stamp}-{digest}"


def unique_run_id(target: Path, title: str, now: datetime | None = None) -> str:
    """A generated id that is free. The id is time-to-the-second plus a digest
    of the title, so re-submitting the SAME specification twice inside one
    second collides — measured, the first time the intake adapter's own
    duplicate test ran. A generated id gets a suffix; an id the operator typed
    is never silently altered."""
    base = new_run_id(title, now)
    rid, n = base, 1
    while (runs_dir(target) / rid).exists() or (archive_dir(target) / rid).exists():
        n += 1
        rid = f"{base}-{n}"
    return rid


def _write(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(state, indent=2, sort_keys=True) + "\n"
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def create(target: Path, kind: str, title: str, run_id: str | None = None,
           now: datetime | None = None) -> dict:
    """Open a run. Refuses to reuse an existing folder: two runs sharing an id
    would interleave their history into one file."""
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r} - one of {', '.join(KINDS)}")
    if not title.strip():
        raise ValueError("a run needs a title - it is what status.py lists")
    rid = run_id or unique_run_id(target, title, now)
    d = runs_dir(target) / rid
    if d.exists() or (archive_dir(target) / rid).exists():
        raise RuntimeError(f"run {rid} already exists - refusing to reuse its folder")
    stamp = _now()
    state = {
        "schema_version": SCHEMA_VERSION,
        "run_id": rid,
        "kind": kind,
        "title": title.strip(),
        "phase": PHASES[0],
        "status": "running",
        "created_at": stamp,
        "updated_at": stamp,
        "harness_version": harness_version(),
        "artifacts": {},
        "history": [{"at": stamp, "phase": PHASES[0], "status": "running",
                     "note": "run opened"}],
    }
    _write(d / STATE_NAME, state)
    return state


def load(target: Path, run_id: str) -> dict:
    path = run_dir(target, run_id) / STATE_NAME
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise RuntimeError(f"no run {run_id} under {runs_dir(target)}") from None
    except ValueError as exc:
        raise RuntimeError(f"{path} is not readable JSON: {exc}") from None
    if not isinstance(state, dict):
        raise RuntimeError(f"{path} does not hold a state object")
    return state


def save(target: Path, state: dict) -> Path:
    path = run_dir(target, state["run_id"]) / STATE_NAME
    _write(path, state)
    return path


def advance(target: Path, run_id: str, phase: str | None = None,
            status: str | None = None, note: str = "",
            forced: bool = False) -> dict:
    """Move a run. Backwards is legal — P5 failures loop to P4 and re-trigger
    G3, and a ledger that forbade it would be lying about the lifecycle it
    records — but a terminal run does not move at all.

    `forced` is RECORDED, never judged. This layer deliberately has no opinion
    about whether a move is allowed - `stage.advance` is the one with the
    opinion (item 41), and a ledger that refused to record a move it disagreed
    with would be a ledger that lies. What changed in item 112 is only the
    SPELLING: the fact of an override rides the history entry as a field, the
    way `approve.record` has always carried `forced: true`, instead of living
    only inside the note's prose where an auditor had to regex English to ask
    "which runs were forced?".

    The prose STAYS, because it says WHAT was overridden and a boolean cannot.
    """
    state = load(target, run_id)
    if state.get("status") in TERMINAL:
        raise RuntimeError(f"run {run_id} is {state['status']} - a terminal run does "
                           f"not move; open a new run instead")
    if phase is not None and phase not in PHASES:
        raise ValueError(f"unknown phase {phase!r} - one of {', '.join(PHASES)}")
    if status is not None and status not in STATUSES:
        raise ValueError(f"unknown status {status!r} - one of {', '.join(STATUSES)}")
    if phase is None and status is None:
        raise ValueError("advance needs a phase, a status, or both")
    state["phase"] = phase or state["phase"]
    state["status"] = status or state["status"]
    state["updated_at"] = _now()
    entry = {"at": state["updated_at"], "phase": state["phase"],
             "status": state["status"], "note": note}
    # Written only when true, so every history entry ever recorded stays
    # byte-identical to what it was - an added key on every row would rewrite
    # the shape of records this project has already committed.
    if forced:
        entry["forced"] = True
    state.setdefault("history", []).append(entry)
    save(target, state)
    return state


def record_artifact(target: Path, run_id: str, rel: str) -> dict:
    """Hash an artifact of the run, by its path inside the run folder. A
    directory is an artifact too — see `digest_of`."""
    state = load(target, run_id)
    path = run_dir(target, run_id) / rel
    state.setdefault("artifacts", {})[rel.replace("\\", "/")] = digest_of(path)
    state["updated_at"] = _now()
    save(target, state)
    return state


def drift(target: Path, run_id: str, recorded: dict) -> list[tuple[str, str, str]]:
    """Compare a map of `rel -> digest` against the tree. Returns
    `(rel, kind, detail)` where kind is `missing`, `changed` or `unhashable`.

    ONE traversal, two vocabularies. `state.verify_artifacts` asks "does this
    still match what was recorded" and `approve.verify_artifacts_for` asks
    "does this still match what was APPROVED" — the same walk with different
    sentences, and they had drifted into two implementations, only one of which
    knew a directory could be an artifact.
    """
    out: list[tuple[str, str, str]] = []
    for rel, digest in sorted(recorded.items()):
        path = run_dir(target, run_id) / rel
        if not (path.is_file() or path.is_dir()):
            out.append((rel, "missing", ""))
            continue
        try:
            current = digest_of(path)
        except RuntimeError as exc:
            # A directory emptied since it was recorded is not "missing" and is
            # certainly not unchanged; say which it is.
            out.append((rel, "unhashable", str(exc)))
            continue
        if current != digest:
            out.append((rel, "changed", ""))
    return out


def verify_artifacts(target: Path, run_id: str) -> list[str]:
    """Every recorded artifact that no longer matches its hash, or is gone.
    An empty list is the only clean answer; the gates read this."""
    state = load(target, run_id)
    said = {"missing": "recorded, now missing", "changed": "changed since it was recorded"}
    return [f"{rel}: {detail or said[kind]}"
            for rel, kind, detail in drift(target, run_id, state.get("artifacts", {}))]


def archive(target: Path, run_id: str, force: bool = False) -> Path:
    """Move a closed run into `runs/archive/`. A run still in flight stays
    where status.py looks for it, unless the operator overrides and says so."""
    state = load(target, run_id)
    if state.get("status") not in TERMINAL and not force:
        raise RuntimeError(f"run {run_id} is {state.get('status')}, not "
                           f"{' or '.join(TERMINAL)} - archiving it would hide a run "
                           f"that is still waiting (pass --force to override)")
    src = runs_dir(target) / run_id
    if not src.is_dir():
        raise RuntimeError(f"run {run_id} is not active under {runs_dir(target)}")
    dest = archive_dir(target) / run_id
    if dest.exists():
        raise RuntimeError(f"{dest} already exists - refusing to merge two runs")
    dest.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dest)
    return dest


def list_runs(target: Path) -> list[dict]:
    """Active runs first, then archived; each row carries where it lives."""
    rows = []
    for where, base in (("active", runs_dir(target)), ("archived", archive_dir(target))):
        if not base.is_dir():
            continue
        for d in sorted(p for p in base.iterdir() if p.is_dir()):
            if where == "active" and d.name == ARCHIVE_NAME:
                continue
            state = json.loads((d / STATE_NAME).read_text(encoding="utf-8")) \
                if (d / STATE_NAME).is_file() else {}
            rows.append({"run_id": d.name, "where": where,
                         "kind": state.get("kind", "?"),
                         "phase": state.get("phase", "?"),
                         "status": state.get("status", "?"),
                         "title": state.get("title", "(no state.json)")})
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="The run ledger: one folder per run, "
                                             "one state.json inside it (item 31).")
    ap.add_argument("--target", required=True, metavar="DIR",
                    help="the repository the runs belong to")
    what = ap.add_mutually_exclusive_group(required=True)
    what.add_argument("--new", action="store_true", help="open a run")
    what.add_argument("--show", metavar="RUN_ID")
    what.add_argument("--list", action="store_true", dest="list_runs")
    what.add_argument("--advance", metavar="RUN_ID")
    what.add_argument("--record", metavar="RUN_ID",
                      help="hash an artifact of this run (--artifact)")
    what.add_argument("--verify", metavar="RUN_ID",
                      help="re-hash every recorded artifact")
    what.add_argument("--archive", metavar="RUN_ID")
    ap.add_argument("--kind", choices=KINDS, default="requirement")
    ap.add_argument("--title", default="")
    ap.add_argument("--id", default=None, help="run id (default: time + title digest)")
    ap.add_argument("--phase", choices=PHASES)
    ap.add_argument("--status", choices=STATUSES)
    ap.add_argument("--note", default="")
    ap.add_argument("--artifact", metavar="REL", help="path inside the run folder")
    ap.add_argument("--force", action="store_true",
                    help="archive a run that has not reached a terminal status")
    args = ap.parse_args(argv)
    target = Path(args.target).resolve()
    if not target.is_dir():
        print(f"!! no directory at {target}")
        return 2

    try:
        if args.new:
            state = create(target, args.kind, args.title, run_id=args.id)
            print(f"run {state['run_id']}: {state['kind']} - {state['title']}")
            print(f"  {run_dir(target, state['run_id'])}")
        elif args.show:
            print(json.dumps(load(target, args.show), indent=2, sort_keys=True))
        elif args.list_runs:
            rows = list_runs(target)
            if not rows:
                print(f"no runs under {runs_dir(target)}")
            for r in rows:
                print(f"  {r['run_id']}  {r['where']:<8} {r['phase']:<4} "
                      f"{r['status']:<17} {r['kind']:<14} {r['title']}")
        elif args.advance:
            state = advance(target, args.advance, args.phase, args.status, args.note)
            print(f"run {state['run_id']}: {state['phase']} / {state['status']}")
        elif args.record:
            if not args.artifact:
                print("!! --record needs --artifact <path inside the run folder>")
                return 2
            state = record_artifact(target, args.record, args.artifact)
            print(f"recorded {args.artifact}: {state['artifacts'][args.artifact]}")
        elif args.verify:
            problems = verify_artifacts(target, args.verify)
            for p in problems:
                print(f"  !! {p}")
            print(f"artifacts: {'all match' if not problems else str(len(problems)) + ' problem(s)'}")
            return 1 if problems else 0
        elif args.archive:
            dest = archive(target, args.archive, force=args.force)
            print(f"archived -> {dest}")
    except (ValueError, RuntimeError) as exc:
        print(f"!! {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
