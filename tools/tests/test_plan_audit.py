"""A plan set that disagrees with its own master index says so.

The real plan set lives outside this repository, so CI cannot read it - which
is exactly why the LOGIC is held here over synthetic fixtures and the real
documents are the operator's to point at, the arrangement `field_run.py` uses
for the same reason.

The historical proof is on the record rather than in a fixture: re-injecting
the five defects the 2026-09-06 hand audit found - four items present in the
master and in no phase plan, and one whose status the master had moved on from
a month earlier - the tool reports all five plus the four count disagreements
they cause, at exit 1.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools import plan_audit as PA  # noqa: E402

MASTER = """# A plan set

| Plan | Covers | Items | Done |
|---|---|---|---|
| [one.md](one.md) | first | 2 | **2 - COMPLETE** |
| [two.md](two.md) | second | 1 | 0 |

### [One](one.md) - 2/2 done

| # | Item | Milestone | Status |
|---|---|---|---|
| 1 | a thing | M7 | **Done** (`abc1234`) |
| 2 | another | M7 | **Done** (`def5678`) |

### [Two](two.md) - 0/1 done

| # | Item | Milestone | Status |
|---|---|---|---|
| 9 | not started | M7 | **Filed 2026-01-01, NOT started** |

The plan stands at **3 items, 2 done**, and that is the headline.
"""

ONE = """# One

| # | Item | Milestone | Status |
|---|---|---|---|
| 1 | a thing | M7 | **Done** (`abc1234`) |
| 2 | another | M7 | **Done** (`def5678`) |
"""

TWO = """# Two

| # | Item | Milestone | Status |
|---|---|---|---|
| 9 | not started | M7 | **Filed 2026-01-01, NOT started** |
"""


@pytest.fixture
def plans(tmp_path: Path) -> Path:
    """A plan set that agrees with itself."""
    where = tmp_path / "plans"
    where.mkdir()
    (where / "master.md").write_text(MASTER, encoding="utf-8")
    (where / "one.md").write_text(ONE, encoding="utf-8")
    (where / "two.md").write_text(TWO, encoding="utf-8")
    return where


def audit(plans: Path) -> list[str]:
    said, looked, _no_marker, _sec, _sec_total = PA.audit(plans, "master.md")
    assert looked, "the audit compared nothing, so an empty answer means nothing"
    return said


def test_a_plan_set_that_agrees_with_itself_is_silent(plans):
    """The negative control. A checker that fires on a correct document is one
    somebody switches off, which is `prepass.py`'s own recorded rule."""
    assert audit(plans) == []


def test_an_item_in_the_master_and_in_no_plan_is_reported(plans):
    """**The defect this was built for, four times over on the real set.** An
    item added to the master and never to its phase plan is invisible, because
    the master is what everybody reads."""
    text = (plans / "one.md").read_text(encoding="utf-8")
    (plans / "one.md").write_text(
        text.replace("| 2 | another | M7 | **Done** (`def5678`) |\n", ""),
        encoding="utf-8")
    said = audit(plans)
    assert any("item 2 is in the master and in no row" in s for s in said), said


def test_an_item_in_a_plan_and_not_in_the_master_is_reported(plans):
    """The other direction, which is not the same defect: work nobody's index
    knows about does not reach any count."""
    text = (plans / "one.md").read_text(encoding="utf-8")
    (plans / "one.md").write_text(
        text + "| 7 | a stray | M7 | **Done** (`aaa`) |\n", encoding="utf-8")
    assert any("item 7 is in the plan and not in the master" in s
               for s in audit(plans))


def test_a_status_that_differs_between_the_two_is_reported(plans):
    """**Item 67's defect on the real set**: a phase plan read *Partially
    done, ahead of schedule* for work the master recorded landed a month
    earlier, so the document a person opens to check that milestone said it
    was unfinished."""
    text = (plans / "one.md").read_text(encoding="utf-8")
    (plans / "one.md").write_text(
        text.replace("| 1 | a thing | M7 | **Done** (`abc1234`) |",
                     "| 1 | a thing | M7 | Partially done, ahead of schedule |"),
        encoding="utf-8")
    said = audit(plans)
    assert any("item 1 reads done in the master and open in the plan" in s
               for s in said), said


def test_an_index_count_that_disagrees_with_the_rows_is_reported(plans):
    """A number in one document derived from rows in another, with nothing
    composing them - which is this project's most-found shape, arriving in its
    own bookkeeping."""
    text = (plans / "master.md").read_text(encoding="utf-8")
    (plans / "master.md").write_text(
        text.replace("| [one.md](one.md) | first | 2 |",
                     "| [one.md](one.md) | first | 5 |"), encoding="utf-8")
    said = audit(plans)
    assert any("claims 5 item(s) and the plan carries 2" in s for s in said)


