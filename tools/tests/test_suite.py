"""Item 38 — the P5 engine: reach, order, verdict, and the commands it does
not own.

The defect this item is about is the one the module docstring names: every
estate profile has carried per-component `build_command`, `test_command`,
`coverage_command`, `coverage_threshold` and `known_failures` since item 15,
and the only reader anywhere asked for the TOP-LEVEL `test_command` — which a
multi-component workspace does not have. So the tests here lean on two
questions: is a declared thing actually read, and does an unanswerable question
say so rather than answering zero.

Commands are `exit N`, which both `cmd.exe` and `sh` understand, so a test
about scheduling is about scheduling rather than about a shell.
"""
from __future__ import annotations

import json
import os
import time
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.graph.core.lib import emit  # noqa: E402
from tools.graph.core.model import Edge, ExtractResult, Node  # noqa: E402
from tools.review import sca as SCANNER  # noqa: E402
from tools.sdlc import adr as A  # noqa: E402
from tools.sdlc import intake as I  # noqa: E402
from tools.sdlc import policy as P  # noqa: E402
from tools.sdlc import stage as ST  # noqa: E402
from tools.sdlc import state as L  # noqa: E402
from tools.sdlc import suite as S  # noqa: E402
from tools.sdlc import worktree as W  # noqa: E402
from tools.tests import _runs as R  # noqa: E402

WHO = "lead@example.invalid"
#: Long enough for intake to accept it - P1 refuses a spec it cannot clarify.
SPEC = ("The shared library must expose the arrival channel on an order event, "
        "and every consumer must keep building.")
PY = f'"{sys.executable}"'

#: `shared` publishes what `api` and `worker` consume; `lonely` publishes
#: nothing anybody wants. The negative control matters as much as the chain:
#: a closure that reaches everything has computed nothing.
COMPONENTS = ("shared", "api", "worker", "lonely")


def _git(where: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(where), *args], check=True, timeout=120,
                   capture_output=True)


def _graph(root: Path, cycle: bool = False) -> None:
    result = ExtractResult()
    for name in COMPONENTS:
        result.nodes.append(Node(f"svc:{name}@{name}", "svc", {"component": name}))
        result.nodes.append(Node(f"repo:{name}", "repo"))
    result.nodes += [Node("mod:g:shared", "mod"), Node("mod:g:api", "mod"),
                     Node("mod:g:lonely", "mod"), Node("mod:vendor:kafka", "mod")]
    result.edges += [
        # Who publishes what.
        Edge("svc:shared@shared", "DG_BUILT_FROM", "mod:g:shared", "pom.xml", 3, "mvn"),
        Edge("svc:api@api", "DG_BUILT_FROM", "mod:g:api", "pom.xml", 3, "mvn"),
        Edge("svc:lonely@lonely", "DG_BUILT_FROM", "mod:g:lonely", "pom.xml", 3, "mvn"),
        # Who consumes what.
        Edge("svc:api@api", "DG_DEPENDS_ON", "mod:g:shared", "pom.xml", 6, "mvn"),
        Edge("svc:worker@worker", "DG_DEPENDS_ON", "mod:g:shared", "pom.xml", 6, "mvn"),
        # Third-party: consumed, published by nobody here.
        Edge("svc:api@api", "DG_DEPENDS_ON", "mod:vendor:kafka", "pom.xml", 9, "mvn"),
    ]
    if cycle:
        result.edges.append(
            Edge("svc:shared@shared", "DG_DEPENDS_ON", "mod:g:api", "pom.xml", 7, "mvn"))
    emit.write_jsonl(root / ".sdlc" / "graph" / "graph.jsonl", result, extractor="fixture")


def _profile(root: Path, per_component: dict | None = None, top: str = "") -> None:
    per = per_component or {}
    lines = ["scope: estate", "gate_profile: team",
             "phases: [intake, requirements, design, implementation, test, "
             "review, triage, closeout]", "review:", "  block_on: [critical, high]"]
    lines += [top] if top else []
    lines.append("components:")
    for name in COMPONENTS:
        lines += [f'  - name: "{name}"', f'    path: "{name}"',
                  "    role: authoritative"]
        for key, value in (per.get(name) or {}).items():
            lines.append(f"    {key}: {value}")
    (root / ".sdlc" / "profile.yml").write_text("\n".join(lines) + "\n",
                                                encoding="utf-8")


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    root = tmp_path / "estate"
    (root / ".sdlc").mkdir(parents=True)
    for name in COMPONENTS:
        where = root / name
        where.mkdir()
        (where / "pom.xml").write_text("<project/>\n", encoding="utf-8")
        (where / "src.java").write_text("class X {}\n", encoding="utf-8")
        _git(where, "init", "-q", "-b", "main")
        _git(where, "add", "-A")
        _git(where, "-c", "user.email=d@x.invalid", "-c", "user.name=D",
             "commit", "-qm", "base")
    _profile(root)
    _graph(root)
    return root


def run_at(ws: Path, phase: str = "P5") -> str:
    st = L.create(ws, "requirement", "a change")
    rid = st["run_id"]
    folder = L.run_dir(ws, rid)
    for rel in ("30-design.md", "50-tests.md"):
        (folder / rel).write_text("Change `api/src.java`.\n", encoding="utf-8")
        L.record_artifact(ws, rid, rel)
    L.advance(ws, rid, phase=phase, status="running")
    return rid


def with_trees(ws: Path, rid: str, names=COMPONENTS, base: bool = True) -> None:
    """A worktree record without running `git worktree` — the record is what
    the engine reads, and making four real trees per test buys nothing."""
    record = {"schema_version": W.SCHEMA_VERSION, "run_id": rid, "components": {}}
    for name in names:
        where = ws / name
        head = W.git(where, "rev-parse", "HEAD", check=False)
        record["components"][name] = {
            "mode": "worktree", "path": name, "branch": f"sdlc/{rid}",
            "pattern": "sdlc/<run-id>", "base": head if base else "",
            "home": str(where), "role": "authoritative", "why": "", "at": ""}
    W.save_record(ws, rid, record)


def touch(ws: Path, name: str, text: str = "class X { int y; }\n") -> None:
    (ws / name / "src.java").write_text(text, encoding="utf-8")


# ── reach: computed from the graph, not declared ─────────────────────────────


def test_the_closure_reaches_consumers_of_what_this_component_publishes(ws):
    ordered, cycles = S.closure(ws, ["shared"])
    assert set(ordered) == {"shared", "api", "worker"}, ordered
    assert cycles == []
    assert ordered.index("shared") < ordered.index("api")
    assert ordered.index("shared") < ordered.index("worker")


def test_a_component_nobody_consumes_reaches_only_itself(ws):
    assert S.closure(ws, ["lonely"])[0] == ["lonely"]


def test_a_consumer_does_not_reach_back_to_its_dependency(ws):
    """Direction is the whole point. Changing `api` does not rebuild `shared`."""
    assert S.closure(ws, ["api"])[0] == ["api"]


def test_a_third_party_module_drops_out_with_no_vendor_list(ws):
    """`kafka` is consumed and published by nobody here, so it is not a
    component and cannot be reached. No prefix list decides that."""
    assert "kafka" not in " ".join(S.closure(ws, ["api"])[0])
    assert set(S.direct_dependents(ws)) == {"shared"}


def test_the_closure_is_transitive(ws):
    """`worker` publishing nothing is the fixture's shape; make it publish
    something `lonely` consumes and the reach must extend two hops."""
    result = ExtractResult()
    result.nodes += [Node("svc:shared@shared", "svc"), Node("svc:api@api", "svc"),
                     Node("svc:lonely@lonely", "svc"), Node("mod:g:shared", "mod"),
                     Node("mod:g:api", "mod")]
    result.edges += [
        Edge("svc:shared@shared", "DG_BUILT_FROM", "mod:g:shared", "pom.xml", 3, "mvn"),
        Edge("svc:api@api", "DG_DEPENDS_ON", "mod:g:shared", "pom.xml", 6, "mvn"),
        Edge("svc:api@api", "DG_BUILT_FROM", "mod:g:api", "pom.xml", 3, "mvn"),
        Edge("svc:lonely@lonely", "DG_DEPENDS_ON", "mod:g:api", "pom.xml", 6, "mvn"),
    ]
    emit.write_jsonl(ws / ".sdlc" / "graph" / "graph.jsonl", result, extractor="chain")
    ordered, _ = S.closure(ws, ["shared"])
    assert ordered == ["shared", "api", "lonely"], ordered


