"""The validation dashboard: the M1 targets, verdicted with their parts shown.

Item 11. The fixture's values below are HAND-DERIVED from EXPECTED.md before the
dashboard ran — the same pre-registration discipline as the golden chain, because a
dashboard checked against its own output is a mood ring. Ratios carry numerator and
denominator; a fact class with no producer is N/A, never 0% or 100%.
"""
from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import tools.graph.build as B  # noqa: E402
import tools.graph.validate as V  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "orderflow"


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("validation") / "out"
    assert B.main(["--workspace", str(FIXTURE / "workspace.toml"),
                   "--out", str(out)]) == 0
    return out


def metric(result: dict, name: str) -> dict:
    (m,) = [m for m in result["metrics"] if m["metric"] == name]
    return m


# ── the fixture's numbers, derived from the answer key by hand ────────────────


def test_the_fixture_metrics_match_the_answer_key(built):
    result = V.validate(built, dict(V.DEFAULT_TARGETS))

    topics = metric(result, "topics_with_producer_and_consumer")
    assert (topics["numerator"], topics["denominator"]) == (1, 5), \
        "order-events-v1 has both; D7's shipment topic is a stub with neither; " \
        "item 91's three topics are producer-only by design"

    spec = metric(result, "endpoints_spec_matched")
    assert (spec["numerator"], spec["denominator"]) == (2, 2), \
        "/orders/v1/list is specified directly (9a); /orders/v1/detail/{} through " \
        "item 93's context-suffix alignment"

    ui = metric(result, "ui_urls_resolved")
    assert ui["value"] == 100.0 and ui["denominator"] >= 2

    write = metric(result, "write_targets_migration_owned")
    assert (write["numerator"], write["denominator"]) == (1, 2), \
        "order_events is written and owned; D7's orphan mapping writes a second table " \
        "that no migration owns — which is exactly the loss this target exists to see"

    host = metric(result, "host_keys_resolved")
    assert (host["numerator"], host["denominator"]) == (3, 3), \
        "inventory's config-pair, item 92's value-shape pair, and its getter-concat " \
        "pair all resolve; nothing is unpaired and the redis address never counted"

    clean = metric(result, "zero_silent_drops")
    assert clean["met"] is True and clean["problems"] == []


def test_orphans_match_an_independent_recount(built):
    """Weak on purpose where hand-derivation is impractical: an independent five-line
    recount from the same files, not the dashboard's own code path."""
    result = V.validate(built, dict(V.DEFAULT_TARGETS))
    rows = [json.loads(line) for line
            in (built / "graph.jsonl").read_text(encoding="utf-8").splitlines()
            if '"header"' not in line]
    nodes = [r for r in rows if "src" not in r and r.get("type") != "repo"]
    edges = [r for r in rows if "src" in r]
    touched = {e["src"] for e in edges} | {e["dst"] for e in edges}
    orphans = [n for n in nodes if n["id"] not in touched]
    m = metric(result, "orphans")
    assert (m["numerator"], m["denominator"]) == (len(orphans), len(nodes))


# ── verdicts, N/A, and the clean-build gate ───────────────────────────────────


def test_missed_targets_name_themselves_and_exit_2(built, tmp_path, capsys):
    """The fixture honestly MISSES the default topic target (50% < 60%) — the dashboard
    must say so and fail, not grade on a curve."""
    rc = V.main(["--graph-dir", str(built)])
    out = capsys.readouterr().out
    assert rc == 2
    assert "FAIL topics_with_producer_and_consumer: 20.0%" in out
    assert "verdict: missed" in out
    written = json.loads((built / "metrics.json").read_text(encoding="utf-8"))
    assert written["verdict"] == "missed"
    assert (built / "report.md").exists()


def test_recalibrated_targets_reach_the_tool_only_through_the_flag(built, tmp_path,
                                                                   capsys):
    override = tmp_path / "targets.toml"
    override.write_text("topics_with_producer_and_consumer = 20.0\n"
                        "endpoints_spec_matched = 50.0\n"
                        "write_targets_migration_owned = 50.0\n", encoding="utf-8")
    rc = V.main(["--graph-dir", str(built), "--targets", str(override)])
    assert rc == 0
    assert "verdict: met" in capsys.readouterr().out