def test_an_index_done_count_that_disagrees_with_the_rows_is_reported(plans):
    """**The mutation pass earned this one.** Removing the done-count check
    left every test green, because the sibling test moves the ITEM count and
    the two are separate branches - so a plan whose rows had all landed could
    keep claiming half of them were open, or the reverse, and nothing said so.
    """
    text = (plans / "master.md").read_text(encoding="utf-8")
    (plans / "master.md").write_text(
        text.replace("| [one.md](one.md) | first | 2 | **2 - COMPLETE** |",
                     "| [one.md](one.md) | first | 2 | **1 - COMPLETE** |"),
        encoding="utf-8")
    said = audit(plans)
    assert any("claims 1 done and the plan's own rows count 2" in s
               for s in said), said


def test_a_headline_that_disagrees_with_its_own_sections_is_reported(plans):
    """The count a reader takes away, checked against the rows it is drawn
    from rather than against a memory of them."""
    text = (plans / "master.md").read_text(encoding="utf-8")
    (plans / "master.md").write_text(
        text.replace("**3 items, 2 done**", "**9 items, 8 done**"),
        encoding="utf-8")
    said = audit(plans)
    assert any("headline says 9 items" in s for s in said), said
    assert any("headline says 8 done" in s for s in said), said


def test_a_numbered_table_that_is_not_items_is_not_counted(plans):
    """**Measured on the real set, and it is why the milestone cell is part of
    the row pattern.** A plan carries other numbered tables - a lifecycle phase
    table for one - and counting those made a plan disagree with its master
    over twelve phantom rows, which is a checker manufacturing its own
    findings."""
    text = (plans / "one.md").read_text(encoding="utf-8")
    (plans / "one.md").write_text(
        text + "\n| # | Phase | Active | The agent does | The human does |\n"
               "|---|---|---|---|---|\n"
               "| 0 | **Intake** | always | parses the spec | nothing |\n"
               "| 1 | **Requirements** | always | writes criteria | signs |\n",
        encoding="utf-8")
    assert audit(plans) == []


def test_the_audit_reports_how_much_it_actually_read(plans):
    """**The mutation pass earned this one too.** `looked` is how an operator
    knows the audit had scope, and blinding it to a constant survived every
    test: the refusal branch only asks whether it is zero, so a run that read
    six rows and one that read one were indistinguishable in the sentence a
    person reads.

    That is item 97's rule about a gate that quietly stops measuring, arriving
    in the report rather than in the verdict. The floor is the fixture's own
    rows, derived rather than typed, so widening the fixture cannot make this
    assertion stale.
    """
    rows = sum(1 for name in ("one.md", "two.md", "master.md")
               for line in (plans / name).read_text(encoding="utf-8").splitlines()
               if PA.ITEM_ROW.match(line))
    said, looked, _no_marker, _sec, _sec_total = PA.audit(plans, "master.md")
    assert said == []
    assert looked >= rows, (
        f"the audit says it read {looked} row(s) and the fixture holds {rows} "
        f"- a count that cannot move is not a measure of anything")


def test_a_master_naming_no_plan_this_directory_holds_refuses(tmp_path):
    """**Could-not-look is not clean**, and this is the vacuity guard rather
    than a nicety: a sweep that found no disagreement and a sweep that read no
    rows hand back the same empty list (item 115)."""
    where = tmp_path / "plans"
    where.mkdir()
    (where / "master.md").write_text(MASTER, encoding="utf-8")
    said, looked, no_marker, sec, sec_total = PA.audit(where, "master.md")
    assert looked == 0
    assert no_marker == 0, (
        "nothing was read, so nothing could have matched or failed to match "
        f"a marker: {no_marker}")
    assert sec == 0, (
        "nothing was read, so no sections could have been compared: "
        f"{sec}")
    assert sec_total == 0, (
        "nothing was read, so no item sections could have been counted: "
        f"{sec_total}")
    assert any("is not in" in s for s in said), said


def test_item_67s_status_is_read_as_open_in_the_spelling_the_set_uses(plans):
    """**The founding example, in the spelling the real document wrote it in.**

    The sibling above re-injects item 67's stale row unbolded. The plan set it
    was found in writes that status BOLD - every marker cell in these tables is
    emphasised by convention - so the tool was field-verified against a
    spelling its own source does not use, and the one row it exists to catch
    fell through to *done*.

    Two of the five markers carried the `**` prefix in their literal and two
    did not, which is why half the set was readable and half was not.
    """
    text = (plans / "one.md").read_text(encoding="utf-8")
    (plans / "one.md").write_text(
        text.replace(
            "| 1 | a thing | M7 | **Done** (`abc1234`) |",
            "| 1 | a thing | M7 | **Partially done, ahead of schedule** |"),
        encoding="utf-8")
    said = audit(plans)
    assert any("item 1 reads done in the master and open in the plan" in s
               for s in said), said


