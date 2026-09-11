"""HITL ergonomics instrumentation: review time and finding acceptance, as data.

Item 27. The plan's #1 adoption risk is human review fatigue, and its answer is a
number with a gate on it — "median G3 batch review < 15 min" is an M4 acceptance
criterion, which only means something if measurement starts two milestones
earlier, the moment impact-review is human-consumed. This records exactly two
things per review session: how long it took, and what happened to each finding
(accepted / rejected / deferred, by source: pre-pass, graph, agent, human).
Downstream, item 52's metrics engine computes the medians and precision rates;
this file only captures.

The data is the ADOPTER'S: rows land in `<target>/.sdlc/metrics/reviews.jsonl`,
append-only, in the repository the review happened in — never in the harness.

Anti-Goodhart, by construction rather than by policy alone: rows carry NO
reviewer identity — no name, no email, no login. The M4 policy layer adds the
view rule (individual metrics visible only to that individual; team views
aggregate); v1 removes the temptation at the capture layer, because a field that
exists gets queried. An ABANDONED review is a row too (`review-abandoned`):
silently dropping the sessions that went badly would bias the exact median the
M4 gate reads.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools import cli  # noqa: E402
from tools.sdlc import state as ledger  # noqa: E402

cli.use_utf8()

SCHEMA_VERSION = 1
METRICS_DIR = Path(".sdlc") / "metrics"
REVIEWS = "reviews.jsonl"
PENDING = "review-in-progress.json"

SOURCES = ("prepass", "graph", "agent", "human")
DISPOSITIONS = ("accepted", "rejected", "deferred")
VERDICTS = ("approved", "rejected", "rework")


def _now() -> str:
    """UTC ISO timestamp; module-level so tests can hold the clock."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _seconds_between(start_iso: str, end_iso: str) -> int:
    a = datetime.fromisoformat(start_iso)
    b = datetime.fromisoformat(end_iso)
    return max(0, int((b - a).total_seconds()))


def paths(target: Path) -> tuple[Path, Path]:
    d = target / METRICS_DIR
    return d / REVIEWS, d / PENDING


class UnreadableSession(RuntimeError):
    """The session file is there and does not parse.

    It used to be told as `None`, the same answer as "nobody opened one", and
    every caller then took the repair for ABSENCE: `--begin` overwrote the
    damaged file, taking the `review-abandoned` row this module's header
    promises with it, and `loop.py` assessed zero findings and reported that
    the gate may open on a session whose critical was still in the bytes. So
    the gate could be bought by damaging the gate's own record. `show()` below
    already keeps this distinction for the OTHER record in this file - damage
    counted and named rather than folded into a clean zero - and this is that
    rule restored for the record it was missing from.
    """


