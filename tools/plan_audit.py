#!/usr/bin/env python3
"""Does a decentralised plan set still agree with its own master index?

**Why this exists, and it is one finding rather than a tidiness.** A plan set
where a master index mirrors per-plan tables has the same failure the harness
keeps finding in code: two documents state one fact, nothing composes them, and
the copy nobody reads goes stale silently. Audited by hand on 2026-09-06 this
found SIX live instances at once - four items present in the master and in no
phase plan at all, one whose status the master had moved on from a month
earlier (a phase plan still read *"Partially done, ahead of schedule"* for work
that had landed, so the document a person opens to check that milestone said it
was unfinished), and one row asserting a defect a later item had closed the
same day. None was visible from the master, which is what everybody reads.

**A maintainer tool, not a shipped one**, and it takes the plan directory as an
argument rather than knowing one: `field_run.py` is the precedent - generic
code, the local path supplied from outside. Nothing here names a repository, a
plan set or a machine.

**What it does NOT do.** It does not read prose, judge whether a status is
true, or check anything about the work; it compares what two documents say
about the same row. A checker that graded prose would be the class this project
has now paid for four times.

    python tools/plan_audit.py --plans <dir> --master <name.md>

Exit 0 when they agree, 1 when they do not, 2 when it could not look.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import cli  # noqa: E402

#: A row in a plan's TO DO table: `| <n> | <title> | <milestone> | <status> |`.
#: The milestone cell is required because these tables live beside others that
#: also start with a number - a lifecycle phase table, for one - and counting
#: those would make every plan disagree with the master for a reason that is
#: not a defect. Measured on the real set: without it the pipeline plan reports
#: twelve phantom rows.
ITEM_ROW = re.compile(r"^\| (\d+) \| (.*?) \| (M[0-9][^|]*) \| (.*) \|\s*$")

#: The master's per-plan sections name their own file, so the mapping is
#: DERIVED rather than a table somebody maintains - which is the same defect
#: one level up.
SECTION = re.compile(r"^###\s+\[[^\]]*\]\(([A-Za-z0-9._-]+\.md)\)")

#: A phase-plan section heading: `## Item <n>` (with optional em-dash and
#: title). These sections carry a `**Status**:` line that states the same
#: fact the table row states, one level below - and nothing composed them
#: until T136.
ITEM_SECTION = re.compile(r"^##\s+Item\s+(\d+)\b")

#: The status line inside an item section: `**Status**: <text>`.
SECTION_STATUS = re.compile(r"^\*\*Status\*\*:\s*(.*)")

#: A row is OPEN when its status SAYS so. Stated as markers rather than
#: inferred, because "is this done" read out of prose is exactly the grading
#: this tool refuses to do. Anything else counts as done, and that is a blind
#: spot rather than a safety net: measured on a real set, one row moved to a
#: sixth spelling in BOTH documents reads done on both sides, agrees with
#: itself, and exits 0 without a word. A spelling this tuple does not carry
#: surfaces only where the comparison is asymmetric - where a human's typed
#: done-count moved and the count made beside it did not - which is the
#: reverse of the case this tool was built for. The summary line names this
#: tuple whenever a comparison completes (the four refusal exits print their
#: own message and nothing else) for exactly that reason: it grades no row
#: and judges no prose, but a spelling missing from that line is now
#: something an operator can see and check against their own plan set,
#: rather than a gap only this source file knew about.
#:
#: **Emphasis is not part of a marker.** Three of these carried a literal `**`
#: and two did not, so the bold spelling of those two - which is the spelling
#: the tables actually use, the cells being emphasised by convention - fell
#: through to *done*. That silently lost the founding example: a row reading
#: `**Partially done, ahead of schedule**` for work that had landed. Markers
#: are written bare here and the cell is stripped before matching, so a marker
#: added tomorrow cannot acquire the same split.
OPEN_MARKERS = ("Filed", "Partly", "Partially", "Owed", "Not started")

#: Read the same way as OPEN_MARKERS - leading emphasis and whitespace off,
#: then a prefix match - so DONE is a claim this tool checks for rather than
#: the default everything else falls into. A cell that matches NEITHER set
#: (`"**Blocked on upstream**"`, review-W398's live case) used to be folded
#: silently into "done" with nothing said anywhere. It still reads as done -
#: grading it either way would be the prose-judgement this tool refuses - but
#: it is now COUNTED, so a spelling neither roster carries is a number an
#: operator sees rather than a gap only this source file knew about. Defined
#: once, here, so the summary line and the count read the same two tuples
#: rather than each carrying its own copy to drift out of step.
DONE_MARKERS = ("Done", "Complete")


def unmatched(status: str) -> bool:
    """True when this status's leading text is neither an OPEN marker nor a
    DONE one - the one shape this tool used to absorb without a word."""
    stripped = status.lstrip().lstrip("*_").lstrip()
    return not stripped.startswith(OPEN_MARKERS + DONE_MARKERS)


class AuditError(RuntimeError):
    """The plan set could not be read. Never a finding about the plans."""


def is_open(status: str) -> bool:
    """Does this status cell SAY the row is open?

    Leading emphasis and whitespace are not part of the claim, so they come off
    before the markers are consulted. Nothing else about the cell is read - a
    marker further in is prose, and prose is not this tool's question.
    """
    return status.lstrip().lstrip("*_").lstrip().startswith(OPEN_MARKERS)


def rows_of(text: str) -> dict[int, str]:
    """`{item number: status}` for every item row in this document."""
    found: dict[int, str] = {}
    for line in text.splitlines():
        hit = ITEM_ROW.match(line)
        if hit:
            found[int(hit.group(1))] = hit.group(4).strip()
    return found


def section_statuses_of(text: str) -> dict[int, str]:
    """`{item number: status}` from `## Item N` sections' `**Status**:` lines.

    A phase plan may carry both a table row and a section for the same item.
    The row is the structured record; the section's `**Status**:` line is
    prose that states the same fact one level below.  When they disagree,
    a reader of the section is told the wrong thing - which is the defect
    class this tool exists to catch, arriving inside a single document.
    """
    found: dict[int, str] = {}
    current_item: int | None = None
    for line in text.splitlines():
        heading = ITEM_SECTION.match(line)
        if heading:
            current_item = int(heading.group(1))
            continue
        if current_item is not None:
            status_match = SECTION_STATUS.match(line)
            if status_match:
                found[current_item] = status_match.group(1).strip().rstrip(".")
                current_item = None  # only the first Status line per section
    return found


def sections_of(master: str) -> dict[str, dict[int, str]]:
    """`{plan filename: {item: status}}` from the master's own sections."""
    out: dict[str, dict[int, str]] = {}
    current: str | None = None
    for line in master.splitlines():
        head = SECTION.match(line)
        if head:
            current = head.group(1)
            out.setdefault(current, {})
            continue
        hit = ITEM_ROW.match(line)
        if hit and current:
            out[current][int(hit.group(1))] = hit.group(4).strip()
    return out