#: The marker set written out BY HAND, so that one assertion in this file does
#: not derive its expectation from the thing it tests. Item 154's frozen roster,
#: for its reason.
FROZEN_MARKERS = {"Filed", "Partly", "Partially", "Owed", "Not started"}


def test_the_marker_set_is_exactly_the_roster_written_down_here():
    """EQUALITY, both directions, against a literal nobody derived.

    The parametrise below takes its cases from `PA.OPEN_MARKERS`, so deleting a
    marker deletes its own test case: measured, dropping one leaves the file
    green at 19 passed instead of 20, and no gate anywhere reads a test count.
    Emptying the tuple IS caught - the parametrise collapses to a skip, which is
    exit 1 under `SDLC_FORBID_SKIPS` - so the extreme was defended and the
    single deletion was not.

    Adding a marker is meant to be cheap: name it here in the same commit.
    Removing one has to be deliberate, and this is the only assertion in the
    repository that can say so.
    """
    declared = set(PA.OPEN_MARKERS)
    assert declared == FROZEN_MARKERS, (
        "the reader's markers and the roster written down here disagree - read "
        f"as open and never written down: {sorted(declared - FROZEN_MARKERS)}; "
        "written down and no longer read as open: "
        f"{sorted(FROZEN_MARKERS - declared)}")
    assert len(PA.OPEN_MARKERS) == len(FROZEN_MARKERS), (
        f"a marker is spelled twice in the tuple: {PA.OPEN_MARKERS}")


@pytest.mark.parametrize("marker", PA.OPEN_MARKERS)
def test_every_open_marker_is_read_in_both_spellings(marker):
    """Derived from the set itself, so a sixth marker added tomorrow inherits
    this guarantee rather than needing somebody to remember it - which is the
    thing that went wrong: the emphasis was baked into three literals and left
    out of two, and nothing compared them. The roster above is the half this
    one cannot supply: derived cases vanish with the marker they came from.

    `** marker**` is here because `is_open`'s docstring says whitespace comes
    off WITH the emphasis, and nothing tested the space between the two - so
    the strip that does it could be deleted with the suite still green.
    """
    plain = marker.strip("*_ ")
    assert plain, "a marker that is only emphasis matches every status"
    assert PA.is_open(plain), f"{plain!r} reads done"
    assert PA.is_open(f"**{plain}**"), f"**{plain}** reads done"
    assert PA.is_open(f"*{plain}*"), f"*{plain}* reads done"
    assert PA.is_open(f"** {plain}**"), f"** {plain}** reads done"
    assert not PA.is_open(f"**Done** - {plain} once, long ago"), (
        "a marker in the middle of a done status must not open the row")


def test_an_absent_master_is_refused_rather_than_reported_as_clean(tmp_path):
    with pytest.raises(PA.AuditError):
        PA.audit(tmp_path, "nothing.md")


def spawn(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ROOT / "tools" / "plan_audit.py"),
                           *args], capture_output=True, text=True, timeout=300)


def test_the_exit_code_says_which_of_the_three_it_is(plans):
    """**Item 96's rule: a CLI's contract is a process, so spawn it.** Three
    answers, because they need three acts - they agree, they do not, and it
    could not look."""
    ok = spawn("--plans", str(plans), "--master", "master.md")
    assert ok.returncode == 0, ok.stdout + ok.stderr

    text = (plans / "one.md").read_text(encoding="utf-8")
    (plans / "one.md").write_text(
        text.replace("| 2 | another | M7 | **Done** (`def5678`) |\n", ""),
        encoding="utf-8")
    bad = spawn("--plans", str(plans), "--master", "master.md")
    assert bad.returncode == 1 and "item 2" in bad.stdout

    gone = spawn("--plans", str(plans / "nowhere"))
    assert gone.returncode == 2


def test_a_candidate_it_cannot_read_is_could_not_look_not_a_disagreement(plans):
    """T541: the search for the master read every `*.md` unguarded, so a directory
    named like a plan was a PermissionError traceback at exit 1 - the code this tool
    keeps for "the plans do not agree". Only files are candidates."""
    (plans / "notes.md").mkdir()
    found = spawn("--plans", str(plans))
    assert found.returncode == 0 and "Traceback" not in found.stdout + found.stderr, \
        found.stdout + found.stderr


def test_a_plan_that_cannot_be_read_is_refused_by_name(plans, monkeypatch, capsys):
    """T541, the reads inside the audit: a plan the master names, present and then
    unreadable (a permission, a lock), is a could-not-look at exit 2, named - not a
    traceback, and not a disagreement about work."""
    real = Path.read_text

    def locked(self, *args, **kwargs):
        if self.name == "one.md":
            raise PermissionError(13, "Permission denied", str(self))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", locked)
    assert PA.main(["--plans", str(plans), "--master", "master.md"]) == 2
    out = capsys.readouterr().out
    assert "one.md" in out and "could not be read" in out, out


