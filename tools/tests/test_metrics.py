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
            "        if (mode == \"whitespace_stderr\") { Console.Error.Write(\"  \\n\"); return 128; }\n"
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
        'if [ "$FAKE_GIT_MODE" = "whitespace_stderr" ]; then printf "  \\n" >&2; exit 128; fi\n'
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


# ── round 2 (review-W445 BLOCK): a fifth of the same family, one leaf deeper ─


def test_a_present_null_disposition_count_does_not_kill_the_document(repo):
    """**The leaf below the shape guards.** `reviews()` now checks `counts`
    and `by_disposition` are dicts, but a disposition's own COUNT can be
    present and wrong-shaped too (`{"accepted": null}`) even when both
    mappings above it are fine - `int(n)` on that raised TypeError uncaught,
    one row among good ones taking the whole document down, with the damage
    record left empty because nothing on this channel had counted it yet.
    SPAWNED."""
    run_id = a_run(repo)
    reviewed(repo, run_id, 100, ["accepted"])       # one good row
    reviews, _ = hitl.paths(repo)
    with reviews.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({
            "kind": "review", "run": run_id, "component": "api",
            "started_at": "2026-08-29T02:00:00+00:00",
            "finished_at": "2026-08-29T02:10:00+00:00",
            "duration_s": 55, "verdict": "approve", "findings": [],
            "counts": {"by_disposition": {"accepted": None}, "by_source": {}}},
            sort_keys=True) + "\n")
    proc, said = spawn_metrics(repo, "--json")
    assert proc.returncode == 0, (
        f"a present-null disposition count must not kill the document; "
        f"got exit {proc.returncode} saying {said!r}")
    doc = json.loads(proc.stdout)
    assert doc["review"]["sessions"]["value"] == 2, "both rows are still real sessions"
    assert doc["unreadable"].get("reviews", 0) >= 1, (
        "the bad leaf value is damage and must be counted on the reviews "
        "channel, not silently dropped and not fatal")
    assert doc["review"]["findings_by_disposition"]["value"].get("accepted") == 1, (
        "the GOOD row's disposition must still be tallied")


def test_whitespace_only_stderr_at_nonzero_exit_does_not_crash(repo, fake_git):
    """**The sixth, smaller.** `(proc.stderr or "git failed").strip()
    .splitlines()[0]` indexes an empty list when stderr is whitespace-only -
    `.strip()` removes it all, `.splitlines()` on "" is `[]`, and `[0]` raises
    IndexError. SPAWNED, real `git` replaced by one that exits 128 and writes
    only whitespace to stderr."""
    env = with_fake_git(fake_git, "whitespace_stderr")
    proc, said = spawn_metrics_env(repo, env, "--json")
    assert proc.returncode == 0, (
        f"whitespace-only stderr at a non-zero exit must not kill the "
        f"document; got exit {proc.returncode} saying {said!r}")
    doc = json.loads(proc.stdout)
    assert doc["delivery"]["commits"]["value"] is None
    assert doc["delivery"]["commits"]["why"], "a could-not-look still needs a reason"


def test_cannot_refuses_an_empty_reason():
    """**The reviewer's mutant.** Swapping a real `cannot()` call's reason for
    `""` passed the whole suite at 35 passed, and `unmeasured()`'s roll-up then
    rendered a bare label with nothing after the colon - `cannot()`'s own
    docstring says the reason travels with the null, so this makes that
    impossible to construct rather than merely documented. Not spawned: this
    is `cannot()`'s own contract, independent of any one call site or the CLI
    around it."""
    with pytest.raises(ValueError):
        M.cannot("")
    with pytest.raises(ValueError):
        M.cannot("   ")


# ── round 3 (review-W445 BLOCK 2): the value, not just the type ─────────────


def disposition_row(repo: Path, run_id: str, count) -> None:
    reviews, _ = hitl.paths(repo)
    reviews.parent.mkdir(parents=True, exist_ok=True)
    with reviews.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({
            "kind": "review", "run": run_id, "component": "api",
            "started_at": "2026-08-29T03:00:00+00:00",
            "finished_at": "2026-08-29T03:10:00+00:00",
            "duration_s": 12, "verdict": "approve", "findings": [],
            "counts": {"by_disposition": {"accepted": count}, "by_source": {}}}) + "\n")


