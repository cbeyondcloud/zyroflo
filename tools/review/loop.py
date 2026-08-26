"""The self-review loop's exit condition — item 36: categorical, and computed.

`SKILL.md` has always described this loop correctly and nothing computed it.
An exit condition that lives only in prose is one the runner evaluates about
itself, which is the same as not having one. Three things were wrong the moment
anybody looked:

**The two vocabularies never met.** The deterministic pre-pass emits rdjson
severities — `ERROR`, `WARNING`, `INFO` — and `block_on` is written in
`critical, high, medium, low`. Comparing one list against the other matches
nothing, so a loop asked "is any `critical` or `high` still open?" could not
see a single machine finding. A leaked credential exits the loop clean. The map
below is the bridge, and an unmapped severity is a REFUSAL: a finding whose
severity cannot be read has not been shown to be outside `block_on`.

**A finding could be deferred out of the gate.** `deferred` is an honest
disposition for a low-severity nit and it is the exact shape of a hole at
`critical`: agree it is real, agree not to fix it, exit. Deferral is allowed
strictly below the `block_on` line and refused at or above it.

**The cap could be silent.** Stopping at `max_iterations` with blockers still
open is legal — but it is a *different* exit, and it owes P7 a list. Exit code
3 says so, and the loop record names every finding that survived.

The rubric score is reported and never read. That is enforced by shape, not by
intention: `may_exit` cannot see a score, because it does not take one.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools import cli  # noqa: E402
from tools.review import hitl, prepass  # noqa: E402
from tools.sdlc import policy as guardrails  # noqa: E402
from tools.sdlc import state as ledger  # noqa: E402

# One console rule, in tools/cli.py - this module had its own copy.
cli.use_utf8()

SCHEMA_VERSION = 1

#: The severity vocabulary `block_on` is written in, worst first.
SEVERITIES = ("critical", "high", "medium", "low")

#: rdjson -> `block_on`. The bridge that did not exist.
RDJSON_SEVERITY = {"ERROR": "high", "WARNING": "medium", "INFO": "low"}

#: What a tool FOUND outranks how it labelled it. gitleaks stamps one severity
#: on everything, and everything it finds is a credential; calling that a
#: "high" because the JSON says ERROR would be taking a schema's word over a
#: fact about the finding.
TOOL_SEVERITY = {"gitleaks": "critical"}

#: A diagnostic may state its severity in `block_on`'s OWN vocabulary instead of
#: rdjson's, by carrying this key. rdjson has three levels and `block_on` has
#: five, so a CVSS 9.8 arriving as `ERROR` becomes `high` — and a profile
#: blocking on `critical` alone lets it through. Item 36 found this mismatch
#: between the pre-pass and `block_on`; this is the same mismatch one level
#: along, where the tool DOES know how severe its finding is and had no way to
#: say so. Presence of the key means the tool is speaking this vocabulary; a
#: blank value means it looked and could not grade, which is UNGRADED and holds
#: the loop open rather than being rounded to something convenient.
SEVERITY_FIELD = "severity_class"

#: Dispositions that leave a finding open. `rejected` closes it — somebody
#: judged it not real. `deferred` closes it only below the block_on line, which
#: `assess` enforces separately because that is where the hole was.
OPEN_DISPOSITIONS = ("accepted", "deferred", "")

LOOP_RECORD = "60-review-loop.json"
DEFAULT_CAP = 5


class LoopError(RuntimeError):
    """The loop cannot be judged, which is never the same as passing it."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def rank(severity: str) -> int:
    try:
        return SEVERITIES.index(severity)
    except ValueError:
        raise LoopError(
            f"unknown severity {severity!r} - one of {', '.join(SEVERITIES)}. A "
            f"finding whose severity cannot be read has not been shown to be "
            f"outside block_on, so the loop refuses rather than guessing") from None


# ── the two finding sources, in one vocabulary ───────────────────────────────