def test_an_unexpected_failure_is_could_not_look(plans, monkeypatch, capsys):
    """T541: the only CLI in its group without `cli.door`, so any failure the body
    did not foresee left at exit 1 - "they do not agree"."""
    def broken(*args, **kwargs):
        raise RuntimeError("injected")

    monkeypatch.setattr(PA, "audit", broken)
    assert PA.main(["--plans", str(plans), "--master", "master.md"]) == 2
    assert "did not finish" in capsys.readouterr().out


def test_the_cli_prints_the_findings_it_holds_even_when_it_read_no_rows(
        tmp_path):
    """**The findings were computed and then thrown away, and only a process
    could see it.** A master whose every named plan is missing produces one
    real finding per section and reads no rows - so the refusal branch fired
    first and returned before printing a single one of them.

    The in-process sibling asserts those findings exist and passes either way,
    because it never spawns: producer and consumer each tested and never
    composed, which is this repository's most-found shape arriving in the tool
    built to find it. The exit code stays 2 - it still could not look - but
    what it DID learn is not silently dropped.
    """
    where = tmp_path / "plans"
    where.mkdir()
    (where / "master.md").write_text(MASTER, encoding="utf-8")
    got = spawn("--plans", str(where), "--master", "master.md")
    assert got.returncode == 2, got.stdout + got.stderr
    assert "nothing was compared" in got.stdout, got.stdout
    for name in ("one.md", "two.md"):
        assert f"{name}: the master has a section for it" in got.stdout, (
            f"{name}'s finding was computed and never printed:\n{got.stdout}")


def test_the_master_is_found_by_its_headline_when_it_is_not_named(plans):
    """A path a caller must get right is a path that will be got wrong, so the
    one document carrying a plan-set headline identifies itself - and two of
    them is a refusal rather than a guess."""
    assert spawn("--plans", str(plans)).returncode == 0
    (plans / "decoy.md").write_text(
        "The plan stands at **1 items, 1 done** here too.\n", encoding="utf-8")
    said = spawn("--plans", str(plans))
    assert said.returncode == 2 and "--master" in said.stdout


def test_the_summary_line_names_the_markers_it_accepts(plans):
    """**T276.** `plan_audit.py`'s own comment used to admit, in writing, that
    naming the accepted markers on the summary line "is not built here" - the
    one thing that would let an operator check their own plan set's
    vocabulary against the tool's. Derived from `PA.OPEN_MARKERS` rather than
    retyped, so a sixth marker added tomorrow cannot go stale here the way
    item 154 found the roster stale elsewhere.
    """
    ok = spawn("--plans", str(plans), "--master", "master.md")
    assert ok.returncode == 0, ok.stdout + ok.stderr
    for marker in PA.OPEN_MARKERS:
        assert marker in ok.stdout, (
            f"{marker!r} is an accepted open marker and the summary line "
            f"does not name it:\n{ok.stdout}")


def test_an_unrecognized_spelling_stays_silent_but_the_accepted_list_still_shows(
        plans):
    """**T276, the row's own reproduction.** A live row spelled with a status
    this tool does not know ("Blocked") in BOTH documents is symmetric, so the
    status comparison staying silent is correct - grading whether "Blocked"
    means done is exactly the prose-judgement this tool refuses (item 170's
    REFUTED boundary), and the count comparisons stay silent too because
    nothing about either document's own arithmetic moved.

    What must NOT stay silent is the operator's ability to notice: the
    accepted-marker list is printed on THIS run's own summary line, so
    "Blocked" missing from it is something a reader of the output - not just
    of the source - can see and disagree with. The tool still names no row and
    grades no prose: "Blocked" itself must not appear in its output.
    """
    text = (plans / "one.md").read_text(encoding="utf-8")
    (plans / "one.md").write_text(
        text.replace("**Done** (`abc1234`)", "**Blocked** (`abc1234`)"),
        encoding="utf-8")
    master_text = (plans / "master.md").read_text(encoding="utf-8")
    (plans / "master.md").write_text(
        master_text.replace("**Done** (`abc1234`)", "**Blocked** (`abc1234`)"),
        encoding="utf-8")
    got = spawn("--plans", str(plans), "--master", "master.md")
    assert got.returncode == 0, (
        "an unrecognized spelling must not be graded into a manufactured "
        f"disagreement:\n{got.stdout}{got.stderr}")
    assert "Blocked" not in got.stdout, (
        "the tool must not name a specific row's prose - that is grading, "
        f"which it refuses to do:\n{got.stdout}")
    for marker in PA.OPEN_MARKERS:
        assert marker in got.stdout, (
            "the accepted markers must still be visible on this exact run's "
            f"summary line so an operator can see 'Blocked' is not among "
            f"them:\n{got.stdout}")
    assert "2 cell(s) matched no marker" in got.stdout, (
        "both the master's and the plan's own copy of item 1 read an "
        f"unrecognized spelling and neither is counted:\n{got.stdout}")