def test_nan_disposition_count_does_not_kill_the_document(repo):
    """**The seventh.** NaN is valid JSON in this parser and IS a float, so it
    passed the round-2 `isinstance(n, (int, float))` guard - and
    `int(float("nan"))` raises `ValueError` uncaught. SPAWNED."""
    run_id = a_run(repo)
    reviewed(repo, run_id, 100, ["accepted"])          # one good row
    disposition_row(repo, run_id, float("nan"))
    proc, said = spawn_metrics(repo, "--json")
    assert proc.returncode == 0, (
        f"a NaN disposition count must not kill the document; "
        f"got exit {proc.returncode} saying {said!r}")
    doc = json.loads(proc.stdout)
    assert doc["unreadable"].get("reviews", 0) >= 1
    assert doc["review"]["findings_by_disposition"]["value"].get("accepted") == 1


def test_infinite_disposition_count_does_not_kill_the_document(repo):
    """Infinity is ALSO valid JSON and a float, and `int(float("inf"))` raises
    `OverflowError` - which is an `ArithmeticError`, not a `ValueError`, so an
    instinctive `except ValueError` misses it too. SPAWNED."""
    run_id = a_run(repo)
    reviewed(repo, run_id, 100, ["accepted"])
    disposition_row(repo, run_id, float("inf"))
    proc, said = spawn_metrics(repo, "--json")
    assert proc.returncode == 0, (
        f"an infinite disposition count must not kill the document; "
        f"got exit {proc.returncode} saying {said!r}")
    doc = json.loads(proc.stdout)
    assert doc["unreadable"].get("reviews", 0) >= 1


def test_a_boolean_disposition_count_is_damage_not_a_tally_of_one(repo):
    """**Decision, documented in the code beside the guard.** A bool is an int
    subtype in this language and would silently tally as 0/1 - but a
    disposition COUNT is never a flag, so a bool here is treated as damage,
    the same as any other wrong shape. SPAWNED (not a crash either way - this
    proves the DECISION, not a crash)."""
    run_id = a_run(repo)
    reviewed(repo, run_id, 100, ["accepted"])
    disposition_row(repo, run_id, True)
    proc, said = spawn_metrics(repo, "--json")
    assert proc.returncode == 0, said
    doc = json.loads(proc.stdout)
    assert doc["unreadable"].get("reviews", 0) >= 1, (
        "a boolean count must be treated as damage, not silently tallied")
    assert doc["review"]["findings_by_disposition"]["value"].get("accepted") == 1, (
        "the bool must not be added to the GOOD row's real tally"
        f": {doc['review']['findings_by_disposition']['value']!r}")


def test_a_fractional_disposition_count_is_damage_not_a_silent_truncation(repo):
    """**The other decision.** `int(2.7)` truncates to 2 without complaint -
    silently accepting data that was never a whole number, which is the same
    present-wrong-shape class one step quieter than a crash. SPAWNED."""
    run_id = a_run(repo)
    reviewed(repo, run_id, 100, ["accepted"])
    disposition_row(repo, run_id, 2.7)
    proc, said = spawn_metrics(repo, "--json")
    assert proc.returncode == 0, said
    doc = json.loads(proc.stdout)
    assert doc["unreadable"].get("reviews", 0) >= 1, (
        "a fractional count must be treated as damage, not truncated and kept")
    assert doc["review"]["findings_by_disposition"]["value"].get("accepted") == 1


