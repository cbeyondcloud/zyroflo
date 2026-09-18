"""The metrics engine — item 52.

Every number comes from a record some gate already writes. Nothing is
instrumented for metrics, because a metric with its own collection path is a
second account of the same events that can disagree with the first.

The properties worth more than the rest of this file:

**A metric nobody can compute is NEVER zero.** *Deployment frequency: 0* reads
as "we deploy nothing"; the truth is "this harness has never deployed". Those
are different sentences and only one is true, so an unmeasurable metric carries
`value: null` and a reason, and the document rolls all of them up so a reader
meets the gaps before deciding what to trust.

**An override must be impossible to miss.** The first cut counted forced
APPROVALS and reported `0 forced` on a repository where a phase had been forced
past 103 open supply-chain findings — because a forced ADVANCE lives in the
run's history, not the approval ledger. That is the exact shape of false
assurance this module exists to refuse, produced by this module, on its first
real run.

**No duckdb**: measured at 53 JSONL rows over 65 KB, and item 62's
zero-dependency gate forbids the import anyway.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.review import hitl  # noqa: E402
from tools.sdlc import approve  # noqa: E402
from tools.sdlc import metrics as M  # noqa: E402
from tools.sdlc import stage as ST  # noqa: E402
from tools.sdlc import state as ledger  # noqa: E402

PROFILE = ("stack: python\ngate_profile: solo\n"
           "phases: [intake, requirements, design, implementation, test, review, "
           "triage, closeout]\n"
           'test_command: "pytest -q"\nreview:\n  block_on: [critical, high]\n')


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / ".sdlc").mkdir(parents=True)
    (r / ".sdlc" / "profile.yml").write_text(PROFILE, encoding="utf-8")
    subprocess.run(["git", "-C", str(r), "init", "-q"], check=True,
                   capture_output=True, timeout=120)
    return r


def a_run(repo: Path, kind: str = "defect") -> str:
    return ledger.create(repo, kind, "a title")["run_id"]


def approved(repo: Path, run_id: str, gate: str = "G3") -> None:
    """A gate taken for real. The run must be a REQUIREMENT: item 45's check
    refuses a G3 on a defect carrying no reproduction, which is that check
    working and not an obstacle to route around."""
    folder = ledger.run_dir(repo, run_id) / "40-impl"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "README.md").write_text("work\n", encoding="utf-8")
    ledger.record_artifact(repo, run_id, "40-impl/")
    ledger.advance(repo, run_id, phase="P4", note="implemented")
    approve.record(repo, run_id, gate, "approve", "owner@example.test",
                   notes="read and approved")


def reviewed(repo: Path, run_id: str, seconds: int, findings=()) -> None:
    reviews, _ = hitl.paths(repo)
    reviews.parent.mkdir(parents=True, exist_ok=True)
    disp: dict[str, int] = {}
    for d in findings:
        disp[d] = disp.get(d, 0) + 1
    with reviews.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({
            "kind": "review", "run": run_id, "component": "api",
            "started_at": "2026-08-29T01:00:00+00:00",
            "finished_at": "2026-08-29T01:10:00+00:00",
            "duration_s": seconds, "verdict": "approve", "findings": [],
            "counts": {"by_disposition": disp, "by_source": {}}},
            sort_keys=True) + "\n")


# ── absent is never zero, which is the whole design ─────────────────────────


def test_every_dora_measure_is_null_with_a_reason_when_nothing_deployed(repo):
    """**The property this module exists for.** All four are defined against
    production, and nothing here has reached it. Reporting 0 would say the
    harness deploys and fails at nothing."""
    doc = M.compute(repo)
    for name in M.DORA:
        assert doc["dora"][name]["value"] is None, name
        assert "never" in doc["dora"][name]["why"] or "deployed" in \
            doc["dora"][name]["why"], name
    assert doc["dora"]["deploys_recorded"]["value"] == 0


def test_the_dora_premise_is_computed_not_assumed(repo):
    """If a run ever reaches P9 or P10 the section stops being an absence and
    starts being work. Asserting the absence would make it permanent."""
    run_id = a_run(repo)
    assert M.deploys_recorded(repo, M.run_states(repo)[0]) == 0
    ledger.advance(repo, run_id, phase="P9", note="deploying")
    doc = M.compute(repo)
    assert doc["dora"]["deploys_recorded"]["value"] == 1
    assert "not implemented yet" in doc["dora"]["deployment_frequency"]["why"]


def test_every_null_is_rolled_up_with_its_reason(repo):
    """A metrics file whose nulls are scattered through it is one where the
    reader finds them last, or not at all."""
    doc = M.compute(repo)
    assert len(doc["unmeasured"]) >= len(M.DORA)
    assert all(": " in line for line in doc["unmeasured"])
    assert any(line.startswith("dora.deployment_frequency") for line in doc["unmeasured"])


def test_a_computed_zero_is_a_value_not_an_absence(repo):
    """The other half of the same rule. Zero runs is a fact; it must not read
    as unmeasured."""
    doc = M.compute(repo)
    assert doc["runs"]["total"] == {"value": 0}
    assert not any(line.startswith("runs.") for line in doc["unmeasured"])


# ── an override must be impossible to miss ──────────────────────────────────


def test_a_forced_advance_is_counted(repo):
    """**The defect this module produced on its own first real run.** A forced
    ADVANCE is in the run's history, not the approval ledger, so counting only
    forced approvals reported `0 forced` for a repository where a phase had
    been forced past 103 open supply-chain findings."""
    run_id = a_run(repo)
    ledger.advance(repo, run_id, phase="P6",
                   note=f"{ST.FORCED_NOTE}P5: 103 open findings")
    doc = M.compute(repo)
    assert doc["gates"]["forced_advances"]["value"] == 1


def test_the_forced_marker_has_one_home(repo):
    """`stage.advance` writes it and `metrics` reads it. Two modules spelling
    the same string independently is how a wording change silently zeroes an
    override count."""
    src = (ROOT / "tools" / "sdlc" / "stage.py").read_text(encoding="utf-8")
    assert f'FORCED_NOTE = "{ST.FORCED_NOTE}"' in src
    assert src.count('"forced past ') == 1        # the declaration, nowhere else


def test_an_unforced_run_counts_none(repo):
    run_id = a_run(repo)
    ledger.advance(repo, run_id, phase="P1", note="ordinary progress")
    assert M.compute(repo)["gates"]["forced_advances"]["value"] == 0


def test_a_gate_taken_twice_is_reported_as_re_taken(repo):
    """A gate re-taken is one that went VOID - the P6-to-P4 loop, or an
    artifact edited after approval. It is the machinery working, and invisible
    in a total."""
    run_id = a_run(repo, "requirement")
    approved(repo, run_id, "G3")
    approve.record(repo, run_id, "G3", "approve", "owner@example.test",
                   notes="re-taken after a correction")
    doc = M.compute(repo)
    assert doc["gates"]["approvals"]["value"] == 2
    assert doc["gates"]["re_taken"]["value"] == {"G3": 1}


# ── the counts themselves ───────────────────────────────────────────────────


def test_runs_are_counted_by_kind_and_status(repo):
    a_run(repo, "defect")
    a_run(repo, "requirement")
    closed = a_run(repo, "defect")
    ledger.advance(repo, closed, status="closed", note="done")
    doc = M.compute(repo)
    assert doc["runs"]["total"]["value"] == 3
    assert doc["runs"]["by_kind"]["value"] == {"defect": 2, "requirement": 1}
    assert doc["runs"]["closed"]["value"] == 1 and doc["runs"]["open"]["value"] == 2


def test_the_review_median_is_taken_over_completed_sessions(repo):
    run_id = a_run(repo)
    reviewed(repo, run_id, 100, ["accepted"])
    reviewed(repo, run_id, 300, ["rejected", "accepted"])
    doc = M.compute(repo)
    assert doc["review"]["sessions"]["value"] == 2
    assert doc["review"]["median_duration_s"]["value"] == 200.0
    assert doc["review"]["findings_by_disposition"]["value"] == \
        {"accepted": 2, "rejected": 1}


def test_no_review_leaves_the_median_unmeasured_not_zero(repo):
    doc = M.compute(repo)
    assert doc["review"]["median_duration_s"]["value"] is None
    assert doc["review"]["sessions"]["value"] == 0


def test_time_to_merge_ready_is_not_called_lead_time(repo):
    """DORA's lead time runs from commit to running in PRODUCTION. Renaming a
    merge-readiness proxy after it would be measuring the wrong subject and
    reporting it under the right name."""
    doc = M.compute(repo)
    assert "time_to_merge_ready_s" in doc["delivery"]
    assert "lead_time_for_changes" in doc["dora"]
    assert doc["dora"]["lead_time_for_changes"]["value"] is None


def test_merge_ready_is_measured_once_a_gate_is_taken(repo):
    run_id = a_run(repo)
    ledger.advance(repo, run_id, phase="P7",
                   note="G4 approved by owner@example.test")
    doc = M.compute(repo)
    assert isinstance(doc["delivery"]["time_to_merge_ready_s"]["value"], float)


# ── damage is counted, never dropped ────────────────────────────────────────


def test_an_unreadable_record_is_counted_rather_than_skipped(repo):
    """A metrics file that quietly loses rows reads exactly like one describing
    a quieter project."""
    run_id = a_run(repo)
    (ledger.run_dir(repo, run_id) / approve.APPROVALS).write_text(
        '{"gate": "G1", "verdict": "approve"}\n{not json\n', encoding="utf-8")
    doc = M.compute(repo)
    assert doc["gates"]["approvals"]["value"] == 1
    assert doc["unreadable"]["approvals"] == 1


def test_a_malformed_state_does_not_stop_the_sweep(repo):
    """Item 44's reason: a sweep that dies on one bad file reports nothing
    about the other fifty."""
    good = a_run(repo)
    bad = ledger.runs_dir(repo) / "20260101-000000-broken"
    bad.mkdir(parents=True)
    (bad / ledger.STATE_NAME).write_text("{not json", encoding="utf-8")
    doc = M.compute(repo)
    assert doc["runs"]["total"]["value"] == 1 and good
    assert doc["unreadable"]["runs"] == 1


# ── stdlib only, which the zero-dependency gate requires ────────────────────


ENGINE = ROOT / "tools" / "sdlc" / "metrics.py"
#: The packages that ARE this repository. Asserted below to be real directories
#: rather than trusted because they are typed here: `third_party` SUBTRACTS this
#: tuple, so a name that reaches it is a package the guard stops seeing, and a
#: name nobody checks is the same blinding as a permanently true alternative -
#: moved out of the assertion's logic and into its data.
FIRST_PARTY = ("tools", "sdlc_templates")
#: Runtime import APIs whose argument is decidable. The attribute spelling was
#: caught and the bare-name one was not, which is a boundary no reader could
#: predict; `__import__` is the same call wearing the builtin's name.
RUNTIME_IMPORTERS = ("import_module", "find_spec", "__import__")


def tracked_top_level_names() -> set[str]:
    """The top-level directory names GIT reports, not the ones the filesystem
    happens to hold. A folder sitting untracked at the root is present on this
    machine and is no part of this repository, and `Path.is_dir()` cannot tell
    the two apart."""
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"],
                         capture_output=True, text=True, check=True).stdout
    return {p.split("/", 1)[0] for p in out.split("\0") if "/" in p}


def called_name(func: ast.expr) -> str | None:
    """`importlib.import_module(...)` -> "import_module", `import_module(...)` ->
    "import_module", anything else -> None."""
    if isinstance(func, ast.Attribute):
        return func.attr
    return func.id if isinstance(func, ast.Name) else None


def imported_packages(src: str) -> set[str]:
    """Every top-level package `src` imports, read off its syntax tree.

    The tree rather than the text, because the text is where item 52's REASON
    for having no duckdb is written - and a substring scan cannot tell a
    docstring explaining a decision from the import that reverses it. An import
    spelled at RUNTIME counts too: the package still has to be installed for the
    drop-in tier to run.

    **The boundary, stated rather than left to be discovered.** Three forms are
    deliberately not caught: a name assembled at runtime (`import_module("duck"
    + "db")`) is undecidable from the tree; a package re-exported by some other
    module is outside a single-file guard; a relative import is first-party by
    definition. There is no fourth - every CONSTANT-argument runtime import is
    caught, in both spellings and through the builtin, because a guard that
    catches one spelling of a call and not another is a boundary no reader can
    predict."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            names.add(node.module.split(".")[0])
        elif (isinstance(node, ast.Call)
              and called_name(node.func) in RUNTIME_IMPORTERS
              and node.args and isinstance(node.args[0], ast.Constant)
              and isinstance(node.args[0].value, str)):
            names.add(node.args[0].value.split(".")[0])
    return names


