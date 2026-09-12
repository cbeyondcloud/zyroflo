"""External sources become runs, through the one door. Items 56 and 49.

A Jira issue, a GitHub issue, a monitoring alert: three names for the same act —
something outside the repository says work is needed, and the lifecycle should be
able to start from it without anybody retyping the request.

**It names no vendor.** The harness has never known what Jira is and does not learn
here. A connector is a MAPPING an adopter writes — *"in this payload the title is
at `fields.summary`"* — read from `.sdlc/connectors/<name>.json`. Jira, GitHub and
an alert manager are then the same code path with three declarations, which is why
this closes the Jira/GitHub stubs and the monitoring stub together rather than
growing three half-integrations.

**One door.** `intake.open_run`, the same one `debt.py` and P10's auto-intake use.
A second way to open a run would be a second answer to what a run IS, and the two
would drift.

**One home for the open-run policy, which is why this module exists at all.**
`debt.py` and `verify.py` carried near-identical copies of *"a run that CLOSED does
not hold this filed"* — the same eighteen lines, differing only in how they built a
title. A third copy is where that becomes a pattern nobody maintains, so it moved
here and both call it.

**Filing is an explicit act.** `--file` writes; without it nothing is opened. Item
17's rule, which `corpus_harvest --write` and `debt.py --file` already follow: a
tool that filed on being run would open a backlog item every time somebody looked.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import cli  # noqa: E402
from tools.sdlc import state as ledger  # noqa: E402

SCHEMA_VERSION = 1

#: Where an adopter's mappings live. One file per source, named by the source.
CONNECTORS = Path(".sdlc") / "connectors"

#: What a mapping must say. `title` is the only required path, because a run with
#: no title is one nobody can find again; everything else has a defensible default.
REQUIRED = ("title",)


class ConnectError(RuntimeError):
    """The source, its mapping, or its payload could not be read."""


# ── the policy that had two copies ──────────────────────────────────────────


def open_runs_for(target: Path, title: str, spec: str) -> list[str]:
    """Runs already open for this specification. **The one home for the rule.**

    `intake.duplicates` finds prior runs with the same specification - the one
    duplicate finder this package has - and the policy is the part worth stating:
    **a run that CLOSED does not hold the item filed.** If the thing is still true
    after somebody's attempt went away, it outlived the attempt.

    Lifted out of `debt.py` and `verify.py`, which carried the same eighteen lines
    differing only in how they built a title. Three copies is where a rule stops
    being maintained; two was already one too many.

    **A run nobody could read is COULD-NOT-LOOK, and this refuses.** It used to
    be skipped, on the reading that "a run whose state cannot be read holds
    nothing" - so the answer was an empty list, which every caller reads as *no
    run holds this, go ahead and file*. Measured: with one candidate's
    `state.json` unreadable, the CLI exited 0 saying `filed ...` and opened a
    SECOND run for an item that already had one, into a ledger already damaged
    enough to have caused the read to fail.

    The care was already taken upstream and this was throwing it away.
    `state.list_runs` makes such a run a ROW carrying its reason and
    `intake.duplicates` forwards it saying whether the specification repeats it
    is unknown - and then this function re-derived the same failure and called it
    a clean miss. `release.notes_from_run` refuses on the same read with the same
    sentence; `worktree.active_runs` resolves the same fork the conservative way,
    counting an unreadable run as live, because its answer is a warning rather
    than a write. Here the answer gates a write, so unknown is said out loud and
    nothing is opened: the external item still exists and can be filed once the
    ledger can be read, whereas a duplicate run is already in the record.

    **And it is scoped to the item nobody could answer about.** The refusal is
    only as good as its blast radius: refusing for every unreadable run, before
    anything was compared, made ONE damaged folder refuse intake from every
    connector for every item, permanently - which is the outage
    `intake.duplicates`'s own docstring records item 158 removing. So
    `duplicates` compares the specification even when the state is unreadable
    (`00-intake.json` is written once at P0; `state.json` on every advance, so
    it is the one that breaks), and a damaged run that demonstrably holds a
    different item never reaches this loop. What arrives here is a run that
    might be this one and cannot be checked, which is the only thing worth
    refusing over.
    """
    from tools.sdlc import intake as entry

    digest = entry.spec_hash(title, spec)
    try:
        prior = entry.duplicates(Path(target), digest, title)
    except (OSError, ValueError) as exc:
        raise ConnectError(f"the run ledger could not be read: {exc}") from None
    live = []
    for row in prior:
        # `duplicates` already carries could-not-look about the state, under the
        # key `list_runs` set - so it is READ here rather than re-derived by
        # loading the same state a second time. That matters beyond tidiness:
        # re-deriving it here put the unknown downstream of the specification
        # comparison, where it could not be scoped, and every unreadable run
        # became a candidate before anything was compared. The second arm stays
        # because a run folder with NO `state.json` is not that row at all -
        # `list_runs` gives it an ordinary row, so its specification is compared
        # like any other and it reaches here only when it matched.
        why, st = row.get("unreadable"), None
        if not why:
            try:
                st = ledger.load(Path(target), row["run_id"])
            except Exception as exc:
                why = str(exc)
        if why:
            raise ConnectError(
                f"run {row['run_id']} is already in the ledger but its state "
                f"could not be read: {why} - so whether it already holds this "
                f"item is unknown. Filing would risk a second run for one item; "
                f"this refuses instead, because the item outside the repository "
                f"keeps until the ledger can be read") from None
        if st.get("status") not in ledger.TERMINAL:
            live.append(row["run_id"])
    return live


# ── the mapping an adopter writes ───────────────────────────────────────────


def mapping_for(target: Path, source: str) -> dict:
    """One source's declared mapping, or a refusal naming what is missing."""
    path = Path(target) / CONNECTORS / f"{source}.json"
    if not path.is_file():
        raise ConnectError(
            f"{path} does not exist - this repository declares no connector "
            f"called {source!r}. A connector is a mapping you write, because the "
            f"harness does not know what your tracker's fields are called")
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConnectError(f"{path} could not be read: {exc}") from None
    if not isinstance(found, dict):
        raise ConnectError(f"{path} holds {type(found).__name__}, not a mapping")
    missing = [k for k in REQUIRED if not str(found.get(k) or "").strip()]
    if missing:
        raise ConnectError(
            f"{path} declares no {', '.join(missing)} - a run with no title is "
            f"one nobody can find again, so this is refused rather than filed "
            f"under a generated name")
    return found