def claimed(master: str) -> dict[str, tuple[int, int | None]]:
    """`{plan filename: (items claimed, done claimed)}` from the index table.

    The done cell is prose in this format (`**33 - COMPLETE**`), so the number
    is taken and the wording is not judged - and where there is no number the
    answer is None rather than a guess.
    """
    out: dict[str, tuple[int, int | None]] = {}
    for line in master.splitlines():
        if not line.startswith("| ["):
            continue
        cells = [c.strip() for c in line.split("|")]
        if len(cells) < 6:
            continue
        name = re.search(r"\]\(([A-Za-z0-9._-]+\.md)\)", cells[1])
        items = re.fullmatch(r"\d+", cells[3])
        if not name or not items:
            continue
        done = re.search(r"\d+", cells[4])
        out[name.group(1)] = (int(items.group()),
                              int(done.group()) if done else None)
    return out


def audit(plans: Path, master_name: str) -> tuple[list[str], int, int, int]:
    """`(findings, rows examined, cells matching no marker, sections compared)`.

    An empty findings list is the only clean answer; the third figure is
    never a finding - it is a could-not-classify count, reported so a
    spelling neither roster carries is a number rather than a silent zero.
    The fourth is how many section-level `**Status**:` lines were compared
    against their own row (T136).

    The row count is returned so a caller can refuse a run that looked at
    nothing: a sweep that found no disagreement and a sweep that read no rows
    hand back the same empty list, which is item 115's lesson and the reason
    this signature carries a count rather than being a list alone.
    """
    master_path = plans / master_name
    if not master_path.is_file():
        raise AuditError(f"no master plan at {master_path}")
    master = master_path.read_text(encoding="utf-8", errors="replace")
    sections = sections_of(master)
    index = claimed(master)
    said: list[str] = []
    looked = 0
    no_marker = 0
    sections_compared = 0

    for name, mine in sorted(sections.items()):
        path = plans / name
        if not path.is_file():
            said.append(f"{name}: the master has a section for it and the file "
                        f"is not in {plans}")
            continue
        plan_text = path.read_text(encoding="utf-8", errors="replace")
        theirs = rows_of(plan_text)
        looked += len(mine) + len(theirs)
        no_marker += sum(1 for s in mine.values() if unmatched(s))
        no_marker += sum(1 for s in theirs.values() if unmatched(s))
        for number in sorted(set(mine) - set(theirs)):
            said.append(f"{name}: item {number} is in the master and in no row "
                        f"of its own plan, so the two disagree about how much "
                        f"work there is")
        for number in sorted(set(theirs) - set(mine)):
            said.append(f"{name}: item {number} is in the plan and not in the "
                        f"master's section for it")
        for number in sorted(set(mine) & set(theirs)):
            if is_open(mine[number]) != is_open(theirs[number]):
                said.append(
                    f"{name}: item {number} reads "
                    f"{'open' if is_open(mine[number]) else 'done'} in the "
                    f"master and "
                    f"{'open' if is_open(theirs[number]) else 'done'} in the "
                    f"plan - somebody reading one of them is being told the "
                    f"wrong thing")
        want_items, want_done = index.get(name, (None, None))
        if want_items is not None and want_items != len(theirs):
            said.append(f"{name}: the master's index claims {want_items} "
                        f"item(s) and the plan carries {len(theirs)}")
        done = sum(1 for s in theirs.values() if not is_open(s))
        if want_done is not None and want_done != done:
            said.append(f"{name}: the master's index claims {want_done} done "
                        f"and the plan's own rows count {done}")

        # T136: compare section-level **Status**: lines against the plan's
        # own rows - two statements of one fact in the same document.
        sec_statuses = section_statuses_of(plan_text)
        no_marker += sum(1 for s in sec_statuses.values() if unmatched(s))
        common_sections = sorted(set(sec_statuses) & set(theirs))
        sections_compared += len(common_sections)
        for number in common_sections:
            if is_open(sec_statuses[number]) != is_open(theirs[number]):
                said.append(
                    f"{name}: item {number}'s section reads "
                    f"{'open' if is_open(sec_statuses[number]) else 'done'}"
                    f" and its own row reads "
                    f"{'open' if is_open(theirs[number]) else 'done'}"
                    f" - the section's **Status** line and the table "
                    f"disagree")

    total = {n for rows in sections.values() for n in rows}
    open_now = sum(1 for rows in sections.values()
                   for n, s in rows.items() if is_open(s))
    headline = re.search(r"stands at \*\*(\d+) items?, (\d+) done\*\*", master)
    if headline:
        want_total, want_done = int(headline.group(1)), int(headline.group(2))
        if want_total != len(total):
            said.append(f"{master_name}: the headline says {want_total} items "
                        f"and its own sections carry {len(total)}")
        if want_done != len(total) - open_now:
            said.append(f"{master_name}: the headline says {want_done} done "
                        f"and its own sections count {len(total) - open_now}")
    return said, looked, no_marker, sections_compared