def test_a_component_that_publishes_and_consumes_one_module_is_not_its_own_dependent(ws):
    """The closure's own guard, against a graph that carries the self-edge.

    Named by item 39's mutation pass: on the real fixture the gradle extractor
    subtracts a manifest's own coordinates, so removing this guard reddened
    nothing there. A profile with its own extractors need not subtract, and a
    component that rebuilds itself is an infinite build order.
    """
    result = ExtractResult()
    result.nodes += [Node("svc:api@api", "svc"), Node("mod:g:api", "mod")]
    result.edges += [
        Edge("svc:api@api", "DG_BUILT_FROM", "mod:g:api", "pom.xml", 3, "mvn"),
        Edge("svc:api@api", "DG_DEPENDS_ON", "mod:g:api", "pom.xml", 6, "mvn"),
    ]
    emit.write_jsonl(ws / ".sdlc" / "graph" / "graph.jsonl", result, extractor="self")
    assert S.direct_dependents(ws) == {}
    assert S.closure(ws, ["api"])[0] == ["api"]


def test_a_cycle_is_reported_and_then_broken(ws):
    """A build order that hangs is worse than one somebody can argue with, so
    the argument is printed and the order still comes out."""
    _graph(ws, cycle=True)
    ordered, cycles = S.closure(ws, ["shared"])
    assert cycles, "a cycle nobody names is a cycle nobody fixes"
    assert set(ordered) == {"shared", "api", "worker"}


# ── the commands are the profile's ───────────────────────────────────────────


def test_a_component_command_is_read_and_says_it_is_the_components(ws):
    _profile(ws, {"api": {"test_command": '"exit 0"'}})
    assert S.declared(ws, "api")["test"] == ("exit 0", "component")


def test_the_top_level_command_is_the_fallback_and_says_so(ws):
    _profile(ws, {}, top='test_command: "exit 0"')
    assert S.declared(ws, "api")["test"] == ("exit 0", "top-level")


def test_a_component_command_beats_the_top_level_one(ws):
    _profile(ws, {"api": {"test_command": '"exit 3"'}}, top='test_command: "exit 0"')
    assert S.declared(ws, "api")["test"] == ("exit 3", "component")


def test_no_command_anywhere_is_a_blank_with_no_source(ws):
    assert S.declared(ws, "api")["test"] == ("", "")


def test_known_failures_survives_the_flow_list_the_installer_writes(ws):
    _profile(ws, {"api": {"known_failures": '["test_a", "test_b"]'}})
    assert S.declared(ws, "api")["known_failures"] == ["test_a", "test_b"]
    assert S.declared(ws, "worker")["known_failures"] == []


def test_a_component_threshold_beats_the_top_level_one(ws):
    _profile(ws, {"api": {"coverage_threshold": "75"}}, top="coverage_threshold: 90")
    assert S.declared(ws, "api")["coverage_threshold"] == 75
    assert S.declared(ws, "worker")["coverage_threshold"] == 90


def test_the_profile_reader_carries_every_key_the_installer_writes():
    """The defect in one line: `components()` read `path` and `role` while
    `render_components` wrote five more beside them."""
    for key in S.COMMAND_KEY.values():
        assert key in P.COMPONENT_KEYS, f"{key} is written and not read"
    assert {"coverage_threshold", "known_failures"} <= set(P.COMPONENT_KEYS)


# ── a verdict is relative to a baseline ──────────────────────────────────────


def test_green_is_green():
    # A count is now part of the claim: "green" is about TESTS, and the
    # exit code is evidence about the COMMAND (item 110).
    assert S.judge("test", 0, 0, [], tests=12)[0] == "green"


def test_red_against_a_green_baseline_is_a_regression():
    verdict, note = S.judge("test", 1, 0, [])
    assert verdict == "new-failures" and "was GREEN" in note


def test_red_against_a_red_baseline_with_a_declared_quarantine_is_not(ws):
    verdict, note = S.judge("test", 1, 1, ["test_a"])
    assert verdict == "quarantined" and "test_a" in note


def test_a_red_baseline_with_an_empty_quarantine_is_refused_not_passed():
    """'No new failures' cannot be judged against a failure set nobody wrote
    down, and answering it anyway is how a red suite reports green."""
    verdict, note = S.judge("test", 1, 1, [])
    assert verdict == "new-failures"
    assert "known_failures` is empty" in note and "cannot be judged" in note


def test_no_baseline_at_all_makes_a_red_suite_simply_red():
    verdict, note = S.judge("test", 1, None, ["test_a"])
    assert verdict == "red" and "no baseline was recorded" in note


def test_a_command_that_never_finished_decided_nothing():
    assert S.judge("test", None, 0, [])[0] == "blocked"


def test_a_build_with_no_baseline_of_its_own_is_simply_red():
    """The property this test has always held, at the level that now owns
    it. `judge` compares a build against whatever baseline it is GIVEN, and
    the ENGINE is what must give it the build's own - asserted by
    `test_the_engine_judges_a_build_against_the_builds_baseline` below. With
    nothing recorded, a red build is a red build."""
    verdict, note = S.judge("build", 1, None, [])
    assert verdict == "red" and "no build baseline was recorded" in note


# ── coverage, or the reason there is none ────────────────────────────────────


COBERTURA = """<coverage><packages><package><classes>
<class filename="src/a.py"><lines>
<line number="1" hits="1"/><line number="2" hits="0"/></lines></class>
</classes></package></packages></coverage>"""

JACOCO = """<report><package name="com/example">
<sourcefile name="X.java"><counter type="LINE" missed="1" covered="3"/></sourcefile>
</package></report>"""

LCOV = """SF:src/a.js
DA:1,1
DA:2,0
DA:3,4
end_of_record
"""


@pytest.mark.parametrize("body,name,key,expect", [
    (COBERTURA, "coverage.xml", "src/a.py", (1, 2)),
    (JACOCO, "jacoco.xml", "com/example/X.java", (3, 4)),
    (LCOV, "lcov.info", "src/a.js", (2, 3)),
])
def test_each_report_format_is_read(tmp_path, body, name, key, expect):
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    reader = S.READERS[S.REPORT_READERS[name]]
    assert reader(path)[key] == expect


def test_a_changed_file_matches_a_report_entry_by_suffix():
    reported = {"com/example/X.java": (3, 4)}
    assert S.match_file("src/main/java/com/example/X.java", reported) \
        == "com/example/X.java"


def test_an_ambiguous_match_is_no_match():
    """A figure attributed to the wrong file is worse than a missing one."""
    reported = {"a/X.java": (1, 1), "b/X.java": (0, 1)}
    assert S.match_file("X.java", reported) == ""


def test_coverage_is_the_ratio_over_changed_files_only(tmp_path):
    (tmp_path / "coverage.xml").write_text(COBERTURA, encoding="utf-8")
    row = S.changed_coverage(tmp_path, ["src/a.py"])
    assert row["percent"] == 50.0 and row["matched"] == 1


def test_a_report_that_names_none_of_the_changed_files_is_unmeasured(tmp_path):
    (tmp_path / "coverage.xml").write_text(COBERTURA, encoding="utf-8")
    row = S.changed_coverage(tmp_path, ["src/elsewhere.py"])
    assert row["percent"] is None
    assert "unmeasured rather than uncovered" in row["why"]


def test_no_report_at_all_is_a_reason_not_a_zero(tmp_path):
    row = S.changed_coverage(tmp_path, ["src/a.py"])
    assert row["percent"] is None and "no coverage report" in row["why"]


def test_no_changed_files_says_there_is_nothing_to_measure(tmp_path):
    row = S.changed_coverage(tmp_path, [])
    assert row["percent"] is None and "nothing to measure coverage OF" in row["why"]


def test_a_report_older_than_the_command_did_not_come_from_it(tmp_path):
    """Item 29's stale-artifact lesson with a percentage attached."""
    report = tmp_path / "coverage.xml"
    report.write_text(COBERTURA, encoding="utf-8")
    fresh = report.stat().st_mtime + 60
    row = S.changed_coverage(tmp_path, ["src/a.py"], not_before=fresh)
    assert row["percent"] is None and "predate the coverage command" in row["why"]


