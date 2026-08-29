"""A number a document states must agree with the number the harness measured.

**This closes a gap that cost two gates twice in one run.** On 2026-08-29 a
defect run's acceptance criterion said *"16 tests run and 0 failures"* for a
module that runs **12**, and the harness had recorded 12 at P0 four minutes
before P1 was written. Nothing compared them, so the wrong number passed G1 and
G2 and reached the owner's own approval note.

The sharper half is what happened next. After the number was corrected BY HAND,
**four more wrong counts were still standing in the same run's artifacts** — in
two files the corrector had already opened and edited. A careful human fixing a
known error missed most of its instances, which is the argument for a machine
doing it.

**The design was decided by measurement, not by taste, and the first cut was
bad.** Run against the real completed run it produced 14 findings of which
**nine were false** — "one test method", "one Java test", and the outright
nonsense "two the test expects", where two words of slack let the pattern
swallow an unrelated sentence. Narrowed to a PLURAL noun with at most one
adjective between, plus a subset exclusion, it reports **five findings, all
five true**. A check with that first ratio is one people switch off, and this
file pins the narrowings so it cannot drift back.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import claims as C  # noqa: E402
from tools.sdlc import intake as entry  # noqa: E402
from tools.sdlc import stage as ST  # noqa: E402
from tools.sdlc import state as ledger  # noqa: E402
from tools.sdlc import validate as V  # noqa: E402

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


def open_run(repo: Path, tests: int | None = 12) -> str:
    run_id = ledger.create(repo, "defect", "a title")["run_id"]
    doc = {"schema_version": 1, "run_id": run_id, "kind": "defect"}
    if tests is not None:
        doc["baseline"] = {"ran": True, "tests": tests, "exit_code": 0}
    path = ledger.run_dir(repo, run_id) / entry.INTAKE_JSON
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return run_id


def say(repo: Path, run_id: str, text: str, name: str = "10-requirements.md"):
    (ledger.run_dir(repo, run_id) / name).write_text(text, encoding="utf-8")
    ledger.record_artifact(repo, run_id, name)


# ── the claim is checked against the measurement ────────────────────────────


def test_a_claim_that_matches_the_measurement_is_silent(repo):
    run_id = open_run(repo, tests=12)
    say(repo, run_id, "The suite runs 12 tests and they pass.\n")
    assert C.problems(repo, run_id) == []


def test_a_claim_that_contradicts_the_measurement_is_reported(repo):
    run_id = open_run(repo, tests=12)
    say(repo, run_id, "WHEN the suite runs, THE SYSTEM SHALL report 16 tests.\n")
    owed = C.problems(repo, run_id)
    assert len(owed) == 1
    assert "says 16" in owed[0] and "measured 12" in owed[0]


def test_a_number_written_as_a_word_is_still_a_number(repo):
    """`fifteen` is how three of the five real wrong claims were spelled."""
    run_id = open_run(repo, tests=12)
    say(repo, run_id, "The other fifteen tests are out of scope.\n")
    assert len(C.problems(repo, run_id)) == 1


# ── what must NOT fire, which is most of the design ─────────────────────────


def test_a_correction_naming_both_numbers_is_not_the_defect_it_records(repo):
    """Without this the note written to RECORD the wrong number is itself
    reported as the wrong number, and the check makes correcting things
    harder than leaving them."""
    run_id = open_run(repo, tests=12)
    say(repo, run_id, "This document said 16 tests. The module runs 12.\n")
    assert C.problems(repo, run_id) == []


def test_an_additive_claim_is_not_a_total(repo):
    run_id = open_run(repo, tests=12)
    say(repo, run_id, "The change adds 3 new tests.\nWe added 2 tests here.\n")
    assert C.problems(repo, run_id) == []


def test_a_subset_claim_is_not_a_total(repo):
    """`233 of 233 tests` was the last false positive standing."""
    run_id = open_run(repo, tests=12)
    say(repo, run_id, "The pack elided 233 of 233 tests.\n")
    assert C.problems(repo, run_id) == []


@pytest.mark.parametrize("line", [
    "One test method, and one line of configuration.",
    "the diff is one test and one config line",
    "two files, one Java test, one line of harness configuration",
    "shows the class running 1 test rather than 0",
])
def test_a_singular_noun_is_never_a_suite_total(repo, line):
    """Four of the nine false positives the first cut produced. A suite total
    is never one test, so the plural is what carries the claim."""
    run_id = open_run(repo, tests=12)
    say(repo, run_id, line + "\n")
    assert C.problems(repo, run_id) == [], line


def test_two_words_of_slack_swallowed_an_unrelated_sentence(repo):
    """The worst of the first cut: allowing two words between the number and
    the noun matched `two the test expects` out of a sentence about enum
    ordinals, and reported it as a claim about the suite."""
    run_id = open_run(repo, tests=12)
    say(repo, run_id,
        "The two the test expects - 21 and 25 - are not enabled by default.\n")
    assert C.claims("The two the test expects - 21 and 25") == []
    assert C.problems(repo, run_id) == []


# ── absent is not zero, and not clean either ────────────────────────────────


def test_no_baseline_is_not_a_suite_of_zero_tests(repo):
    """A run whose profile declares no test command measured nothing. Reading
    that as zero would make every count in every document a contradiction."""
    run_id = open_run(repo, tests=None)
    assert C.measured(repo, run_id) is None


def test_a_claim_with_no_baseline_to_check_it_against_is_said_once(repo):
    """Reported, because a number nobody can check is not a number anybody
    should rely on - and ONCE, because the repair is the same for all of them
    and a wall of identical sentences is a wall nobody reads."""
    run_id = open_run(repo, tests=None)
    say(repo, run_id, "The suite runs 16 tests.\nAnd elsewhere, 20 tests.\n")
    owed = C.problems(repo, run_id)
    assert len(owed) == 1 and "no baseline" in owed[0]


def test_no_claims_and_no_baseline_is_silent(repo):
    """The check must have nothing to say about a run that says nothing."""
    run_id = open_run(repo, tests=None)
    say(repo, run_id, "A change to one configuration line.\n")
    assert C.problems(repo, run_id) == []


def test_an_unreadable_intake_is_reported_not_swallowed(repo):
    run_id = open_run(repo, tests=12)
    (ledger.run_dir(repo, run_id) / entry.INTAKE_JSON).write_text(
        "{not json", encoding="utf-8")
    say(repo, run_id, "The suite runs 16 tests.\n")
    owed = C.problems(repo, run_id)
    assert len(owed) == 1 and "could not be read" in owed[0]


# ── only what the run recorded ──────────────────────────────────────────────


def test_an_unrecorded_document_is_not_read(repo):
    """Item 32's rule from the other side: an artifact nobody recorded has
    bytes nothing pins, so it is not part of the run's evidence yet."""
    run_id = open_run(repo, tests=12)
    (ledger.run_dir(repo, run_id) / "10-requirements.md").write_text(
        "The suite runs 16 tests.\n", encoding="utf-8")
    assert C.problems(repo, run_id) == []