def third_party(src: str) -> set[str]:
    """Derived from the interpreter's own list of stdlib modules, so it names no
    package and cannot go stale the way a typed ban list does: duckdb, pandas
    and numpy were the three anyone thought of, and a metrics engine can be
    killed by any fourth."""
    return {name for name in imported_packages(src)
            if name not in sys.stdlib_module_names and name not in FIRST_PARTY}


def test_the_engine_imports_no_third_party_package():
    """Item 62 asserts single-repo mode completes with no third-party packages.
    A metrics engine on duckdb would be dead in exactly the configuration this
    harness promises to work in - which is why the task line's duckdb was
    measured and set aside rather than installed."""
    assert third_party(ENGINE.read_text(encoding="utf-8")) == set()


def test_the_first_party_exemption_names_directories_of_this_repository():
    """`third_party` subtracts FIRST_PARTY, so a name that reaches that tuple is
    a package the guard stops seeing - and an unrelated real package name added
    to it survived every other test in this file. The blinding had moved out of
    the assertion's logic and into its data, which is the defect this section
    was rewritten for, one level out.

    Derived from the TREE rather than enumerated as names that must not appear:
    a list of the forbidden goes stale the moment somebody invents a package
    nobody thought to forbid, which is the same reason the guard itself stopped
    naming duckdb.

    **Asked of GIT, not of the filesystem.** The first cut asked whether the
    name was a directory on disk, which accepts a directory that merely EXISTS
    beside the checkout: an untracked folder at the root, plus its name here,
    passed. That is a claim ("of this repository") broader than the catch
    ("on this machine"), which is the defect this whole section exists to refuse.

    **THE BOUND that remains.** An entry naming a TRACKED directory of this
    repository that is not an importable package is still accepted - `docs`
    would pass. Closing that is not a trade-off, it is **unavailable**, and
    measured rather than assumed: `importlib.util.find_spec` returns a spec for
    both `docs` and `sdlc_templates` whose **loader is None** - they are
    namespace packages. So the loose form of an importability check (a spec
    exists) accepts the residual and closes nothing, while the strict form (a
    loader exists) rejects `sdlc_templates`, which is a legitimate entry. There
    is no version of that check which keeps the true case and drops this one.

    **The reversal condition**: the realistic way this bites is a third-party
    distribution whose IMPORT name collides with a tracked directory of this
    repository. If that ever happens, this bound closes.

    **A re-cased entry cannot blind the guard, on any platform.** `Tools` is a
    directory on a case-insensitive filesystem but is not what `git ls-files`
    reports, so this test fails - and the exemption subtracts by EXACT match, so
    `tools` stops being exempt and the test above fails too. The two fail in
    opposite directions and the reachable outcome is red, never green-and-blind.
    A red here on one platform and not another is designed, not flaky."""
    tracked = tracked_top_level_names()
    for name in FIRST_PARTY:
        assert name in tracked, (
            f"{name!r} is exempt from the third-party check but is not a "
            f"tracked directory of this repository, so the exemption hides a "
            f"package. Tracked: {sorted(tracked)}")


