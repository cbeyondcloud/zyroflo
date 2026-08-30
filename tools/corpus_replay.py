"""Corpus floors, and the replay that says whether ranking got worse — item 51.

Item 17 harvested an adopter's own merge history into `.sdlc/corpus/`, and item
18 proved once, blind, that the impact engine beat grep on three real commits.
Neither of them RECURS, and a one-off proof is a claim about a day.

Two questions, and they are different questions:

**Do the floors still hold?** `corpus_harvest.probe_report` answers this at
HARVEST time, from the results of a live harvest — so nothing anywhere asks it
of the corpus an adopter actually has on disk. A corpus that decayed, or was
never written because `--write` is the adopter's own act, reports nothing at
all.

**Did ranking get worse?** This is the nightly replay: rebuild the graph at each
case's pre-merge parent, rank from the seed, and score against the files the
merge actually touched.

**The comparison is PER CASE, and that is the whole design.** An aggregate
precision over a different sample each night moves for reasons that have
nothing to do with the engine — pick three easy commits and it rises, pick three
hard ones and it falls, and either way somebody reads a trend into sampling
noise. So every case is scored under its own sha and compared only against the
last run that scored THAT sha. A case absent tonight was not picked; it is not a
regression, and saying so would train people to ignore the ones that are.

**A replay that scored nothing is a failure, not a pass.** Item 96's rule: an
empty result is not evidence of absence. Zero cases replayed and zero
regressions found are the same output from a working corpus and a missing one,
so the count is reported and an empty replay is refused.

**Scoring is not reimplemented here.** `impact_ab` owns `recall_at`,
`precision_at` and the k's, because two implementations of a metric is how the
number in a report stops meaning the number in the gate.
"""
from __future__ import annotations

import argparse
import calendar
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import cli  # noqa: E402
from tools import corpus_harvest as harvest  # noqa: E402
from tools import impact_ab as ab  # noqa: E402

SCHEMA_VERSION = 1

#: One row per replay, in the ADOPTER's tree beside the other metrics — item
#: 17's rule that the corpus is adopter-resident, and item 29's that a record
#: which becomes a gate must be protected. `policy.yml` covers
#: `.sdlc/metrics/**`, so this file is not writable by a run.
HISTORY = Path(".sdlc") / "metrics" / "corpus-replay.jsonl"
CORPUS = Path(".sdlc") / "corpus"

#: The metrics a case carries, and the direction that is bad. Named here so the
#: trend rule cannot silently start reading a different one.
MEASURES = ("precision_at_10", "recall_at_20")

#: A drop this large, on the same case, is a regression rather than noise. A
#: fall to zero is always one, whatever the margin — the difference between
#: "ranks worse" and "ranks nothing" is the difference item 97 exists for.
MARGIN = 0.10

#: How stale the newest replay may be before the SCHEDULE is the finding.
#:
#: **Two nights, not one.** A single missed run is a runner hiccup, and a check
#: that reddens on one is a check somebody switches off - which is this module's
#: own argument about the margin, one level up: the thing being measured is no
#: longer the ranking but whether anybody is still asking.
MAX_AGE_DAYS = 2

#: What `freshness` can answer. Four, because they need four different repairs,
#: and because folding any of them into "fine" is the defect this whole item
#: exists to prevent.
FRESH, STALE, NEVER, UNREADABLE = "fresh", "stale", "never", "unreadable"


class ReplayError(RuntimeError):
    """The corpus or its history cannot be read at all."""


# ── the corpus an adopter actually has ──────────────────────────────────────


def cases(repo: Path) -> list[dict]:
    """Every stored case, newest first. Unreadable ones are REFUSED, not
    skipped: a case that cannot be parsed is not a case that is absent, and a
    floor counted over a silently shrunken corpus is a floor nobody checked."""
    folder = Path(repo) / CORPUS
    if not folder.is_dir():
        return []
    out, broken = [], []
    for path in sorted(folder.glob("*.json")):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            broken.append(f"{path.name} ({exc})")
            continue
        if isinstance(row, dict) and row.get("sha"):
            out.append(row)
        else:
            broken.append(f"{path.name} (no sha)")
    if broken:
        raise ReplayError(
            "these corpus cases could not be read: " + "; ".join(broken)
            + " - a case that cannot be read is not a case that is absent, and "
              "a floor counted over the rest would be counted over a corpus "
              "nobody has")
    out.sort(key=lambda c: str(c.get("date", "")), reverse=True)
    return out