def main(argv: list[str] | None = None) -> int:
    cli.use_utf8()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--plans", required=True, metavar="DIR",
                    help="the directory holding the plan set")
    ap.add_argument("--master", default="", metavar="NAME",
                    help="the master index's filename; when omitted, the one "
                         "document carrying a plan-set headline")
    args = ap.parse_args(argv)
    plans = Path(args.plans)
    if not plans.is_dir():
        print(f"!! {plans} is not a directory")
        return 2
    name = args.master
    if not name:
        found = [p.name for p in sorted(plans.glob("*.md"))
                 if re.search(r"stands at \*\*\d+ items?, \d+ done\*\*",
                              p.read_text(encoding="utf-8", errors="replace"))]
        if len(found) != 1:
            print(f"!! {len(found)} document(s) in {plans} carry a plan-set "
                  f"headline, so --master has to say which")
            return 2
        name = found[0]
    try:
        said, looked, no_marker, sec_compared = audit(plans, name)
    except AuditError as exc:
        print(f"!! {exc}")
        return 2
    for problem in said:
        print(f"  - {problem}")
    if not looked:
        print(f"!! {name} names no plan this directory holds - nothing was "
              f"compared, which is not the same as nothing being wrong")
        return 2
    line = (f"{name}: {len(said)} disagreement(s) over {looked} row reading(s)"
            f" and {sec_compared} section(s), "
            f"reading open as: {', '.join(OPEN_MARKERS)}")
    if no_marker:
        line += f", {no_marker} cell(s) matched no marker"
    print(line)
    return 1 if said else 0


if __name__ == "__main__":
    raise SystemExit(main())
