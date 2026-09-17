"""The metrics engine — item 52: what the records already know, counted once.

Every number here comes from a record some gate already writes: run state,
the approval ledger, review sessions, the learning audit, the debt register.
Nothing is instrumented for this module, because a metric with its own
collection path is a second account of the same events that can disagree with
the first.

**No duckdb, and the reason is measured rather than preferred.** The task line
names it; the real data on a live repository is **53 JSONL rows totalling 65 KB
across four runs**. Scale a mature adopter a thousandfold and it is still tens
of thousands of rows read once — `json.loads` in a loop, not a query engine. The
structural half matters more: item 62's zero-dependency parity gate asserts
single-repo mode completes with NO third-party packages, enforced by an import
gate that refuses them, so a metrics engine on duckdb would be dead in exactly
the configuration this harness promises to work in. Item 11 was specified the
same way and reached the same answer — the 8th measure-first correction — and
item 50 met the shape again with `deepeval` days ago. This is the third time,
and doing it a third way would be the inconsistency.

**A metric nobody can compute is NEVER zero.** This is the whole of the design
and it is where a metrics document usually lies. *Deployment frequency: 0* reads
as "we deploy nothing"; the truth here is "this harness has never deployed, and
P9/P10 are not built". Those are different sentences and only one of them is
true, so an unmeasurable metric carries `value: null` and a REASON, and the
document ends with a roll-up of every one of them. A reader sees what it does
not know before deciding what to trust.

**DORA is reported as four nulls, on purpose.** All four of its measures are
defined against production — deployments, failed deployments, time to restore
service — and nothing here has ever reached production. `time_to_merge_ready`
is offered beside them and is deliberately NOT called lead time: DORA's lead
time runs from commit to RUNNING IN PRODUCTION, and renaming a merge-readiness
proxy after it would be measuring the wrong subject and reporting it under the
right name. The M4 ruling on C8 is the same lesson from the other end.
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import cli  # noqa: E402
from tools.sdlc import state as ledger  # noqa: E402

SCHEMA_VERSION = 1

#: Written beside the other records, in the adopter's tree.
RESULT = Path(".sdlc") / "metrics" / "metrics.json"

#: DORA's four, each defined against PRODUCTION. Named here so the document
#: cannot quietly report three of them and imply the fourth.
DORA = ("deployment_frequency", "lead_time_for_changes",
        "change_failure_rate", "time_to_restore_service")


class MetricsError(RuntimeError):
    """The records cannot be read, which is not the same as no activity."""


def value(v) -> dict:
    return {"value": v}


def cannot(why: str) -> dict:
    """A metric that could not be computed, and why.

    `value` stays None rather than 0 so no caller can average it, sum it, or
    render it on a chart as a floor. The reason travels with it because a null
    without one is indistinguishable from a bug in this module.
    """
    return {"value": None, "why": why}


def _when(text: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None


def _rows(path: Path, counter: dict, label: str) -> list[dict]:
    """Every readable object in a JSONL record; damage is COUNTED, not dropped."""
    if not path.is_file():
        return []
    out = []
    for line in cli.jsonl_rows(path):
        if line.row is None:
            counter[label] = counter.get(label, 0) + 1
            continue
        out.append(line.row)
    return out


def run_states(target: Path) -> tuple[list[dict], dict]:
    """Every run's state, and what could not be read.

    Folders rather than `state.list_runs`, for item 44's reason: a sweep that
    dies on one malformed state.json reports nothing about the other fifty.
    """
    target = Path(target)
    base = ledger.runs_dir(target)
    out, damage = [], {}
    for where in (base, ledger.archive_dir(target)):
        if not where.is_dir():
            continue
        for entry in sorted(p for p in where.iterdir() if p.is_dir()):
            if entry.name == ledger.ARCHIVE_NAME:
                continue
            path = entry / ledger.STATE_NAME
            if not path.is_file():
                damage["runs"] = damage.get("runs", 0) + 1
                continue
            try:
                out.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                damage["runs"] = damage.get("runs", 0) + 1
    return out, damage


def runs(states: list[dict]) -> dict:
    by_kind: dict[str, int] = {}
    by_status: dict[str, int] = {}
    by_phase: dict[str, int] = {}
    for st in states:
        by_kind[str(st.get("kind", "?"))] = by_kind.get(str(st.get("kind", "?")), 0) + 1
        by_status[str(st.get("status", "?"))] = \
            by_status.get(str(st.get("status", "?")), 0) + 1
        by_phase[str(st.get("phase", "?"))] = by_phase.get(str(st.get("phase", "?")), 0) + 1
    closed = by_status.get("closed", 0)
    return {"total": value(len(states)), "by_kind": value(dict(sorted(by_kind.items()))),
            "by_status": value(dict(sorted(by_status.items()))),
            "by_phase": value(dict(sorted(by_phase.items()))),
            "open": value(len(states) - closed - by_status.get("rejected", 0)),
            "closed": value(closed)}


def gates(target: Path, states: list[dict], damage: dict) -> dict:
    """Approvals, and the two things a bare count would hide: overrides, and
    gates that had to be taken more than once."""
    from tools.sdlc import approve

    per_gate: dict[str, int] = {}
    verdicts: dict[str, int] = {}
    forced = 0
    retaken: dict[str, int] = {}
    for st in states:
        rid = str(st.get("run_id") or "")
        if not rid:
            continue
        seen: dict[str, int] = {}
        for row in _rows(ledger.run_dir(Path(target), rid) / approve.APPROVALS,
                         damage, "approvals"):
            gate = str(row.get("gate", "?"))
            per_gate[gate] = per_gate.get(gate, 0) + 1
            verdicts[str(row.get("verdict", "?"))] = \
                verdicts.get(str(row.get("verdict", "?")), 0) + 1
            if row.get("forced"):
                forced += 1
            seen[gate] = seen.get(gate, 0) + 1
        for gate, n in seen.items():
            if n > 1:
                retaken[gate] = retaken.get(gate, 0) + n - 1
    # A forced ADVANCE is an override too, and it is not in this ledger - it
    # lives in the run's history, because `state.advance` records the move and
    # `stage.advance` is the layer with the opinion. Counting only forced
    # APPROVALS reported `0 forced` for a session that had forced a phase past
    # 103 open supply-chain findings: the exact shape of false assurance this
    # module exists to refuse, produced by this module, on its first real run.
    #
    # Item 112: the FIELD is the answer, matching how a forced approval is
    # recorded, so this asks one question instead of matching a sentence. The
    # note match stays for entries written BEFORE the field existed - a run
    # folder is a durable record and an adopter's history does not migrate
    # itself - and `or` rather than two counters, because one override recorded
    # both ways is one override.
    from tools.sdlc import stage as stages

    forced_advances = 0
    for st in states:
        for step in st.get("history") or []:
            if step.get("forced") or stages.FORCED_NOTE in str(step.get("note", "")):
                forced_advances += 1
    return {
        "approvals": value(sum(per_gate.values())),
        "forced_advances": value(forced_advances),
        "by_gate": value(dict(sorted(per_gate.items()))),
        "by_verdict": value(dict(sorted(verdicts.items()))),
        # An override is a gate somebody walked past on purpose. Counting it
        # beside the approvals is the only way a reader can tell a clean record
        # from one that was forced through.
        "forced": value(forced),
        # A gate taken twice is a gate that went VOID and was re-taken - the
        # P6-to-P4 loop, or an artifact edited after approval. It is a sign of
        # the machinery working, and invisible in a total.
        "re_taken": value(dict(sorted(retaken.items()))),
    }


def reviews(target: Path, damage: dict) -> dict:
    from tools.review import hitl

    path, _ = hitl.paths(Path(target))
    rows = _rows(path, damage, "reviews")
    done = [r for r in rows if r.get("kind") == "review"]
    abandoned = [r for r in rows if r.get("kind") == "review-abandoned"]
    seconds = [r["duration_s"] for r in done
               if isinstance(r.get("duration_s"), (int, float))]
    disp: dict[str, int] = {}
    for r in done:
        for name, n in (r.get("counts", {}).get("by_disposition") or {}).items():
            disp[str(name)] = disp.get(str(name), 0) + int(n)
    return {
        "sessions": value(len(done)),
        # Abandoned reviews are rows, never deletions - dropping them would bias
        # every duration here toward the reviews that went well.
        "abandoned": value(len(abandoned)),
        "median_duration_s": (value(round(statistics.median(seconds), 1))
                              if seconds else
                              cannot("no completed review session records a "
                                     "duration, so there is no median to take")),
        "findings_by_disposition": value(dict(sorted(disp.items()))),
    }


def learning(target: Path, damage: dict) -> dict:
    from tools.sdlc import learning as learn

    rows = _rows(Path(target) / learn.LOG, damage, "learning")
    applied = [r for r in rows if r.get("action") == "applied"]
    undone = [r for r in rows if r.get("action") == "undone"]
    standing = {r.get("proposal") for r in applied} - {r.get("proposal") for r in undone}
    return {"applied": value(len(applied)), "undone": value(len(undone)),
            "standing": value(len(standing))}


def debt(target: Path) -> dict:
    from tools.sdlc import debt as owed

    try:
        every = owed.candidates(Path(target))
        outstanding = owed.outstanding(Path(target))
    except owed.DebtError as exc:
        return {"declared": cannot(str(exc)), "outstanding": cannot(str(exc))}
    by_source: dict[str, int] = {}
    for item in every:
        by_source[item["source"]] = by_source.get(item["source"], 0) + 1
    return {"declared": value(len(every)),
            "by_source": value(dict(sorted(by_source.items()))),
            "outstanding": value(len(outstanding))}


def deploys_recorded(target: Path, states: list[dict]) -> int:
    """How many runs reached a deploy phase. The premise every DORA measure
    rests on, computed rather than assumed absent."""
    reached = 0
    for st in states:
        for step in st.get("history") or []:
            if str(step.get("phase", "")) in ("P9", "P10"):
                reached += 1
                break
    return reached


def dora(target: Path, states: list[dict]) -> dict:
    """DORA's four, and why each is null here.

    **Every one is defined against PRODUCTION** — deployments, failed
    deployments, time to restore service. Reporting `0` for any of them would
    say the harness deploys and fails at nothing, when the truth is that it has
    never deployed. Computed from the run history rather than asserted: if a run
    ever reaches P9 or P10, this section stops being null and starts being work.
    """
    reached = deploys_recorded(Path(target), states)
    if not reached:
        why = ("no run has reached a deploy phase (P9/P10), so nothing here has "
               "been deployed - DORA's four measures are all defined against "
               "production and none of them is zero, they are unmeasured")
        return {**{name: cannot(why) for name in DORA},
                "deploys_recorded": value(0)}
    return {**{name: cannot(f"{reached} run(s) reached a deploy phase and the "
                            f"measure is not implemented yet - the premise now "
                            f"holds, so this is work rather than an absence")
               for name in DORA},
            "deploys_recorded": value(reached)}


def time_to_merge_ready(states: list[dict]) -> dict:
    """Run opened to the merge gate approved, in seconds.

    **Deliberately NOT called lead time.** DORA's lead time for changes runs
    from commit to running in production, and renaming a merge-readiness proxy
    after it would be measuring the wrong subject and reporting it under the
    right name. This is a real number about a real thing; it is simply a
    different thing.
    """
    spans = []
    for st in states:
        opened = _when(st.get("created_at"))
        if opened is None:
            continue
        for step in st.get("history") or []:
            if "G4 approved" in str(step.get("note", "")):
                done = _when(step.get("at"))
                if done is not None:
                    spans.append((done - opened).total_seconds())
                break
    if not spans:
        return cannot("no run has a recorded G4 approval, so nothing has "
                      "reached merge-ready")
    return value(round(statistics.median(spans), 1))


def commits(target: Path) -> dict:
    try:
        proc = subprocess.run(["git", "-C", str(target), "rev-list", "--count", "HEAD"],
                              capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return cannot(f"git did not run: {exc}")
    if proc.returncode != 0:
        return cannot((proc.stderr or "git failed").strip().splitlines()[0][:160])
    return value(int(proc.stdout.strip() or 0))


def unmeasured(doc: dict) -> list[str]:
    """Every metric this document could not compute, with its reason.

    The roll-up exists so a reader meets the gaps before deciding what to
    trust. A metrics file whose nulls are scattered through it is one where the
    reader finds them last, or not at all.
    """
    out = []

    def walk(node, path):
        if isinstance(node, dict):
            if "value" in node and node["value"] is None:
                out.append(f"{path}: {node.get('why', 'no reason recorded')}")
                return
            for key, sub in node.items():
                walk(sub, f"{path}.{key}" if path else str(key))

    walk(doc, "")
    return sorted(out)


def corpus(target: Path) -> dict:
    """Whether the nightly replay is still running, and how old its answer is.

    **A number nobody can compute is never zero**, and a schedule nobody is
    running is the sharpest case of that: a stopped nightly replay reports no
    regressions for exactly the reason a healthy one does. So `never` and
    `stale` carry `value: null` and a REASON, and only a fresh replay produces a
    number.

    This exists because `--check-fresh` needed a reader. A check with a CLI and
    no consumer is the shape this project has now found six times over - a field
    written, or askable, and asked by nothing.
    """
    from tools import corpus_replay as replay

    try:
        found = replay.freshness(Path(target))
    except Exception as exc:                      # never a metric that crashed
        return {"replay_age_days": cannot(f"the replay history could not be "
                                          f"read: {exc}")}
    if found["state"] == replay.FRESH:
        return {"replay_age_days": {"value": found["age_days"],
                                    "at": found["at"]}}
    return {"replay_age_days": cannot(found["why"])}


def compute(target: Path) -> dict:
    target = Path(target)
    states, damage = run_states(target)
    doc = {
        "schema_version": SCHEMA_VERSION,
        "runs": runs(states),
        "gates": gates(target, states, damage),
        "review": reviews(target, damage),
        "learning": learning(target, damage),
        "debt": debt(target),
        "corpus": corpus(target),
        "delivery": {"time_to_merge_ready_s": time_to_merge_ready(states),
                     "commits": commits(target)},
        "dora": dora(target, states),
    }
    doc["unreadable"] = dict(sorted(damage.items()))
    doc["unmeasured"] = unmeasured(doc)
    return doc


def write(target: Path, doc: dict) -> Path:
    path = Path(target) / RESULT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8", newline="\n")
    return path


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", default=".", metavar="DIR")
    ap.add_argument("--write", action="store_true",
                    help=f"write {RESULT.as_posix()}; without it nothing lands")
    ap.add_argument("--dashboard", action="store_true",
                    help="render the org/team/developer views as a self-contained "
                         "page plus a flat series file; nothing is written without it")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    target = Path(args.target).resolve()
    # Absent is not zero, at the DOOR as well as in the document. `compute`
    # answers a null-with-reason for one metric it cannot read, but an absent
    # target is the whole subject missing, not one metric - it reached the record
    # readers, which treat a missing file as empty, and the door emitted a whole
    # document carrying `debt.declared: 0` at exit 0. So this refuses ABOVE the
    # document: the shared check names it, MetricsError carries could-not-look.
    if why := cli.missing_target(target):
        raise MetricsError(why)
    doc = compute(target)
    if args.json:
        print(json.dumps(doc, indent=2, sort_keys=True))
    else:
        r, g = doc["runs"], doc["gates"]
        print(f"runs     : {r['total']['value']} "
              f"({r['open']['value']} open, {r['closed']['value']} closed)")
        print(f"gates    : {g['approvals']['value']} approval(s), "
              f"{g['forced']['value']} forced approval(s), "
              f"{g['forced_advances']['value']} forced advance(s), "
              f"{sum(g['re_taken']['value'].values())} re-taken")
        rv = doc["review"]["median_duration_s"]
        print(f"review   : {doc['review']['sessions']['value']} session(s), "
              f"median "
              + (f"{rv['value']}s" if rv["value"] is not None else "unmeasured"))
        print(f"learning : {doc['learning']['standing']['value']} rule(s) standing")
        print(f"debt     : {doc['debt']['outstanding']['value']} outstanding")
        if doc["unreadable"]:
            print(f"unreadable rows: {doc['unreadable']}")
        print(f"unmeasured: {len(doc['unmeasured'])}")
        for line in doc["unmeasured"]:
            print(f"  -- {line}")
    if args.write:
        print(f"wrote {write(target, doc)}")
    if args.dashboard:
        # `run_states` again rather than threading it out of `compute`: the views
        # need the raw states, and `compute` deliberately returns the DOCUMENT.
        # Widening its return to serve one caller would make every other reader
        # carry a tuple it does not want.
        from tools.sdlc import dashboard as views_mod

        states, _ = run_states(target)
        view = views_mod.views(target, doc, states)
        page, rows = views_mod.write(target, view)
        print(f"dashboard: {page}")
        print(f"series   : {rows} ({len(views_mod.series(view))} row(s))")
    return 0


def main(argv: list[str] | None = None) -> int:
    """The door. `3` is could-not-look: records that cannot be read are not an
    absence of activity."""
    cli.use_utf8()
    return cli.door(_main, argv, "the metrics engine", 3)


if __name__ == "__main__":
    raise SystemExit(main())