def _list(value: object) -> list:
    """A list, or nothing at all.

    `or []` covers a present null and an empty one and looks like it
    covers the class - it does not cover a truthy value of the wrong
    type, which is the same document being wrong a different way.
    `prepass.tool_rows` asks isinstance for this reason; this asked
    `or []` and still died on `"sources": 7`."""
    return value if isinstance(value, list) else []


def _line_of(loc: dict) -> int:
    """The line from an rdjson location, or 0 when it does not carry one.

    Every level of `range.start.line` can be a present null, and the previous
    spelling wrapped the lot in `int()`, which raises on a non-numeric line as
    well - so a malformed location took the whole translation down."""
    rng = loc.get("range")
    start = rng.get("start") if isinstance(rng, dict) else None
    line = start.get("line") if isinstance(start, dict) else None
    return line if isinstance(line, int) and not isinstance(line, bool) else 0


def from_prepass(report: dict) -> list[dict]:
    """The deterministic pre-pass's findings, translated into `block_on`'s
    vocabulary. An unmapped rdjson severity refuses.

    CLAUDE.md: "Zero silent drops ... it never discards it." A malformed entry
    used to `continue` into nothing - no count, no trace - so a runner
    emitting one junk sources[] entry ahead of a real gitleaks source dropped
    findings the loop was supposed to weigh. They are refused by name now, the
    way the sibling `prepass.tool_rows` keeps an unreadable row rather than
    letting it disappear."""
    if not isinstance(report, dict):
        raise LoopError("the pre-pass report is not an object, so nothing in "
                        "it can be read as findings")
    out: list[dict] = []
    dropped: list[str] = []
    # `or []`, not a `.get` default: this reads documents it did not write
    # (a runner's prepass.json, and the SCA result `suite.py` hands it),
    # and a PRESENT null is not a missing key - the default never fires
    # for it. Item 40 met the shape in the field when a clean scan emitted
    # `"results": null`.
    for source in _list(report.get("sources")):
        if not isinstance(source, dict):
            dropped.append("a sources[] entry that is not an object")
            continue
        named = source.get("source")
        tool = str(named.get("name", "")).strip() if isinstance(named, dict) else ""
        for diag in _list(source.get("diagnostics")):
            if not isinstance(diag, dict):
                dropped.append(f"a diagnostic from {tool or 'an unnamed tool'} "
                               f"that is not an object")
                continue
            if SEVERITY_FIELD in diag:
                stated = str(diag.get(SEVERITY_FIELD, "")).strip().lower()
                if stated and stated not in SEVERITIES:
                    raise LoopError(
                        f"{tool or 'a pre-pass tool'} stated {SEVERITY_FIELD} "
                        f"{stated!r}, which is not one of {', '.join(SEVERITIES)} - "
                        f"a tool speaking this vocabulary must speak it exactly")
                severity = stated
            else:
                raw = str(diag.get("severity", "")).strip().upper()
                if raw not in RDJSON_SEVERITY:
                    raise LoopError(
                        f"{tool or 'a pre-pass tool'} reported severity "
                        f"{raw or '(blank)'!r}, which no rule maps into "
                        f"{', '.join(SEVERITIES)} - the loop cannot say whether it "
                        f"is a blocker, so it does not say it is not")
                severity = TOOL_SEVERITY.get(tool, RDJSON_SEVERITY[raw])
            loc = diag.get("location")
            loc = loc if isinstance(loc, dict) else {}
            code = diag.get("code")
            code = code if isinstance(code, dict) else {}
            out.append({
                "id": str(code.get("value", "")) or "(unnamed)",
                "source": "prepass",
                "tool": tool,
                "severity": severity,
                "disposition": "",
                "path": str(loc.get("path", "")),
                "line": _line_of(loc),
                "message": str(diag.get("message", "")),
            })
    if dropped:
        raise LoopError(
            "the pre-pass report carries entries this loop cannot read: "
            + "; ".join(sorted(set(dropped)))
            + " - a finding that cannot be read is not a finding that is absent")
    return out


