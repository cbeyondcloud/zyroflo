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

from tools.sdlc import state as ledger  # noqa: E402

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


def load_pending(target: Path) -> dict | None:
    _, pending = paths(target)
    if not pending.is_file():
        return None
    try:
        data = json.loads(pending.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


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
    session = load_pending(target)
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
    rows = [json.loads(line) for line
            in reviews.read_text(encoding="utf-8").splitlines() if line.strip()]
    done = [r for r in rows if r.get("kind") == "review"]
    dropped = [r for r in rows if r.get("kind") == "review-abandoned"]
    if not done:
        return (f"no completed reviews recorded ({len(dropped)} abandoned)"
                if dropped else "no completed reviews recorded")
    durations = [r["duration_s"] for r in done]
    disp: dict[str, int] = {}
    src_total: dict[str, int] = {}
    src_accepted: dict[str, int] = {}
    for r in done:
        for f in r.get("findings", []):
            disp[f["disposition"]] = disp.get(f["disposition"], 0) + 1
            src_total[f["source"]] = src_total.get(f["source"], 0) + 1
            if f["disposition"] == "accepted":
                src_accepted[f["source"]] = src_accepted.get(f["source"], 0) + 1
    lines = [f"reviews: {len(done)} completed, {len(dropped)} abandoned",
             f"median duration: {statistics.median(durations) / 60:.1f} min "
             f"(M4 gate: < 15 min at G3)",
             "findings by disposition: " + (", ".join(
                 f"{k}={v}" for k, v in sorted(disp.items())) or "(none)")]
    for s in sorted(src_total):
        lines.append(f"  {s}: {src_accepted.get(s, 0)}/{src_total[s]} accepted")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
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

    if args.begin:
        message = begin(target, args.component, args.diff_files, args.run)
    elif args.finding is not None:
        if not args.source or not args.disposition:
            print("!! --finding needs --source and --disposition")
            return 2
        message = finding(target, args.finding, args.source, args.disposition,
                          args.severity, args.path, args.line)
    elif args.end is not None:
        message = end(target, args.end)
    elif args.abandon:
        message = abandon(target)
    else:
        message = show(target)

    print(message)
    return 2 if message.startswith("!!") else 0


if __name__ == "__main__":
    raise SystemExit(main())