def test_a_huge_integer_duration_does_not_kill_the_document(repo):
    """**The fourteenth - the most reachable defect on this row, and it needs
    no stub.** `math.isfinite()` converts its argument to a C double
    internally and raises `OverflowError` on a Python integer too large to
    fit - JSON's arbitrary-precision integers can be exactly that, so an
    ORDINARY ROW with a large integer duration kills the whole document.
    No monkeypatch, no foreign module, no exotic type - a person can write
    this file by hand. This is the line round 3 added as the EIGHTH's fix.
    PLAIN DATA ROW."""
    run_id = a_run(repo)
    reviews, _ = hitl.paths(repo)
    reviews.parent.mkdir(parents=True, exist_ok=True)
    with reviews.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({
            "kind": "review", "run": run_id, "component": "api",
            "started_at": "2026-08-29T05:00:00+00:00",
            "finished_at": "2026-08-29T05:10:00+00:00",
            "duration_s": 10 ** 400, "verdict": "approve", "findings": [],
            "counts": {"by_disposition": {}, "by_source": {}}}) + "\n")
    proc, said = spawn_metrics(repo, "--json")
    assert proc.returncode == 0, (
        f"a huge integer duration must not kill the document; "
        f"got exit {proc.returncode} saying {said!r}")


def test_a_huge_integer_fresh_age_does_not_kill_the_document(repo):
    """The same overflow, at the sibling guard (`corpus()`'s tenth) - a
    Python-integer `age_days` too large to convert to a C double raises the
    identical `OverflowError`. SPAWNED, `replay.freshness` monkeypatched
    (this value is FOREIGN, computed by `tools.corpus_replay`, unlike the
    duration above which an adopter writes directly)."""
    setup = ('from tools import corpus_replay as replay\n'
             'def _fresh_huge(target):\n'
             '    return {"state": replay.FRESH, "age_days": 10 ** 400, '
             '"at": "2026-09-19T00:00:00+00:00", "why": ""}\n'
             'replay.freshness = _fresh_huge\n')
    proc, said = spawn_with_monkeypatch(repo, setup, "--json")
    assert proc.returncode == 0, (
        f"a huge integer age must not kill the document; "
        f"got exit {proc.returncode} saying {said!r}")


# ── round 9 (review-W445 BLOCK 5): moved, not removed; and a trace-less kill ─


def test_two_huge_durations_do_not_overflow_the_median(repo):
    """**The fifteenth - round 8 moved the overflow rather than removing
    it.** `_finite_number` accepts a huge int as finite (correctly - there
    is no integer NaN), but the CALLER needs more than finite:
    `statistics.median` averages the two middle values for an even-length
    list, and that division overflows converting an astronomically large
    int to a float. PLAIN ROWS, no stub: two ordinary reviews with a huge
    duration_s."""
    run_id = a_run(repo)
    reviews, _ = hitl.paths(repo)
    reviews.parent.mkdir(parents=True, exist_ok=True)
    with reviews.open("a", encoding="utf-8", newline="\n") as fh:
        for i in range(2):
            fh.write(json.dumps({
                "kind": "review", "run": run_id, "component": "api",
                "started_at": f"2026-08-29T0{i}:00:00+00:00",
                "finished_at": f"2026-08-29T0{i}:10:00+00:00",
                "duration_s": 10 ** 400, "verdict": "approve", "findings": [],
                "counts": {"by_disposition": {}, "by_source": {}}}) + "\n")
    proc, said = spawn_metrics(repo, "--json")
    assert proc.returncode == 0, (
        f"two huge durations must not overflow the median; "
        f"got exit {proc.returncode} saying {said!r}")


def test_a_single_huge_duration_does_not_reach_the_published_record(repo):
    """The odd-length half of the same finding: with only ONE huge value,
    `statistics.median` needs no division and returns it unchanged - a
    four-hundred-digit number would land directly in the published, diffed
    document as the median, bounded reason or not. PLAIN ROW."""
    run_id = a_run(repo)
    reviews, _ = hitl.paths(repo)
    reviews.parent.mkdir(parents=True, exist_ok=True)
    with reviews.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({
            "kind": "review", "run": run_id, "component": "api",
            "started_at": "2026-08-29T06:00:00+00:00",
            "finished_at": "2026-08-29T06:10:00+00:00",
            "duration_s": 10 ** 400, "verdict": "approve", "findings": [],
            "counts": {"by_disposition": {}, "by_source": {}}}) + "\n")
    proc, said = spawn_metrics(repo, "--json")
    assert proc.returncode == 0, said
    doc = json.loads(proc.stdout)
    med = doc["review"]["median_duration_s"]
    assert med["value"] is None, (
        f"a duration the median cannot safely average must not be "
        f"published as though it were a real one: {med!r}")
    assert "10000000000" not in proc.stdout, (
        "a huge value must never reach the published document at all")