def test_an_unparsable_report_is_named_not_swallowed(tmp_path):
    (tmp_path / "coverage.xml").write_text("<not xml", encoding="utf-8")
    row = S.changed_coverage(tmp_path, ["src/a.py"])
    assert row["percent"] is None and "will not parse" in row["why"]


def test_files_in_no_report_are_excluded_and_counted_out_loud(tmp_path):
    (tmp_path / "coverage.xml").write_text(COBERTURA, encoding="utf-8")
    row = S.changed_coverage(tmp_path, ["src/a.py", "src/b.py"])
    assert row["percent"] == 50.0
    assert "excluded from the figure rather than counted as zero" in row["why"]


# ── what changed, and when that cannot be answered ───────────────────────────


def test_changed_files_come_from_the_recorded_base(ws):
    rid = run_at(ws)
    with_trees(ws, rid)
    touch(ws, "api")
    diffs, notes = S.changed_files(ws, rid)
    assert diffs["api"] == ["src.java"] and diffs["shared"] == []
    assert notes == []


def test_a_component_with_no_recorded_base_says_so_rather_than_nothing(ws):
    """An empty answer and an unanswerable one look identical downstream."""
    rid = run_at(ws)
    with_trees(ws, rid, base=False)
    diffs, notes = S.changed_files(ws, rid)
    assert "api" not in diffs
    assert any("no base commit was recorded" in n for n in notes)


def test_a_run_with_no_worktrees_says_to_name_the_components(ws):
    rid = run_at(ws)
    diffs, notes = S.changed_files(ws, rid)
    assert diffs == {} and any("--changed" in n for n in notes)


# ── running it ───────────────────────────────────────────────────────────────


def test_the_engine_runs_the_reached_components_in_order(ws):
    _profile(ws, {name: {"test_command": '"exit 0"'} for name in COMPONENTS})
    rid = run_at(ws)
    result = S.execute(ws, rid, ["shared"])
    assert result["order"].index("shared") == 0
    # This test's subject is REACH and ORDER, so it asks which steps ran -
    # `exit_code == 0` - rather than using the verdict as a proxy for it. The
    # fixture's command is `exit 0`, which runs no tests and writes no report,
    # so item 110 rightly calls its verdict `unmeasurable`: the command
    # succeeded at doing nothing. That is a different question from whether
    # the engine reached the component.
    ran = [(r["component"], r["step"]) for r in result["steps"]
           if r["exit_code"] == 0]
    assert ("api", "test") in ran and ("worker", "test") in ran
    assert ("lonely", "test") not in ran, "the negative control was built anyway"
    assert result["verdict"] == "green"


def test_a_failed_build_blocks_the_test_that_would_mean_nothing(ws):
    _profile(ws, {"api": {"build_command": '"exit 2"', "test_command": '"exit 0"'}})
    rid = run_at(ws)
    result = S.execute(ws, rid, ["api"])
    steps = {r["step"]: r for r in result["steps"] if r["component"] == "api"}
    assert steps["build"]["verdict"] == "red"
    assert steps["test"]["verdict"] == "blocked"
    assert "would mean nothing" in steps["test"]["note"]


def test_an_undeclared_command_is_named_not_silently_skipped(ws):
    rid = run_at(ws)
    result = S.execute(ws, rid, ["api"])
    assert {r["verdict"] for r in result["steps"] if r["component"] == "api"} \
        == {"not-declared"}
    assert any("coverage_command" in r["note"] and "not a pass" in r["note"]
               for r in result["steps"])


def test_without_a_graph_no_dependent_is_computed_and_it_says_so(ws):
    (ws / ".sdlc" / "graph" / "graph.jsonl").unlink()
    rid = run_at(ws)
    result = S.execute(ws, rid, ["shared"])
    assert result["order"] == ["shared"]
    assert any("no built graph" in n for n in result["notes"])


def test_the_closure_without_a_graph_refuses_in_its_own_words(ws):
    """Found by the before/after run: this raised the ADR module's error, which
    is about trigger evidence and says nothing about dependents."""
    (ws / ".sdlc" / "graph" / "graph.jsonl").unlink()
    with pytest.raises(S.SuiteError, match="no dependent can be computed"):
        S.closure(ws, ["shared"])


def test_the_estate_prompt_no_longer_states_one_components_command_as_the_workspaces(ws):
    """The sharpest half of this item's before/after. `profile_scalar` read at
    any depth, so an estate's exact-value block carried the FIRST component's
    suite - a plausible wrong value in a block whose whole purpose is
    exactness."""
    _profile(ws, {"shared": {"test_command": '"exit 0"'},
                  "api": {"test_command": '"exit 7"'}})
    rid = run_at(ws)
    text = ST.render_prompt(ws, rid, "P5", {"baseline": "0"})
    line = [ln for ln in text.splitlines() if ln.startswith("test-command:")][0]
    assert line.strip() == "test-command:", f"a component's command leaked: {line!r}"
    assert P.profile_scalar(ws, "test_command") is None


def test_an_unknown_component_is_refused_by_name(ws):
    rid = run_at(ws)
    with pytest.raises(S.SuiteError, match="no component named 'nope'"):
        S.execute(ws, rid, ["nope"])


def test_a_string_target_is_read_the_same_as_a_path(ws):
    rid = run_at(ws)
    assert S.execute(str(ws), rid, ["api"])["order"] == ["api"]


def test_a_regression_is_judged_against_the_recorded_baseline(ws):
    _profile(ws, {"api": {"test_command": '"exit 1"'}})
    rid = run_at(ws)
    folder = L.run_dir(ws, rid)
    (folder / "00-intake.json").write_text(json.dumps(
        {"baseline": {"components": {"api": {"exit_code": 0}}}}), encoding="utf-8")
    result = S.execute(ws, rid, ["api"])
    assert result["verdict"] == "new-failures"
    assert any("was GREEN" in r["note"] for r in result["steps"])


def test_a_plan_reports_that_nothing_ran_rather_than_green(ws):
    """The field run printed `verdict: green` for a dry run. Nothing had run,
    and a plan that reports a pass is the false green this module is against."""
    rid = run_at(ws)
    assert S.execute(ws, rid, ["shared"], dry_run=True)["verdict"] == "not-run"


def test_a_coverage_tool_that_will_not_run_is_not_breakage(ws):
    """The field run proposed widening the approved scope for a component whose
    build and tests were both green, because its coverage tool was missing.
    That is a measurement failure and it is already reported as one."""
    _profile(ws, {"api": {"test_command": '"exit 0"', "build_command": '"exit 0"',
                          "coverage_command": '"exit 1"'}})
    rid = run_at(ws)
    (L.run_dir(ws, rid) / "30-design.md").write_text(
        "Change `worker/src.java`.\n", encoding="utf-8")
    result = S.execute(ws, rid, ["api"])
    assert result["unscoped"] == [], result["unscoped"]
    assert S.stopped(result) == []
    assert result["verdict"] == "red", "a red coverage command is still red"


def test_a_failed_build_is_not_called_a_new_test_failure(ws):
    """'Its tests regressed' and 'its build failed so its tests never ran' are
    different sentences, and the field run printed the second as the first."""
    _profile(ws, {"api": {"build_command": '"exit 2"', "test_command": '"exit 0"'}})
    rid = run_at(ws)
    result = S.execute(ws, rid, ["api"])
    S.save(ws, rid, result)
    assert S.stopped(result) == [("api", "red")]
    owed = S.obligations(ws, rid)
    assert any("api (red)" in o for o in owed), owed
    assert not any("NEW failures" in o for o in owed)


# ── breakage nobody scoped ───────────────────────────────────────────────────


def scoped_run(ws: Path) -> str:
    """A run whose design scopes `api` only — so a red `worker` is un-scoped."""
    _profile(ws, {"worker": {"test_command": '"exit 1"'},
                  "shared": {"test_command": '"exit 0"'}})
    rid = run_at(ws)
    (L.run_dir(ws, rid) / "30-design.md").write_text(
        "Change `api/src.java`.\n", encoding="utf-8")
    return rid


def test_breakage_outside_the_approved_scope_is_named(ws):
    rid = scoped_run(ws)
    result = S.execute(ws, rid, ["shared"])
    S.save(ws, rid, result)
    assert result["unscoped"] == ["worker"], result["unscoped"]


