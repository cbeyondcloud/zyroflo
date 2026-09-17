"""The validation dashboard: does this graph meet the targets the plan holds it to?

Item 11, an M1 kill-gate condition. Reads one build's output directory — `graph.jsonl`,
`unresolved.jsonl`, `build_report.json` — computes the M1 coverage metrics, verdicts them
against declared targets, and writes `metrics.json` + `report.md` beside them. Exit 0
means every applicable target is met on a clean build; exit 2 names what missed; exit 3
says nothing could be graded (below).

## Every number is a ratio with its parts shown

A percentage whose numerator and denominator are hidden is a mood. Each metric records
value, numerator, denominator and target, and a fact class that has NO producer in this
build (no write-mapping profile ran, say) is N/A — stated, never counted as 0% (which
would fail a workspace for not having a technology) nor as 100% (which would pass it for
the same reason).

## A dashboard that graded nothing verdicts `unmeasurable`, not `met`

N/A is the right answer for ONE target. It is not an answer for ALL of them at once:
a build with an empty graph made every ratio N/A, read orphans' 0/0 as `0.0% <= 5.0`,
and `all(m["met"] is not False)` was vacuously true over a list of `None`s — so a check
that looked at nothing returned a clean bill of health at exit 0. Nothing graded is now
`unmeasurable` at **exit 3**: could-not-look outranks a finding, and the word never
reads as a fail for a reader who skims. A PARTIALLY graded dashboard is unchanged — the
graded targets decide the verdict and the N/A ones are listed by name. A build that is
not clean is still `missed` whatever the ratios say: that is a finding, not a blind
spot, and this module has always read it first.

## Every input door refuses damage the same way

`graph.jsonl`, `unresolved.jsonl`, `build_report.json` and a `--targets` file are all
documents a human or a merge can damage, and `build_report.json` is COMMITTED and
diffed in pull requests. A conflict marker left in one used to be a refusal at one door
(`!!`, exit 2) and a raw `JSONDecodeError`/`TOMLDecodeError` traceback at the others —
the identical defect `snapshot.py` fixed for its manifests. Every door now names the
file and the reason at exit 2.

## "Zero silent drops" is the pipeline's own contract, verdicted here

The build already fails when an unresolved row lacks the fields its kind promises, and
already reports extractor failures and contract violations. This dashboard's half is the
verdict: a build with failed extractors, contract violations, or a non-ok status is not
a build whose coverage numbers mean anything, so it fails validation before any ratio is
read.

## Computed in the standard library, deliberately

The plan said "duckdb-backed"; the measurement said otherwise. Every metric here is a
fixed aggregation over three files — no ad-hoc SQL surface exists for a library to earn
its keep on — and duckdb is an uninstalled optional extra (`metrics` in pyproject),
while the zero-dependency doctrine (item 62) wants the lifecycle's tools to run on the
standard library. duckdb remains the tool for item 52's org-level metrics engine, where
ad-hoc queries over many runs are the point.

## `--sample N` — the spot-audit half of the kill gate

A seeded random sample of edges, each with its `source_file:line`, for a human to check
against the tree. The seed is recorded in the output, so "the sample was clean" is a
claim someone else can re-draw and re-check.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools import cli  # noqa: E402
from tools.graph.impact import GraphError  # noqa: E402

cli.use_utf8()

SCHEMA_VERSION = 1

#: M1 targets, verbatim from the plan ("recalibrate once with rationale" — in the plan,
#: with the rationale, never silently here). Percentages 0–100; orphans is a ceiling.
DEFAULT_TARGETS = {
    "host_keys_resolved": 85.0,
    "topics_with_producer_and_consumer": 60.0,
    "endpoints_spec_matched": 50.0,
    "write_targets_migration_owned": 80.0,
    "ui_urls_resolved": 90.0,
    "orphans_max": 5.0,
}


def _rows(path: Path) -> list[dict]:
    """Every fact row, header dropped. Damage refuses rather than truncates.

    This dashboard grades RATIOS, so a reader that silently dropped the rows
    after a damaged line would not fail — it would report a smaller
    denominator and a plausible percentage, and the M1 gate is read off these
    numbers. `impact.GraphError` is the same refusal for the same file.
    """
    out = []
    for line in cli.jsonl_rows(path):
        if line.row is None:
            raise GraphError(
                f"{path}:{line.number} {line.problem} - these ratios would be "
                f"computed over a truncated denominator. Rebuild the graph")
        if line.row.get("kind") != "header":
            out.append(line.row)
    return out


def _read_document(path: Path, parse, what: str) -> dict:
    """A document this dashboard reads, or a refusal naming it. Never a traceback.

    `build_report.json` is committed and diffed in pull requests and a `--targets`
    file is a recorded recalibration; either can arrive with a conflict marker in
    it. `_rows` already refuses a damaged `graph.jsonl` by name at exit 2, and
    these doors were the ones that still handed over a decoder traceback at exit
    1. `GraphError` is this module's one refusal channel, so they use it too.

    A conflict marker is not the only damage. A `build_report.json` that parses to
    a JSON list, scalar or `null` is VALID json of the wrong shape, and the return
    annotation promised a dict it did not enforce — so `report.get(...)` met a
    non-dict and exited on a raw `AttributeError` at exit 1, the traceback this
    door exists to refuse. The shape is checked here, once, for every caller.
    """
    try:
        doc = parse(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GraphError(f"{path}: {type(exc).__name__}: {exc} - {what}") from exc
    if not isinstance(doc, dict):
        raise GraphError(
            f"{path}: parsed to a {type(doc).__name__}, not an object - {what}")
    return doc


def _ratio(name: str, num: int, den: int, target: float | None) -> dict:
    """N/A when the denominator is empty: no producer of this fact class ran, and both
    0% and 100% would be statements about a technology the workspace does not have."""
    value = round(100.0 * num / den, 1) if den else None
    met = None if value is None or target is None else value >= target
    return {"metric": name, "value": value, "numerator": num, "denominator": den,
            "target": target, "met": met}


def compute_metrics(nodes: list[dict], edges: list[dict], unresolved: list[dict],
                    targets: dict) -> list[dict]:
    by_type: dict[str, list[dict]] = {}
    for e in edges:
        by_type.setdefault(e["type"], []).append(e)
    dsts = {t: {e["dst"] for e in es} for t, es in by_type.items()}

    # Host keys: service-to-service calls the config layer resolved (src is a service —
    # UI calls have their own metric below) vs the host refs it could not pair.
    # Structural rather than by extractor name, so every stack's resolver counts.
    host_hits = [e for e in by_type.get("DG_CALLS", [])
                 if e.get("src", "").startswith("svc:")]
    host_miss = [r for r in unresolved if r.get("kind") == "unpaired-host"]

    # Every topic NODE, stubs included: a topic known only from a mapping or a config
    # key is exactly the one whose missing producer the target exists to expose.
    topics = {n["id"] for n in nodes if n.get("type") == "topic"}
    both = dsts.get("DG_PRODUCES", set()) & dsts.get("DG_CONSUMES", set()) & topics

    exposed = dsts.get("DG_EXPOSES", set())
    specified = dsts.get("DG_SPECIFIES", set())

    written = dsts.get("DG_WRITES_TABLE", set())
    owned = dsts.get("DG_OWNS_TABLE", set())

    ui_hits = [e for e in by_type.get("DG_CALLS", [])
               if e.get("src", "").startswith("ui:")]
    # By KIND, exactly as host_miss above — NOT by extractor. The frontend
    # extractor's only unresolved kinds are `grammar-unavailable` and `unparsable`,
    # both of which mean the source was never READ; keyed by extractor they landed
    # in this denominator, so one unreadable file read as a 50% UI-URL miss. A URL
    # that was read and failed to resolve is `unpaired-url` (no extractor emits it
    # today, so this reads N/A-or-100% honestly). The could-not-read rows are
    # reported beside the metric in the unresolved rollup, never inside it here.
    ui_miss = [r for r in unresolved if r.get("kind") == "unpaired-url"]

    return [
        _ratio("host_keys_resolved", len(host_hits), len(host_hits) + len(host_miss),
               targets.get("host_keys_resolved")),
        _ratio("topics_with_producer_and_consumer", len(both), len(topics),
               targets.get("topics_with_producer_and_consumer")),
        _ratio("endpoints_spec_matched", len(exposed & specified), len(exposed),
               targets.get("endpoints_spec_matched")),
        _ratio("write_targets_migration_owned", len(written & owned), len(written),
               targets.get("write_targets_migration_owned")),
        _ratio("ui_urls_resolved", len(ui_hits), len(ui_hits) + len(ui_miss),
               targets.get("ui_urls_resolved")),
    ]


def compute_orphans(nodes: list[dict], edges: list[dict], ceiling: float) -> dict:
    """Nodes touching no edge. Some are honest stubs; past the ceiling they are noise.

    `repo:` nodes are excluded: they are the graph's own bookkeeping, emitted per
    component by construction and linked only where an IN_REPO pass covers that
    component's facts — an unlinked one says something about the link pass, not about
    an unreferenced knowledge claim, which is what this target watches for.

    With nothing to count this is N/A like every other empty denominator. It used to
    read 0/0 as `0.0%` and pass — the one ratio that graded an empty graph, and so the
    reason an empty graph came back `met`.
    """
    touched = {e["src"] for e in edges} | {e["dst"] for e in edges}
    counted = [n for n in nodes if n.get("type") != "repo"]
    orphan = sorted(n["id"] for n in counted if n["id"] not in touched)
    value = round(100.0 * len(orphan) / len(counted), 1) if counted else None
    return {"metric": "orphans", "value": value, "numerator": len(orphan),
            "denominator": len(counted), "target": ceiling,
            "met": None if value is None else value <= ceiling,
            "examples": orphan[:10]}


def compute_clean_build(report: dict) -> dict:
    """The zero-silent-drops verdict: the pipeline's own contracts, respected."""
    failed = [e["name"] for e in report.get("extractors", []) if e.get("failed")]
    problems = []
    if report.get("status") != "ok":
        problems.append(f"build status is {report.get('status')!r}, not ok")
    problems += [f"contract violation: {p}" for p in report.get("contract_violations", [])]
    problems += [f"extractor failed: {n}" for n in failed]
    return {"metric": "zero_silent_drops", "met": not problems, "problems": problems}