def dig(payload: Any, path: str) -> Any:
    """One dotted path into a payload, or None.

    **None means the path was not there**, which the caller must not confuse with
    a field that was present and empty - a distinction this project has had to
    make in four other readers.
    """
    current = payload
    for part in str(path).split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        # `isdecimal`, not `isdigit`: `isdigit` is True for superscripts and
        # other Unicode digits that `int()` then REFUSES, so the guard admitted
        # values its own body crashed on - an uncaught ValueError escaping the
        # module's ConnectError vocabulary.
        elif isinstance(current, list) and part.isdecimal() and int(part) < len(current):
            current = current[int(part)]
        else:
            return None
    return current


def spec_for(source: str, mapping: dict, payload: dict) -> tuple[str, str]:
    """`(title, specification)` for one external item.

    **Deterministic**, because `intake.spec_hash` over this text is the identity
    `duplicates` matches on. Nothing here reads a clock or a run id: the same issue
    seen twice must be the same item, or the connector files a new run every time
    anybody syncs.
    """
    title = dig(payload, mapping["title"])
    if title is None or not str(title).strip():
        raise ConnectError(
            f"the payload has nothing at {mapping['title']!r}, which this "
            f"connector declares as the title - so either the mapping is wrong or "
            f"this is not the payload it describes")
    title = str(title).strip()

    body = dig(payload, mapping.get("body", "")) if mapping.get("body") else None
    external = dig(payload, mapping.get("id", "")) if mapping.get("id") else None
    link = dig(payload, mapping.get("url", "")) if mapping.get("url") else None

    lines = [f"title: {title}", "", f"kind: {mapping.get('kind', 'requirement')}",
             "", "## What was reported", "",
             str(body).strip() if body else "*The source carried no description.*",
             "", "## Where it came from", ""]
    lines.append(f"Filed by `connect.py` from the `{source}` connector, not written "
                 f"by hand.")
    if external is not None:
        lines.append(f"Its identity there is `{external}`.")
    if link:
        lines.append(f"Reported at {link}.")
    lines += ["", "Nothing about this text varies per invocation, so the same item "
              "seen twice is the same run rather than a new one.", ""]
    return title, "\n".join(lines)