def test_the_scope_add_is_owed_and_the_run_cannot_leave_p5_without_it(ws):
    rid = scoped_run(ws)
    S.save(ws, rid, S.execute(ws, rid, ["shared"]))
    owed = ST.verify(ws, rid, "P5")
    assert any(S.SCOPE_ADD in o for o in owed), owed
    with pytest.raises(ST.StageError, match="50-scope-add.md"):
        ST.advance(ws, rid, "P6")


def test_writing_the_proposal_closes_that_obligation(ws):
    rid = scoped_run(ws)
    result = S.execute(ws, rid, ["shared"])
    S.save(ws, rid, result)
    text = S.scope_add_text(result)
    assert "worker" in text and "G1" in text
    (L.run_dir(ws, rid) / S.SCOPE_ADD).write_text(text, encoding="utf-8")
    assert not any(S.SCOPE_ADD in o for o in S.obligations(ws, rid))


def test_a_green_run_owes_no_proposal(ws):
    _profile(ws, {name: {"test_command": '"exit 0"'} for name in COMPONENTS})
    rid = run_at(ws)
    S.save(ws, rid, S.execute(ws, rid, ["shared"]))
    R.sca_clean(ws, rid, list(COMPONENTS))
    assert S.obligations(ws, rid) == []


def test_a_new_failure_is_something_p5_owes(ws):
    _profile(ws, {"api": {"test_command": '"exit 1"'}})
    rid = run_at(ws)
    (L.run_dir(ws, rid) / "00-intake.json").write_text(json.dumps(
        {"baseline": {"components": {"api": {"exit_code": 0}}}}), encoding="utf-8")
    S.save(ws, rid, S.execute(ws, rid, ["api"]))
    assert any("api (new-failures)" in o and "back to P4" in o
               for o in S.obligations(ws, rid))


def test_no_recorded_result_owes_nothing_here(ws):
    """`verify` already says `50-tests.md` is missing; saying it twice in two
    vocabularies helps nobody."""
    rid = run_at(ws)
    assert S.obligations(ws, rid) == []


# ── the SCA gate's teeth at P5 (item 40) ─────────────────────────────────────


def sca_doc(ws, findings=(), status="ok", detail=""):
    """An SCA document built through `sca._document`, so this fixture cannot
    spell a shape the real one has moved away from."""
    from tools.review import sca as scanner
    diags = [{"message": msg, "code": {"value": ident},
              "location": {"path": path, "range": {"start": {"line": 1}}},
              "severity": "ERROR", "severity_class": grade}
             for ident, grade, path, msg in findings]
    sources = [{"source": {"name": scanner.TOOL}, "diagnostics": diags}] if diags else []
    return scanner._document(
        ws, [{"name": scanner.TOOL, "status": status, "detail": detail,
              "findings": len(diags), "components": ["api"]}],
        sources, [{"name": "api"}])


def with_sca(ws, rid, **kwargs):
    path = L.run_dir(ws, rid) / SCANNER.RESULT_NAME
    path.write_text(json.dumps(sca_doc(ws, **kwargs), indent=2, sort_keys=True),
                    encoding="utf-8")
    L.record_artifact(ws, rid, SCANNER.RESULT_NAME)


def sca_run(ws):
    """A run whose engine has finished, so the SCA gate is owed."""
    rid = run_at(ws)
    S.save(ws, rid, S.execute(ws, rid, ["api"]))
    return rid


def test_an_engine_run_with_no_sca_result_owes_one(ws):
    """The gate cannot be skipped by not running it."""
    rid = sca_run(ws)
    owed = S.sca_problems(ws, rid)
    assert len(owed) == 1 and "no SCA gate result is recorded" in owed[0]
    assert "not a clean one" in owed[0]
    assert any("SCA" in o for o in S.obligations(ws, rid)), "it does not reach P5"


def test_a_clean_sca_result_owes_nothing(ws):
    rid = sca_run(ws)
    with_sca(ws, rid)
    assert S.sca_problems(ws, rid) == []


def test_a_blocking_vulnerability_is_owed_at_p5(ws):
    rid = sca_run(ws)
    with_sca(ws, rid, findings=[("CVE-1", "critical", "api/requirements.txt",
                                 "PyPI x 1.0 is affected by CVE-1")])
    owed = S.sca_problems(ws, rid)
    assert len(owed) == 1 and "SCA [critical]" in owed[0]
    assert "api/requirements.txt" in owed[0] and "CVE-1" in owed[0]


def test_a_vulnerability_below_block_on_is_not_owed(ws):
    """`block_on` is [critical, high] here. A medium is reported by the gate
    and does not hold the phase."""
    rid = sca_run(ws)
    with_sca(ws, rid, findings=[("CVE-2", "medium", "api/requirements.txt", "m")])
    assert S.sca_problems(ws, rid) == []


def test_an_ungraded_vulnerability_holds_p5_open(ws):
    """The same answer the review loop gives an ungraded finding: nobody has
    shown it is outside block_on."""
    rid = sca_run(ws)
    with_sca(ws, rid, findings=[("OSV-9", "", "api/requirements.txt", "unscored")])
    owed = S.sca_problems(ws, rid)
    assert len(owed) == 1 and "[ungraded]" in owed[0]


def test_an_errored_sca_gate_is_owed(ws):
    """A scanner that failed has not found nothing."""
    rid = sca_run(ws)
    with_sca(ws, rid, status="error", detail="1 component(s) errored (api)")
    owed = S.sca_problems(ws, rid)
    assert any("the SCA gate errored" in o for o in owed), owed


def test_a_not_run_gate_is_reported_and_not_owed(ws):
    """No vendored binary is not work P5 can do. `sca.py` still exits 3, which
    is where a script would otherwise read exit 0 as clean."""
    rid = sca_run(ws)
    with_sca(ws, rid, status="not-run", detail="not run: vendored missing")
    assert S.sca_problems(ws, rid) == []


def test_a_component_with_no_manifest_is_reported_and_not_owed(ws):
    rid = sca_run(ws)
    with_sca(ws, rid, status="nothing-scanned", detail="no manifest")
    assert S.sca_problems(ws, rid) == []


def test_an_unreadable_sca_result_is_refused_not_ignored(ws):
    rid = sca_run(ws)
    (L.run_dir(ws, rid) / SCANNER.RESULT_NAME).write_text("{not json", encoding="utf-8")
    with pytest.raises(S.SuiteError, match="not readable JSON"):
        S.sca_problems(ws, rid)


def test_the_severity_judgement_is_the_loops_not_a_second_one(ws, monkeypatch):
    """One implementation of 'is this a blocker', so a CVE and an agent finding
    are weighed by the same rule."""
    from tools.review import loop as review
    seen = []
    real = review.assess
    monkeypatch.setattr(review, "assess",
                        lambda f, b: (seen.append(list(b)), real(f, b))[1])
    rid = sca_run(ws)
    with_sca(ws, rid, findings=[("CVE-1", "critical", "p", "m")])
    S.sca_problems(ws, rid)
    assert seen and set(seen[0]) >= {"critical", "high"}, seen


# ── the coverage dial ────────────────────────────────────────────────────────


def test_the_dial_defaults_to_the_constitutions_floor(ws):
    assert S.enforcement(ws) == "advisory"


def test_a_profile_may_tighten_the_dial(ws):
    _profile(ws, {}, top="coverage_enforcement: hard")
    assert S.enforcement(ws) == "hard"


def test_a_profile_may_not_loosen_the_dial():
    policy = {**P.DEFAULTS, "coverage_enforcement_floor": "hard"}
    problems = P.tighten_only(policy, {"coverage_enforcement": "advisory"})
    assert any("looser than the floor" in p and "coverage_enforcement" in p
               for p in problems), problems


def test_under_hard_a_short_or_unmeasurable_figure_is_owed(ws):
    _profile(ws, {"api": {"test_command": '"exit 0"'}},
             top="coverage_enforcement: hard")
    rid = run_at(ws)
    S.save(ws, rid, S.execute(ws, rid, ["api"]))
    owed = S.obligations(ws, rid)
    assert any("coverage_enforcement is hard" in o for o in owed), owed


def test_under_advisory_the_same_figure_is_not_owed(ws):
    _profile(ws, {"api": {"test_command": '"exit 0"'}})
    rid = run_at(ws)
    S.save(ws, rid, S.execute(ws, rid, ["api"]))
    assert not any("coverage" in o for o in S.obligations(ws, rid))


# ── the stage contract's second judge ────────────────────────────────────────