def test_an_unusable_duration_leaves_a_trace_in_the_damage_record(repo):
    """**The ninth shape - a correct rejection that leaves no trace.**
    Before this fix, filtering an unusable duration out of the median was
    SILENT: the damage record stayed empty while the sibling disposition-
    count guard counts damage for the identical class of problem. A
    filtered input must not be indistinguishable from an absent one. PLAIN
    ROWS: one review with a real duration, one with an unusable one."""
    run_id = a_run(repo)
    reviewed(repo, run_id, 100, ["accepted"])
    reviews, _ = hitl.paths(repo)
    with reviews.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({
            "kind": "review", "run": run_id, "component": "api",
            "started_at": "2026-08-29T07:00:00+00:00",
            "finished_at": "2026-08-29T07:10:00+00:00",
            "duration_s": 10 ** 400, "verdict": "approve", "findings": [],
            "counts": {"by_disposition": {}, "by_source": {}}}) + "\n")
    proc, said = spawn_metrics(repo, "--json")
    assert proc.returncode == 0, said
    doc = json.loads(proc.stdout)
    assert doc["unreadable"].get("reviews", 0) >= 1, (
        "an unusable duration must be counted as damage, leaving a trace, "
        "not silently dropped")
    assert doc["review"]["median_duration_s"]["value"] == 100, (
        "the GOOD row's duration must still produce a real median")


def test_a_huge_finite_fresh_age_is_rejected_not_published(repo):
    """**"Do the same for the corpus age."** There is no arithmetic on
    `age_days` within this module, but a value this large is the identical
    not-safe-to-compute-with shape, so it gets the identical stricter check
    - rejected rather than accepted, with a properly bounded, traced reason
    via the existing `cannot()` (corpus() has no damage dict; its trace IS
    the reason string, already bounded to 160 chars in round 8)."""
    setup = ('from tools import corpus_replay as replay\n'
             'def _fresh_huge2(target):\n'
             '    return {"state": replay.FRESH, "age_days": 10 ** 400, '
             '"at": "2026-09-19T00:00:00+00:00", "why": ""}\n'
             'replay.freshness = _fresh_huge2\n')
    proc, said = spawn_with_monkeypatch(repo, setup, "--json")
    assert proc.returncode == 0, said
    doc = json.loads(proc.stdout)
    assert doc["corpus"]["replay_age_days"]["value"] is None, (
        "a huge, not-safe-to-compute-with age must not be published as a "
        "real value")
    assert doc["corpus"]["replay_age_days"]["why"]


def test_commits_failure_reason_names_the_command_target_and_exit_code(repo, fake_git):
    """**The two-word fallback.** "git failed" named neither the command, the
    target, nor the exit code, while every sibling reason in this module is a
    sentence a person can act on. SPAWNED, real `git` replaced by one that
    exits 128 and writes only whitespace to stderr - the exact input that used
    to fall back to those two words."""
    env = with_fake_git(fake_git, "whitespace_stderr")
    proc, said = spawn_metrics_env(repo, env, "--json")
    assert proc.returncode == 0, said
    doc = json.loads(proc.stdout)
    why = doc["delivery"]["commits"]["why"]
    assert why != "git failed", "the fallback must no longer be the bare two words"
    assert "rev-list" in why and "128" in why and str(repo) in why, (
        f"the fallback must name the command, the exit code and the target: {why!r}")


