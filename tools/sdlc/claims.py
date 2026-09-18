"""A number a document states must agree with the number the harness measured.

**The gap this closes cost two gates twice in one run.** On 2026-08-29 a defect
run's acceptance criterion said *"16 tests run and 0 failures"*. The module runs
**12**, and the harness had known that since P0: `00-intake.json` recorded
`"tests": 12` four minutes before P1 was written, and the repository's own
profile comment said the same. Nothing compared them, so the wrong number passed
**G1 and G2**, reached the design twice, and reached the owner's approval note.

Then it got worse in the way that makes the case: after the number was corrected
BY HAND, **four more wrong counts were still sitting in the same run's
artifacts** — `20-scope.md` said 16 twice and *"the other fifteen tests"*, and
`10-requirements.md` had a fifteen of its own. A careful human correcting a known
error missed most of its instances. That is what a mechanical check is for.

**Scope, stated narrowly on purpose: this checks claims against numbers the
HARNESS ITSELF MEASURED.** The same run also said *"25 assertions"* where there
were 21, and this module is silent about that — nothing here counts assertions,
and a checker that guessed would be grading prose. The one number the harness
records is the baseline test count, so that is the one claim it can judge.

**It names no ecosystem** — item 40's rule. It knows the English word "test",
not `pytest`, `surefire`, or `jest`, and the count comes from whatever command
the profile declares.

Two rules keep it usable, because an unusable check gets switched off:

**A differing number is ACKNOWLEDGED if the measured number is on the same
line.** That is what a correction looks like — *"This document said 16 tests. The
module runs 12."* is a true sentence and must not be flagged. Measured against
the real run: this rule passes the correction note and still catches all three
genuinely wrong claims.

**An ADDITIVE claim is not a total.** "3 new tests" and "added 2 tests" are
statements about a delta, not about the suite, so they are left alone.

**Considered and declined: two acceptance criteria against EACH OTHER**
(item 124, observation O2). Item 114's walk wrote AC4 requiring 12 tests and
AC5 obliging one to be added, in the same pass, and only a hand correction
caught the contradiction.

It is declined for the reason this module's scope was drawn in the first
place: everything here checks a claim against a number the HARNESS MEASURED.
Criterion against criterion is prose against prose, and this module already
refuses its nearest neighbour on exactly that ground - nothing here counts
assertions, because a checker that guessed would be grading prose. A checker
that read *12 tests* and *add one* and decided they disagreed would be
inferring an arithmetic relationship nobody stated, and it would be wrong the
first time a criterion said *at least*.

Written down so it is not rediscovered. What would change the ruling is
criteria carrying a machine-readable count, which is a change to what P1
writes rather than to what this reads.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import cli  # noqa: E402
from tools.sdlc import intake as entry  # noqa: E402
from tools.sdlc import state as ledger  # noqa: E402

SCHEMA_VERSION = 1

#: The noun this module can judge, because it is the count the harness records.
NOUN = "test"

#: Words a document writes numbers as. Twenty is the ceiling on purpose: past it
#: people write digits, and a longer table is more surface for no more signal.
WORDS = {w: n for n, w in enumerate([
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen", "eighteen", "nineteen", "twenty"])}

#: A claim about a DELTA is not a claim about the suite.
ADDITIVE = ("new", "added", "adds", "add", "extra", "additional", "further",
            "more", "another", "fewer")

_NUMBER = r"(?:\d{1,4}|" + "|".join(WORDS) + r")"

#: **PLURAL only, and at most one adjective between the number and the noun.**
#: Both narrowings were forced by measurement rather than taste. The first cut
#: allowed a singular noun and two intervening words, ran against a real
#: completed run, and produced 14 findings of which **9 were false** — "one test
#: method", "one Java test", and the outright nonsense "two the test expects",
#: where the slack swallowed an unrelated sentence. A suite total is never one
#: test, and a number sitting two words from its noun is usually not modifying
#: it. Signal that low is a check people switch off.
_CLAIM = re.compile(rf"\b({_NUMBER})\s+(\w+\s+)?{NOUN}s\b", re.I)

#: `N of M tests` is an explicit SUBSET, not a total — "233 of 233 tests" was
#: the last false positive standing after the narrowing above, and it is a true
#: sentence about a different subject.
_SUBSET_BEFORE = ("of",)

#: An ATX heading's own numbering, blanked before the line is scanned.
#:
#: **Item 140, and it is the TENTH false-positive shape — the first that BLOCKS.**
#: `\b` treats a full stop as a word boundary, so the fractional part of a
#: section number matched as a standalone number: MEASURED, `### 4.5 Tests to
#: run-or-edit — 234 total, 9 fall under <component>` returned **5**, a number
#: appearing nowhere in the document, and `stage.verify` then refused a phase
#: whose document was CORRECT — nine test files listed under a heading saying
#: nine. Because `approve.record` calls `stage.verify`, **a human could not
#: approve the gate**, and the only ways past were renumbering the heading (item
#: 100's recorded cost: the check makes documents worse) or forcing (item 98's:
#: indistinguishable from what the gate exists to catch).
#:
#: **A leading number in a heading is section numbering, and that is a RULING
#: rather than a detection.** `### 3 tests are failing` and `### 3 Widgets` are
#: the same bytes; nothing can separate them, so this errs toward reading LESS
#: in the one place where reading more blocks a gate. The cost is stated: a
#: genuine count written as the first token of a heading is not checked, and a
#: count that matters belongs in the body, where it still is.
_HEADING_NUMBER = re.compile(r"^(#{1,6}\s+)(\d+(?:\.\d+)*\.?)(\s)")


class ClaimError(RuntimeError):
    """Raised when the run cannot be read at all."""


def value_of(token: str) -> int | None:
    token = token.strip().lower()
    if token.isdigit():
        return int(token)
    return WORDS.get(token)


def measured(target: Path, run_id: str) -> int | None:
    """The test count P0 recorded, or None if it recorded none.

    **None is "nobody measured", never zero.** A run whose profile declares no
    test command has no baseline, and reading that as a suite of zero tests
    would make every count in every document a contradiction.
    """
    folder = ledger.run_dir(Path(target), run_id)
    path = folder / entry.INTAKE_JSON
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ClaimError(f"{path} could not be read: {exc}") from None
    base = doc.get("baseline")
    if not isinstance(base, dict):
        return None
    count = base.get("tests")
    if count is None:
        return None
    if isinstance(count, int) and not isinstance(count, bool):
        return count
    raise ClaimError(
        f"{path}: baseline 'tests' is {count!r} (type {type(count).__name__})"
        f" — present but could not be read as a count"
    )


def _scannable(line: str) -> str:
    """The line with an ATX heading's own numbering blanked out.

    Blanked rather than stripped so every offset still points where it did.
    **That reason is weaker than it first looks and the mutation pass said so**:
    the caller reports the ORIGINAL line, and the only positional read is the
    character immediately LEFT of a match, which stripping preserves too - so
    stripping is an equivalent mutant today and is recorded rather than chased.
    Blanking is kept because the equivalence holds only while nothing uses an
    absolute offset, which is a property of the callers rather than of this
    function.

    Digits ONLY, deliberately: section numbering is never spelled out in words,
    so `### twelve tests fail` is a real claim and must survive. Widening this
    to any leading token reads identically on every other case and drops it.
    """
    m = _HEADING_NUMBER.match(line)
    if not m:
        return line
    return line[:m.start(2)] + " " * len(m.group(2)) + line[m.end(2):]


def claims(text: str) -> list[tuple[int, int, str]]:
    """`(line number, number claimed, the line)` for every total-shaped claim."""
    out: list[tuple[int, int, str]] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = _scannable(raw)
        for match in _CLAIM.finditer(line):
            # **Item 140.** A number whose left neighbour is a full stop OR a
            # hyphen is the tail of a larger token - `4.5` in a section number,
            # `v2.3` in a version, `2026-08-29` in a date, `12-16` in a range,
            # `135-139` in a hyphenated id - and `\b` cannot see that, because it
            # treats both as boundaries. Measured shapes: `4.5 Tests` -> 5,
            # `4.5.1 Tests` -> 1, `v2.3 tests` -> 3, `2026-08-29 tests` -> 29,
            # `12-16 tests` -> 16, `135-139 tests` -> 139. A hyphen with a space
            # after it - a markdown bullet, `- 16 tests` - is not adjacent, so
            # the number is a real claim and survives.
            if match.start() and line[match.start() - 1] in ".-":
                continue
            number, between = match.group(1), match.group(2) or ""
            value = value_of(number)
            if value is None:
                continue
            before = line[:match.start()].lower().split()
            words = set(between.lower().split()) | set(before[-2:])
            if words & set(ADDITIVE):
                continue                    # a delta, not the suite
            if before and before[-1].strip("(,*_`") in _SUBSET_BEFORE:
                continue                    # a subset, not the suite
            out.append((lineno, value, raw.strip()))
    return out


def acknowledged(line: str, count: int) -> bool:
    """Is the measured number on this line too?

    A document that names both is correcting itself or quoting history, and
    both are honest. Without this the correction note written to record the
    ORIGINAL defect would itself be reported as the defect.
    """
    spellings = {str(count)}
    for word, value in WORDS.items():
        if value == count:
            spellings.add(word)
    low = line.lower()
    return any(re.search(rf"\b{re.escape(s)}\b", low) for s in spellings)


def documents(target: Path, run_id: str) -> list[Path]:
    """The run's recorded markdown, in the order a reader meets it."""
    state = ledger.load(Path(target), run_id)
    folder = ledger.run_dir(Path(target), run_id)
    out = []
    for rel in sorted(state.get("artifacts") or {}):
        if not rel.lower().endswith(".md"):
            continue
        path = folder / rel
        if path.is_file():
            out.append(path)
    return out