def test_that_guard_can_see_the_import_it_exists_to_refuse():
    """**The guard above could not fail.** It read `"duckdb" not in src ... or
    "No duckdb" in src`, and the engine's docstring SAYS *No duckdb* - so the
    alternative was permanently satisfied and the disjunction was decorative.
    Replayed against the real engine plus `from duckdb import connect` it
    passed, as it did for `import_module("duckdb")` and for a pandas import;
    only the three literal spellings in the loop were ever live.

    So the guard now carries a positive control. Each one is the real engine
    with one import spliced in - VALID python, compiled here to prove it,
    because a control that fails to parse would only show that the file must
    parse and would say nothing about which package it names."""
    engine = ENGINE.read_text(encoding="utf-8")
    anchor = "from __future__ import annotations\n"
    # UNIQUE, not merely present: `str.replace(..., 1)` takes the FIRST match, so
    # a docstring line quoting the future-import would move every control below
    # into prose, where it tests nothing.
    assert engine.count(anchor) == 1, (
        "the splice point is not unique; a control spliced at the wrong one "
        "proves nothing about imports")
    for line, expected in (
            ("from duckdb import connect", "duckdb"),
            ("import duckdb as ddb", "duckdb"),
            ("import pandas", "pandas"),
            ('import importlib\nengine = importlib.import_module("duckdb")', "duckdb"),
            ('from importlib import import_module\nengine = import_module("duckdb")',
             "duckdb"),
            ('engine = __import__("duckdb")', "duckdb"),
    ):
        control = engine.replace(anchor, anchor + line + "\n", 1)
        compile(control, "<control>", "exec")
        assert expected in third_party(control), line


