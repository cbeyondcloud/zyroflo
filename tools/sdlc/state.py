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
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath

# Windows is a first-class target: piped stdout is cp1252 and run titles are
# arbitrary text.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SCHEMA_VERSION = 1
HARNESS = Path(__file__).resolve().parents[2]
# Run as a script, the harness root is not on sys.path, and `create` imports its
# siblings as `tools.sdlc.*` (T463).
if str(HARNESS) not in sys.path:
    sys.path.insert(0, str(HARNESS))

#: The lifecycle's ordered phase ids — the master plan's numbering. THE canonical
#: order: `state` is the lowest module `stage` imports, so the id sequence lives
#: here and `stage.PHASES` aliases it (a test ties both to `stage.STAGES`). Spelled
#: as an explicit tuple, not `range(12)`: the ids are DATA, so nothing derives a
#: phase from a length-12 count, and the resolver's order comes from one list a
#: future custom step can be spliced into.
PHASES = ("P0", "P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8", "P9", "P10", "P11")
#: `awaiting_approval` is the halt the gates produce; `halted` is everything
#: else that stops a run (a failed precondition, an operator's hand).
STATUSES = ("running", "awaiting_approval", "halted", "rejected", "closed")
TERMINAL = ("rejected", "closed")
KINDS = ("requirement", "defect", "deploy-change")

#: Where run folders live, relative to the target. The default, and what a
#: committed run folder means; a framework may move it (Z3, `set_runs_root`),
#: and `RUNS_REL` - set below, after the checks - is the one every reader asks.
DEFAULT_RUNS_REL = Path(".sdlc") / "runs"
ARCHIVE_NAME = "archive"
STATE_NAME = "state.json"
#: What `list_runs` shows for a run whose state carries no title. A stand-in for
#: a person reading the listing, never a title: `intake.duplicates` must not
#: compare it as one, or a titleless run reads as a different item.
UNTITLED = "(no state.json)"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


#: Child processes inherit a moved run-folder root through this variable (Z3).
RUNS_ROOT_ENV = "SDLC_RUNS_ROOT"
#: A device name is reserved whatever follows its first dot: `con.tar.gz` too.
_WIN_RESERVED = re.compile(r"(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?", re.IGNORECASE)
_WIN_FORBIDDEN = set('<>"|?*:')
_MAX_PART, _MAX_ROOT = 100, 200


def _runs_root_refusal(rel: object) -> str:
    """Why `rel` cannot be the run-folder root, or "" if it can.

    Relative and inside the target (no drive, no leading `/`, no `.` or `..`
    part), never inside `.git`, and named so Windows stores it under the same
    name - the reasoning `_safe_run_id` and `_win_storage_refusal` apply to one
    run id, applied to every part of the root."""
    text = str(rel).replace("\\", "/") if isinstance(rel, (str, Path)) else ""
    parts = [p for p in text.split("/") if p]
    why = ""
    if not parts or text.startswith("/") or len(text) > _MAX_ROOT:
        why = "it must be a relative folder inside the target"
    elif any(p in (".", "..") for p in parts):
        why = "a '.' or '..' part would leave the target"
    elif any(p.lower() == ".git" for p in parts):
        why = "run folders never go inside .git"
    elif any(_WIN_FORBIDDEN & set(p) or p != p.rstrip(". ") or _WIN_RESERVED.fullmatch(p)
             or len(p) > _MAX_PART for p in parts):
        why = ("a part is stored under another name, or not at all, on Windows (a "
               "reserved name, a trailing dot or space, or one of <>\"|?*:)")
    return (f"runs root {rel!r}: {why} - e.g. .agentic/local/runs" if why else "")


def set_runs_root(rel: str | Path | None) -> None:
    """Z3: keep this process's run folders under `<target>/<rel>` instead of
    `.sdlc/runs`. None restores the default.

    For a framework built on the harness that keeps its runs OUT of the
    committed tree (agentic-sdlc: `.agentic/local/runs`, git-ignored, its runs
    shipped to its own control plane). Everything that finds a run reads
    `RUNS_REL` when it runs, so one setting moves them all, the archive and the
    clean-checkout exemption (`intake.dirty_paths`) included. What the harness
    builds on committed run folders - the CI ledger gate, `validate` - then has
    nothing in git to check, and that persistence is the framework's to own.

    Set once, at startup: it is one value per process. It is exported as
    `SDLC_RUNS_ROOT`, so a harness CLI the process spawns finds the same runs."""
    global RUNS_REL
    if rel is None:
        RUNS_REL = DEFAULT_RUNS_REL
        os.environ.pop(RUNS_ROOT_ENV, None)
        return
    if why := _runs_root_refusal(rel):
        raise ValueError(why)
    RUNS_REL = Path(*[p for p in str(rel).replace("\\", "/").split("/") if p])
    os.environ[RUNS_ROOT_ENV] = RUNS_REL.as_posix()


def _inherited_runs_root() -> Path:
    """The root a parent process set (`SDLC_RUNS_ROOT`), refused by name if bad."""
    inherited = os.environ.get(RUNS_ROOT_ENV, "")
    if not inherited:
        return DEFAULT_RUNS_REL
    if why := _runs_root_refusal(inherited):
        raise ValueError(f"{RUNS_ROOT_ENV}: {why}")
    return Path(*[p for p in inherited.replace("\\", "/").split("/") if p])


RUNS_REL = _inherited_runs_root()


def runs_dir(target: Path) -> Path:
    return target / RUNS_REL


def archive_dir(target: Path) -> Path:
    return runs_dir(target) / ARCHIVE_NAME