def test_debt_error_with_no_message_still_gets_an_actionable_reason(repo, monkeypatch):
    """**The ruling.** `cannot()` now raises on an empty reason (round 2), and
    two callers in this module (`debt()`, `corpus()`) forward text authored by
    ANOTHER module (`tools.sdlc.debt`'s exception message, `corpus_replay`'s
    `why` field) that this module does not control - `debt.py:263` and `:349`
    raise `DebtError` with a bare value and no guaranteed prefix. Nothing
    produces an empty one today (LATENT, per review), but the constructor that
    exists so the document survives a gap must not be the thing a blank
    upstream message turns into a crash. Ruling: `cannot()` stays strict (an
    empty reason IS a bug and must be loud), and the two callers that consume
    foreign text now guarantee a non-empty, actionable fallback of their own -
    not spawned, this exercises the fallback directly against a crafted empty
    exception rather than needing a real corrupt profile to produce one."""
    from tools.sdlc import debt as owed

    def fake_candidates(target):
        raise owed.DebtError("")
    monkeypatch.setattr(owed, "candidates", fake_candidates)
    doc = M.debt(repo)
    assert doc["declared"]["value"] is None
    assert doc["declared"]["why"], "an empty upstream message must not reach cannot() bare"
    assert doc["outstanding"]["why"]


def test_corpus_why_with_no_message_still_gets_an_actionable_reason(repo, monkeypatch):
    """The same ruling, for `corpus()`'s consumption of `replay.freshness()`'s
    `why` field (`corpus_replay.py:270` returns `str(exc)` with no guaranteed
    prefix on one path)."""
    from tools import corpus_replay as replay

    def fake_freshness(target):
        return {"state": "stale-shaped-not-fresh", "age_days": None, "at": "",
                "why": ""}
    monkeypatch.setattr(replay, "freshness", fake_freshness)
    doc = M.corpus(repo)
    assert doc["replay_age_days"]["value"] is None
    assert doc["replay_age_days"]["why"], (
        "an empty upstream why must not reach cannot() bare")


# ── round 4 (review-W445 BLOCK 3): two readers of one fact drift apart ──────


def spawn_with_monkeypatch(repo: Path, setup: str, *extra: str):
    """Spawn the REAL metrics.py CLI in a fresh process that runs `setup`
    first - the only way to engineer an upstream condition no real writer in
    this repository produces today (a whitespace-only exception message)
    while still measuring the fix through the spawned door, per this row's
    own contract, rather than in-process."""
    script = ("import sys\n"
             f"sys.path.insert(0, {str(ROOT)!r})\n"
             f"{setup}\n"
             "from tools.sdlc import metrics as M\n"
             f"sys.exit(M.main([{', '.join(repr(a) for a in ('--target', str(repo), *extra))}]))\n")
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=180)
    return proc, (proc.stdout or "") + (proc.stderr or "")


def test_whitespace_only_debt_error_does_not_crash_the_door(repo):
    """**The block.** `str(exc) or fallback` treats a string of SPACES as
    truthy - `or` only fires on empty - so whitespace-only text passes this
    predicate straight into `cannot()`, whose OWN predicate is `not why or
    not why.strip()`: two readers of one fact, disagreeing on what "empty"
    means, exactly where round 3 wrote the fix meant to close this. SPAWNED,
    with `debt.candidates` monkeypatched INSIDE the spawned process (nothing
    in this repository drives a real `DebtError` to a whitespace-only
    message today) to raise `DebtError("   ")`."""
    setup = ('from tools.sdlc import debt as owed\n'
             'def _boom(target):\n'
             '    raise owed.DebtError("   ")\n'
             'owed.candidates = _boom\n'
             'owed.outstanding = _boom\n')
    proc, said = spawn_with_monkeypatch(repo, setup, "--json")
    assert proc.returncode == 0, (
        f"whitespace-only upstream text must not crash the door; "
        f"got exit {proc.returncode} saying {said!r}")
    doc = json.loads(proc.stdout)
    assert doc["debt"]["declared"]["why"].strip(), (
        "the fallback itself must not be whitespace-only either")


#: The exact sentence `corpus()`'s fallback produces for state
#: "stale-shaped-not-fresh", used to assert CORRECTNESS (the real fallback
#: text) rather than mere survival (some non-empty string) - a laundered
#: `str(None)` ("None") or `str(5)` ("5") is ALSO non-empty and would pass a
#: weaker check, which is exactly the gap round 6 closed.
CORPUS_STALE_FALLBACK = ("the replay history is not fresh (state="
                         "'stale-shaped-not-fresh') and carried no reason")