def from_session(session: dict | None) -> list[dict]:
    """The agent's and the human's findings, from the open review session.

    A finding recorded with no severity counts as UNGRADED and is treated as
    open, loudly. `hitl.py` accepts a blank severity on purpose — the field is
    optional to keep the recording step cheap — and cheap-to-record is exactly
    how an ungraded critical gets past a severity gate.
    """
    out: list[dict] = []
    for row in (session or {}).get("findings", []):
        severity = str(row.get("severity", "")).strip().lower()
        out.append({
            "id": str(row.get("id", "")) or "(unnamed)",
            "source": str(row.get("source", "")) or "agent",
            "tool": "",
            "severity": severity,
            "disposition": str(row.get("disposition", "")).strip().lower(),
            "path": str(row.get("path", "")),
            "line": int(row.get("line", 0) or 0),
            "message": "",
        })
    return out


# ── the exit condition ───────────────────────────────────────────────────────


def assess(findings: list[dict], block_on: list[str]) -> dict:
    """Which findings hold the loop open, and why — categorically.

    Deliberately takes no score. `review_threshold` is reported for continuity
    and can never move this verdict, and the cheapest way to guarantee that is
    for the function that decides to be unable to see it.
    """
    if not block_on:
        raise LoopError(
            "block_on is empty, so nothing can hold the loop open - that is not a "
            "faster review, it is none. The tighten-only floor (item 35) refuses "
            "this in a profile; it is refused here too, in case it arrived some "
            "other way")
    blocking = {s for s in block_on}
    if unknown := sorted(blocking - set(SEVERITIES)):
        raise LoopError(f"block_on names {', '.join(unknown)}, which "
                        f"{'are' if len(unknown) > 1 else 'is'} not "
                        f"{'severities' if len(unknown) > 1 else 'a severity'} - "
                        f"one of {', '.join(SEVERITIES)}")
    open_rows: list[dict] = []
    for f in findings:
        # Rejection is judged FIRST, and that ordering is load-bearing: somebody
        # decided this is not a real finding, and an ungraded non-finding cannot
        # be graded, so checking severity first would leave the loop unclearable.
        if f["disposition"] == "rejected":
            continue
        if not f["severity"]:
            open_rows.append({**f, "why": "ungraded"})
            continue
        if f["severity"] not in blocking:
            continue
        if f["disposition"] == "deferred":
            open_rows.append({**f, "why": "deferred-blocker"})
        elif f["disposition"] in OPEN_DISPOSITIONS:
            open_rows.append({**f, "why": "open"})
    counts: dict[str, int] = {}
    for f in findings:
        key = f["severity"] or "(ungraded)"
        counts[key] = counts.get(key, 0) + 1
    return {
        "block_on": sorted(blocking, key=rank),
        "open": open_rows,
        "ungraded": [f for f in open_rows if f["why"] == "ungraded"],
        "deferred_blockers": [f for f in open_rows if f["why"] == "deferred-blocker"],
        "counts": dict(sorted(counts.items())),
    }


def may_exit(verdict: dict) -> bool:
    """The whole gate, in one line, over data that contains no score."""
    return not verdict["open"]


def reasons(verdict: dict) -> list[str]:
    """Why the loop is still open, as sentences somebody can act on. Each open
    row carries its own `why`, so two identical findings cannot be explained by
    whichever of them was classified first."""
    out: list[str] = []
    for f in verdict["open"]:
        where = f" at {f['path']}:{f['line']}" if f["path"] else ""
        if f["why"] == "ungraded":
            out.append(f"{f['id']} ({f['source']}){where} carries no severity, so it "
                       f"cannot be shown to be outside block_on - grade it, or it "
                       f"holds the loop")
        elif f["why"] == "deferred-blocker":
            out.append(f"{f['id']} is {f['severity']} and was DEFERRED - a severity in "
                       f"block_on cannot be deferred out of the gate; fix it, or "
                       f"reject it as not real and say why")
        else:
            out.append(f"{f['id']} [{f['severity']}]{where} is open")
    return out