def _safe_run_id(run_id: str) -> None:
    """A run id names ONE folder under the runs directory, so it must be a single
    relative path segment. Refuse anything else BY NAME.

    A run id can arrive from a branch name through `policy.run_of_branch` -
    attacker-influenced in a fork-CI context - and `run_dir` joins it straight onto
    a path, so an unguarded `..`, separator or drive letter resolves OUTSIDE the
    runs directory (the ledger gate then probes and reads from there). This is the
    one place every run-id-to-folder resolution funnels through, so the segment is
    enforced here for every caller. Refusing by name is item 117's altitude: a
    clear refusal a person can act on, not a path silently built outside the
    sandbox and not an empty result.
    """
    if run_id in ("", ".", ".."):
        this = "is empty or a directory reference"
    elif "/" in run_id or "\\" in run_id:
        this = "contains a path separator"
    elif PureWindowsPath(run_id).drive or PureWindowsPath(run_id).is_absolute():
        this = "is drive- or root-qualified"
    else:
        return
    raise ValueError(
        f"unsafe run id {run_id!r}: a run id is a single relative path segment - "
        f"one folder under {RUNS_REL.as_posix()} - but this {this}")


def _win_storage_refusal(run_id: str) -> str:
    """Why Windows would store `run_id` under a different name, or "".

    A trailing dot or space is dropped, and `name:x` is read as a stream of
    `name`, so the folder would carry another name than the record inside it.
    Checked only where a run is CREATED, never where one is read (review F7):
    this is the `artifact_key`/`key_refusal` asymmetry - refuse what somebody
    types, never refuse a run we already wrote, which on Linux may legitimately
    carry a `:` a person typed before this existed. A generated id
    (`new_run_id`) never has either shape.
    """
    if run_id != run_id.rstrip(". ") or ":" in run_id:
        return (f"run id {run_id!r} is stored under another name on Windows (a "
                f"trailing dot or space, or a ':'), so the folder and its record "
                f"would disagree - choose an id of letters, digits, '.', '-' or '_'")
    return ""


def run_dir(target: Path, run_id: str) -> Path:
    """The run's folder, active or archived. Active wins if both exist, and
    that collision is reported by `list_runs` rather than resolved silently."""
    _safe_run_id(run_id)
    active = runs_dir(target) / run_id
    if active.is_dir():
        return active
    return archive_dir(target) / run_id


def run_rel(target: Path, run_id: str) -> str:
    """The run's location relative to `target`, forward-slash: `.sdlc/runs/<id>`
    for an active run, `.sdlc/runs/archive/<id>` for an archived one - DERIVED
    from `run_dir()` rather than typed, so the label stays correct once a run
    moves (T381: a typed `.sdlc/runs/<id>` literal is always the ACTIVE shape
    and is wrong for an archived run).

    Correct only for a run that EXISTS. `run_dir` resolves an ABSENT run to the
    archive fallback, so a refusal about a MISSING run names the runs directory
    (`RUNS_REL.as_posix()`) or the active location it deliberately points at,
    never this (T256, `test_stages.py`; worktree.py's `save_record`, which types
    the active location for a missing run). Used by worktree.py's `load_record`
    refusals, which name an EXISTING run's record and derive it here. The
    `except ValueError` fallback to the basename - the guard `digest_of` also
    carries - keeps a machine path out of the string if `run_dir` ever resolves
    outside `target`; it is unreachable through this function because
    `_safe_run_id` refuses any id that could escape, and is kept for that guard
    and to match `digest_of`'s shape.
    """
    path = run_dir(target, run_id)
    try:
        return path.relative_to(target).as_posix()
    except ValueError:
        return path.name


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