def load_pending(target: Path) -> dict | None:
    """The open session; None ONLY when no session file exists.

    Absent and unreadable are different answers needing different repairs, so
    they are different answers here: absent returns, damaged raises.
    """
    _, pending = paths(target)
    if not pending.is_file():
        return None
    try:
        data = json.loads(pending.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise UnreadableSession(
            f"{METRICS_DIR / PENDING} exists but cannot be read ({exc}) - a "
            f"session nobody can read is not a session nobody opened") from exc
    if not isinstance(data, dict):
        raise UnreadableSession(
            f"{METRICS_DIR / PENDING} exists but cannot be read as a session: "
            f"it holds a {type(data).__name__}, not an object")
    return data


def append_row(target: Path, row: dict) -> None:
    reviews, _ = paths(target)
    reviews.parent.mkdir(parents=True, exist_ok=True)
    with reviews.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


def begin(target: Path, component: str = "", diff_files: int | None = None,
          run: str = "") -> str:
    """Open a session. `run` is the only identifier a row ever carries.

    Item 34 needed to ask "which findings did the human reject on THIS change"
    and could not: these rows and the run's own record were two accounts of one
    review with no join between them, so a rejected finding could never be
    attributed to the change it was about. A run id names WORK, not a person —
    the anti-Goodhart property this file is built on is untouched, and it is
    the identity ban below rather than the shortness of the key list that
    carries it.

    An id the target does not have is REFUSED. Accepting it would write rows
    that join to nothing, which reads downstream as a review that found nothing
    — the silent zero this project keeps meeting.
    """
    open_session = load_pending(target)
    if open_session is not None:
        return (f"!! a review session is already open (started "
                f"{open_session.get('started_at', '?')}). Close it with --end "
                f"<verdict>, or record it as dropped with --abandon — an "
                f"abandoned review is data, not noise.")
    if run:
        try:
            ledger.load(target, run)
        except RuntimeError as exc:
            return (f"!! {exc} - a session tagged with a run nobody has would "
                    f"join to nothing, and read as a review that found nothing")
    _, pending = paths(target)
    pending.parent.mkdir(parents=True, exist_ok=True)
    session: dict = {"schema_version": SCHEMA_VERSION,
                     "started_at": _now(), "component": component,
                     "findings": []}
    if run:
        session["run"] = run
    if diff_files is not None:
        session["diff_files"] = diff_files
    pending.write_text(json.dumps(session, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")
    return f"recording: review started at {session['started_at']}"


def finding(target: Path, finding_id: str, source: str, disposition: str,
            severity: str = "", path: str = "", line: int = 0) -> str:
    session = load_pending(target)
    if session is None:
        return "!! no review session is open - start one with --begin"
    row = {"id": finding_id, "source": source, "disposition": disposition}
    if severity:
        row["severity"] = severity
    if path:
        row["path"] = path
    if line:
        row["line"] = line
    session["findings"].append(row)
    _, pending = paths(target)
    pending.write_text(json.dumps(session, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")
    return (f"recorded: {finding_id} ({source}) {disposition} - "
            f"{len(session['findings'])} finding(s) this session")


def end(target: Path, verdict: str) -> str:
    session = load_pending(target)
    if session is None:
        return "!! no review session is open - start one with --begin"
    finished = _now()
    counts_disp: dict[str, int] = {}
    counts_src: dict[str, int] = {}
    for f in session["findings"]:
        counts_disp[f["disposition"]] = counts_disp.get(f["disposition"], 0) + 1
        counts_src[f["source"]] = counts_src.get(f["source"], 0) + 1
    row = {
        "schema_version": SCHEMA_VERSION, "kind": "review",
        "started_at": session["started_at"], "finished_at": finished,
        "duration_s": _seconds_between(session["started_at"], finished),
        "verdict": verdict, "component": session.get("component", ""),
        "findings": session["findings"],
        "counts": {"by_disposition": dict(sorted(counts_disp.items())),
                   "by_source": dict(sorted(counts_src.items()))},
    }
    if "diff_files" in session:
        row["diff_files"] = session["diff_files"]
    if session.get("run"):
        row["run"] = session["run"]
    append_row(target, row)
    paths(target)[1].unlink()
    mins = row["duration_s"] / 60
    return (f"review closed: {verdict} after {mins:.1f} min, "
            f"{len(session['findings'])} finding(s) recorded -> "
            f"{METRICS_DIR / REVIEWS}")


def abandon(target: Path) -> str:
    try:
        session = load_pending(target)
    except UnreadableSession as exc:
        # The way OUT of a damaged session, and the reason the other verbs can
        # afford to refuse. The row carries no `started_at` and no
        # `findings_so_far`: both are unknown, and 0 would be a count nobody
        # took. What it does carry is that a session was open and went badly,
        # which is the fact the M4 median would otherwise be biased by losing.
        append_row(target, {"schema_version": SCHEMA_VERSION,
                            "kind": "review-abandoned", "abandoned_at": _now(),
                            "unreadable": True})
        paths(target)[1].unlink()
        return (f"review abandoned and RECORDED as unreadable - {exc}. The row "
                f"stands; deleting the session instead would bias the median "
                f"the M4 gate reads")
    if session is None:
        return "!! no review session is open - nothing to abandon"
    row = {"schema_version": SCHEMA_VERSION, "kind": "review-abandoned",
           "started_at": session["started_at"], "abandoned_at": _now(),
           "component": session.get("component", ""),
           "findings_so_far": len(session["findings"])}
    if session.get("run"):
        row["run"] = session["run"]
    append_row(target, row)
    paths(target)[1].unlink()
    return ("review abandoned and RECORDED - dropped sessions are data; deleting "
            "them would bias the median the M4 gate reads")


def show(target: Path) -> str:
    """The aggregate view — deliberately the only view this file offers."""
    reviews, _ = paths(target)
    if not reviews.is_file():
        return "no reviews recorded here yet"
    # `provider_smoke.verify()` reads THIS file and treats a corrupt row as a
    # stated problem, because dying there would drop a run out of the
    # recurring record. The same record, read here, used to take the whole
    # aggregate down instead - and this aggregate is where the M4 kill
    # gate's median G3 duration is read from. Both readers ask `cli.jsonl_rows`
    # now; what damage MEANS still differs, and that difference is the point.
    lines = cli.jsonl_rows(reviews)
    rows = [ln.row for ln in lines if ln.row is not None]
    unreadable = len(lines) - len(rows)
    damage = f", {unreadable} unreadable" if unreadable else ""
    done = [r for r in rows if r.get("kind") == "review"]
    dropped = [r for r in rows if r.get("kind") == "review-abandoned"]
    if not done:
        return (f"no completed reviews recorded ({len(dropped)} abandoned{damage})"
                if dropped or unreadable
                else "no completed reviews recorded")
    # The line guard above stopped at "does this row parse". A row that parses
    # and carries the wrong SHAPE took the aggregate down just as completely,
    # and this aggregate is where the M4 kill gate's median G3 duration is
    # read from: a row without `duration_s` raised KeyError, `findings: null`
    # raised TypeError. Every field read below is a field a row might not have.
    durations = [r["duration_s"] for r in done
                 if isinstance(r.get("duration_s"), (int, float))
                 and not isinstance(r.get("duration_s"), bool)]
    ungraded = len(done) - len(durations)
    disp: dict[str, int] = {}
    src_total: dict[str, int] = {}
    src_accepted: dict[str, int] = {}
    malformed = 0
    for r in done:
        findings = r.get("findings")
        if not isinstance(findings, list):
            malformed += 1 if findings is not None else 0
            continue
        for f in findings:
            if (not isinstance(f, dict) or "disposition" not in f
                    or "source" not in f):
                malformed += 1
                continue
            disp[f["disposition"]] = disp.get(f["disposition"], 0) + 1
            src_total[f["source"]] = src_total.get(f["source"], 0) + 1
            if f["disposition"] == "accepted":
                src_accepted[f["source"]] = src_accepted.get(f["source"], 0) + 1
    # Counted, never dropped silently: a median over 3 of 400 rows must not
    # read as a healthy sample.
    if ungraded:
        damage += f", {ungraded} without a duration"
    if malformed:
        damage += f", {malformed} malformed finding(s)"
    median = (f"{statistics.median(durations) / 60:.1f} min"
              if durations else "no row carries one")
    lines = [f"reviews: {len(done)} completed, {len(dropped)} abandoned{damage}",
             f"median duration: {median} "
             f"(M4 gate: < 15 min at G3)",
             "findings by disposition: " + (", ".join(
                 f"{k}={v}" for k, v in sorted(disp.items())) or "(none)")]
    for s in sorted(src_total):
        lines.append(f"  {s}: {src_accepted.get(s, 0)}/{src_total[s]} accepted")
    return "\n".join(lines)


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", required=True, metavar="DIR",
                    help="the repository the review belongs to; rows land in its "
                         ".sdlc/metrics/")
    ap.add_argument("--begin", action="store_true",
                    help="open a review session (refuses if one is open)")
    ap.add_argument("--component", default="", metavar="ALIAS",
                    help="with --begin: the component under review")
    ap.add_argument("--diff-files", type=int, default=None, metavar="N",
                    help="with --begin: how many files the diff touches")
    ap.add_argument("--run", default="", metavar="RUN_ID",
                    help="with --begin: the run this review is about, so its "
                         "rejected findings reach the run's feedback record "
                         "(item 34); refused if the target has no such run")
    ap.add_argument("--finding", metavar="ID",
                    help="record one finding's disposition (rule id or a short "
                         "slug); needs --source and --disposition")
    ap.add_argument("--source", choices=SOURCES,
                    help="who produced the finding")
    ap.add_argument("--disposition", choices=DISPOSITIONS,
                    help="what the human decided")
    ap.add_argument("--severity", default="", metavar="S")
    ap.add_argument("--path", default="", metavar="FILE")
    ap.add_argument("--line", type=int, default=0, metavar="N")
    ap.add_argument("--end", choices=VERDICTS, metavar="VERDICT",
                    help="close the session with the review's verdict "
                         f"({'/'.join(VERDICTS)}); writes the one summary row")
    ap.add_argument("--abandon", action="store_true",
                    help="close the session as dropped - recorded, never deleted")
    ap.add_argument("--show", action="store_true",
                    help="the aggregate view: counts, median duration, "
                         "acceptance by source")
    args = ap.parse_args(argv)

    target = Path(args.target)
    if not target.is_dir():
        print(f"!! --target is not a directory: {target}")
        return 2
    actions = [bool(args.begin), args.finding is not None,
               args.end is not None, bool(args.abandon), bool(args.show)]
    if sum(actions) != 1:
        print("!! exactly one of --begin, --finding, --end, --abandon, --show")
        return 2

    try:
        if args.begin:
            message = begin(target, args.component, args.diff_files, args.run)
        elif args.finding is not None:
            if not args.source or not args.disposition:
                print("!! --finding needs --source and --disposition")
                return 2
            message = finding(target, args.finding, args.source,
                              args.disposition, args.severity, args.path,
                              args.line)
        elif args.end is not None:
            message = end(target, args.end)
        elif args.abandon:
            message = abandon(target)
        else:
            message = show(target)
    except UnreadableSession as exc:
        # Every verb but --abandon refuses here, and --abandon is the one this
        # refusal names: an open session that cannot be read is recorded as a
        # row, never opened over. --end refuses too, because the row it writes
        # carries `duration_s` and there is no honest one to compute.
        print(f"!! {exc}. Record it with --abandon - an abandoned review is a "
              f"row, not a deletion")
        return 2

    print(message)
    return 2 if message.startswith("!!") else 0


def main(argv: list[str] | None = None) -> int:
    """The door. This CLI reports the aggregate the M4 kill gate reads its
    median from, and it used to traceback rather than say it could not -
    exit 1, which no row of the contract's table explains. 3 is the same
    could-not-look this repository uses everywhere else."""
    return cli.door(_main, argv, "the review record", 3)


if __name__ == "__main__":
    raise SystemExit(main())