def test_every_conditional_artifact_has_something_that_judges_it():
    """The prophylactic. A conditional artifact with no judge is a suggestion,
    which is exactly what item 64 found the ADR to be."""
    declared = {s.phase for s in ST.STAGES if s.conditional}
    assert declared == set(ST.CONDITIONAL_JUDGE), (
        f"phases declaring a conditional artifact: {sorted(declared)}; "
        f"phases with a judge: {sorted(ST.CONDITIONAL_JUDGE)}")


def test_the_p5_prompt_points_at_the_engine_rather_than_a_single_command(ws):
    rid = run_at(ws)
    text = ST.render_prompt(ws, rid, "P5", {"baseline": "0"})
    assert "suite.py" in text and "50-scope-add.md" in text
    assert "scope-add for G1" in text


def test_the_p3_prompt_still_says_what_it_said(ws):
    rid = run_at(ws, "P3")
    text = ST.render_prompt(ws, rid, "P3")
    assert "adr.py" in text and "backticks" in text
    assert "dismissed with a stated reason" in text


def test_the_p5_sop_points_at_the_engine():
    text = (ROOT / "sdlc" / "stages" / "P5-test.md").read_text(encoding="utf-8")
    assert "suite.py" in text and "known_failures" in text
    assert "--scope-add" in text


# ── P0 records a baseline per component ──────────────────────────────────────


def test_intake_records_a_baseline_for_every_component_that_declares_one(ws):
    _profile(ws, {"api": {"test_command": '"exit 0"'}})
    rows = I.component_baselines(ws, run=False)["components"]
    assert set(rows) == set(COMPONENTS)
    assert rows["api"]["command"] == "exit 0"
    assert "NOT run" in rows["api"]["note"]
    assert "nothing to compare against" in rows["worker"]["note"]


def test_a_recorded_baseline_carries_the_exit_code_p5_compares_against(ws):
    _profile(ws, {"api": {"test_command": '"exit 3"'}})
    rows = S.baselines(ws, ["api"], run=True)["components"]
    assert rows["api"]["exit_code"] == 3 and rows["api"]["ran"] is True
    assert "RED" in rows["api"]["note"]


# ── an exit code is not a claim about tests, per component too ───────────────
#
# Found by the first field run against a real repository, one release after the
# same defect was fixed in `intake.baseline`. The top-level baseline learned to
# count tests; THIS function - in the module that owns the counting - went on
# writing `baseline green` off the exit code alone, so a run whose suite
# executed 0 of 59 was recorded green per component. The fix beside the defect
# and not the one one indent along is the shape this whole arc keeps finding.


def _writes_report(where: Path, tests: int, skipped: int = 0) -> str:
    """A test command that WRITES its own JUnit report, which is the only flow
    the freshness rule accepts: a report predating the command did not come
    from it."""
    (where / "write_report.py").write_text(
        "import pathlib\n"
        "d = pathlib.Path('target'); d.mkdir(exist_ok=True)\n"
        f"d.joinpath('TEST-R.xml').write_text('<testsuite name=\"R\" "
        f"tests=\"{tests}\" skipped=\"{skipped}\"/>')\n",
        encoding="utf-8")
    return '"python write_report.py"'


def test_a_component_baseline_that_shows_no_test_ran_is_not_reported_green(ws):
    """`exit 0` with no report anywhere. Before the fix this row said, in
    those words, `baseline green`."""
    _profile(ws, {"api": {"test_command": '"exit 0"'}})
    row = S.baselines(ws, ["api"], run=True)["components"]["api"]
    assert row["exit_code"] == 0 and row["ran"] is True
    assert row["tests"] is None, "no report found is not zero tests"
    assert "baseline green" not in row["note"]
    assert "NOTHING here shows a test ran" in row["note"]


def test_a_component_suite_that_succeeds_at_doing_nothing_is_not_green(ws):
    """The field case exactly: the command exits 0 and its OWN report says it
    ran nothing. `None` and `0` stay different answers."""
    command = _writes_report(ws / "api", tests=0)
    _profile(ws, {"api": {"test_command": command}})
    row = S.baselines(ws, ["api"], run=True)["components"]["api"]
    assert row["exit_code"] == 0
    assert row["tests"] == 0, "a report was read, and it said zero"
    assert "baseline green" not in row["note"]
    assert "0 tests" in row["note"]


def test_a_component_baseline_earns_green_by_counting(ws):
    """The positive control: green is a claim, and here it is paid for."""
    command = _writes_report(ws / "api", tests=6, skipped=2)
    _profile(ws, {"api": {"test_command": command}})
    row = S.baselines(ws, ["api"], run=True)["components"]["api"]
    assert row["tests"] == 4, "6 reported minus 2 skipped is what actually ran"
    assert "baseline green - 4 test(s) ran" in row["note"]


def test_a_component_that_never_ran_still_carries_the_field(ws):
    """`tests` is present on every row, absent-shaped. A key that appears only
    on the rows that ran makes every reader choose between `.get` and a
    KeyError, and one of them will choose wrong."""
    _profile(ws, {"api": {"test_command": '"exit 0"'}})
    rows = S.baselines(ws, run=False)["components"]
    assert all("tests" in row for row in rows.values())
    assert rows["api"]["tests"] is None and rows["worker"]["tests"] is None


@pytest.mark.parametrize("code,count", [
    (0, None), (0, 0), (0, 7), (1, None), (1, 0), (3, 9), (None, None)])
def test_both_baselines_speak_with_one_voice(code, count):
    """The guard for the class rather than the instance. These two notes were
    written out twice and drifted; if either is ever inlined again, this goes
    red before a field run has to find it."""
    assert I._baseline_note(code, count) == S.baseline_note(code, count)


def test_the_estate_hole_this_item_closes(ws):
    """One assertion for the defect: the top-level reader answers 'none' for a
    workspace whose components all declare a suite."""
    _profile(ws, {name: {"test_command": '"exit 0"'} for name in COMPONENTS})
    assert I.profile_value(ws, "test_command") in (None, "")
    rows = I.component_baselines(ws, run=False)["components"]
    assert all(row["command"] == "exit 0" for row in rows.values())


def test_p0s_artifact_carries_what_p5_reads_back(ws):
    """Named by the mutation pass: reverting intake to the single top-level
    baseline reddened NOTHING, because every test called the function and none
    of them went through `00-intake.json`. Producer and consumer were each
    tested and never composed - the seam item 29 found between two fakes.
    """
    _profile(ws, {"api": {"test_command": '"exit 3"'},
                  "shared": {"test_command": '"exit 0"'}})
    rec = I.open_run(ws, SPEC, allow_dirty=True, run_baseline=True)
    rid = rec["run_id"]
    written = json.loads((L.run_dir(ws, rid) / I.INTAKE_JSON)
                         .read_text(encoding="utf-8"))
    assert "components" in written["baseline"], \
        "the artifact carries only the top-level baseline"
    read_back = S.recorded_baselines(ws, rid)
    assert read_back["api"]["exit_code"] == 3
    assert read_back["shared"]["exit_code"] == 0
    assert written["baseline"]["command"] == "", \
        "the single-repository field is still there and still honest"


def test_the_chain_end_to_end_calls_a_component_regression(ws):
    """P0 records green, the code changes, P5 asks the same component and gets
    red - and the verdict is a REGRESSION rather than 'the suite is red'."""
    _profile(ws, {"api": {"test_command": '"exit 0"'}})
    rec = I.open_run(ws, SPEC, allow_dirty=True, run_baseline=True)
    rid = rec["run_id"]
    for rel in ("30-design.md", "50-tests.md"):
        (L.run_dir(ws, rid) / rel).write_text("Change `api/src.java`.\n",
                                              encoding="utf-8")
        L.record_artifact(ws, rid, rel)
    L.advance(ws, rid, phase="P5", status="running")
    _profile(ws, {"api": {"test_command": '"exit 1"'}})
    result = S.execute(ws, rid, ["api"])
    assert result["verdict"] == "new-failures"
    assert any("was GREEN" in r["note"] for r in result["steps"]), result["steps"]


# ── the CLI ──────────────────────────────────────────────────────────────────


def test_cli_plan_runs_nothing_and_shows_the_order(ws, capsys):
    rid = run_at(ws)
    assert S.main(["--target", str(ws), "--run", rid, "--changed", "shared",
                   "--plan"]) == 0
    out = capsys.readouterr().out
    assert "shared -> " in out and "verdict: not-run" in out