def digest_of(path: Path, target: Path) -> str:
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

    A refusal below names *path* relative to *target* instead of the absolute
    *path* (T381), so the location is one a person can act on. It is DERIVED
    here rather than typed by the caller: `path` is already computed through
    `run_dir()`, which resolves to the archive folder once a run is archived,
    so deriving from `path` itself keeps the label correct for an archived
    run too (T381 F2 — a caller-typed `.sdlc/runs/<id>/...` literal is always
    the ACTIVE shape and is wrong once a run moves). `target` is a required
    argument, not an optional label with an absolute-path default, so there
    is no longer a way for a caller to omit it and silently reintroduce a
    machine path (T381 N1). The `except ValueError` below is not just
    defensive: `drift()` joins a recorded artifact key straight from
    `state.json` without `artifact_key`'s validation, so a hand-edited
    absolute key can land `path` outside `target` even though shipped
    callers normally pass one under it. The fallback — `show` becomes
    `path.name`, never an absolute path — is what makes that case safe,
    not an assumption that it cannot occur.
    """
    try:
        show = path.relative_to(target).as_posix()
    except ValueError:
        show = path.name
    if path.is_file():
        return sha256_of(path)
    if not path.is_dir():
        raise RuntimeError(f"no artifact at {show} - record what exists, "
                           f"or the hash records nothing")
    entries = sorted(f for f in path.rglob('*') if f.is_file())
    if not entries:
        raise RuntimeError(f"{show} is an empty directory - hashing it would give "
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
    guessed.

    Installed as a dependency (Z2), the folder above `tools/` is the HOST's
    site-packages, inside the host's repository - so `git rev-parse` there
    would record the host's commit as the harness's. An installed harness
    answers from its install record instead: the commit it was installed from
    (`direct_url.json`, PEP 610), else its package version."""
    from tools.sdlc import content
    if content.installed():
        dist = _harness_distribution()
        if dist is None:
            return "unknown"
        try:
            commit = json.loads(dist.read_text("direct_url.json") or "{}").get(
                "vcs_info", {}).get("commit_id", "")
        except (ValueError, AttributeError):
            commit = ""
        return commit or f"sdlc-harness=={dist.version}"
    try:
        proc = subprocess.run(["git", "-C", str(HARNESS), "rev-parse", "HEAD"],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    return proc.stdout.strip() if proc.returncode == 0 else "unknown"


def _harness_distribution():
    """This harness's installed distribution, or None."""
    from importlib import metadata
    try:
        return metadata.distribution("sdlc-harness")
    except metadata.PackageNotFoundError:
        return None


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
    # T487: the id is checked BEFORE it names a folder. This function joins it
    # itself rather than through `run_dir`, so an unsafe `--id` wrote
    # `state.json` wherever it pointed and was refused only afterwards, by the
    # first event append. The Windows-storage check (F4) sits HERE, on the write
    # path only, so it never strands a run already created under such an id (F7).
    _safe_run_id(rid)
    if why := _win_storage_refusal(rid):
        raise ValueError(why)
    d = runs_dir(target) / rid
    if d.exists() or (archive_dir(target) / rid).exists():
        raise RuntimeError(f"run {rid} already exists - refusing to reuse its folder")
    # T465: everything that can refuse is asked BEFORE the folder exists. The
    # folder is the run's id claimed, so a refusal after it - a profile that
    # cannot be read was the measured one - left a run with no `run_opened`
    # whose id every retry was then refused, even once the profile was fixed.
    # What follows the write appends to a chain that is new and empty, so it
    # can fail only on I/O.
    # T446a: the audit trail opens with the run. Imported here, not at module
    # level: `events` reads the run folder through this module.
    from tools.sdlc import events
    # Z1: a framework's default ext provider is asked here too, so a faulty one
    # refuses before the folder claims the id, not at the first append after it.
    events.check_default_ext(rid)
    # T447a: the pre-INIT metadata (T442) rides the opening event, so every
    # run in an exported store carries the repository, team and criticality
    # it was opened under - the roll-up key an estate's dashboards group by.
    from tools.sdlc import policy
    meta = policy.profile_project(target)
    # T462: the opening step's timing starts with the run - a driving tool's P0
    # is otherwise the one step nothing ever enters through `stage.advance`.
    from tools.sdlc import stage
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
    events.append(target, rid, events.RUN_OPENED, PHASES[0], kind=kind,
                  **{k: meta[f] for k, f in (('project', 'name'), ('owner', 'owner'),
                                          ('criticality', 'criticality'))
                     if meta.get(f)})
    stage.open_step(target, rid, PHASES[0])
    return state


def _record_events(target: Path, run_id: str, before: dict, after: dict) -> None:
    """The events a move implies (T446a), written from the move itself so the
    audit trail and `state.json` cannot tell two stories."""
    from tools.sdlc import events

    old, new = before.get("phase", ""), after["phase"]
    if old in PHASES and new in PHASES and PHASES.index(new) < PHASES.index(old):
        events.append(target, run_id, events.LOOP_BACK, new, **{"from": old, "to": new})
    if after["status"] == "awaiting_approval" and before.get("status") != "awaiting_approval":
        from tools.sdlc import stage
        try:
            pipe = stage.pipeline(target)
            hard = set(stage.resolved_hard_gates(target))
            gates = [g.id for g in pipe.gates if g.after_phase == new]
        except Exception:               # noqa: BLE001 - a profile that cannot
            gates, hard = [""], set()   # resolve still records THAT it waited
        for gate in gates or [""]:
            events.append(target, run_id, events.GATE_WAITING, new, gate=gate,
                          hard=(gate in hard) if gate else None)
    if after["status"] in TERMINAL and before.get("status") not in TERMINAL:
        events.append(target, run_id, events.RUN_CLOSED, new, status=after["status"])


class LedgerError(RuntimeError):
    """An operation on the run ledger that cannot proceed.

    The module's own error type, so callers (including ``stage.py``'s door)
    can catch it by name rather than catching bare ``RuntimeError``.
    """


class Unreadable(LedgerError):
    """A `state.json` that is there and cannot be read as a run record.

    Carries the reason twice on purpose: `str(exc)` names the file, which is
    what a person reading a refusal needs, and `.why` is the same sentence
    without the machine path, which is what a ROW in a listing needs — the row
    already says which run it is.
    """

    def __init__(self, why: str, path: Path):
        super().__init__(f"{path} {why}")
        self.why = why


def read_state(path: Path) -> dict:
    """The one reader for a `state.json`, guard included.

    It exists because the guard was in `load` and not in `list_runs`: one run
    folder holding `[]` answered `--list` with an `AttributeError` — which
    `main`'s `except (ValueError, RuntimeError)` cannot catch — and, through
    `intake.duplicates`, made every FUTURE run of P0 traceback as well. A guard
    that lives in one caller is a guard the next caller does not have.

    Raises `FileNotFoundError` for absent (which is not the same answer as
    damaged) and `Unreadable` for anything present that is not a state object.
    """
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise Unreadable(f"could not be read: {exc}", path) from None
    except ValueError as exc:
        raise Unreadable(f"is not readable JSON: {exc}", path) from None
    if not isinstance(state, dict):
        raise Unreadable("does not hold a state object", path)
    return state


def load(target: Path, run_id: str) -> dict:
    path = run_dir(target, run_id) / STATE_NAME
    try:
        return read_state(path)
    except FileNotFoundError:
        raise LedgerError(
            f"no run {run_id} under {RUNS_REL.as_posix()}") from None


def phase_of(target: Path, run_id: str) -> str:
    """The phase a run is AT, for a caller that decides from it what is owed.

    Raises `Unreadable` for a label that is not one of `PHASES` - absent, null,
    or a string shaped like a phase that is not one - and lets `load`'s own
    refusals through. T336: three judges each folded both into an empty label,
    which compared unequal to their phase and read as *not there yet*, so a
    gate that could not look was told the phase owed nothing.
    """
    phase = load(target, run_id).get("phase")
    if phase not in PHASES:
        raise Unreadable(f"records the phase {phase!r}, which is not one of "
                         f"{', '.join(PHASES)}", run_dir(target, run_id) / STATE_NAME)
    return phase


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
    before = {"phase": state.get("phase", ""), "status": state.get("status", "")}
    if state.get("status") in TERMINAL:
        raise RuntimeError(f"run {run_id} is {state['status']} - a terminal run does "
                           f"not move; open a new run instead")
    if phase is not None and phase not in PHASES:
        raise ValueError(f"unknown phase {phase!r} - one of {', '.join(PHASES)}")
    if status is not None and status not in STATUSES:
        raise ValueError(f"unknown status {status!r} - one of {', '.join(STATUSES)}")
    if phase is None and status is None:
        raise ValueError("advance needs a phase, a status, or both")
    current = state.get("phase")
    if phase is None and current not in PHASES:
        # T489: this read `state["phase"]` bare, so a record with no phase
        # answered a status-only move with a KeyError traceback.
        raise ValueError(
            f"run {run_id}'s {STATE_NAME} records no phase it can stand on "
            f"({current!r}), so a status-only move has nowhere to leave the run - "
            f"pass --phase to say where it is")
    held = state.get("status")
    if status is None and held not in STATUSES:
        # The same bare index one line down (batch-3 review B3-2): a record
        # with no status answered a phase-only move with a KeyError traceback.
        raise ValueError(
            f"run {run_id}'s {STATE_NAME} records no status it can hold "
            f"({held!r}), so a phase-only move leaves it in none - pass --status "
            f"to say what it is")
    state["phase"] = phase or current
    state["status"] = status or held
    state["updated_at"] = _now()
    entry = {"at": state["updated_at"], "phase": state["phase"],
             "status": state["status"], "note": note}
    # Written only when true, so every history entry ever recorded stays
    # byte-identical to what it was - an added key on every row would rewrite
    # the shape of records this project has already committed.
    if forced:
        entry["forced"] = True
    state.setdefault("history", []).append(entry)
    # T465: asked before the save, and for EVERY move - not only one that writes
    # an event of its own here, because `stage.advance` writes T462's step
    # timing after this returns. A move saved onto a chain that cannot then take
    # its events is the two stories `_record_events` exists to prevent; measured
    # before this line, the command refused at exit 2 while the run had moved.
    from tools.sdlc import events
    events.require_appendable(target, run_id)
    save(target, state)
    _record_events(target, run_id, before, state)
    return state


#: Where the bytes behind a pin are kept, CONTENT-ADDRESSED by their own
#: digest. **Item 139, and the address is what makes it compose**: item 32 has
#: pinned an artifact map into every approval and rejection since the ledger
#: existed, so a digest recorded there already names the content - it simply
#: had nowhere to resolve to. Keyed by digest rather than by name, re-recording
#: unchanged bytes is free and every draft a gate ever saw stays reachable.
KEPT_DIR = ".pinned"


#: The one spelling of a digest the store is addressed by: `sha256_of` and
#: `digest_of` write `sha256:` and 64 hex characters, and a bare hex digest is
#: accepted too, since the store is named by the hex alone.
_SHA256 = re.compile(r"(?:sha256:)?([0-9a-fA-F]{64})")


def digest_refusal(digest: str) -> str:
    """Why `digest` names no kept version, or `""` if it does.

    **T486.** `--digest` becomes a path inside the run folder, and it was taken
    as typed, so a value that is not a digest named some other file and
    `--restore` copied that file over the artifact. Only a sha256 digest names a
    kept version, so anything else is refused by name. A reader of a RECORDED
    digest asks here instead of catching, as `key_refusal` does for keys.
    """
    if _SHA256.fullmatch(str(digest)):
        return ""
    return (f"digest {digest!r} is not a sha256 digest - a kept version is named "
            f"by the sha256:<64 hex> value recorded in state.json or in the "
            f"approval entry that pinned it")


def kept_path(target: Path, run_id: str, digest: str) -> Path:
    """Where the bytes for `digest` live. A sha256 hex digest contains no dot,
    so the `.tree` suffix below cannot collide with one."""
    if why := digest_refusal(digest):
        raise ValueError(why)
    return (run_dir(target, run_id) / KEPT_DIR
            / _SHA256.fullmatch(str(digest)).group(1).lower())


def keep(target: Path, run_id: str, rel: str, digest: str) -> Path | None:
    """Keep what this digest is ABOUT, so a pin can be undone.

    **Item 139, and it was found by the loop working.** Item 32 pins a gate to
    exact bytes so an approval cannot silently come to mean something else -
    and it kept no copy of those bytes. A re-run answering a standing G1
    rejection rewrote `20-scope.md` from 1,801 bytes to 569, destroying the
    scope prose, the couplings with their `source_file:line` citations and the
    overlap section, and NOTHING anywhere could restore it: the run folder is
    untracked until merge, `feedback.jsonl` records a correction COUNT rather
    than content, and `state.json` holds a hash and nothing else. **The pin
    detected the loss and could not undo it**, which is `stage.stale` working
    exactly as built while the artifact is gone.

    **This is the ORDINARY case rather than an edge**: every re-run after a
    rejection overwrites the one document a human currently has their
    attention on.

    A DIRECTORY keeps its per-file digest map rather than its content, and the
    limit is stated instead of hidden: `40-impl/` is a diff the run's own
    branch already holds, and copying a tree on every record is a different
    kind of work. What that buys is still real - WHICH file inside it moved is
    answerable, where a single digest can only say that something did.

    Measured before it was built: one copy of every pinned artifact across
    five whole real runs is 405,535 bytes.
    """
    where = kept_path(target, run_id, digest)
    source = run_dir(target, run_id) / rel
    if source.is_file():
        if not where.exists():
            where.parent.mkdir(parents=True, exist_ok=True)
            where.write_bytes(source.read_bytes())
        return where
    if not source.is_dir():
        return None
    tree = where.with_name(where.name + ".tree")
    if not tree.exists():
        tree.parent.mkdir(parents=True, exist_ok=True)
        rows = {f.relative_to(source).as_posix(): sha256_of(f)
                for f in sorted(source.rglob("*")) if f.is_file()}
        tree.write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8", newline="\n")
    return tree


def artifact_key(rel: str) -> str:
    """The ONE spelling of an artifact's path inside a run folder, and the one
    place that refuses a path which is not inside it.

    Two defects, one function. `record_artifact` used to normalise AFTER
    computing the key while `main` re-indexed the map with the raw argument, so
    `--artifact "10-intake\\00-intake.json"` — the spelling of the platform
    this project calls first-class — answered with a `KeyError` traceback. And
    `rel` was never checked at all, so an ABSOLUTE path was recorded verbatim
    into `state.json`, which is a committed file, and `--restore` wrote the
    kept bytes outside the run folder entirely.

    Deliberately minimal: separators only. `40-impl/` keeps its trailing slash
    because that trailing slash is how `stage.IMPL_DIR` spells a directory
    artifact, and collapsing it here would silently rename every key already
    recorded. Old records are not rewritten by any of this.

    A second character of `:` is read as a drive letter, because on Windows it
    is one.
    """
    key = str(rel).replace("\\", "/")
    if not key.strip():
        raise ValueError("an artifact is named by its path inside the run "
                         "folder; '' is not one")
    if key.startswith("/") or (len(key) > 1 and key[1] == ":"):
        raise ValueError(f"artifact path {rel!r} is absolute - an artifact is "
                         f"named by its path INSIDE the run folder, such as "
                         f"10-intake/00-intake.json")
    if ".." in key.split("/"):
        raise ValueError(f"artifact path {rel!r} climbs out of the run folder "
                         f"with '..' - an artifact is named by its path INSIDE "
                         f"it, such as 10-intake/00-intake.json")
    if "\0" in key:
        # Otherwise `stat` refuses it four frames later with "embedded null
        # character", which does not say WHICH artifact.
        raise ValueError(f"artifact path {rel!r} holds a NUL, which no file "
                         f"system will name")
    return key


def key_refusal(rel: str) -> str:
    """Why `artifact_key` would refuse `rel`, or `""` if it would not.

    **The asymmetry, stated where a reader meets it.** `artifact_key` refuses
    NEW input — `record_artifact` and `restore` take what somebody typed and
    say no at exit 2, because an absolute path in a committed `state.json` is
    how `--restore` came to write outside the run folder. A key ALREADY IN a
    recorded map is not new input: it is what this harness itself wrote
    yesterday, before that refusal existed. Refusing it is refusing our own
    record, and it lands on the person taking a gate — who typed nothing.

    So a reader that re-keys a recorded map asks HERE instead of calling
    `artifact_key` and catching: reading such a key is COULD-NOT-LOOK, and the
    caller keeps the entry exactly as recorded and says so (see
    `feedback.repin`). Refuse what somebody types; never refuse what we wrote.
    """
    try:
        artifact_key(rel)
    except ValueError as exc:
        return str(exc)
    return ""


def restore(target: Path, run_id: str, rel: str, digest: str) -> Path:
    """Put the bytes for `digest` back at `rel`. Records nothing.

    **A copy nobody can read back is the class this project has found most**,
    and item 102 is its worst instance - a wall of digests shipped into every
    adopter's repository that no code anywhere consumed. So the store gets a
    reader in the same commit, and the driver's own refusal names it.

    Deliberately does NOT re-record: putting a draft back is the operator
    looking at what was destroyed, and re-pinning it is a separate act with a
    gate's opinion attached.

    Where it writes is CHECKED before anything is read: a door that puts bytes
    back is a door, and this one used to open onto any path the caller named.
    """
    rel = artifact_key(rel)
    where = kept_path(target, run_id, digest)
    if not where.is_file():
        raise RuntimeError(
            f"no kept copy for {digest} - it was recorded before this run kept "
            f"them, or the artifact was a directory, whose per-file map is at "
            f"{where.name}.tree and is not a restore")
    out = run_dir(target, run_id) / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(where.read_bytes())
    return out


def restore_command(target: Path, run_id: str, rel: str, digest: str) -> str:
    """The one spelling of the restore (item 147), RUNNABLE AS PRINTED (T415).

    It was `tools/sdlc/state.py --target <repo> ...`: no interpreter, no harness
    root, and a placeholder where the target goes - so a person who pasted it at
    the moment they were stuck met "not recognised" instead of their bytes. It is
    now the prefix every SOP teaches, filled with this harness and this target.
    Those are this machine's paths, which is right for a line printed to the
    person on it: the remedy is only ever printed, never written into a run.

    **T474: every value is spelled by `pasteable`.** The artifact and the run id
    were printed bare, and `record_artifact` accepts a space, `;` and `&` - all
    legal in a Windows file name, and reachable here through a glob like P3's
    `30-adr-*.md` - while a run id can come from a branch name. So a pasted remedy
    split the name, or ran whatever followed a `;`. A value no single spelling
    makes safe in bash, PowerShell and cmd gets the step in words instead."""
    h = HARNESS.as_posix()
    parts = [_quoted(h), _quoted(f"{h}/tools/sdlc/state.py"),
             _quoted(Path(target).resolve().as_posix()),
             pasteable(run_id), pasteable(rel), pasteable(digest)]
    if None in parts:
        return (f"tools/sdlc/state.py --restore, by hand: the run id, the artifact's "
                f"name or a path holds a character bash, PowerShell and cmd do not read "
                f"alike, so no one line pastes safely in all three - run {run_id!r}, "
                f"artifact {rel!r}, recorded as {digest}")
    project, script, where, run, artifact, pinned = parts
    return (f"uv run --project {project} python {script} --target {where} "
            f"--restore {run} --artifact {artifact} --digest {pinned}")


def append_record(path: Path, text: str) -> None:
    """Append JSONL `text` (whole lines, newline-terminated) to `path`, starting a
    fresh line first when the file does not end in one.

    T475: every record log appended with `open("a")` and assumed the file already
    ended in a newline. An editor or merge tool that strips it made the next
    record share its line - `}{`, not readable JSON - which in a hash-chained
    ledger refused every later operation on the run, and in a plain log hid two
    records from their reader. THE one append, so the assumption lives nowhere.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lead = ""
    if path.is_file():
        with path.open("rb") as fh:
            fh.seek(0, 2)
            if fh.tell():
                fh.seek(-1, 2)
                lead = "" if fh.read(1) == b"\n" else "\n"
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(lead + text)


#: A value every shell a person pastes into reads as one literal argument bare.
_BARE_ARG = re.compile(r"[A-Za-z0-9._/:+-]+")


def _quoted(value: str) -> str | None:
    """`value` in double quotes, which keep a space, `;`, `&`, `|`, `<`, `>`, `(`,
    `)` and `'` literal in bash, PowerShell and cmd alike - or None when it holds
    what one of them still reads inside them: `$` and a backtick (bash and
    PowerShell expand), `%` (cmd expands), `!` (bash history, cmd's delayed
    expansion), `"` itself, or a control character. Single quotes are no quotes
    at all to cmd, so such a value has no spelling safe in all three."""
    if any(c in '"$`%!' or ord(c) < 32 or ord(c) == 127 for c in value):
        return None
    return f'"{value}"'


def pasteable(value: str) -> str | None:
    """`value` as one argument in every shell: bare when nothing in it is special
    to any of them, else `_quoted` (T474)."""
    return value if _BARE_ARG.fullmatch(value) else _quoted(value)


def record_artifact(target: Path, run_id: str, rel: str) -> dict:
    """Hash an artifact of the run, by its path inside the run folder. A
    directory is an artifact too - see `digest_of`.

    Item 139: the bytes behind the hash are KEPT, so a pin can be undone.

    The path is normalised and checked FIRST, before it is joined onto
    anything: one spelling is one key, and a key that is not inside the run
    folder never reaches the record.
    """
    rel = artifact_key(rel)
    state = load(target, run_id)
    path = run_dir(target, run_id) / rel
    digest = digest_of(path, target)
    keep(target, run_id, rel, digest)
    state.setdefault("artifacts", {})[rel] = digest
    state["updated_at"] = _now()
    save(target, state)
    return state


def drift(target: Path, run_id: str, recorded: dict) -> list[tuple[str, str, str]]:
    """Compare a map of `rel -> digest` against the tree. Returns
    `(rel, kind, detail)` where kind is `missing`, `changed`, `unhashable` or
    `converted`.

    ONE traversal, two vocabularies. `state.verify_artifacts` asks "does this
    still match what was recorded" and `approve.verify_artifacts_for` asks
    "does this still match what was APPROVED" — the same walk with different
    sentences, and they had drifted into two implementations, only one of which
    knew a directory could be an artifact.

    `converted` (T483) is a FILE whose bytes differ from its pin only in line
    endings, and its detail is the lossless remedy. It is its own kind rather
    than `changed` with a detail, because callers act on `changed`:
    `feedback.pending_edits` treats it as a person's edit and re-pins it at the
    next gate, which would approve the bytes a checkout made (review F5). Each
    caller keeps its own sentence and adds the remedy.
    """
    out: list[tuple[str, str, str]] = []
    for rel, digest in sorted(recorded.items()):
        path = run_dir(target, run_id) / rel
        if not (path.is_file() or path.is_dir()):
            out.append((rel, "missing", ""))
            continue
        try:
            current = digest_of(path, target)
        except RuntimeError as exc:
            # A directory emptied since it was recorded is not "missing" and is
            # certainly not unchanged; say which it is.
            out.append((rel, "unhashable", str(exc)))
            continue
        if current != digest:
            remedy = _line_endings_only(target, run_id, rel, path, digest)
            out.append((rel, "converted", remedy) if remedy else (rel, "changed", ""))
    return out


#: The largest file `_line_endings_only` reads whole to test (review F6). The
#: digest itself streams in 64 KiB chunks; this probe holds the file and two
#: converted copies, so past this size the plain `changed` sentence stands.
LINE_ENDING_PROBE_MAX = 8 * 1024 * 1024


def _line_endings_only(target: Path, run_id: str, rel: str, path: Path,
                       digest: str) -> str:
    """The remedy for a FILE whose bytes differ from its pin only in line
    endings, or "" when they do not (or it is a directory, or too large).

    **T483.** The pin must still fail - it is over exact bytes - but "changed
    since it was recorded" sent a person looking for an edit nobody made. There
    are two directions, and the remedy differs (review F1):

    - on disk CRLF, pinned LF: a CHECKOUT converted it (`core.autocrlf`). With
      `.sdlc/.gitattributes` committed, deleting the file and checking it out
      again restores the pinned bytes, and loses nothing - they are the same
      content. A checkout alone does not, wherever a person runs it (T626).
    - on disk LF, pinned CRLF: the pin was taken over CRLF and git stored LF on
      COMMIT, so the pinned bytes are not in the repository and no checkout can
      bring them back. Only a copy that still has CRLF can: re-store that one
      file byte for byte with `git add --renormalize`, once the attributes are
      committed. Telling this case to check out again sent a person round a
      loop that never ended.
    """
    try:
        if not path.is_file() or path.stat().st_size > LINE_ENDING_PROBE_MAX:
            return ""
        data = path.read_bytes()
    except OSError:
        return ""
    where = f"{run_rel(target, run_id)}/{rel}"
    # Spelled by `pasteable` (T474): a printed remedy is pasted, and a bare path
    # with a space or `;` in it would split or run what follows.
    spelled = pasteable(where)
    lf = data.replace(b"\r\n", b"\n")
    if lf != data and f"sha256:{hashlib.sha256(lf).hexdigest()}" == digest:
        # The cause is not always a checkout: an editor that saves CRLF, committed
        # byte for byte under `-text`, looks the same here, and then a checkout
        # hands back the same converted bytes (review G2). The run's KEPT copy
        # (item 139) holds the pinned bytes whatever the commit holds, so it is
        # the remedy whenever it is intact; a checkout only when it is not - in a
        # clone converted before the attributes existed, it was converted too.
        kept = kept_path(target, run_id, digest)
        try:
            intact = kept.is_file() and sha256_of(kept) == digest
        except OSError:
            # A kept copy that cannot be read (locked, no permission) is not one
            # to restore from; it falls to the checkout below rather than turning
            # the whole drift report into a traceback (review R1).
            intact = False
        if intact:
            return (f"only its line endings differ - a checkout (core.autocrlf) or an "
                    f"editor converted them. The run's kept copy still holds the pinned "
                    f"bytes: {restore_hint(run_id, rel, digest)} puts them back and loses "
                    f"nothing")
        # T626: deleted first. Once git's index is refreshed outside its
        # racy-timestamp window - any `git status` a second after the clone -
        # git trusts the converted file's stat, and a checkout alone rewrites
        # nothing (measured on Windows: 0 of 3; deleting first, 3 of 3).
        remedy = (f"deleting the file and then running `git checkout -- {spelled}`"
                  if spelled else f"deleting {where!r} and checking it out again")
        return (f"only its line endings differ - a checkout (core.autocrlf) converted "
                f"them. With `.sdlc/.gitattributes` (written by sdlc-init.py) committed, "
                f"{remedy} restores the pinned bytes and loses nothing - a checkout alone "
                f"rewrites nothing once git has recorded the converted file as unchanged")
    crlf = lf.replace(b"\n", b"\r\n")
    if crlf != data and f"sha256:{hashlib.sha256(crlf).hexdigest()}" == digest:
        remedy = (f"`git add --renormalize -- {spelled}`" if spelled
                  else f"re-adding {where!r} with --renormalize")
        return (f"only its line endings differ - it was pinned over CRLF, and git "
                f"stored it as LF when it was committed (core.autocrlf), so no checkout "
                f"restores it. On a machine whose copy still ends its lines in CRLF, "
                f"commit `.sdlc/.gitattributes` (written by sdlc-init.py), then run "
                f"{remedy} and commit")
    return ""


def restore_hint(run_id: str, rel: str, digest: str) -> str:
    """The restore, in `stage.record_command`'s placeholder form (review G2).

    A `converted` drift's detail can land in a COMMITTED record - `feedback`
    keeps it on an `artifact-lost` row - so it carries no machine path, unlike
    `restore_command`, which is only ever printed. Every value is spelled by
    `pasteable` (T474), and one that no shell reads alike is named in words.
    """
    run, art, pin = pasteable(run_id), pasteable(rel), pasteable(digest)
    if None in (run, art, pin):
        return (f"tools/sdlc/state.py --restore, by hand, with run {run_id!r}, artifact "
                f"{rel!r} and digest {digest}")
    return (f"`tools/sdlc/state.py --target <repo> --restore {run} --artifact {art} "
            f"--digest {pin}`")


def drift_line(rel: str, kind: str, detail: str, said: dict[str, str]) -> str:
    """One drift row in a caller's own vocabulary. `said` maps each kind to the
    caller's sentence; a `converted` file keeps the caller's `changed` sentence
    and ADDS its remedy, so a gate still names itself (review F5)."""
    if kind == "converted":
        return f"{rel}: {said['changed']} - {detail}"
    return f"{rel}: {detail or said[kind]}"


def verify_artifacts(target: Path, run_id: str) -> list[str]:
    """Every recorded artifact that no longer matches its hash, or is gone.
    An empty list is the only clean answer; the gates read this."""
    state = load(target, run_id)
    said = {"missing": "recorded, now missing", "changed": "changed since it was recorded"}
    return [drift_line(rel, kind, detail, said)
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
        raise LedgerError(
            f"run {run_id} is not active under {RUNS_REL.as_posix()}")
    dest = archive_dir(target) / run_id
    if dest.exists():
        raise LedgerError(
            f"{RUNS_REL.as_posix()}/{ARCHIVE_NAME}/{run_id} already exists "
            f"- refusing to merge two runs")
    dest.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dest)
    return dest


def list_runs(target: Path) -> list[dict]:
    """Active runs first, then archived; each row carries where it lives.

    **A run whose record cannot be read is a ROW**, kind `unreadable`, carrying
    the reason. Three answers, kept apart, because they are three different
    facts about the estate: a run with a record, a run with no `state.json` at
    all, and a run whose `state.json` is there and unreadable. It used to be
    two — one damaged file aborted the whole listing, so the healthy runs went
    unmentioned at exactly the moment something was wrong, and a file that was
    valid JSON but not an object was a traceback rather than an answer.
    """
    rows = []
    for where, base in (("active", runs_dir(target)), ("archived", archive_dir(target))):
        if not base.is_dir():
            continue
        for d in sorted(p for p in base.iterdir() if p.is_dir()):
            if where == "active" and d.name == ARCHIVE_NAME:
                continue
            try:
                state = read_state(d / STATE_NAME)
            except FileNotFoundError:
                state = {}
            except Unreadable as exc:
                rows.append({"run_id": d.name, "where": where,
                             "kind": "unreadable", "phase": "?", "status": "?",
                             "title": f"({exc.why})", "unreadable": exc.why})
                continue
            # A hand-edited record can be valid JSON and a dict yet carry a JSON
            # `null` where a phase/status/kind belongs (T433). Those three
            # columns are printed with a `:<N` spec (here and in status.py), and
            # `None.__format__` raises `TypeError` - which `main`'s
            # `except (ValueError, RuntimeError)` cannot catch - so both doors
            # handed the operator a bare traceback for the one document class
            # they exist to read back. A null field is a damaged record: it
            # joins the same `unreadable` row `read_state`'s refusals already
            # produce, named by which field is null. ABSENT is not null - a
            # missing key keeps its `.get` default (a run with no `kind` has
            # always read as "?"), so only a key that is PRESENT and null is
            # caught here. `title` is NOT in this set: it is printed without a
            # format spec (a null renders harmlessly), and a null title has its
            # own tested meaning downstream (connect.py falls back to the
            # specification title) that this must not override.
            nulled = [k for k in ("kind", "phase", "status")
                      if k in state and state[k] is None]
            if nulled:
                why = f"records a null {', '.join(nulled)}"
                rows.append({"run_id": d.name, "where": where,
                             "kind": "unreadable", "phase": "?", "status": "?",
                             "title": f"({why})", "unreadable": why})
                continue
            rows.append({"run_id": d.name, "where": where,
                         "kind": state.get("kind", "?"),
                         "phase": state.get("phase", "?"),
                         "status": state.get("status", "?"),
                         "title": state.get("title", UNTITLED)})
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
    what.add_argument("--restore", metavar="RUN_ID",
                      help="put a kept artifact back (needs --artifact and "
                           "--digest); records nothing")
    ap.add_argument("--kind", choices=KINDS, default="requirement")
    ap.add_argument("--title", default="")
    ap.add_argument("--id", default=None, help="run id (default: time + title digest)")
    ap.add_argument("--phase", choices=PHASES)
    ap.add_argument("--status", choices=STATUSES)
    ap.add_argument("--note", default="")
    ap.add_argument("--artifact", metavar="REL", help="path inside the run folder")
    ap.add_argument("--digest", metavar="SHA",
                    help="which kept version to restore, as recorded in "
                         "state.json or in an approval entry")
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
                # T398: the relative runs directory, not an absolute machine
                # path - the twin of the T256/T381 refusals. The runs are
                # ABSENT here, so this names the directory (RUNS_REL), the same
                # choice state.py:320 and status.py make for a missing run.
                print(f"no runs under {RUNS_REL.as_posix()}")
            for r in rows:
                print(f"  {r['run_id']}  {r['where']:<8} {r['phase']:<4} "
                      f"{r['status']:<17} {r['kind']:<14} {r['title']}")
            unreadable = [r for r in rows if r["kind"] == "unreadable"]
            if unreadable:
                print(f"!! {len(unreadable)} of {len(rows)} run(s) could not be "
                      f"read - listed above, not omitted")
                # Not reported as clean only when NOTHING could be read: a
                # listing where every row is a shrug is not a listing.
                if len(unreadable) == len(rows):
                    return 2
        elif args.advance:
            state = advance(target, args.advance, args.phase, args.status, args.note)
            print(f"run {state['run_id']}: {state['phase']} / {state['status']}")
        elif args.record:
            if not args.artifact:
                print("!! --record needs --artifact <path inside the run folder>")
                return 2
            state = record_artifact(target, args.record, args.artifact)
            # Read back through the SAME function that wrote the key, or the
            # Windows spelling of a path indexes a map it is not in.
            key = artifact_key(args.artifact)
            print(f"recorded {key}: {state['artifacts'][key]}")
        elif args.verify:
            problems = verify_artifacts(target, args.verify)
            for p in problems:
                print(f"  !! {p}")
            print(f"artifacts: {'all match' if not problems else str(len(problems)) + ' problem(s)'}")
            return 1 if problems else 0
        elif args.archive:
            dest = archive(target, args.archive, force=args.force)
            print(f"archived -> {dest}")
        elif args.restore:
            # Item 139. Both arguments are REQUIRED rather than guessed: the
            # point of a content-addressed store is that a name alone does not
            # say WHICH version, and restoring the wrong draft over a live one
            # would be this fix becoming the defect.
            if not args.artifact or not args.digest:
                print("!! --restore needs --artifact and --digest; the digest "
                      "is in state.json or in the approval entry that pinned "
                      "the version you want back")
                return 2
            out = restore(target, args.restore, args.artifact, args.digest)
            print(f"restored -> {out} (not re-recorded: pinning it again is a "
                  f"separate act)")
    except (ValueError, RuntimeError) as exc:
        print(f"!! {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