def floors(stored: list[dict]) -> dict:
    """Per-class counts of the STORED corpus against the M5 floors.

    `corpus_harvest.FLOORS` rather than a second copy: one home for the numbers
    a milestone is judged on.
    """
    counts = {kind: 0 for kind in harvest.FLOORS}
    for case in stored:
        for kind in case.get("classes") or []:
            if kind in counts:
                counts[kind] += 1
    return {"schema_version": SCHEMA_VERSION, "cases": len(stored),
            "classes": counts, "floors": dict(harvest.FLOORS),
            "met": {k: counts[k] >= n for k, n in harvest.FLOORS.items()}}


def floor_problems(stored: list[dict]) -> list[str]:
    found = floors(stored)
    if not stored:
        return ["no corpus is stored in this repository - `corpus_harvest.py "
                "--write` is the adopter's own act, and an absent corpus "
                "reports the same nothing as a corpus that meets no floor"]
    return [f"corpus floor for {kind}: {found['classes'][kind]} case(s), "
            f"{harvest.FLOORS[kind]} required"
            for kind, met in sorted(found["met"].items()) if not met]


# ── the replay ──────────────────────────────────────────────────────────────


def score(ranked: list, truth: set) -> dict:
    """One case's scores, through `impact_ab`'s implementation and no other."""
    return {"precision_at_10": ab.precision_at(ranked, truth),
            "recall_at_20": ab.recall_at(ranked, truth),
            "first_tp_rank": ab.first_tp_rank(ranked, truth),
            "truth_size": len(truth)}


def replay_one(case: dict, components: list[dict], root: Path, scratch: Path,
               ranker=None) -> dict:
    """Rebuild at the case's pre-merge parent, rank, and score against truth.

    `ranker` is injectable for the same reason `impact_ab.run_one` takes a
    `grep_runner`: the real path builds a graph per case, and a test that needed
    one would be a test nobody runs.
    """
    alias = case.get("repo") or ""
    seed, truth_files = ab.seed_and_truth(case.get("files") or [])
    truth = {(alias, f) for f in truth_files}
    if ranker is not None:
        ranked = ranker(case, alias, seed)
    else:                                       # pragma: no cover - the real path
        from tools.graph import build as build_mod

        toml, _, _ = ab.derived_workspace(components, root, alias,
                                          case["parent"], scratch)
        ws_root = toml.parent
        try:
            graph_out = ws_root / "graph-out"
            build_mod.build(toml, graph_out, run_id=f"replay-{case['sha'][:8]}")
            ranked, _ = ab.impact_rank(graph_out, alias, seed,
                                       exclude=(alias, seed))
        finally:
            ab.cleanup(ws_root)
    return {"sha": case["sha"], "component": alias, "seed": seed,
            "date": case.get("date", ""), **score(ranked, truth)}


def replay(repo: Path, stored: list[dict], components: list[dict], root: Path,
           scratch: Path, limit: int = 3, ranker=None, at: str = "") -> dict:
    """One replay: N cases scored, as a row for the history."""
    picked = [c for c in stored if c.get("parent") and c.get("files")][:limit]
    scored = [replay_one(c, components, root, scratch, ranker) for c in picked]
    return {"schema_version": SCHEMA_VERSION, "at": at,
            "replayed": len(scored), "available": len(stored),
            "cases": {row["sha"]: {m: row[m] for m in MEASURES} for row in scored},
            "detail": scored}


# ── the trend, compared case by case ────────────────────────────────────────


def history(repo: Path) -> list[dict]:
    path = Path(repo) / HISTORY
    if not path.is_file():
        return []
    rows, broken = [], 0
    for line in cli.jsonl_rows(path):
        if line.row is None:
            broken += 1
            continue
        rows.append(line.row)
    if broken:
        raise ReplayError(f"{path}: {broken} unreadable row(s) - a history with "
                          f"holes cannot say whether anything got worse")
    return rows