def test_a_status_this_tool_cannot_classify_is_counted_on_the_summary_line(
        plans):
    """**T276 round 2, review-W398's fixture (BLOCK).** A live row reading
    "**Blocked on upstream**" is neither an OPEN marker nor a recognized DONE
    one. `is_open` still reads it as done by default - unchanged, and the run
    must stay honest about disagreements: nothing here makes the master and
    the plan disagree, so none may be manufactured. What must change is that
    the cell is COUNTED rather than silently folded into "done" with nothing
    said: absent is not zero, and a spelling this tool cannot classify is
    could-not-look, never a silent zero. Only `one.md`'s own copy is changed,
    so exactly one cell - not two - is unmatched, which is also the proof
    that the count is read from real cells and not a fixed number.
    """
    text = (plans / "one.md").read_text(encoding="utf-8")
    (plans / "one.md").write_text(
        text.replace("**Done** (`abc1234`)",
                     "**Blocked on upstream** (`abc1234`)"),
        encoding="utf-8")
    got = spawn("--plans", str(plans), "--master", "master.md")
    assert got.returncode == 0, (
        "an unrecognized spelling must not be graded into a manufactured "
        f"disagreement:\n{got.stdout}{got.stderr}")
    assert "0 disagreement(s)" in got.stdout, (
        f"nothing about this fixture disagrees - the count must not turn "
        f"into a finding:\n{got.stdout}")
    assert "1 cell(s) matched no marker" in got.stdout, (
        f"the unmatched cell must be counted, not silently folded into "
        f"done:\n{got.stdout}")


def test_the_live_set_stays_byte_identical_when_nothing_is_unmatched(plans):
    """The count clause is owed only when N > 0 - appending ", 0 cell(s)
    matched no marker" to every clean run would be its own kind of noise, and
    the reviewer's own sweep of the real 394-row set found zero unmatched
    there. The unmodified fixture is the local stand-in for that set: nothing
    in it is unrecognized, so the summary line carries no `matched no marker`
    count clause at all - and the coverage reads `0 of 0` because this fixture
    holds no `## Item N` sections (T393 added the denominator).
    """
    got = spawn("--plans", str(plans), "--master", "master.md")
    assert got.returncode == 0, got.stdout + got.stderr
    assert "cell(s) matched no marker" not in got.stdout, (
        f"nothing here is unmatched - the line must stay unchanged:\n"
        f"{got.stdout}")
    assert got.stdout == (
        "master.md: 0 disagreement(s) over 6 row reading(s) and 0 of 0 "
        "section(s), reading open as: Filed, Partly, Partially, Owed, "
        "Not started\n"), got.stdout


def test_the_docstring_no_longer_claims_the_marker_print_is_unbuilt():
    """**T276, clause 3.** The explanatory comment above `OPEN_MARKERS` used to
    end "...that is not built here" - true when written, false the moment the
    feature above landed. Read as TEXT, not `__doc__`: the block is a `#:`
    comment, not the module docstring, so the check has to look at exactly
    what a reader of the source sees rather than at Python's own docstring
    attribute.
    """
    source = Path(PA.__file__).read_text(encoding="utf-8")
    assert "is not built here" not in source, (
        "the comment still disclaims the marker-print feature that now "
        "exists - stated code and actual code disagree")
    assert "summary line" in source, (
        "the comment should still say where the accepted markers are named")


def test_the_docstring_does_not_claim_every_run_prints_the_summary_line():
    """**T276 round 2, finding 5 (review-W398).** All four `return 2` doors
    print their own refusal and nothing else - `main`'s summary line is only
    reached when a comparison actually completes, so a comment claiming it
    prints "on every run" overclaims relative to the four refusal exits.
    """
    source = Path(PA.__file__).read_text(encoding="utf-8")
    assert "on every run" not in source, (
        "the comment claims the summary line prints unconditionally, but "
        "four refusal exits in main() print nothing")


def test_open_and_done_markers_are_read_from_one_shared_place(plans):
    """**T276 round 2, mutation target.** `DONE_MARKERS` is defined once
    beside `OPEN_MARKERS` and both the summary line and the unmatched count
    must read the SAME tuples - a second, separately typed copy of either
    roster is exactly the class this module's own docstring names as the
    defect it exists to catch one level up (two documents state one fact,
    nothing composes them). Proven by mutating `is_open`'s status via a status
    that matches a real `DONE_MARKERS` entry and confirming it is never
    counted - if the count used its own copy, drifting `DONE_MARKERS` here
    would not be visible to it.
    """
    assert PA.unmatched("Something else entirely")
    for marker in PA.DONE_MARKERS:
        assert not PA.unmatched(marker), (
            f"{marker!r} is a recognized done marker and must not count as "
            f"unmatched")
    for marker in PA.OPEN_MARKERS:
        assert not PA.unmatched(marker), (
            f"{marker!r} is a recognized open marker and must not count as "
            f"unmatched")