def test_cli_execute_saves_the_result_and_exits_one_when_red(ws, capsys):
    _profile(ws, {"api": {"test_command": '"exit 1"'}})
    rid = run_at(ws)
    assert S.main(["--target", str(ws), "--run", rid, "--changed", "api",
                   "--execute"]) == 1
    assert (L.run_dir(ws, rid) / S.RESULT).is_file()
    assert S.RESULT in L.load(ws, rid)["artifacts"], "the result is not pinned"


def test_cli_scope_add_writes_the_proposal_and_pins_it(ws, capsys):
    rid = scoped_run(ws)
    S.save(ws, rid, S.execute(ws, rid, ["shared"]))
    assert S.main(["--target", str(ws), "--run", rid, "--scope-add"]) == 0
    assert S.SCOPE_ADD in L.load(ws, rid)["artifacts"]


def test_cli_scope_add_on_a_clean_run_proposes_nothing(ws, capsys):
    _profile(ws, {name: {"test_command": '"exit 0"'} for name in COMPONENTS})
    rid = run_at(ws)
    S.save(ws, rid, S.execute(ws, rid, ["shared"]))
    assert S.main(["--target", str(ws), "--run", rid, "--scope-add"]) == 0
    assert "nothing to propose" in capsys.readouterr().out


def test_cli_scope_add_without_a_result_refuses(ws, capsys):
    rid = run_at(ws)
    assert S.main(["--target", str(ws), "--run", rid, "--scope-add"]) == 2
    assert "no 50-suite.json" in capsys.readouterr().err


def test_cli_baseline_lists_every_component(ws, capsys):
    _profile(ws, {"api": {"test_command": '"exit 0"'}})
    assert S.main(["--target", str(ws), "--baseline"]) == 0
    out = capsys.readouterr().out
    assert "api: exit 0" in out and "worker: (none)" in out


# ── a command that exits 0 having run nothing ────────────────────────────────
#
# Found in the field, on a real repository. `mvn test` exited 0 having executed
# 0 of 59 tests (surefire died before booting), and P0 recorded `baseline
# green` on the strength of the exit code alone. CLAUDE.md's cardinal rule, one
# level up: an external binary returning nothing at exit 0 is not evidence of
# absence, and a test command exiting 0 having run no tests is not evidence of
# passing tests.


def _report(dir_: Path, name: str, body: str) -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    p = dir_ / name
    p.write_text(body, encoding="utf-8")
    return p


def test_no_report_at_all_is_none_rather_than_zero(tmp_path):
    """`None` and `0` are different answers and must stay different: NOBODY
    TOLD US and A REPORT SAYS NOTHING RAN call for different sentences, and
    folding them is how "empty reads as clean" gets another foothold."""
    assert S.tests_reported(tmp_path) is None


def test_a_directory_that_is_not_there_is_also_unknown(tmp_path):
    assert S.tests_reported(tmp_path / "nope") is None


def test_tests_are_counted_across_reports_and_both_root_shapes(tmp_path):
    """Maven and Gradle write one `TEST-*.xml` per class; some writers wrap
    them in `<testsuites>`. Both shapes are read, and the count is summed."""
    _report(tmp_path / "target" / "surefire-reports", "TEST-Foo.xml",
            '<testsuite name="Foo" tests="7" skipped="0"/>')
    _report(tmp_path / "build" / "test-results", "TEST-Bar.xml",
            '<testsuites><testsuite name="Bar" tests="3" skipped="0"/></testsuites>')
    assert S.tests_reported(tmp_path) == 10


def test_a_skipped_test_did_not_run(tmp_path):
    """A suite that reports 59 tests and skips 59 of them ran nothing. Saying
    "59" there would reintroduce the whole defect wearing a number."""
    _report(tmp_path / "reports", "TEST-All.xml",
            '<testsuite name="All" tests="59" skipped="59"/>')
    assert S.tests_reported(tmp_path) == 0


def test_a_report_that_predates_the_command_did_not_come_from_it(tmp_path):
    """The freshness rule the coverage reader already applies, for the same
    reason: without it a `target/` left over from last week vouches for a
    suite that did not run today."""
    p = _report(tmp_path / "target", "TEST-Old.xml",
                '<testsuite name="Old" tests="12" skipped="0"/>')
    os.utime(p, (1_000_000, 1_000_000))
    assert S.tests_reported(tmp_path) == 12          # no bound: counted
    assert S.tests_reported(tmp_path, not_before=2_000_000) is None


def test_an_unreadable_report_is_unknown_not_zero(tmp_path):
    """Damaged is not empty — the distinction this repository has now made
    four times. A truncated report leaves the answer None."""
    _report(tmp_path / "target", "TEST-Broken.xml", "<testsuite tests=")
    assert S.tests_reported(tmp_path) is None


def test_a_report_buried_deeper_than_the_bound_is_not_walked(tmp_path):
    """Bounded for the same reason the coverage search is: an unbounded walk
    of a tree with node_modules in it is measured in minutes."""
    deep = tmp_path.joinpath(*[f"d{i}" for i in range(8)])
    _report(deep, "TEST-Deep.xml", '<testsuite tests="5" skipped="0"/>')
    assert S.tests_reported(tmp_path) is None
    assert S.tests_reported(tmp_path, depth=9) == 5


def test_a_scan_that_wrote_to_the_tree_is_owed_at_p5(ws):
    """The field defect's reader. `sca.scan_one` records what the scan changed;
    without this it was another field written and never read, which is the
    class this project keeps meeting."""
    rid = sca_run(ws)
    with_sca(ws, rid)
    path = L.run_dir(ws, rid) / SCANNER.RESULT_NAME
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["tools"][0]["per_component"] = [
        {"component": "api", "mutated": ["go.mod", "go.sum"]}]
    path.write_text(json.dumps(doc, indent=2, sort_keys=True), encoding="utf-8")

    owed = S.sca_problems(ws, rid)
    assert len(owed) == 1, owed
    assert "wrote to" in owed[0] and "go.mod" in owed[0]


def test_a_scan_that_changed_nothing_owes_nothing_for_it(ws):
    """The control: an empty `mutated` is the ordinary case and must stay
    silent, or the warning becomes the noise nobody reads."""
    rid = sca_run(ws)
    with_sca(ws, rid)
    assert S.sca_problems(ws, rid) == []


# ── the top-level quarantine, and the single-repository case it unblocked ────
#
# Found by a field run against a real repository, and the shape is this
# project's oldest: `declared()` gives the three commands and
# `coverage_threshold` a top-level fallback and gave `known_failures` none,
# four lines below its own docstring promising one. The sweep hardened the
# lines it was looking at and left the neighbour.


def test_known_failures_falls_back_to_the_top_level(ws):
    """What the docstring already promised for every other field here."""
    _profile(ws, top='known_failures: ["flaky_one", "flaky_two"]')
    assert S.declared(ws, "api")["known_failures"] == ["flaky_one", "flaky_two"]


def test_a_component_quarantine_still_wins_over_the_top_level(ws):
    """Per-component wins. The fallback is a fallback, not an override."""
    _profile(ws, {"api": {"known_failures": '["mine"]'}},
             top='known_failures: ["everyones"]')
    assert S.declared(ws, "api")["known_failures"] == ["mine"]
    assert S.declared(ws, "worker")["known_failures"] == ["everyones"]


def test_a_single_repository_can_declare_a_quarantine_at_all(tmp_path):
    """The headline case, and the measured cost of the defect.

    A single-repository install declares no `components:`, so the component
    `policy.components` synthesises carries name/path/role/vcs and nothing
    else. Without the fallback `known_failures` is UNREACHABLE there - and
    since `judge` refuses a red baseline with an empty quarantine, a repository
    with one pre-existing failure could never leave P5.
    """
    root = tmp_path / "solo"
    (root / ".sdlc").mkdir(parents=True)
    (root / ".sdlc" / "profile.yml").write_text(
        "scope: repo\ngate_profile: solo\n"
        'test_command: "pytest"\n'
        'known_failures: ["test_already_red"]\n', encoding="utf-8")
    name = P.components(root)[0]["name"]
    spec = S.declared(root, name)
    assert spec["known_failures"] == ["test_already_red"]
    # The whole point: with a quarantine the verdict is quarantined, not a
    # refusal to judge.
    verdict, note = S.judge("test", 1, 1, spec["known_failures"])
    assert verdict == "quarantined", note