def freshness(repo: Path, max_age_days: float = MAX_AGE_DAYS,
              now: float | None = None) -> dict:
    """Whether the nightly replay is still running, and how long since it did.

    **A schedule that silently stopped and a schedule that finds no regressions
    produce the same output**, which is item 97's quiet-gate class arriving at
    the one check this project describes in its own docstring as *nightly* while
    nothing anywhere makes it so. The word only means something if somebody can
    tell it stopped being true.

    Four answers, because they need four repairs. `never` - nobody has ever run
    it, so there is no trend and the floors are all anybody has. `stale` - it
    ran and has stopped, which is a broken job rather than a healthy ranking.
    `unreadable` - could-not-look, never folded into either. `fresh` - it ran
    recently enough that a quiet result is worth believing.

    `now` is injectable because a test that has to wait a day is a test nobody
    runs, and because this project does not estimate timestamps.
    """
    try:
        rows = history(Path(repo))
    except ReplayError as exc:
        return {"state": UNREADABLE, "age_days": None, "at": "",
                "why": str(exc)}
    stamps = [str(r.get("at") or "") for r in rows if str(r.get("at") or "")]
    if not stamps:
        return {"state": NEVER, "age_days": None, "at": "",
                "why": f"no replay has ever been recorded in {HISTORY} - the "
                       f"floors say the corpus is big enough and nothing says "
                       f"whether ranking has got worse since"}
    newest = max(stamps)
    when = _epoch(newest)
    if when is None:
        return {"state": UNREADABLE, "age_days": None, "at": newest,
                "why": f"the newest replay is stamped {newest!r}, which is not "
                       f"a time this can read - so how long ago it ran is "
                       f"could-not-look, not recent"}
    age = ((time.time() if now is None else now) - when) / 86400.0
    if age > max_age_days:
        return {"state": STALE, "age_days": round(age, 2), "at": newest,
                "why": f"the last replay was {age:.1f} day(s) ago and the limit "
                       f"is {max_age_days} - a nightly check nobody is running "
                       f"reports no regressions for the same reason a working "
                       f"one does"}
    return {"state": FRESH, "age_days": round(age, 2), "at": newest, "why": ""}


def _epoch(stamp: str) -> float | None:
    """Seconds for one recorded timestamp, or None when it cannot be read.

    The rows are written with `%Y-%m-%dT%H:%M:%S+00:00`; anything else is a row
    this cannot date, and inventing a time for it would be the invention this
    module refuses everywhere else.
    """
    text = stamp.strip().replace("Z", "+00:00")
    for shape in ("%Y-%m-%dT%H:%M:%S+00:00", "%Y-%m-%dT%H:%M:%S"):
        try:
            return calendar.timegm(time.strptime(text, shape))
        except ValueError:
            continue
    return None


def last_scoring(rows: list[dict], sha: str) -> dict | None:
    """The most recent earlier run that scored THIS case, or None.

    Per case, never per run. A case absent from a run was not picked, and
    comparing a night that sampled it against one that did not would report
    sampling as regression.
    """
    for row in reversed(rows):
        got = (row.get("cases") or {}).get(sha)
        if isinstance(got, dict):
            return got
    return None


def regressions(rows: list[dict], now: dict) -> list[str]:
    """Cases that scored worse than the last run which scored them."""
    out = []
    for sha, scores in sorted((now.get("cases") or {}).items()):
        before = last_scoring(rows, sha)
        if before is None:
            continue                        # first sighting: nothing to compare
        for measure in MEASURES:
            was, is_now = before.get(measure), scores.get(measure)
            if not isinstance(was, (int, float)):
                continue                    # it was never measured, so nothing fell
            if not isinstance(is_now, (int, float)):
                out.append(f"{sha[:12]}: {measure} was {was} and is not reported "
                           f"at all now - a measure that disappeared cannot be "
                           f"read as a measure that held")
                continue
            if is_now == 0 and was > 0:
                out.append(f"{sha[:12]}: {measure} fell {was} -> 0 - the engine "
                           f"ranked nothing true for a case it used to rank")
            elif was - is_now > MARGIN:
                out.append(f"{sha[:12]}: {measure} fell {was} -> {is_now}, more "
                           f"than the {MARGIN} margin")
    return out