# ── the CLI ─────────────────────────────────────────────────────────────────


def test_the_cli_writes_nothing_without_the_flag(repo, capsys):
    assert M.main(["--target", str(repo)]) == 0
    assert not (repo / M.RESULT).exists()


def test_the_cli_writes_where_the_other_records_live(repo, capsys):
    assert M.main(["--target", str(repo), "--write"]) == 0
    assert (repo / M.RESULT).is_file()
    assert M.RESULT.parts[:2] == (".sdlc", "metrics")
    doc = json.loads((repo / M.RESULT).read_text(encoding="utf-8"))
    assert doc["schema_version"] == M.SCHEMA_VERSION


def test_the_cli_names_every_unmeasured_metric(repo, capsys):
    M.main(["--target", str(repo)])
    out = capsys.readouterr().out
    # **Asserted by NAME, not by total.** I wrote this assertion as a magic
    # count twice and got it wrong twice - four, then five, when an empty
    # repository actually reports seven (the four DORA measures, the review
    # median, merge-readiness, and the commit count, because a fresh git repo
    # has no commits either). The total is a property of the FIXTURE; what the
    # test is for is that every metric the document could not compute is named
    # where a reader will see it.
    for expected in ("dora.deployment_frequency", "dora.change_failure_rate",
                     "dora.lead_time_for_changes", "dora.time_to_restore_service",
                     "review.median_duration_s"):
        assert expected in out, expected
    assert "unmeasured: " in out