# ---------- T136: section-level status -----------------------------------

PHASE_WITH_SECTION = """\
# Phase plan

| # | Item | Milestone | Status |
|---|---|---|---|
| 1 | widget | M7 | **Done** (`abc1234`) |
| 2 | gadget | M7 | **Done** (`def5678`) |
| 3 | sprocket | M7 | **Done** (`ghi9012`) |

## Item 1 — widget

**Status**: Filed, not started.

The work has not begun.

## Item 2 — gadget

**Status**: Done (`def5678`).

Delivered.

## Item 3 — sprocket

**Status**: Done (`ghi9012`).

Also delivered.
"""

MASTER_FOR_SECTIONS = """\
# A plan set

| Plan | Covers | Items | Done |
|---|---|---|---|
| [phase.md](phase.md) | first | 3 | **2 - partial** |

### [Phase](phase.md) - 2/3 done

| # | Item | Milestone | Status |
|---|---|---|---|
| 1 | widget | M7 | **Done** (`abc1234`) |
| 2 | gadget | M7 | **Done** (`def5678`) |
| 3 | sprocket | M7 | **Filed 2026-01-01, NOT started** |

The plan stands at **3 items, 2 done**, and that is the headline.
"""


@pytest.fixture
def plans_with_section_disagreement(tmp_path: Path) -> Path:
    """A plan set where item 1's section Status disagrees with its row,
    and item 3's master disagrees with its plan row while the section
    agrees with the plan's own row -- the M4 discriminator."""
    where = tmp_path / "plans"
    where.mkdir()
    (where / "master.md").write_text(MASTER_FOR_SECTIONS, encoding="utf-8")
    (where / "phase.md").write_text(PHASE_WITH_SECTION, encoding="utf-8")
    return where


def test_a_section_status_that_disagrees_with_its_own_row_is_reported(
        plans_with_section_disagreement):
    """**T136.** A phase-plan SECTION whose `**Status**:` line says "Filed"
    while its own table row says "Done" is the same class of disagreement
    this tool exists to catch - two statements of one fact, nothing composing
    them. The checker read rows only, so the section-level status was
    invisible even though it lived in the same document. Measured on the real
    plan set: 11 sections carry a `**Status**:` line across 4 files."""
    said = audit(plans_with_section_disagreement)
    assert any("item 1" in s and "section" in s.lower() for s in said), (
        "item 1's section says Filed and its row says Done, but the checker "
        f"does not see the section-level disagreement:\n{said}")


def test_a_section_status_that_agrees_with_its_row_is_silent(
        plans_with_section_disagreement):
    """**T136 negative control.** Item 2's section and row both say Done, so
    nothing about item 2 should appear in the findings."""
    said = audit(plans_with_section_disagreement)
    assert not any("item 2" in s and "section" in s.lower() for s in said), (
        f"item 2 agrees in both places and must not be reported:\n{said}")


def test_section_compares_against_its_own_row_not_the_master(
        plans_with_section_disagreement):
    """**T136 round 2, M4 kill.** Item 3's master row reads Filed (open)
    while the plan's own row AND the section both read Done. Comparing the
    section against the MASTER would wrongly report a disagreement; comparing
    against the plan's own row - the correct source - stays silent.
    Review-W408's mutant M4 replaced `theirs[number]` with `mine[number]`
    and survived every round-1 test because master row == plan row for all
    items; this fixture breaks that symmetry for item 3."""
    said = audit(plans_with_section_disagreement)
    assert not any("item 3" in s and "section" in s.lower() for s in said), (
        "item 3's section agrees with its own row (both Done), so no "
        f"section finding should appear - comparing against the master "
        f"instead of the plan row would cause this:\n{said}")


def test_section_disagreement_is_visible_on_the_cli(
        plans_with_section_disagreement):
    """**T136 spawn test.** The section-level disagreement must be visible
    to an operator running the CLI, not just to the in-process API.
    Strengthened in round 2 (N2): asserts the section COUNT rather than just
    the word, because the summary always prints "N section(s)"."""
    got = spawn("--plans", str(plans_with_section_disagreement),
                "--master", "master.md")
    assert got.returncode == 1, (
        "a section/row disagreement should produce exit 1, "
        f"got {got.returncode}:\n{got.stdout}{got.stderr}")
    assert "3 section(s)" in got.stdout, (
        f"the summary should report 3 sections compared:\n{got.stdout}")
    assert "item 1" in got.stdout and "section" in got.stdout.lower(), (
        f"the finding about item 1 must be in the output:\n{got.stdout}")