def problems(repo: Path, now: dict, rows: list[dict] | None = None) -> list[str]:
    """Everything wrong with this replay: an empty one, or a case that got worse."""
    if now.get("replayed", 0) == 0:
        return ["the replay scored no cases at all - zero cases replayed and "
                "zero regressions found are the same output from a working "
                "corpus and a missing one, so this is a failure rather than a "
                "clean trend"]
    return regressions(history(repo) if rows is None else rows, now)


def append(repo: Path, row: dict) -> Path:
    path = Path(repo) / HISTORY
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")
    return path


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", default=".", metavar="DIR",
                    help="the repository whose corpus is replayed")
    ap.add_argument("--workspace", metavar="FILE",
                    help="workspace.toml, for --replay's graph build")
    ap.add_argument("--floors", action="store_true",
                    help="does the STORED corpus meet the M5 floors")
    ap.add_argument("--replay", action="store_true")
    ap.add_argument("--check-fresh", action="store_true",
                    help="is the nightly replay still running? a schedule that "
                         "stopped reports no regressions exactly like a healthy "
                         "one")
    ap.add_argument("--max-age-days", type=float, default=MAX_AGE_DAYS,
                    metavar="N")
    ap.add_argument("--limit", type=int, default=3, metavar="N")
    ap.add_argument("--scratch", metavar="DIR")
    ap.add_argument("--write", action="store_true",
                    help="append the replay to the history; without it nothing "
                         "is written, which is item 17's rule")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    target = Path(args.target).resolve()

    # BEFORE `cases()`, deliberately. Whether the schedule is still running is a
    # question about the HISTORY, and a repository whose corpus has gone missing
    # is exactly one where somebody needs that answer.
    if args.check_fresh:
        found = freshness(target, args.max_age_days)
        if args.json:
            print(json.dumps(found, indent=2, sort_keys=True))
        else:
            age = ("never" if found["age_days"] is None
                   else f"{found['age_days']} day(s) ago")
            print(f"replay: {found['state'].upper()} - last {age}")
            if found["why"]:
                print(f"  !! {found['why']}")
        return {FRESH: 0, STALE: 1, NEVER: 1, UNREADABLE: 3}[found["state"]]

    stored = cases(target)

    if args.floors or not args.replay:
        found = floors(stored)
        owed = floor_problems(stored)
        if args.json:
            print(json.dumps({**found, "problems": owed}, indent=2, sort_keys=True))
        else:
            print(f"corpus: {found['cases']} case(s)")
            for kind, n in sorted(found["classes"].items()):
                mark = "ok " if found["met"][kind] else "!! "
                print(f"  {mark}{kind}: {n} of {harvest.FLOORS[kind]}")
            for line in owed:
                print(f"  !! {line}")
        if not args.replay:
            return 1 if owed else 0

    if not args.workspace:
        print("refused: --replay needs --workspace, because rebuilding a case "
              "at its pre-merge parent needs the component list", file=sys.stderr)
        return 2
    root, components = ab.read_workspace_components(Path(args.workspace))
    scratch = Path(args.scratch) if args.scratch else target / ".sdlc" / "replay"
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    now = replay(target, stored, components, root, scratch, args.limit, at=stamp)
    owed = problems(target, now)
    if args.json:
        print(json.dumps({**now, "problems": owed}, indent=2, sort_keys=True))
    else:
        print(f"replayed {now['replayed']} of {now['available']} case(s)")
        for row in now["detail"]:
            print(f"  {row['sha'][:12]}  precision@10={row['precision_at_10']}  "
                  f"recall@20={row['recall_at_20']}  truth={row['truth_size']}")
        for line in owed:
            print(f"  !! {line}")
    if args.write:
        print(f"recorded: {append(target, now)}")
    return 1 if owed else 0


def main(argv: list[str] | None = None) -> int:
    """The door. `3` is could-not-look, which a recurring check must never
    report as a clean trend."""
    cli.use_utf8()
    return cli.door(_main, argv, "the corpus replay", 3)


if __name__ == "__main__":
    raise SystemExit(main())