def abandoned(repo: Path, run_id: str) -> None:
    reviews, _ = hitl.paths(repo)
    reviews.parent.mkdir(parents=True, exist_ok=True)
    with reviews.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({
            "kind": "review-abandoned", "run": run_id, "component": "api",
            "started_at": "2026-08-29T01:00:00+00:00",
            "abandoned_at": "2026-08-29T09:00:00+00:00",
            "findings_so_far": 0}, sort_keys=True) + "\n")


def test_an_abandoned_review_is_a_row_and_not_a_session(repo):
    """**Found by a surviving mutation.** The docstring said abandoned reviews
    are rows and never deletions, because dropping them would bias every
    duration toward the reviews that went well - and nothing tested it, so
    counting them AS sessions survived. The live repository has one."""
    run_id = a_run(repo)
    reviewed(repo, run_id, 100, ["accepted"])
    abandoned(repo, run_id)
    doc = M.compute(repo)
    assert doc["review"]["sessions"]["value"] == 1
    assert doc["review"]["abandoned"]["value"] == 1
    # An abandoned review records no duration, so it must not enter the median.
    assert doc["review"]["median_duration_s"]["value"] == 100


def test_a_run_with_no_G4_note_is_not_merge_ready(repo):
    """**The second surviving mutation.** Every test for merge-readiness used a
    history containing `G4 approved`, so reading ANY note as the gate passed
    them all. A run that never reached G4 must stay unmeasured, not be handed
    the duration of whatever it did last."""
    run_id = a_run(repo)
    ledger.advance(repo, run_id, phase="P4", note="implemented, nothing approved")
    ledger.advance(repo, run_id, phase="P5", note="tests run")
    doc = M.compute(repo)
    assert doc["delivery"]["time_to_merge_ready_s"]["value"] is None
    assert "merge-ready" in doc["delivery"]["time_to_merge_ready_s"]["why"]


# ── item 112: one spelling for a forced advance ─────────────────────────────
#
# `approve.record` carried `forced: true` as a FIELD; `stage.advance` recorded
# the same concept as PROSE inside a history note. One concept, two encodings,
# and only one of them queryable — an auditor asking "which runs were forced?"
# had to regex English.


def test_a_forced_advance_is_recorded_as_a_field(repo):
    rid = a_run(repo)
    ST.advance(repo, rid, "P1", force=True)
    entry = ledger.load(repo, rid)["history"][-1]
    assert entry.get("forced") is True, "the fact of the override is not a field"
    assert ST.FORCED_NOTE in entry["note"], \
        "the prose must stay - it says WHAT was overridden, which a boolean cannot"