def test_whitespace_only_corpus_why_does_not_crash_the_door(repo):
    """The same drift, for `corpus()`'s `found["why"]`. SPAWNED, with
    `replay.freshness` monkeypatched INSIDE the spawned process to return a
    non-fresh state whose `why` is whitespace-only."""
    setup = ('from tools import corpus_replay as replay\n'
             'def _stale(target):\n'
             '    return {"state": "stale-shaped-not-fresh", "age_days": None, '
             '"at": "", "why": "   "}\n'
             'replay.freshness = _stale\n')
    proc, said = spawn_with_monkeypatch(repo, setup, "--json")
    assert proc.returncode == 0, (
        f"whitespace-only upstream text must not crash the door; "
        f"got exit {proc.returncode} saying {said!r}")
    doc = json.loads(proc.stdout)
    assert doc["corpus"]["replay_age_days"]["why"] == CORPUS_STALE_FALLBACK


def test_a_non_finite_fresh_age_does_not_render_as_a_bare_literal(repo):
    """**The tenth.** The FRESH branch renders `found["age_days"]` directly -
    the one number in this module that never passes through `value()`/
    `cannot()`. Unlikely from the real writer (traced by review), but it is
    the same shape as everything else fixed in this row, two lines away.
    SPAWNED, `replay.freshness` monkeypatched to report FRESH with a NaN age."""
    setup = ('from tools import corpus_replay as replay\n'
             'def _fresh_nan(target):\n'
             '    return {"state": replay.FRESH, "age_days": float("nan"), '
             '"at": "2026-09-19T00:00:00+00:00", "why": ""}\n'
             'replay.freshness = _fresh_nan\n')
    proc, said = spawn_with_monkeypatch(repo, setup, "--json")
    assert proc.returncode == 0, said
    assert "NaN" not in proc.stdout, (
        f"a non-finite age must not reach the rendered document as a bare "
        f"literal: {proc.stdout!r}")
    doc = json.loads(proc.stdout)
    assert doc["corpus"]["replay_age_days"]["value"] is None
    assert doc["corpus"]["replay_age_days"]["why"]


def test_an_object_fresh_age_does_not_write_an_address_into_the_document(repo):
    """**The thirteenth - the sixth shape, one function up, in code written
    two rounds ago.** `{age!r}` formats the rejected value with the
    DEBUGGING representation - for an arbitrary object that EMBEDS ITS
    MEMORY ADDRESS, written into the published, diffed record. The identical
    hazard round 6 fixed at the sibling `why` site; this guard, written one
    round earlier, still had it. SPAWNED, `replay.freshness` monkeypatched
    to report FRESH with an object `age_days`."""
    setup = ('from tools import corpus_replay as replay\n'
             'def _fresh_object(target):\n'
             '    return {"state": replay.FRESH, "age_days": object(), '
             '"at": "2026-09-19T00:00:00+00:00", "why": ""}\n'
             'replay.freshness = _fresh_object\n')
    proc, said = spawn_with_monkeypatch(repo, setup, "--json")
    assert proc.returncode == 0, said
    doc = json.loads(proc.stdout)
    why = doc["corpus"]["replay_age_days"]["why"]
    assert "0x" not in why, (
        f"a memory address must never reach the published document: {why!r}")
    assert "object" in why, f"the TYPE should still be named: {why!r}"


def test_a_scalar_fresh_age_still_names_its_value(repo):
    """The other half: a simple scalar (a string, here) is deterministic and
    still worth showing - only an arbitrary object's repr is the hazard, not
    every rejected value. SPAWNED."""
    setup = ('from tools import corpus_replay as replay\n'
             'def _fresh_string(target):\n'
             '    return {"state": replay.FRESH, "age_days": "not-a-number", '
             '"at": "2026-09-19T00:00:00+00:00", "why": ""}\n'
             'replay.freshness = _fresh_string\n')
    proc, said = spawn_with_monkeypatch(repo, setup, "--json")
    assert proc.returncode == 0, said
    doc = json.loads(proc.stdout)
    why = doc["corpus"]["replay_age_days"]["why"]
    assert "not-a-number" in why, f"a simple scalar's value should still be named: {why!r}"