def draw_sample(edges: list[dict], n: int, seed: str) -> dict:
    picked = random.Random(seed).sample(edges, min(n, len(edges)))
    rows = [{"type": e["type"], "src": e["src"], "dst": e["dst"],
             "where": f"{e.get('source_file', '')}:{e.get('line', 0)}",
             "extractor": e.get("extractor", "")} for e in picked]
    return {"seed": seed, "requested": n,
            "rows": sorted(rows, key=lambda r: (r["type"], r["src"], r["dst"]))}


def unresolved_rollup(unresolved: list[dict]) -> list[dict]:
    counts: dict[str, int] = {}
    for r in unresolved:
        counts[r.get("kind", "?")] = counts.get(r.get("kind", "?"), 0) + 1
    return [{"kind": k, "rows": v}
            for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def validate(out_dir: Path, targets: dict, sample: int = 0,
             seed: str = "") -> dict:
    rows = _rows(out_dir / "graph.jsonl")
    nodes = [r for r in rows if "src" not in r]
    edges = [r for r in rows if "src" in r]
    unresolved = _rows(out_dir / "unresolved.jsonl")
    report = _read_document(
        out_dir / "build_report.json", json.loads,
        "this build's own provenance is unreadable, so there is no build to grade. "
        "Rebuild, or restore the file from the commit that produced it")

    metrics = compute_metrics(nodes, edges, unresolved, targets)
    orphans = compute_orphans(nodes, edges, targets.get("orphans_max", 5.0))
    clean = compute_clean_build(report)
    seed = seed or str(report.get("run_id", "sdlc"))

    graded = [m for m in metrics + [orphans] if m["met"] is not None]
    if not clean["met"] or any(m["met"] is False for m in graded):
        verdict = "missed"
    elif not graded:
        verdict = "unmeasurable"
    else:
        verdict = "met"

    result = {
        "schema_version": SCHEMA_VERSION, "run_id": report.get("run_id", ""),
        "metrics": metrics + [orphans, clean],
        "unresolved": unresolved_rollup(unresolved),
        "verdict": verdict,
    }
    if sample:
        result["sample"] = draw_sample(edges, sample, seed)
    return result


def render_markdown(result: dict) -> str:
    lines = ["# Validation dashboard", "",
             f"run `{result['run_id']}` — verdict: **{result['verdict'].upper()}**", "",
             "| metric | value | n/d | target | verdict |", "|---|---|---|---|---|"]
    for m in result["metrics"]:
        if m["metric"] == "zero_silent_drops":
            verdict = "PASS" if m["met"] else "FAIL"
            lines.append(f"| zero_silent_drops | — | — | clean build | {verdict} |")
            lines += [f"|  · {p} | | | | |" for p in m["problems"]]
            continue
        value = "N/A" if m["value"] is None else f"{m['value']}%"
        # One sentence for every empty denominator, orphans included — nothing was
        # counted, so there is no grade. (For the five fact classes the reason is
        # that no producer of that class ran in this build.)
        verdict = ("N/A — nothing was counted, so there is nothing to grade"
                   if m["met"] is None else "PASS" if m["met"] else "FAIL")
        sign = "≤" if m["metric"] == "orphans" else "≥"
        lines.append(f"| {m['metric']} | {value} | {m['numerator']}/{m['denominator']} "
                     f"| {sign}{m['target']}% | {verdict} |")
    lines += ["", "## Unresolved, by kind", ""]
    lines += [f"- {r['kind']}: {r['rows']}" for r in result["unresolved"][:12]]
    if "sample" in result:
        s = result["sample"]
        lines += ["", f"## Spot-audit sample (seed `{s['seed']}`)", "",
                  "| type | src → dst | where |", "|---|---|---|"]
        lines += [f"| {r['type']} | {r['src']} → {r['dst']} | {r['where']} |"
                  for r in s["rows"]]
    return "\n".join(lines) + "\n"


def _checked_targets(path: Path, overrides: dict) -> dict:
    """A recalibration names KNOWN targets and gives each a NUMBER, or it is refused.

    The MCP door (`mcp_server.validate_graph`, item 23) already refuses an unknown
    target by name; this CLI door accepted one silently — a typo'd key updated the
    dict inertly, the metric it meant to move kept its default, the `.get()` for the
    typo never fired, and the run graded `met` against a target nobody set. And a
    target whose value is not a number (`orphans_max = "five"`) reached the ratio as
    `value <= "five"` and exited on a raw `TypeError`. Both are refused here, by
    name, through `GraphError` — the same channel and exit 2 as every other door.
    """
    unknown = sorted(set(overrides) - set(DEFAULT_TARGETS))
    if unknown:
        raise GraphError(
            f"{path}: unknown target(s): {', '.join(unknown)} "
            f"(known: {', '.join(sorted(DEFAULT_TARGETS))}) - a target nobody set is "
            f"graded against its default, so the recalibration silently did nothing")
    bad = sorted(k for k, v in overrides.items()
                 if not isinstance(v, (int, float)) or isinstance(v, bool))
    if bad:
        raise GraphError(
            f"{path}: non-numeric target(s): {', '.join(bad)} - a target is a "
            f"percentage or a ceiling, so each must be a number")
    return overrides


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--graph-dir", required=True, metavar="DIR",
                    help="a build's output directory (graph.jsonl + unresolved.jsonl + "
                         "build_report.json); metrics.json and report.md are written "
                         "beside them")
    ap.add_argument("--targets", metavar="FILE",
                    help="TOML overriding the default M1 targets — recalibration lives "
                         "in the plan with its rationale, and this flag is how a "
                         "recorded recalibration reaches the tool")
    ap.add_argument("--sample", type=int, default=0, metavar="N",
                    help="draw a seeded random sample of N edges for the human "
                         "spot-audit; the seed is recorded so the draw is repeatable")
    ap.add_argument("--seed", default="", help="override the sample seed (default: the "
                                               "build's run id)")
    args = ap.parse_args(argv)

    out_dir = Path(args.graph_dir)
    for name in ("graph.jsonl", "unresolved.jsonl", "build_report.json"):
        if not (out_dir / name).exists():
            print(f"!! {out_dir / name} does not exist — point --graph-dir at a "
                  f"completed build's output directory")
            return 2

    targets = dict(DEFAULT_TARGETS)
    try:
        if args.targets:
            overrides = _read_document(
                Path(args.targets), tomllib.loads,
                "a recalibration this tool cannot read is not a recalibration. Fix "
                "the TOML, or drop --targets to grade against the shipped targets")
            targets.update(_checked_targets(Path(args.targets), overrides))
        result = validate(out_dir, targets, sample=args.sample, seed=args.seed)
    except GraphError as exc:
        # The loop above refuses an ABSENT input by name and exit 2. A damaged
        # one deserves the same answer, not a traceback that exits 1 — and that
        # goes for EVERY door, which is what this try now covers.
        print(f"!! {exc}")
        return 2
    (out_dir / "metrics.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out_dir / "report.md").write_text(render_markdown(result), encoding="utf-8")

    for m in result["metrics"]:
        if m["metric"] == "zero_silent_drops":
            print(f"  zero_silent_drops: {'PASS' if m['met'] else 'FAIL'}")
            for p in m["problems"]:
                print(f"    !! {p}")
        else:
            # The parts come first and stay glued to the value, so an empty
            # denominator reads `N/A (0/0)` — never `0.0%`, which is a grade.
            shown = "N/A" if m["value"] is None else f"{m['value']}%"
            state = "N/A " if m["met"] is None else "PASS" if m["met"] else "FAIL"
            print(f"  {state} {m['metric']}: {shown} "
                  f"({m['numerator']}/{m['denominator']}), target {m['target']}%")
    # Plain ASCII on purpose: Windows consoles still default to cp1252, and a verdict
    # line that renders mojibake is a verdict people stop reading.
    print(f"\nverdict: {result['verdict']} - wrote {out_dir}/metrics.json, "
          f"{out_dir}/report.md")
    # 3, not 0 and not 2: a dashboard that graded nothing has neither met its
    # targets nor missed them, and a reader who sees 0 stops looking.
    return {"met": 0, "unmeasurable": 3}.get(result["verdict"], 2)


if __name__ == "__main__":
    raise SystemExit(main())