# ── the loop's own record ────────────────────────────────────────────────────


def record_path(target: Path, run_id: str) -> Path:
    return ledger.run_dir(target, run_id) / LOOP_RECORD


def load_record(target: Path, run_id: str) -> dict:
    path = record_path(target, run_id)
    if not path.is_file():
        return {"schema_version": SCHEMA_VERSION, "iterations": [], "capped": False}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise LoopError(f"{path} is not readable JSON ({exc}) - the loop's own "
                        f"record deciding nothing is worse than no record") from None
    if not isinstance(data, dict):
        raise LoopError(f"{path} is not an object")
    data.setdefault("iterations", [])
    return data


def cap_of(target: Path) -> int:
    """`review.max_iterations`, or the spec's 5. A cap of 0 or less is refused:
    a loop that may not run once cannot review anything."""
    value = guardrails.profile_scalar(target, "max_iterations")
    if value is None:
        return DEFAULT_CAP
    try:
        cap = int(value)
    except (TypeError, ValueError):
        raise LoopError(f"review.max_iterations is {value!r}, which is not a "
                        f"number") from None
    if cap < 1:
        raise LoopError(f"review.max_iterations is {cap} - a loop capped below one "
                        f"iteration never reviews anything")
    return cap


def block_on_of(target: Path) -> list[str]:
    """`block_on` as the run is governed, not as the profile wrote it: the
    regime can add a severity the profile never listed (item 63), and reading
    the profile alone would let a `regulated` run gate like a solo one."""
    rules = guardrails.load(target)
    dials = guardrails.profile_dials(target)
    resolved = guardrails.effective(rules, dials, guardrails.gate_overrides(target))
    return [str(s) for s in resolved["block_on"]]


def iterate(target: Path, run_id: str, findings: list[dict],
            score: float | None = None, note: str = "") -> dict:
    """One pass of the loop: assess, append to the record, and say what next.

    The iteration NUMBER is counted from the record rather than taken from the
    caller. A cap the thing being capped gets to report is not a cap.
    """
    verdict = assess(findings, block_on_of(target))
    record = load_record(target, run_id)
    cap = cap_of(target)
    number = len(record["iterations"]) + 1
    entry = {
        "at": _now(),
        "iteration": number,
        "counts": verdict["counts"],
        "open": [{k: f[k] for k in ("id", "source", "severity", "path", "line")}
                 for f in verdict["open"]],
        "block_on": verdict["block_on"],
        "note": note,
    }
    if score is not None:
        # Reported for continuity across runs and providers. Nothing above reads
        # it; `assess` is not given it at all.
        entry["score_reported"] = score
    record["iterations"].append(entry)
    clean = may_exit(verdict)
    record["capped"] = bool(not clean and number >= cap)
    record["cap"] = cap
    record["outcome"] = "clean" if clean else ("capped" if record["capped"] else "open")
    folder = ledger.run_dir(target, run_id)
    if not folder.is_dir():
        raise LoopError(f"no run folder for {run_id} at {folder}")
    record_path(target, run_id).write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"verdict": verdict, "record": record, "iteration": number, "cap": cap,
            "outcome": record["outcome"]}


def exit_code(outcome: str) -> int:
    """0 clean · 1 iterate again · 3 capped with blockers open.

    3 is not a worse 1. It means the loop STOPPED with findings alive, and they
    are owed to P7 as tracker rows; a caller that treats it as "keep going"
    loops forever and one that treats it as "clean" drops them.
    """
    return {"clean": 0, "open": 1, "capped": 3}[outcome]