def test_a_fact_class_nobody_produced_is_na_not_a_grade(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "graph.jsonl").write_text(
        '{"id": "svc:a@a", "type": "svc"}\n'
        '{"src": "svc:a@a", "dst": "ep:/x", "type": "DG_EXPOSES", '
        '"source_file": "f", "line": 1, "extractor": "e"}\n'
        '{"src": "cfg:a/s.yaml", "dst": "ep:/x", "type": "DG_SPECIFIES", '
        '"source_file": "s.yaml", "line": 1, "extractor": "e"}\n'
        '{"id": "ep:/x", "type": "ep"}\n', encoding="utf-8")
    (out / "unresolved.jsonl").write_text("", encoding="utf-8")
    (out / "build_report.json").write_text(
        '{"run_id": "r", "status": "ok", "contract_violations": [], "extractors": []}',
        encoding="utf-8")
    result = V.validate(out, dict(V.DEFAULT_TARGETS))
    m = metric(result, "write_targets_migration_owned")
    assert m["value"] is None and m["met"] is None
    assert result["verdict"] == "met", "N/A must not fail a workspace for not having " \
                                       "a technology"


def test_a_dirty_build_fails_validation_before_any_ratio_is_believed(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "graph.jsonl").write_text('{"id": "svc:a@a", "type": "svc"}\n',
                                     encoding="utf-8")
    (out / "unresolved.jsonl").write_text("", encoding="utf-8")
    (out / "build_report.json").write_text(json.dumps({
        "run_id": "r", "status": "ok",
        "contract_violations": ["row missing promised field"],
        "extractors": [{"name": "x", "failed": 1}]}), encoding="utf-8")
    result = V.validate(out, dict(V.DEFAULT_TARGETS))
    clean = metric(result, "zero_silent_drops")
    assert clean["met"] is False
    assert any("contract violation" in p for p in clean["problems"])
    assert any("extractor failed: x" in p for p in clean["problems"])
    assert result["verdict"] == "missed"


# ── the spot-audit sample ─────────────────────────────────────────────────────


def test_the_sample_is_seeded_and_repeatable(built):
    a = V.validate(built, dict(V.DEFAULT_TARGETS), sample=5, seed="audit-1")
    b = V.validate(built, dict(V.DEFAULT_TARGETS), sample=5, seed="audit-1")
    c = V.validate(built, dict(V.DEFAULT_TARGETS), sample=5, seed="audit-2")
    assert a["sample"] == b["sample"], "same seed, same draw — re-checkable by anyone"
    assert a["sample"]["rows"] != c["sample"]["rows"]
    assert len(a["sample"]["rows"]) == 5
    assert all(":" in r["where"] for r in a["sample"]["rows"]), \
        "every sampled edge carries the provenance the audit checks"


def test_the_report_renders_the_sample_and_the_rollup(built):
    result = V.validate(built, dict(V.DEFAULT_TARGETS), sample=3, seed="s")
    md = V.render_markdown(result)
    assert "## Spot-audit sample (seed `s`)" in md
    assert "## Unresolved, by kind" in md
    assert "| topics_with_producer_and_consumer | 20.0% | 1/5 | " in md


def test_a_missing_input_is_a_loud_refusal(tmp_path, capsys):
    assert V.main(["--graph-dir", str(tmp_path)]) == 2
    assert "does not exist" in capsys.readouterr().out


# ── what a PERSON sees: the CLI is spawned, not called ────────────────────────
#
# Item 96's rule. A refusal and a traceback are both "a failing call" in-process;
# only a spawned process distinguishes them, and both defects below are about the
# exit code and the sentence a reader gets — neither is visible from `main()`.

VALIDATE = Path(V.__file__).resolve()

CLEAN_REPORT = ('{"run_id": "r", "status": "ok", "contract_violations": [], '
                '"extractors": []}')


def _run(*args, cwd):
    import subprocess
    return subprocess.run([sys.executable, str(VALIDATE), *args],
                          cwd=str(cwd), capture_output=True, text=True)


def _out_dir(tmp_path, graph: str, report: str = CLEAN_REPORT) -> Path:
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    (out / "graph.jsonl").write_text(graph, encoding="utf-8")
    (out / "unresolved.jsonl").write_text("", encoding="utf-8")
    (out / "build_report.json").write_text(report, encoding="utf-8")
    return out


#: One node, one endpoint, both ends of a spec match: endpoints_spec_matched is
#: 1/1 and orphans 0/2, so two targets are actually GRADED and both are met.
GRADED_GRAPH = (
    '{"id": "svc:a@a", "type": "svc"}\n'
    '{"src": "svc:a@a", "dst": "ep:/x", "type": "DG_EXPOSES", '
    '"source_file": "f", "line": 1, "extractor": "e"}\n'
    '{"src": "cfg:a/s.yaml", "dst": "ep:/x", "type": "DG_SPECIFIES", '
    '"source_file": "s.yaml", "line": 1, "extractor": "e"}\n'
    '{"id": "ep:/x", "type": "ep"}\n')