def test_an_ordinary_advance_carries_no_forced_key(repo):
    """Written only when true, so every history entry already committed stays
    byte-identical - an added key on every row would rewrite the shape of
    records this project has shipped."""
    rid = a_run(repo)
    ledger.advance(repo, rid, phase="P1", note="ordinary")
    assert "forced" not in ledger.load(repo, rid)["history"][-1]


def test_the_low_level_ledger_records_the_flag_without_judging_it(repo):
    """`state.advance` deliberately has no opinion (item 41) - a ledger that
    refused to record a move it disagreed with would be a ledger that lies.
    Item 112 changes the SPELLING, not who decides."""
    rid = a_run(repo)
    ledger.advance(repo, rid, phase="P1", note="n", forced=True)
    assert ledger.load(repo, rid)["history"][-1]["forced"] is True


def test_metrics_counts_a_forced_advance_from_the_field(repo):
    """FROM THE FIELD, which is what makes this test discriminate. Driving it
    through `stage.advance` would pass before this item too, because the note
    it also writes is what `metrics` used to match on - a test that passes
    either way documents behaviour rather than defending it. This entry
    carries the field and NO forced prose."""
    rid = a_run(repo)
    ledger.advance(repo, rid, phase="P1", note="moved on", forced=True)
    entry = ledger.load(repo, rid)["history"][-1]
    assert ST.FORCED_NOTE not in entry["note"], "the fixture must not carry prose"
    assert M.compute(repo)["gates"]["forced_advances"]["value"] == 1



def test_metrics_still_counts_history_written_before_the_field_existed(repo):
    """A run folder is a durable record and an adopter's history does not
    migrate itself, so the note match stays for entries written earlier."""
    rid = a_run(repo)
    ledger.advance(repo, rid, phase="P1",
              note=f"{ST.FORCED_NOTE}P0: owed something")
    entry = ledger.load(repo, rid)["history"][-1]
    assert "forced" not in entry, "the fixture must be the OLD shape"
    assert M.compute(repo)["gates"]["forced_advances"]["value"] == 1


def test_one_override_recorded_both_ways_is_counted_once(repo):
    """`or`, not two counters: an entry carrying the field AND the prose is one
    override, and adding them would inflate every forced advance to two."""
    rid = a_run(repo)
    ST.advance(repo, rid, "P1", force=True)
    entry = ledger.load(repo, rid)["history"][-1]
    assert entry.get("forced") and ST.FORCED_NOTE in entry["note"], \
        "the fixture must carry BOTH spellings"
    assert M.compute(repo)["gates"]["forced_advances"]["value"] == 1


# ── absent is never zero, at the DOOR as well as in the document ─────────────


def spawn_metrics(target: Path, *extra: str):
    """The real door, spawned - `3` is could-not-look, which an in-process call
    cannot tell from a traceback."""
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "sdlc" / "metrics.py"),
         "--target", str(target), *extra],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180)
    return proc, (proc.stdout or "") + (proc.stderr or "")


def spawn_metrics_env(target: Path, env: dict, *extra: str):
    """`spawn_metrics`, with a caller-built environment - so a test can swap
    which `git` the engine's own subprocess call finds on PATH."""
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "sdlc" / "metrics.py"),
         "--target", str(target), *extra],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180, env=env)
    return proc, (proc.stdout or "") + (proc.stderr or "")