# ── filing ──────────────────────────────────────────────────────────────────


def file_one(target: Path, source: str, payload: dict, *,
             allow_dirty: bool = False, resolved: tuple | None = None) -> dict | None:
    """Open a run for one external item, or None when one is already open.

    `resolved` is `(mapping, title, spec, live)` when the caller has already
    computed them. **`open_runs_for` is a full ledger sweep** - `list_runs`
    reads every run's state, `duplicates` reads every run's intake, and each
    candidate is loaded again - so recomputing here made filing one item cost
    three sweeps (this one, the CLI's own, and `open_run`'s), growing with the
    ledger rather than with the work.
    """
    from tools.sdlc import intake as entry

    if resolved is None:
        mapping = mapping_for(Path(target), source)
        title, spec = spec_for(source, mapping, payload)
        live = open_runs_for(Path(target), title, spec)
    else:
        mapping, title, spec, live = resolved
    if live:
        return None
    return entry.open_run(Path(target), spec,
                          kind=str(mapping.get("kind", "requirement")),
                          src_kind="connector", src_name=source,
                          allow_dirty=allow_dirty)


def read_payload(where: str) -> dict:
    """One JSON payload, from a file or stdin. Refuses anything that is not an
    object, because a list of issues is a different act than filing one."""
    try:
        raw = sys.stdin.read() if where == "-" else Path(where).read_text(
            encoding="utf-8")
    except OSError as exc:
        raise ConnectError(f"the payload could not be read: {exc}") from None
    try:
        found = json.loads(raw)
    except ValueError as exc:
        raise ConnectError(f"the payload is not JSON: {exc}") from None
    if not isinstance(found, dict):
        raise ConnectError(
            f"the payload is a {type(found).__name__}, not one item. Filing a "
            f"list would open runs nobody counted; pass them one at a time")
    return found


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", default=".", metavar="DIR")
    ap.add_argument("--source", required=True, metavar="NAME",
                    help="a connector declared in .sdlc/connectors/<name>.json")
    ap.add_argument("--payload", required=True, metavar="FILE",
                    help="the item as JSON, or - for stdin")
    ap.add_argument("--file", action="store_true",
                    help="open a run; without it nothing is written")
    ap.add_argument("--allow-dirty", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    target = Path(args.target).resolve()
    payload = read_payload(args.payload)
    mapping = mapping_for(target, args.source)
    title, spec = spec_for(args.source, mapping, payload)
    live = open_runs_for(target, title, spec)

    if not args.file:
        found = {"source": args.source, "title": title, "already_open": live,
                 "would_file": not live}
        if args.json:
            print(json.dumps(found, indent=2, sort_keys=True))
        else:
            print(f"{args.source}: {title}")
            print(f"  already open: {', '.join(live) if live else 'no'}")
            print("  --file opens a run; nothing was written")
        return 0

    opened = file_one(target, args.source, payload, allow_dirty=args.allow_dirty,
                      resolved=(mapping, title, spec, live))
    if opened is None:
        print(f"already open ({', '.join(live)}) - not filed again")
        return 0
    if args.json:
        print(json.dumps({"run_id": opened["run_id"], "title": title},
                         indent=2, sort_keys=True))
    else:
        print(f"filed {opened['run_id']}: {title}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """The door. `2` is could-not-look, matching the other intake-side CLIs."""
    cli.use_utf8()
    return cli.door(_main, argv, "the connector", 2)


if __name__ == "__main__":
    raise SystemExit(main())