def test_an_unmatched_section_status_is_counted(tmp_path):
    """**T136 round 2, N1.** T276 established that a spelling the tool
    cannot classify must be visible as a count on the summary line. A
    section's `**Status**:` line is now a thing the tool classifies, so an
    unrecognized section status must be counted the same way rows are."""
    where = tmp_path / "plans"
    where.mkdir()
    master_text = """\
# A plan set

| Plan | Covers | Items | Done |
|---|---|---|---|
| [phase.md](phase.md) | first | 1 | **1 - COMPLETE** |

### [Phase](phase.md) - 1/1 done

| # | Item | Milestone | Status |
|---|---|---|---|
| 1 | widget | M7 | **Done** (`abc1234`) |

The plan stands at **1 items, 1 done**, and that is the headline.
"""
    phase_text = """\
# Phase plan

| # | Item | Milestone | Status |
|---|---|---|---|
| 1 | widget | M7 | **Done** (`abc1234`) |

## Item 1 — widget

**Status**: Blocked on upstream (`abc1234`).

Waiting for a dependency.
"""
    (where / "master.md").write_text(master_text, encoding="utf-8")
    (where / "phase.md").write_text(phase_text, encoding="utf-8")
    got = spawn("--plans", str(where), "--master", "master.md")
    assert got.returncode == 0, (
        "an unrecognized section spelling must not be graded into a "
        f"manufactured disagreement:\n{got.stdout}{got.stderr}")
    assert "1 cell(s) matched no marker" in got.stdout, (
        "the unmatched section status must be counted on the summary "
        f"line:\n{got.stdout}")


# ---------- T393: the section check states its coverage denominator --------

MASTER_PARTIAL_COVER = """\
# A plan set

| Plan | Covers | Items | Done |
|---|---|---|---|
| [phase.md](phase.md) | first | 3 | **3 - COMPLETE** |
| [phase2.md](phase2.md) | second | 2 | **2 - COMPLETE** |

### [Phase](phase.md) - 3/3 done

| # | Item | Milestone | Status |
|---|---|---|---|
| 1 | widget | M7 | **Done** (`abc1234`) |
| 2 | gadget | M7 | **Done** (`def5678`) |
| 3 | sprocket | M7 | **Done** (`ghi9012`) |

### [Phase2](phase2.md) - 2/2 done

| # | Item | Milestone | Status |
|---|---|---|---|
| 4 | flywheel | M7 | **Done** (`jkl3456`) |
| 5 | gasket | M7 | **Done** (`mno7890`) |

The plan stands at **5 items, 5 done**, and that is the headline.
"""

PHASE_PARTIAL_COVER = """\
# Phase plan

| # | Item | Milestone | Status |
|---|---|---|---|
| 1 | widget | M7 | **Done** (`abc1234`) |
| 2 | gadget | M7 | **Done** (`def5678`) |
| 3 | sprocket | M7 | **Done** (`ghi9012`) |

## Item 1 - widget

**Status**: Done (`abc1234`).

Delivered.

## Item 2 - gadget

This section records its state in free prose and carries no status line - the
shape 144 of the real set's 155 item sections take.

## Item 3 - sprocket

Narrative only, so the section check never reads this one either.
"""

PHASE2_PARTIAL_COVER = """\
# Phase two plan

| # | Item | Milestone | Status |
|---|---|---|---|
| 4 | flywheel | M7 | **Done** (`jkl3456`) |
| 5 | gasket | M7 | **Done** (`mno7890`) |

## Item 4 - flywheel

**Status**: Done (`jkl3456`).

Delivered.

## Item 5 - gasket

Narrative only, no status line, so the section check does not read it.
"""


def _partial_cover(tmp_path: Path) -> Path:
    """A plan set with TWO referenced plans that carry item sections: phase.md
    (3 `## Item N` sections, 1 with a `**Status**:` line) and phase2.md (2
    sections, 1 with a status line). The section check compares 2 sections of
    5 that exist, and the denominator 5 is a SUM across both plans - larger
    than either plan alone (max 3), so a per-plan denominator (the reviewer's
    `+= -> =` mutant) cannot reach it. Round 1's single-plan fixture could not
    tell a summed denominator from a per-plan one; this one can.
    """
    where = tmp_path / "plans"
    where.mkdir()
    (where / "master.md").write_text(MASTER_PARTIAL_COVER, encoding="utf-8")
    (where / "phase.md").write_text(PHASE_PARTIAL_COVER, encoding="utf-8")
    (where / "phase2.md").write_text(PHASE2_PARTIAL_COVER, encoding="utf-8")
    return where


def _heading_count(path: Path) -> int:
    """`## Item N` headings in a file, via the module's own regex."""
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines()
               if PA.ITEM_SECTION.match(line))