@pytest.fixture(scope="session")
def fake_git(tmp_path_factory):
    """A `git` on PATH that ignores every argument and instead does exactly
    what `FAKE_GIT_MODE` says: print nothing, or print something non-numeric -
    always exit 0. Built once per session and reused, keyed by env var rather
    than argv, since the caller's argv (`-C <target> rev-list --count HEAD`) is
    fixed by the source under test.

    **A `.bat`/`.cmd` on PATH is not enough on Windows, measured while building
    this fixture.** `subprocess.run(["git", ...], shell=False)` - exactly how
    `commits()` calls it - resolves through `CreateProcess`, which auto-appends
    only `.exe` to an extension-less name; a batch file sitting first on PATH
    is never tried and the real `git.exe` further down PATH answers instead.
    So Windows gets a real compiled console app, using the C# compiler that
    ships with the .NET Framework on every Windows host (no internet, nothing
    installed); POSIX gets an ordinary shebang script and needs none of that.

    **Also measured while building this fixture**: passing a modified `PATH`
    only through `env=` on the SAME call that searches for the executable does
    NOT work on Windows - `CreateProcess`'s own search reads the calling
    process's real environment, not the `lpEnvironment` block being prepared
    for the child. It works one level down: spawn a NEW process whose real
    environment already has the shim dir first, and let THAT process make its
    own unqualified `subprocess.run(["git", ...])` call, exactly as
    `spawn_metrics_env` below does for the metrics engine.
    """
    shim = tmp_path_factory.mktemp("fake_git")
    if os.name == "nt":
        exe = shim / "git.exe"
        src_file = shim / "fakegit.cs"
        src_file.write_text(
            "using System;\n"
            "class FakeGit {\n"
            "    static int Main(string[] args) {\n"
            "        string mode = Environment.GetEnvironmentVariable(\"FAKE_GIT_MODE\") ?? \"\";\n"
            "        if (mode == \"nonnumeric\") { Console.Write(\"not-a-number\"); }\n"
            "        return 0;\n"
            "    }\n"
            "}\n", encoding="ascii")
        compile_cmd = (
            f"Add-Type -TypeDefinition (Get-Content -Raw -LiteralPath '{src_file}') "
            f"-OutputType ConsoleApplication -OutputAssembly '{exe}'")
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", compile_cmd],
            capture_output=True, text=True, timeout=120)
        assert exe.is_file(), (
            f"the fake git.exe did not compile: rc={proc.returncode} "
            f"stdout={proc.stdout!r} stderr={proc.stderr!r}")
        return shim
    script = shim / "git"
    script.write_text(
        "#!/bin/sh\n"
        'if [ "$FAKE_GIT_MODE" = "nonnumeric" ]; then printf "%s" "not-a-number"; fi\n'
        "exit 0\n", encoding="ascii")
    script.chmod(0o755)
    return shim


def with_fake_git(fake_git: Path, mode: str) -> dict:
    env = dict(os.environ)
    env["PATH"] = str(fake_git) + os.pathsep + env.get("PATH", "")
    env["FAKE_GIT_MODE"] = mode
    return env


# ── four inputs the engine must survive rather than die on or lie about ─────


def test_a_present_null_review_counts_does_not_kill_the_document(repo):
    """**Present null, not absent.** `counts` existing and JSON `null` walks
    past `.get("counts", {})`'s default because the key IS there, so the
    chained `.get("by_disposition")` raised AttributeError and took every
    other metric in the document down with it - reviews is one section among
    many. SPAWNED, because an in-process call cannot tell this apart from the
    door's own refusal path."""
    run_id = a_run(repo)
    reviews, _ = hitl.paths(repo)
    reviews.parent.mkdir(parents=True, exist_ok=True)
    with reviews.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({
            "kind": "review", "run": run_id, "component": "api",
            "started_at": "2026-08-29T01:00:00+00:00",
            "finished_at": "2026-08-29T01:10:00+00:00",
            "duration_s": 42, "verdict": "approve", "findings": [],
            "counts": None}, sort_keys=True) + "\n")
    proc, said = spawn_metrics(repo, "--json")
    assert proc.returncode == 0, (
        f"a present-null counts field must not kill the document; "
        f"got exit {proc.returncode} saying {said!r}")
    doc = json.loads(proc.stdout)
    assert doc["review"]["sessions"]["value"] == 1, "the row is still a real session"
    assert doc["unreadable"].get("reviews", 0) >= 1, (
        "a malformed counts field is damage and must be counted, not silently "
        "folded into 'no dispositions'")


def test_a_valid_json_non_object_state_does_not_stop_the_sweep(repo):
    """**Parses fine, wrong shape.** `run_states` catches `OSError`/`ValueError`
    from a `state.json` that will not PARSE, but `json.loads("null")` raises
    neither - it returns `None`, handed to `st.get(...)` deeper in and used to
    crash. The docstring's own promise ('a sweep that dies on one malformed
    state.json reports nothing about the other fifty') is exactly what this
    input broke. SPAWNED."""
    good = a_run(repo)
    bad = ledger.runs_dir(repo) / "20260101-000000-wrongshape"
    bad.mkdir(parents=True)
    (bad / ledger.STATE_NAME).write_text("null", encoding="utf-8")
    proc, said = spawn_metrics(repo, "--json")
    assert proc.returncode == 0, (
        f"a valid-JSON, wrong-shape state.json must not kill the document; "
        f"got exit {proc.returncode} saying {said!r}")
    doc = json.loads(proc.stdout)
    assert doc["runs"]["total"]["value"] == 1 and good
    assert doc["unreadable"]["runs"] == 1