def test_a_str_subclass_does_not_write_its_own_representation(repo):
    """**The eighth shape: a type TEST taken as a value GUARANTEE.**
    `isinstance(x, str)` is True for ANY subclass of `str`, including one
    that overrides `__repr__` to embed something non-deterministic -
    `isinstance` proves the value BEHAVES like a str (has its methods); it
    proves nothing about what `repr()` on it will print. An EXACT type check
    is required at this one guard specifically, because the guard's whole
    job is choosing what text is safe to publish. SPAWNED, `replay.freshness`
    monkeypatched to report FRESH with a one-line str subclass whose repr
    embeds an address."""
    setup = ('from tools import corpus_replay as replay\n'
             'class SneakyStr(str):\n'
             '    def __repr__(self):\n'
             '        return "<sneaky at 0x%x>" % id(self)\n'
             'def _fresh_sneaky(target):\n'
             '    return {"state": replay.FRESH, "age_days": SneakyStr("x"), '
             '"at": "2026-09-19T00:00:00+00:00", "why": ""}\n'
             'replay.freshness = _fresh_sneaky\n')
    proc, said = spawn_with_monkeypatch(repo, setup, "--json")
    assert proc.returncode == 0, said
    doc = json.loads(proc.stdout)
    why = doc["corpus"]["replay_age_days"]["why"]
    assert "0x" not in why, (
        f"a subclass's own repr must never reach the published document: "
        f"{why!r}")
    assert "SneakyStr" in why, f"the actual type should still be named: {why!r}"


def test_a_huge_age_string_is_truncated_like_its_sibling(repo):
    """A sibling function in this module (`commits()`) truncates every
    reason it builds from external text at 160 characters, in three places.
    This guard did not match it - a hundred-thousand-character value became
    a hundred-thousand-character reason in the published record. SPAWNED."""
    setup = ('from tools import corpus_replay as replay\n'
             'def _fresh_huge_str(target):\n'
             '    return {"state": replay.FRESH, "age_days": "x" * 100000, '
             '"at": "2026-09-19T00:00:00+00:00", "why": ""}\n'
             'replay.freshness = _fresh_huge_str\n')
    proc, said = spawn_with_monkeypatch(repo, setup, "--json")
    assert proc.returncode == 0, said
    doc = json.loads(proc.stdout)
    why = doc["corpus"]["replay_age_days"]["why"]
    assert len(why) <= 300, (
        f"the reason must be truncated like commits()'s siblings (160 chars "
        f"of the rejected value plus the surrounding sentence), not "
        f"{len(why)} characters")


# ── round 5 (review-W445 BLOCK 4): the row's opening shape, one method later ─


def test_a_present_null_corpus_why_does_not_crash_the_door(repo):
    """**The eleventh - the row's own opening defect, verbatim.** Round 4's
    `found["why"].strip() or fallback` REGRESSED round 3's `found["why"] or
    fallback`: the old form tolerated `why` being `None` (`None or fallback`
    picks the fallback, no crash); the trimmed form calls `.strip()` on it
    FIRST, and `None.strip()` raises `AttributeError`, uncaught - a present
    value walking past a guard into a chained access that assumes a type
    nobody checked, which is finding (1) of this row with a different method
    name. SPAWNED, `replay.freshness` monkeypatched to return a non-fresh
    state whose `why` is `None`."""
    setup = ('from tools import corpus_replay as replay\n'
             'def _null_why(target):\n'
             '    return {"state": "stale-shaped-not-fresh", "age_days": None, '
             '"at": "", "why": None}\n'
             'replay.freshness = _null_why\n')
    proc, said = spawn_with_monkeypatch(repo, setup, "--json")
    assert proc.returncode == 0, (
        f"a present-null why must not crash the door; "
        f"got exit {proc.returncode} saying {said!r}")
    doc = json.loads(proc.stdout)
    why = doc["corpus"]["replay_age_days"]["why"]
    # NOT `assert why` (truthy) - `str(None)` is "None", the four-letter word,
    # which IS truthy and would pass that check while being exactly the
    # laundered-null defect round 6 found: this must be the REAL fallback
    # sentence, not a string that merely looks like one.
    assert why == CORPUS_STALE_FALLBACK, (
        f"a present-null why must produce the real fallback sentence, not a "
        f"laundered str(None): {why!r}")