def render(result: dict) -> str:
    verdict, record = result["verdict"], result["record"]
    lines = [f"iteration {result['iteration']} of at most {result['cap']} - "
             f"{result['outcome'].upper()}",
             f"block_on: {', '.join(verdict['block_on'])}",
             f"findings by severity: " +
             (", ".join(f"{k} {v}" for k, v in verdict["counts"].items()) or "(none)")]
    if score := record["iterations"][-1].get("score_reported"):
        lines.append(f"rubric score: {score} - reported for continuity, never the gate")
    for reason in reasons(verdict):
        lines.append(f"  !! {reason}")
    if result["outcome"] == "capped":
        lines.append(f"the cap is reached and {len(verdict['open'])} finding(s) are "
                     f"still open - they are owed to P7 as tracker rows, not dropped")
    elif result["outcome"] == "clean":
        lines.append("no block_on severity is open - the loop may exit")
    return "\n".join(lines)


# ── CLI ──────────────────────────────────────────────────────────────────────


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", required=True, metavar="DIR",
                    help="the repository the run and the review session live in")
    ap.add_argument("--run", required=True, metavar="ID", help="the run id")
    ap.add_argument("--prepass", metavar="FILE",
                    help="the pre-pass report (prepass.json) to fold in")
    ap.add_argument("--iterate", action="store_true",
                    help="assess one iteration and append it to the loop record")
    ap.add_argument("--score", type=float, default=None, metavar="N",
                    help="the rubric score, RECORDED and never read by the gate")
    ap.add_argument("--note", default="", metavar="TEXT")
    ap.add_argument("--show", action="store_true", help="the loop record so far")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    target = Path(args.target).resolve()

    try:
        if args.show:
            record = load_record(target, args.run)
            print(json.dumps(record, indent=2, sort_keys=True) if args.json else
                  f"{len(record['iterations'])} iteration(s), "
                  f"outcome {record.get('outcome', '(none yet)')}")
            return 0
        findings: list[dict] = []
        if args.prepass:
            path = Path(args.prepass)
            if not path.is_file():
                print(f"!! no pre-pass report at {path} - a review that skipped the "
                      f"deterministic gate is not a reviewed change")
                return 2
            report = json.loads(path.read_text(encoding="utf-8",
                                                errors="replace"))
            # The module that WRITES this document owns the question of
            # whether it evidences a review. This loop decides whether the
            # review may exit and never asked it, so a report in which
            # gitleaks says `not-run` arrived here as a clean, findings-free
            # document and the loop reported it may exit.
            why = prepass.unreviewed(report, expected=prepass.GATES)
            if why:
                for reason in why:
                    print(f"!! pre-pass: {reason}")
                print("the loop cannot conclude on a report whose gates did "
                      "not all fire - this is exit 3, not a clean exit")
                return 3
            findings += from_prepass(report)
        findings += from_session(hitl.load_pending(target))
        if not args.iterate:
            verdict = assess(findings, block_on_of(target))
            lines = [f"block_on: {', '.join(verdict['block_on'])}"]
            lines += ([f"  !! {r}" for r in reasons(verdict)] if verdict["open"]
                      else ["no block_on severity is open - the loop may exit"])
            print(json.dumps(verdict, indent=2, sort_keys=True) if args.json
                  else "\n".join(lines))
            return 0 if may_exit(verdict) else 1
        result = iterate(target, args.run, findings, args.score, args.note)
        print(json.dumps(result["record"], indent=2, sort_keys=True) if args.json
              else render(result))
        return exit_code(result["outcome"])
    except (LoopError, guardrails.PolicyError, RuntimeError, ValueError) as exc:
        print(f"!! {exc}")
        return 2


def main(argv: list[str] | None = None) -> int:
    """The door. `exit_code` maps 1 to "open - iterate again", so an escaping
    exception (Python's own exit 1) told a runner to keep iterating over a
    loop that had crashed. 3 is this module's "capped": it stopped without
    concluding, which is what a crash did."""
    return cli.door(_main, argv, "the review loop", 3)


if __name__ == "__main__":
    raise SystemExit(main())