def test_a_dashboard_that_graded_nothing_is_unmeasurable_not_met(tmp_path):
    """The defect this pins: a build with NOTHING in it verdicted `met` at exit 0.

    Every ratio was N/A, orphans read 0/0 as `0.0% <= 5.0` and passed, and
    `all(m["met"] is not False)` is vacuously true over a list of Nones — a
    clean bill of health from a check that looked at nothing.
    """
    out = _out_dir(tmp_path, '{"kind": "header", "schema_version": 1}\n')
    r = _run("--graph-dir", str(out), cwd=tmp_path)
    combined = r.stdout + r.stderr
    assert r.returncode == 3, combined
    assert "verdict: unmeasurable" in combined, combined
    assert "N/A (0/0)" in combined, \
        "0 of 0 is not 0.0% — a ratio with an empty denominator has no value"
    assert "orphans: 0.0%" not in combined, combined
    assert "PASS orphans" not in combined, \
        "orphans was the one ratio that graded an empty graph, and passing it is " \
        "how the whole dashboard came back met"
    written = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    assert written["verdict"] == "unmeasurable"


def test_a_graded_dashboard_that_meets_its_targets_still_says_met(tmp_path):
    """The control. Nothing about a dashboard that DID measure something changes."""
    out = _out_dir(tmp_path, GRADED_GRAPH)
    r = _run("--graph-dir", str(out), cwd=tmp_path)
    combined = r.stdout + r.stderr
    assert r.returncode == 0, combined
    assert "verdict: met" in combined, combined
    assert "PASS endpoints_spec_matched: 100.0%" in combined, combined
    assert "N/A (0/0)" in combined, \
        "the ungraded targets are still listed, by name, as N/A"


#: The same build report as the dirty-build test above, as the CLI reads it from disk.
UNCLEAN_REPORT = json.dumps({
    "run_id": "r", "status": "ok",
    "contract_violations": ["row missing promised field"],
    "extractors": [{"name": "x", "failed": 1}]})


def test_an_unclean_build_outranks_a_dashboard_that_graded_nothing(tmp_path):
    """The control on the other side of the precedence: a finding beats could-not-look.

    The graph that verdicts `unmeasurable` at exit 3 two tests up must verdict
    `missed` at exit 2 once the build report is dirty. An unclean build is a fact
    the tool READ — not a blind spot — so it is not softened into "nothing could
    be graded", and a reader still gets the exit code that means a failure.
    """
    out = _out_dir(tmp_path, '{"kind": "header", "schema_version": 1}\n',
                   report=UNCLEAN_REPORT)
    r = _run("--graph-dir", str(out), cwd=tmp_path)
    combined = r.stdout + r.stderr
    assert r.returncode == 2, combined
    assert "verdict: missed" in combined, combined
    assert "verdict: unmeasurable" not in combined, combined
    assert "zero_silent_drops: FAIL" in combined, combined
    assert "!! contract violation: row missing promised field" in combined, combined
    assert "!! extractor failed: x" in combined, combined
    written = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    assert written["verdict"] == "missed"


CONFLICT = "<<<<<<< HEAD\n"


@pytest.mark.parametrize("door", ["graph.jsonl", "unresolved.jsonl",
                                  "build_report.json", "targets.toml"])
def test_every_input_door_refuses_damage_the_same_way(tmp_path, door):
    """The defect this pins: damage was a refusal at one door and a traceback at
    the others. `build_report.json` and a `--targets` file are both COMMITTED,
    PR-diffed documents; a conflict marker left in either took the dashboard down
    with a JSONDecodeError/TOMLDecodeError at exit 1 — the one failure mode
    `_rows` was hardened against in this very module."""
    out = _out_dir(tmp_path, GRADED_GRAPH)
    args = ["--graph-dir", str(out)]
    if door == "targets.toml":
        targets = tmp_path / door
        targets.write_text(CONFLICT + "orphans_max = 5.0\n", encoding="utf-8")
        args += ["--targets", str(targets)]
    else:
        (out / door).write_text(CONFLICT, encoding="utf-8")

    r = _run(*args, cwd=tmp_path)
    combined = r.stdout + r.stderr
    assert "Traceback (most recent call last)" not in combined, combined
    assert r.returncode == 2, combined
    assert "!!" in combined, combined
    assert door in combined, "the refusal names the file that is unreadable"


# ── T103: a file that could not be READ is not a UI URL that failed to resolve ─
#
# `ui_urls_resolved` keyed its miss denominator by EXTRACTOR name, so every
# `grammar-unavailable` / `unparsable` row the frontend extractor emits — each of
# which means the source was never READ — counted as an unresolved UI URL. One
# unreadable file read as a 50% miss, on a target that is an M1 floor (90%). The
# sibling `host_keys_resolved` keys by KIND (`unpaired-host`); this now does too,
# by `unpaired-url`, and the could-not-read rows are reported BESIDE the metric in
# the unresolved rollup, never INSIDE its denominator.