def test_every_recorded_document_is_read_not_only_the_first(repo):
    """The hand correction missed four instances across two files. A checker
    that stopped at one document would repeat exactly that failure."""
    run_id = open_run(repo, tests=12)
    say(repo, run_id, "AC-2: the suite reports 16 tests.\n")
    say(repo, run_id, "The other fifteen tests are out of scope.\n", "20-scope.md")
    owed = C.problems(repo, run_id)
    assert len(owed) == 2
    assert any("10-requirements.md" in p for p in owed)
    assert any("20-scope.md" in p for p in owed)


# ── wired in, not merely available ──────────────────────────────────────────


def test_verify_surfaces_a_contradicted_claim(repo):
    """A CLI nobody runs is a CLI nobody runs. `stage.verify` is what the
    gates stand on, and it must carry this."""
    run_id = open_run(repo, tests=12)
    say(repo, run_id, "AC-2: the suite reports 16 tests.\n")
    problems = ST.verify(repo, run_id, "P1")
    assert any("says 16" in p for p in problems), problems


def test_the_check_runs_at_every_phase_not_one(repo):
    """A wrong count is a contradiction wherever it is noticed, and the
    documents carrying them accumulate as the run goes."""
    run_id = open_run(repo, tests=12)
    say(repo, run_id, "The other fifteen tests are out of scope.\n", "20-scope.md")
    for phase in ("P1", "P2", "P5"):
        assert any("fifteen" in p or "says 15" in p
                   for p in ST.verify(repo, run_id, phase)), phase


def test_the_lazy_import_is_declared_in_the_bundle_map(repo):
    """`stage.py` is embedded in the emitted ledger check, so a sibling it
    names as a string must be declared or `render_check` refuses to emit -
    proven under a bare interpreter at 2ef18f4."""
    assert ST.CLAIM_CHECK.rsplit(".", 1)[1] in V.LAZY_OUTSIDE_THE_BUNDLE


# ── the CLI ─────────────────────────────────────────────────────────────────


def test_the_cli_exits_one_while_a_claim_is_contradicted(repo, capsys):
    run_id = open_run(repo, tests=12)
    say(repo, run_id, "AC-2: the suite reports 16 tests.\n")
    assert C.main(["--target", str(repo), "--run", run_id, "--check"]) == 1
    assert "measured 12" in capsys.readouterr().out


def test_the_cli_exits_zero_when_the_numbers_agree(repo, capsys):
    run_id = open_run(repo, tests=12)
    say(repo, run_id, "AC-2: the suite reports 12 tests.\n")
    assert C.main(["--target", str(repo), "--run", run_id, "--check"]) == 0


def test_the_cli_reports_as_data(repo, capsys):
    run_id = open_run(repo, tests=12)
    say(repo, run_id, "AC-2: the suite reports 16 tests.\n")
    assert C.main(["--target", str(repo), "--run", run_id, "--json"]) == 0
    found = json.loads(capsys.readouterr().out)
    assert found["measured"] == 12
    assert found["disagreements"][0]["claimed"] == 16
    assert found["disagreements"][0]["document"] == "10-requirements.md"


@pytest.mark.parametrize("baseline", [
    {"ran": True, "exit_code": 0},                    # ran, and counted nothing
    {"ran": True, "tests": None, "exit_code": 0},     # the key is there and null
    {"ran": True, "tests": "12", "exit_code": 0},     # a string, not a count
])
def test_a_baseline_that_ran_without_counting_is_still_no_measurement(repo, baseline):
    """**Three absences, not one, and a mutation found the two I had missed.**
    The first cut only tested a run with NO baseline key at all, which returns
    None on an earlier line - so a mutant making the counting path answer `0`
    survived every test in this file.

    A baseline that ran and could not parse a count is the interesting case,
    because `intake.baseline` records `tests` only when it managed to read one.
    Zero there would mean "the suite has no tests", and every count in every
    document would become a contradiction against a number nobody measured.
    """
    run_id = ledger.create(repo, "defect", "a title")["run_id"]
    (ledger.run_dir(repo, run_id) / entry.INTAKE_JSON).write_text(
        json.dumps({"schema_version": 1, "run_id": run_id, "kind": "defect",
                    "baseline": baseline}) + "\n", encoding="utf-8")
    assert C.measured(repo, run_id) is None
    say(repo, run_id, "The suite runs 16 tests.\n")
    owed = C.problems(repo, run_id)
    assert len(owed) == 1 and "no baseline" in owed[0]