def test_the_summary_states_section_coverage_out_of_the_total(tmp_path):
    """**T393.** The section check reads only `## Item N` sections that carry a
    `**Status**:` line - measured on the real set, 11 of 155. A summary that
    says only "2 section(s)" lets a reader believe every item section was
    compared. Coverage is stated as compared-of-total so a partial pass cannot
    read as a full one: here 2 sections carry a status line and are compared,
    of 5 item sections that exist across the two plans.
    """
    where = _partial_cover(tmp_path)
    got = spawn("--plans", str(where), "--master", "master.md")
    assert got.returncode == 0, got.stdout + got.stderr
    assert "2 of 5 section(s)" in got.stdout, (
        "the summary must state how many sections were compared (2) out of "
        f"how many item sections exist (5):\n{got.stdout}")


def test_the_coverage_denominator_sums_across_plans_not_per_plan(tmp_path):
    """**T393 round 2 (Opus-5 review BLOCK).** The denominator must be SUMMED
    across every plan the check reads, not left at the last plan's own heading
    count. `sections_total = item_headings_of(...)` (the accumulator's `+=`
    turned to `=`) survives a single-plan fixture and, on the real set, prints
    "11 of 7 section(s)" - a denominator BELOW its numerator, 157% coverage -
    the very looks-complete-but-isn't defect this row exists to kill, one level
    up. Two referenced plans of 3 and 2 sections make the summed 5 exceed
    either alone (max 3), so a per-plan denominator cannot reach it.
    """
    where = _partial_cover(tmp_path)
    said, _looked, _no_marker, sec_compared, sec_total = PA.audit(
        where, "master.md")
    h_phase = _heading_count(where / "phase.md")
    h_phase2 = _heading_count(where / "phase2.md")
    assert (h_phase, h_phase2) == (3, 2), (h_phase, h_phase2)
    assert sec_total == h_phase + h_phase2 == 5, sec_total
    assert sec_total > max(h_phase, h_phase2), (
        "the denominator must be summed across plans, so it exceeds the "
        f"largest single plan; a per-plan total could not: {sec_total} vs "
        f"max {max(h_phase, h_phase2)}")
    assert sec_compared == 2, sec_compared
    assert said == [], said


def test_the_coverage_denominator_moves_when_an_uncovered_item_is_added(
        tmp_path):
    """**T393, cell 7 clause 3: a test fails when the covered fraction falls
    without the summary saying so.** A further `## Item N` section carrying no
    status line lifts the denominator and not the numerator, so coverage falls
    from 2/5 to 2/6. A denominator wired to the numerator ("2 of 2") or frozen
    to a constant ("2 of 5") cannot say so, and this fails against both.
    """
    where = _partial_cover(tmp_path)
    (where / "phase.md").write_text(
        PHASE_PARTIAL_COVER
        + "\n## Item 8 - flange\n\nNarrative only, no status line.\n",
        encoding="utf-8")
    got = spawn("--plans", str(where), "--master", "master.md")
    assert got.returncode == 0, got.stdout + got.stderr
    assert "2 of 6 section(s)" in got.stdout, (
        "an added item section with no status line lowers coverage to 2/6; "
        f"the summary's denominator must move to say so:\n{got.stdout}")
    assert "2 of 5 section(s)" not in got.stdout, got.stdout


def test_audit_returns_the_total_item_section_count(tmp_path):
    """**T393.** The denominator is derived, not typed: `audit` returns the
    count of `## Item N` headings across the plans it read, summed, so the
    summary and any caller read one number rather than each counting its own -
    two readers of one fact drift, this module's own founding defect one level
    up. Re-derived here through the module's own `ITEM_SECTION` regex over both
    plans and anchored to a hand-counted 5.
    """
    where = _partial_cover(tmp_path)
    said, looked, _no_marker, sec_compared, sec_total = PA.audit(
        where, "master.md")
    headings = _heading_count(where / "phase.md") + _heading_count(
        where / "phase2.md")
    assert headings == 5, headings
    assert sec_total == headings, (sec_total, headings)
    assert sec_compared == 2, sec_compared
    assert sec_compared <= sec_total, (sec_compared, sec_total)
    assert said == [], said


def test_a_section_with_two_status_lines_uses_the_first():
    """**T393, cell 4 ride-along 2 (plan_audit.py:150).** `section_statuses_of`
    keeps only the FIRST `**Status**:` line in a section - a choice nothing
    tested, so dropping the `current_item = None` that enforces it (taking the
    LAST instead) survived every test at the T136 tip. A section whose first
    status says Done and whose second says Filed must read Done; the mutant
    would read Filed and manufacture a section/row disagreement.
    """
    text = """\
# Phase plan

| # | Item | Milestone | Status |
|---|---|---|---|
| 1 | widget | M7 | **Done** (`abc1234`) |

## Item 1 - widget

**Status**: Done (`abc1234`).

Some intervening prose about the work.

**Status**: Filed, reopened after all.
"""
    statuses = PA.section_statuses_of(text)
    assert statuses == {1: "Done (`abc1234`)"}, (
        "only the first Status line in a section is kept; a second must be "
        f"ignored, not overwrite the first: {statuses}")
