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
    said, looked = PA.audit(plans, "master.md")
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
    said, looked = PA.audit(plans, "master.md")
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
    said, looked = PA.audit(where, "master.md")
    assert looked == 0
    assert any("is not in" in s for s in said), said


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


def test_the_master_is_found_by_its_headline_when_it_is_not_named(plans):
    """A path a caller must get right is a path that will be got wrong, so the
    one document carrying a plan-set headline identifies itself - and two of
    them is a refusal rather than a guess."""
    assert spawn("--plans", str(plans)).returncode == 0
    (plans / "decoy.md").write_text(
        "The plan stands at **1 items, 1 done** here too.\n", encoding="utf-8")
    said = spawn("--plans", str(plans))
    assert said.returncode == 2 and "--master" in said.stdout