def test_a_could_not_read_row_is_not_a_ui_url_miss(tmp_path):
    out = _out_dir(
        tmp_path,
        '{"id": "ui:app@a", "type": "ui"}\n'
        '{"src": "ui:app@a", "dst": "ep:/x", "type": "DG_CALLS", '
        '"source_file": "app.js", "line": 1, "extractor": "frontend-urls"}\n'
        '{"id": "ep:/x", "type": "ep"}\n')
    (out / "unresolved.jsonl").write_text(
        # One genuine URL miss (must count) beside TWO could-not-read rows (must
        # not) — TWO on purpose, so inverting the kind test to `!=` gives 1/3, not
        # 1/2, and cannot masquerade as the correct answer with one row each side.
        '{"extractor": "frontend-urls", "kind": "unpaired-url", '
        '"source_file": "app.js", "line": 2}\n'
        '{"extractor": "frontend-urls", "kind": "grammar-unavailable", '
        '"language": "typescript", "source_file": "", "line": 0}\n'
        '{"extractor": "frontend-urls", "kind": "unparsable", '
        '"source_file": "b.js", "line": 0, "error": "x"}\n',
        encoding="utf-8")
    result = V.validate(out, dict(V.DEFAULT_TARGETS))
    ui = metric(result, "ui_urls_resolved")
    assert (ui["numerator"], ui["denominator"]) == (1, 2), \
        "one hit, one true miss (unpaired-url); the two could-not-read rows are " \
        "files that were never read, not URLs that failed to resolve — keyed by " \
        "extractor they made this 1/4 and deflated the ratio to 25.0%"
    assert ui["value"] == 50.0
    kinds = {r["kind"] for r in result["unresolved"]}
    assert {"grammar-unavailable", "unparsable"} <= kinds, \
        "the could-not-read rows are reported beside the metric, in the rollup"


# ── T123: a document of the WRONG SHAPE is a named refusal, not a traceback ────
#
# Item 117's altitude, and a CLI's contract is a process (item 96) — so these are
# spawned, not called. `_read_document` is annotated `-> dict` but returned
# whatever parsed: a build_report.json that is a JSON list/scalar/null reached
# `report.get(...)` and exited on a raw AttributeError, and a --targets value of
# the wrong type reached `value <= "five"` and exited on a raw TypeError — both at
# exit 1 with a traceback, the one document class this door exists to refuse.


@pytest.mark.parametrize("report", ["[]", "null", '"ok"'])
def test_a_non_dict_build_report_is_refused_by_name(tmp_path, report):
    out = _out_dir(tmp_path, GRADED_GRAPH, report=report)
    r = _run("--graph-dir", str(out), cwd=tmp_path)
    combined = r.stdout + r.stderr
    assert "Traceback (most recent call last)" not in combined, combined
    assert r.returncode == 2, combined
    assert "!!" in combined and "build_report.json" in combined, combined


def test_a_non_numeric_target_is_refused_by_name(tmp_path):
    out = _out_dir(tmp_path, GRADED_GRAPH)
    targets = tmp_path / "targets.toml"
    targets.write_text('orphans_max = "five"\n', encoding="utf-8")
    r = _run("--graph-dir", str(out), "--targets", str(targets), cwd=tmp_path)
    combined = r.stdout + r.stderr
    assert "Traceback (most recent call last)" not in combined, combined
    assert r.returncode == 2, combined
    assert "!!" in combined and "orphans_max" in combined, \
        "the refusal names the target whose value is not a number"


# ── T234: an unknown --targets metric is refused by name, not silently accepted ─
#
# The guard was PRESENT in mcp_server.validate_graph (item 23) and ABSENT here, so
# a typo'd metric updated the targets dict inertly: the metric it meant to move
# kept its default, the `.get()` for the typo never fired, and the run graded `met`
# against a target nobody set. The MCP door refused the identical input by name.


def test_an_unknown_target_is_refused_by_name(tmp_path):
    typo = "orphans_maxx"  # a typo of a REAL target, so the guard must compare
    assert typo not in V.DEFAULT_TARGETS  # against the actual DEFAULT_TARGETS set
    out = _out_dir(tmp_path, GRADED_GRAPH)
    targets = tmp_path / "targets.toml"
    targets.write_text(f"{typo} = 5.0\n", encoding="utf-8")
    r = _run("--graph-dir", str(out), "--targets", str(targets), cwd=tmp_path)
    combined = r.stdout + r.stderr
    assert "Traceback (most recent call last)" not in combined, combined
    assert r.returncode == 2, combined
    assert "!!" in combined and typo in combined, "the refusal names the unknown target"
    assert "unknown target" in combined, combined