def report(target: Path, run_id: str) -> dict:
    """Everything this module knows about one run, as data."""
    target = Path(target)
    count = measured(target, run_id)
    folder = ledger.run_dir(target, run_id)
    found: list[dict] = []
    for path in documents(target, run_id):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise ClaimError(f"{path} could not be read: {exc}") from None
        for lineno, value, line in claims(text):
            if count is not None and value == count:
                continue
            if count is not None and acknowledged(line, count):
                continue
            found.append({"document": path.relative_to(folder).as_posix(),
                          "line": lineno, "claimed": value,
                          "text": line[:200]})
    return {"schema_version": SCHEMA_VERSION, "run_id": run_id,
            "measured": count, "disagreements": found}


def problems(target: Path, run_id: str) -> list[str]:
    """Sentences for every claim that contradicts the measurement.

    Not "obligations": nothing here is work somebody forgot to do. These are
    statements the run's own recorded evidence contradicts, which is a
    different thing and is phrased as one.
    """
    try:
        found = report(Path(target), run_id)
    except ClaimError as exc:
        return [str(exc)]
    count = found["measured"]
    if not found["disagreements"]:
        return []
    if count is None:
        # Claims with nothing to check them against. Reported once rather than
        # per claim: the repair is the same for all of them, and one sentence
        # per line would be a wall nobody reads.
        where = ", ".join(sorted({d["document"] for d in found["disagreements"]}))
        return [f"{where} states a {NOUN} count and this run recorded no "
                f"baseline to check it against - P0 measures one so that a "
                f"number in a requirement is not just an assertion "
                f"(intake.py records it in {entry.INTAKE_JSON})"]
    out = []
    for row in found["disagreements"]:
        out.append(
            f"{row['document']}:{row['line']} says {row['claimed']} {NOUN}(s) "
            f"and this run measured {count} at P0 - if it counts something "
            f"else, say so on the line, or name the measured number beside it: "
            f"\"{row['text']}\"")
    return out


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", default=".", metavar="DIR")
    ap.add_argument("--run", required=True, metavar="ID")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    target = Path(args.target).resolve()
    found = report(target, args.run)
    if args.json:
        print(json.dumps(found, indent=2, sort_keys=True))
        # `--json` alone is a report and stays 0; with `--check` it is a GATE,
        # and a gate that returns 0 with a claim contradicted is the silent pass
        # this module exists to remove. `found["disagreements"]` is what the
        # non-json path's `problems()` keys off, so the two exit the same way.
        return 1 if (args.check and found["disagreements"]) else 0
    owed = problems(target, args.run)
    measured_note = ("no baseline recorded" if found["measured"] is None
                     else f"P0 measured {found['measured']} {NOUN}(s)")
    print(f"run {args.run}: {measured_note}, "
          f"{len(found['disagreements'])} disagreement(s)")
    for line in owed:
        print(f"  !! {line}")
    return 1 if (args.check and owed) else 0


def main(argv: list[str] | None = None) -> int:
    """The door. `3` is could-not-look, which a checker must never report as a
    clean answer - the rule `defect.py` and `adr.py` both state."""
    cli.use_utf8()
    return cli.door(_main, argv, "the claim check", 3)


if __name__ == "__main__":
    raise SystemExit(main())