def test_a_numeric_corpus_why_does_not_crash_the_door(repo):
    """Same regression, the numeric case named alongside it: `(5).strip()`
    raises `AttributeError` identically to `None.strip()` - the guard must
    coerce to text before it trims, not assume `why` is already a string.
    SPAWNED."""
    setup = ('from tools import corpus_replay as replay\n'
             'def _numeric_why(target):\n'
             '    return {"state": "stale-shaped-not-fresh", "age_days": None, '
             '"at": "", "why": 5}\n'
             'replay.freshness = _numeric_why\n')
    proc, said = spawn_with_monkeypatch(repo, setup, "--json")
    assert proc.returncode == 0, (
        f"a numeric why must not crash the door; "
        f"got exit {proc.returncode} saying {said!r}")
    doc = json.loads(proc.stdout)
    why = doc["corpus"]["replay_age_days"]["why"]
    assert why == CORPUS_STALE_FALLBACK, (
        f"a numeric why must produce the real fallback sentence, not a "
        f"laundered str(5): {why!r}")


def test_an_object_corpus_why_does_not_crash_the_door(repo):
    """**The twelfth - the sixth shape.** A bare object's `str()` form
    CONTAINS ITS MEMORY ADDRESS - laundering it into `why` would make this
    project's determinism-diffed `metrics.json` non-reproducible between
    runs, which is worse than the null/numeric cases: nothing fails (no
    exception, the result is non-empty), so shapes 1-5 are structurally
    blind to it. SPAWNED."""
    setup = ('from tools import corpus_replay as replay\n'
             'def _object_why(target):\n'
             '    return {"state": "stale-shaped-not-fresh", "age_days": None, '
             '"at": "", "why": object()}\n'
             'replay.freshness = _object_why\n')
    proc, said = spawn_with_monkeypatch(repo, setup, "--json")
    assert proc.returncode == 0, said
    doc = json.loads(proc.stdout)
    why = doc["corpus"]["replay_age_days"]["why"]
    assert why == CORPUS_STALE_FALLBACK, (
        f"an object why must produce the real fallback sentence, not a "
        f"laundered str(object()): {why!r}")
    assert "0x" not in why, (
        f"a memory address must never reach the published document: {why!r}")


def test_nan_duration_does_not_corrupt_the_rendered_document(repo):
    """**Found in my own round-3 re-sweep, same shape as the seventh, one
    sibling over.** `seconds` type-checks `duration_s` with the identical
    `isinstance(x, (int, float))` the leaf guard used before round 3 - so a
    NaN duration passes it too. It does not crash: `statistics.median` and
    `round()` both accept NaN silently, and `json.dumps` serialises it as the
    literal token `NaN` by default - a non-finite value reaching the
    rendered JSON document as though it were a real median, past every
    `is None` check this whole document relies on to mark a gap. SPAWNED."""
    run_id = a_run(repo)
    reviewed(repo, run_id, 100, ["accepted"])          # one good duration
    reviews, _ = hitl.paths(repo)
    with reviews.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({
            "kind": "review", "run": run_id, "component": "api",
            "started_at": "2026-08-29T04:00:00+00:00",
            "finished_at": "2026-08-29T04:10:00+00:00",
            "duration_s": float("nan"), "verdict": "approve", "findings": [],
            "counts": {"by_disposition": {}, "by_source": {}}}) + "\n")
    proc, said = spawn_metrics(repo, "--json")
    assert proc.returncode == 0, said
    assert "NaN" not in proc.stdout, (
        f"a non-finite duration must never reach the rendered document as "
        f"though it were a real median: {proc.stdout!r}")
    med = doc = json.loads(proc.stdout)["review"]["median_duration_s"]["value"]
    assert med == 100, f"the NaN row must be excluded from the median, not poison it: {med!r}"


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