def test_an_undeclared_quarantine_is_empty_and_not_a_failure_named_none(ws):
    """`_flow_list(None)` answers `["None"]` - one declared failure, named
    None. A red baseline would then read as QUARANTINED against a name nothing
    can ever match, which is a silent pass wearing a verdict."""
    _profile(ws)
    assert S.declared(ws, "api")["known_failures"] == []
    verdict, _ = S.judge("test", 1, 1, S.declared(ws, "api")["known_failures"])
    assert verdict == "new-failures"


def test_a_blank_component_quarantine_reaches_the_top_level(ws):
    """`known_failures:` with nothing after it is not a declaration of nothing."""
    _profile(ws, {"api": {"known_failures": ""}},
             top='known_failures: ["inherited"]')
    assert S.declared(ws, "api")["known_failures"] == ["inherited"]


def test_every_field_declared_here_has_the_fallback_its_docstring_promises(ws):
    """The rule rather than the instance. `declared`'s docstring says a
    top-level key is the fallback for what a component does not state; this
    asserts it of every field it returns, so the next one added cannot quietly
    be the exception this one was."""
    _profile(ws, top=('build_command: "top-build"\ntest_command: "top-test"\n'
                      'coverage_command: "top-cov"\ncoverage_threshold: 77\n'
                      'known_failures: ["top-quarantine"]'))
    spec = S.declared(ws, "api")
    assert spec["build"] == ("top-build", "top-level")
    assert spec["test"] == ("top-test", "top-level")
    assert spec["coverage"] == ("top-cov", "top-level")
    assert spec["coverage_threshold"] == 77
    assert spec["known_failures"] == ["top-quarantine"]


def test_a_quarantined_name_may_contain_a_comma(ws):
    """A parametrised test id is exactly the shape a quarantine gets written in
    by hand, and `policy._inline_list` used to split it in two - so the entry
    reached here as `['"test_thing[1', '2]"']`, neither half matching anything.

    Fixed at the splitter rather than here, because it is the shared one for
    every inline list the stdlib reader handles. This is the end of the path,
    and it is what says so if the splitter regresses."""
    _profile(ws, top='known_failures: ["test_thing[1,2]"]')
    assert S.declared(ws, "api")["known_failures"] == ["test_thing[1,2]"]


def test_a_blank_entry_is_not_a_quarantined_failure(ws):
    """An empty item would make the quarantine non-empty, so a red baseline
    would read as QUARANTINED against a failure named nothing - the same
    silent pass as a failure named None, one shape along."""
    _profile(ws, top='known_failures: ["real_one", ""]')
    assert S.declared(ws, "api")["known_failures"] == ["real_one"]


# ── item 110: an exit code is evidence about the COMMAND ────────────────────
#
# "Green" is a claim about TESTS. `baseline_note` has said so since item 38's
# amendment, and `judge` — the function that decides whether P5 is passed —
# read the exit code alone, so a suite that collected ZERO tests and exited 0
# was a green test step. Item 40's rule, met at P8/P9/P10 and open at P5.


def test_a_suite_that_ran_nothing_is_not_green():
    """**The item.** The command succeeded at doing nothing, which is the
    loudest thing a test step can be asked to notice."""
    verdict, note = S.judge("test", 0, 0, [], tests=0)
    assert verdict == "unmeasurable"
    assert "0 tests ran" in note and "succeeded at doing nothing" in note


def test_a_suite_with_no_report_cannot_claim_a_test_ran():
    verdict, note = S.judge("test", 0, 0, [], tests=None)
    assert verdict == "unmeasurable"
    assert "no JUnit-XML report" in note
    assert "untested rather than as passed" in note


def test_a_suite_that_really_ran_is_green():
    assert S.judge("test", 0, 0, [], tests=1)[0] == "green"


def test_unmeasurable_is_reported_and_does_NOT_stop_the_phase():
    """**Deliberate, and the reason it is not `red`.** A suite that reports no
    count is unreadable, not failing — and reddening every repository whose
    runner writes no JUnit XML is how a real check gets switched off, which is
    the risk `prepass.py`'s own docstring names and item 100 paid for."""
    result = {"enforcement": "advisory", "coverage": {}, "steps": [
        {"component": "api", "step": "test", "verdict": "unmeasurable",
         "exit_code": 0}]}
    assert S._verdict(result) == "green"
    assert S.stopped(result) == [], "an unreadable count must not stop the phase"
    assert "unmeasurable" in S.VERDICTS


def test_only_the_test_step_needs_a_count():
    """A build or a lint step makes no claim about tests, so an exit code IS
    its whole evidence. Asking them for a count would report every green build
    as unmeasurable."""
    for step in ("build", "lint", "coverage"):
        assert S.judge(step, 0, 0, [], tests=None)[0] == "green"


def test_a_baseline_that_counted_zero_is_not_evidence_that_it_worked():
    """`new-failures` still fires — the suite IS red — but the sentence must
    not claim a baseline proved something it never measured."""
    verdict, note = S.judge("test", 1, 0, [], tests=3, baseline_tests=0)
    assert verdict == "new-failures"
    assert "having run 0 tests" in note and "not evidence" in note


def test_an_unknown_baseline_count_is_not_read_as_zero():
    """**Absent is not measured-zero.** `None` means nobody supplied a count;
    reading it as 'the baseline measured nothing' would fold absent into
    invalid, the distinction this package keeps having to make."""
    verdict, note = S.judge("test", 1, 0, [], tests=3, baseline_tests=None)
    assert verdict == "new-failures"
    assert "was GREEN" in note, "the original sentence, for an unknown count"
    assert "0 tests" not in note


# ── item 110, composed: the ENGINE must actually hand judge the counts ──────
#
# The mutation pass earned these. `judge` was tested and the engine was tested
# and NOTHING composed them, so three mutations survived: the engine passing no
# count at all, passing a STALE count, and never reading the baseline's. That
# is this project's most-found class, reproduced in the item that fixes another
# instance of it — so these run the real engine and real commands.


def _junit(n: int) -> str:
    cases = "".join(f'<testcase name="t{i}" classname="c"/>' for i in range(n))
    return f'<testsuite name="s" tests="{n}" failures="0" skipped="0">{cases}</testsuite>'


def test_the_engine_reports_a_suite_that_wrote_no_report_as_unmeasurable(ws):
    """End to end: a command that exits 0 and writes nothing is not green."""
    _profile(ws, {name: {"test_command": '"exit 0"'} for name in COMPONENTS})
    rid = run_at(ws)
    result = S.execute(ws, rid, ["shared"])
    tests = [r for r in result["steps"] if r["step"] == "test"]
    assert tests, "no test step ran"
    assert {r["verdict"] for r in tests} == {"unmeasurable"}
    assert all("no JUnit-XML report" in r["note"] for r in tests)


def test_the_engine_reports_a_suite_that_really_ran_as_green(ws):
    """The other half — without it, `unmeasurable` everywhere would also pass.

    The command WRITES the report, because freshness demands the report come
    from this step: a file staged beforehand is exactly what the next test
    proves must not count."""
    for name in COMPONENTS:
        (ws / name).mkdir(parents=True, exist_ok=True)
        (ws / name / "mk.py").write_text(chr(10).join([
            "from pathlib import Path",
            f"Path('junit.xml').write_text({_junit(3)!r}, encoding='utf-8')",
        ]) + chr(10), encoding="utf-8")
    _profile(ws, {name: {"test_command": '"python mk.py"'} for name in COMPONENTS})
    rid = run_at(ws)
    result = S.execute(ws, rid, ["shared"])
    tests = [r for r in result["steps"] if r["step"] == "test"]
    assert tests and {r["verdict"] for r in tests} == {"green"},         [(r["component"], r["verdict"], r["note"]) for r in tests]



def test_the_engine_ignores_a_report_that_predates_the_step(ws):
    """**Freshness, composed.** A `target/` left over from last week would
    otherwise vouch for a suite that did not run today — the mutation that
    dropped `not_before` survived until this test existed."""
    for name in COMPONENTS:
        (ws / name).mkdir(parents=True, exist_ok=True)
        (ws / name / "junit.xml").write_text(_junit(9), encoding="utf-8")
    old = time.time() - 3600
    for name in COMPONENTS:
        os.utime(ws / name / "junit.xml", (old, old))
    _profile(ws, {name: {"test_command": '"exit 0"'} for name in COMPONENTS})
    rid = run_at(ws)
    result = S.execute(ws, rid, ["shared"])
    tests = [r for r in result["steps"] if r["step"] == "test"]
    assert tests and {r["verdict"] for r in tests} == {"unmeasurable"}, \
        "a stale report vouched for a suite that never ran"