def test_empty_git_stdout_at_exit_0_is_not_a_measured_zero(repo, fake_git):
    """**CLAUDE.md's own headline rule.** `int(proc.stdout.strip() or 0)` folds
    empty stdout at exit 0 into the same answer as a real zero-commit
    repository, so a `git` that printed nothing because something was wrong is
    reported as a confident '0 commits'. SPAWNED, with the real `git` on PATH
    replaced by one that exits 0 and prints nothing."""
    env = with_fake_git(fake_git, "empty")
    proc, said = spawn_metrics_env(repo, env, "--json")
    assert proc.returncode == 0, (
        f"empty git stdout must not kill the document; "
        f"got exit {proc.returncode} saying {said!r}")
    doc = json.loads(proc.stdout)
    commits = doc["delivery"]["commits"]
    assert commits["value"] != 0, (
        f"empty stdout at exit 0 must not be read as a real zero commit "
        f"count: {commits!r}")


def test_non_numeric_git_stdout_at_exit_0_does_not_kill_the_document(repo, fake_git):
    """`int()` on non-numeric text raises `ValueError` uncaught - unlike the
    reviews AttributeError this is not even guarded by a try/except, so it
    takes `delivery.commits` and every metric computed after it down too.
    SPAWNED, real `git` replaced by one that exits 0 and prints "not-a-number"."""
    env = with_fake_git(fake_git, "nonnumeric")
    proc, said = spawn_metrics_env(repo, env, "--json")
    assert proc.returncode == 0, (
        f"non-numeric git stdout must not kill the document; "
        f"got exit {proc.returncode} saying {said!r}")
    doc = json.loads(proc.stdout)
    assert doc["delivery"]["commits"]["value"] is None
    assert "commits" in " ".join(doc["unmeasured"])


def test_unmeasured_debt_does_not_print_as_a_number_in_the_headline(repo):
    """`cannot()`'s own docstring says `value` stays `None` so no caller can
    render it as a floor - and the headline printer did exactly that, printing
    the literal text 'None' where a count belongs, while the review line three
    lines above it already carries the guard this one is missing. SPAWNED,
    text mode (the guard is text-mode-only, so `--json` cannot see it): a
    profile with a duplicate top-level key is refused by
    `policy.components`/`ProfileError`, which `debt()` turns into `DebtError`
    and thus `cannot()` for `outstanding`."""
    (repo / ".sdlc" / "profile.yml").write_text(PROFILE + "stack: python\n",
                                                 encoding="utf-8")
    proc, said = spawn_metrics(repo)
    assert proc.returncode == 0, (
        f"an unreadable profile must not kill the document; "
        f"got exit {proc.returncode} saying {said!r}")
    assert "None outstanding" not in proc.stdout, (
        f"a null must never print as though it were a number in the "
        f"headline: {proc.stdout!r}")
    assert "debt" in proc.stdout and "unmeasured" in proc.stdout.lower(), proc.stdout


def test_an_absent_target_is_could_not_look_not_a_document_of_zeros(tmp_path):
    """**SPAWNED. Absent is not zero, at the door (T230).** This module's design
    is "a metric nobody can compute is NEVER zero" - null and a reason - but a
    `--target` that does not exist reached the record readers, which treat a
    missing file as an empty record, and the door emitted a whole DOCUMENT with
    `debt.declared` at 0 and exited 0. An absent target is not one metric that
    could not be computed; it is the whole subject missing, so the door refuses
    above the document: it names the target and exits 3, and prints no JSON to be
    read as measured."""
    absent = tmp_path / "no-such-dir"                    # never created
    proc, said = spawn_metrics(absent, "--json")
    assert proc.returncode == 3, \
        f"could-not-look is exit 3; got {proc.returncode} saying {said!r}"
    assert "does not exist" in said and absent.name in said, \
        f"the door did not name the absent target: {said!r}"
    assert not said.lstrip().startswith("{"), \
        f"an absent target still emitted a metrics document: {said!r}"