def test_the_engine_reads_the_baselines_own_count_not_just_its_exit(ws):
    """**The mutation that survived until this test existed.** `judge` was
    proven to caveat a zero-count baseline, and nothing proved the ENGINE reads
    `tests` off the recorded baseline row — the producer/consumer-never-composed
    class, in the item that fixes another instance of it.

    The baseline exited 0 having run 0 tests, so it is not evidence that
    anything worked before; the suite is red now, and the note must say which."""
    rid = run_at(ws)
    intake_json = L.run_dir(ws, rid) / "00-intake.json"
    intake_json.write_text(json.dumps({"baseline": {"components": {
        name: {"exit_code": 0, "tests": 0} for name in COMPONENTS}}}),
        encoding="utf-8")
    _profile(ws, {name: {"test_command": '"exit 1"'} for name in COMPONENTS})
    result = S.execute(ws, rid, ["shared"])
    tests = [r for r in result["steps"] if r["step"] == "test"]
    assert tests, "no test step ran"
    assert {r["verdict"] for r in tests} == {"new-failures"}
    assert all("having run 0 tests" in r["note"] for r in tests), \
        [r["note"] for r in tests]
    assert all("not evidence" in r["note"] for r in tests)


# ── item 111: P0 baselines the BUILD, so a broken one stops being re-blamed ──
#
# P0 recorded the test command and never the build, so P5 judged the build
# absolutely: a repository whose build was already red had every future change
# blamed for it, forever, and the only way past was `--force`. Found by the
# first field run and recorded as deliberately unfixed, because it changes P5's
# verdicts rather than its reporting.


def test_a_build_broken_by_this_change_is_a_regression():
    verdict, note = S.judge("build", 1, 0, [])
    assert verdict == "new-failures" and "broke the build" in note


def test_a_build_that_was_already_red_is_not_this_changes_fault():
    """**The item.** The baseline IS the declaration."""
    verdict, note = S.judge("build", 1, 1, [])
    assert verdict == "quarantined"
    assert "already red at the baseline" in note
    assert "did not break it" in note


def test_no_build_quarantine_list_is_invented():
    """A test suite is a SET, so `known_failures` must name WHICH failures are
    pre-existing — which is why item 38 refuses a red test baseline with an
    empty quarantine. A build is atomic: it built or it did not, and the
    baseline's exit code says everything a list could. A third spelling of one
    idea (`known_failures`, `sca_known_findings`, and now this) would buy no
    information."""
    from tools.sdlc import policy as P

    assert not any("build" in k and "fail" in k for k in P.COMPONENT_KEYS)
    # And it judges the same with or without a test quarantine declared.
    assert S.judge("build", 1, 1, [])[0] == "quarantined"
    assert S.judge("build", 1, 1, ["test_a"])[0] == "quarantined"


def test_a_quarantined_build_does_not_stop_the_phase():
    result = {"enforcement": "advisory", "coverage": {}, "steps": [
        {"component": "api", "step": "build", "verdict": "quarantined",
         "exit_code": 1}]}
    assert S._verdict(result) == "green"
    assert S.stopped(result) == []


def test_the_recorder_writes_a_build_baseline_beside_the_test_one(ws):
    _profile(ws, {name: {"build_command": '"exit 0"',
                         "test_command": '"exit 0"'} for name in COMPONENTS})
    found = S.baselines(ws, run=True)
    for name in COMPONENTS:
        row = found["components"][name]
        assert "build" in row, f"{name} recorded no build baseline"
        assert row["build"]["exit_code"] == 0
        assert "GREEN" in row["build"]["note"]


def test_a_recorded_build_baseline_says_when_it_was_red(ws):
    _profile(ws, {name: {"build_command": '"exit 3"'} for name in COMPONENTS})
    row = S.baselines(ws, run=True)["components"]["api"]["build"]
    assert row["exit_code"] == 3
    assert "RED (exit 3) before any change" in row["note"]
    assert "pre-existing" in row["note"]


def test_an_undeclared_build_command_says_so_rather_than_passing(ws):
    _profile(ws, {name: {"test_command": '"exit 0"'} for name in COMPONENTS})
    row = S.baselines(ws, run=True)["components"]["api"]["build"]
    assert row["exit_code"] is None
    assert "no build_command declared" in row["note"]


def test_the_engine_judges_a_build_against_the_builds_baseline(ws):
    """**Composed, not merely present.** Reading the TEST baseline for a build
    would compare a build against a suite — which is how "the build broke" and
    "a test broke" become one sentence. The baseline here is red for the build
    and GREEN for tests, so a verdict of `new-failures` would prove the engine
    read the wrong row."""
    rid = run_at(ws)
    (L.run_dir(ws, rid) / "00-intake.json").write_text(json.dumps(
        {"baseline": {"components": {
            name: {"exit_code": 0, "tests": 5,
                   "build": {"exit_code": 2}} for name in COMPONENTS}}}),
        encoding="utf-8")
    _profile(ws, {name: {"build_command": '"exit 2"'} for name in COMPONENTS})
    result = S.execute(ws, rid, ["shared"])
    builds = [r for r in result["steps"] if r["step"] == "build"]
    assert builds, "no build step ran"
    assert {r["verdict"] for r in builds} == {"quarantined"}, \
        [(r["component"], r["verdict"], r["note"]) for r in builds]
    assert all("already red at the baseline" in r["note"] for r in builds)


# ── item 117 part 7: a metric nobody can compute is never zero ───────────────
#
# `_int_of(threshold, 0)` folded "this floor cannot be read" into "the floor is
# zero", and `met` is `percent >= threshold`, so the coverage gate passed
# EVERYTHING — a silent pass in the gate that value exists to arm. Measured on
# the shape that motivated it: `90\xa0# floor`, a threshold pasted out of a web
# page with an invisible space in it, read as 0. The profile now refuses that
# particular file outright, so these drive an unreadable value that is
# perfectly legal YAML — which is the case no refusal can catch.


def test_an_unreadable_coverage_threshold_is_unmeasurable_not_zero(ws):
    """The rule this harness states everywhere and broke in the gate it arms."""
    _profile(ws, {"api": {"coverage_threshold": "ninety"}})
    spec = S.declared(ws, "api")
    assert spec["coverage_threshold"] is None, (
        "an unreadable floor became 0, and `percent >= 0` passes everything")
    assert "unmeasurable" in spec["threshold_note"]


def test_a_blank_coverage_threshold_still_means_no_floor(ws):
    """The control that keeps the change honest: ABSENT is not the same as
    present-and-unreadable, and only the second is a finding. Nobody declared a
    floor, so there is nothing to miss."""
    spec = S.declared(ws, "api")
    assert spec["coverage_threshold"] == 0
    assert "threshold_note" not in spec


def test_a_readable_threshold_is_unchanged(ws):
    """The other control: this must not turn every declared floor into None."""
    _profile(ws, {"api": {"coverage_threshold": "75"}})
    assert S.declared(ws, "api")["coverage_threshold"] == 75


def test_an_unreadable_threshold_holds_the_coverage_gate(ws):
    """**The composition, which is this project's most-found gap.** `declared`
    tested and `obligations` tested and nothing joining them is exactly how the
    SCA filename drift, the `judge`/engine split and item 46's component reader
    all shipped. The engine is RUN, so an unreadable floor has to travel from the
    profile through the coverage row to a sentence P5 owes.
    """
    _profile(ws, {"api": {"test_command": '"exit 0"', "build_command": '"exit 0"',
                          "coverage_command": '"exit 0"',
                          "coverage_threshold": "ninety"}},
             top="coverage_enforcement: hard")
    rid = run_at(ws)
    result = S.execute(ws, rid, ["api"])
    S.save(ws, rid, result)
    row = result["coverage"]["api"]
    assert row["threshold"] is None and row["met"] is None, row
    assert result["verdict"] == "coverage-short", (
        f"an unreadable floor met itself and the run went green: {result['verdict']}")
    owed = S.obligations(ws, rid)
    assert any("unmeasurable" in o and "api" in o for o in owed), owed
