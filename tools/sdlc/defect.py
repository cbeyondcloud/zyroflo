"""The defect flow — item 45: what a defect owes that a requirement does not.

`KINDS = ("requirement", "defect", "deploy-change")` has been written at intake
since item 31, and **the pipeline treated all three identically**: no code read
a run's kind, and no SOP carried an obligation keyed to it. UC-06 promises *"a
defect from symptom to fix **plus regression tests** via the same pipeline"*,
and "the same pipeline" had been implemented as "no difference at all", which
left the promise unenforced. The same class as `phases[]` before item 36,
`gate_profile` before item 63 and `known_failures` before its own field run: a
declared field nothing reads cannot be wrong, and cannot be right either.

**What a defect owes is a REPRODUCTION.** A fix with no failing-first test is a
claim that something was broken, and nothing behind it. The maintainer of this
harness supplies one out of discipline; an adopter's agent has no such habit,
and a gate that relies on habit is not a gate.

**The check is on the CLAIM's anchoring, not on a checklist.** A document can
assert anything, so this does not grade prose or count sections. It requires
the repro to NAME its regression tests in backticks and then requires each
named test to appear in the change's own CHANGED CONTENT. The name is spelled
exactly as the change spells the test — a function or a `Class#method`, the
identifier the diff introduces, never the file it lives in, because a name
found only in a file header can never ground. The document makes a claim; the
judge checks the claim is grounded in what was actually written.

That keeps it **generic**, which is the harder half. A judge that recognised
test files by looking for `test_`, `*Test.java` or `*.spec.ts` would be a gate
that names ecosystems, and item 40 settled that argument: *nothing in the gate
names an ecosystem*. Here the RUN says what its regression test is called, in
whatever its language calls it, and the harness checks that the name is really
in the diff rather than only in the prose.

**Absent is not clean, and there are two absences here rather than one.** A
defect whose P4 has produced nothing at all owes nothing *here* — `stage.verify`
already says P4 owes its artifact, by name, and saying it twice in different
words trains people to skim both. But a delivered `40-impl/` carrying no diff
is this module's business: the check cannot look, and for a defect that is not
the same as looking and finding nothing.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools import cli  # noqa: E402
from tools.sdlc import prose  # noqa: E402
from tools.sdlc import stage as stages  # noqa: E402
from tools.sdlc import state as ledger  # noqa: E402

cli.use_utf8()

SCHEMA_VERSION = 1

#: The kind this module is about. The other two owe nothing here, and that is
#: the point of reading the field at all.
KIND = "defect"

#: The phase whose artifact carries the reproduction. P4 writes the fix, so P4
#: writes the evidence that there was something to fix. Derived from the stage
#: contract rather than spelled, so it cannot drift from `stage.py`.
PHASE = stages.by_name("implementation").phase

#: A backticked token that could be a test's name. The lower bound keeps a
#: stray `` ` `` pair out; the upper bound keeps a paragraph out.
_TICKED = re.compile(r"`([^`\n]{2,240})`")

#: What a name has to look like before it is treated as one. A backticked
#: English word in a sentence is not a claim about a test, and demanding that
#: every one of them appear in the diff would make the check unusable.
_NAME_ISH = re.compile(r"^[\w./\\:\[\]#+-]{2,240}$")

#: **The line that DECLARES the regression tests, and the only line read for
#: them.** The first cut took every backticked identifier anywhere in the
#: document, and a field run measured what that costs: a reproduction written
#: in this project's ordinary house style — sibling artifacts, the base commit,
#: the run id in backticks — produced SEVEN false obligations, and after those
#: were removed a backticked review-finding id produced an eighth.
#:
#: The cost was not the noise. It was that the only way past the check was to
#: strip backticks off every legitimate cross-reference, so the check made
#: documents WORSE — and this module's own tests warn that an unusable check is
#: one people switch off.
#:
#: A declaration is unambiguous, asks nothing of a heuristic, and leaves the
#: rest of the document free to cite whatever it likes. The SOP already told
#: runners to write this line; now it is the line that counts.
#: Two forms, because both are how people really write it: a labelled line
#: (`Regression test: ...`) and a heading whose subject is the regression test.
#: A heading may carry numbering and decoration before the phrase, so it is
#: matched loosely; a labelled line must LEAD with it, or every sentence
#: mentioning regression tests in passing would become a declaration.
_DECLARES = re.compile(
    r"^(?:#{1,6}\s+.*\bregression tests?\b"
    r"|\s*(?:[-*]\s*)?(?:\*\*)?regression tests?(?:\*\*)?\s*[:—-])",
    re.I)


class DefectError(RuntimeError):
    """The question could not be asked, which is never the same as a clean answer."""


def kind_of(target: Path, run_id: str) -> str:
    """This run's kind, or "" when it cannot be read.

    From `state.json`, which every phase already maintains, rather than from
    the intake artifact - a run whose intake document is edited afterwards is
    still the kind it was opened as, and `state` is the record of what the run
    IS rather than of what it was asked for.
    """
    try:
        return str(ledger.load(Path(target), run_id).get("kind") or "")
    except (RuntimeError, OSError, ValueError):
        return ""


def named_tests(text: str) -> list[str]:
    """The regression tests this reproduction DECLARES.

    Only lines that declare them are read — `Regression test: ...` — and only
    the backticked names on those lines. Everything else in the document is
    prose this check does not grade, which is what lets a reproduction cite its
    sibling artifacts, its base commit and its own run id without inventing
    obligations out of them.
    """
    seen: list[str] = []
    # Item 149, the same rule as `adr.change_set_lines` and from the same
    # module: a fenced block is an example. The P4 SOP shows this very
    # declaration inside a fence, so a repro quoting it obliged on a test
    # nobody wrote.
    lines = prose.unfenced(text)
    for i, line in enumerate(lines):
        if not _DECLARES.match(line):
            continue
        # The declaration line, or - when it carries no name - the next line
        # with anything on it. Two forms, because both are how people really
        # write it: `Regression test: `x`` inline, and a heading followed by
        # the name. Bounded at ONE following line on purpose: a section that
        # scanned until the next heading would sweep up every backticked word
        # in the paragraph, which is the defect this whole change is undoing.
        found = [t.strip() for t in _TICKED.findall(line)]
        if not found:
            for nxt in lines[i + 1:]:
                if nxt.strip():
                    found = [t.strip() for t in _TICKED.findall(nxt)]
                    break
        for name in found:
            if _NAME_ISH.match(name) and name not in seen:
                seen.append(name)
    return seen


def hunks(diff: str) -> str:
    """The CHANGED CONTENT of a patch, with its metadata left out.

    A name is grounded by being in the change, and `git format-patch` writes a
    great deal that is not the change: a `From <sha>` header, the subject, the
    commit message, `index` lines. A field run grounded a COMMIT SHA as a
    regression test through that header, which is a claim the check was built
    to refuse being satisfied by the patch's own plumbing.

    Context lines are kept. A test whose body changed but whose signature line
    did not is still a test this change touched, and demanding a `+` would
    refuse it — narrower than the truth rather than closer to it.
    """
    out, inside = [], False
    for line in diff.splitlines():
        if line.startswith("@@"):
            inside = True
            continue
        if line.startswith("diff --git ") or line.startswith("From "):
            inside = False
        if inside and line[:1] in ("+", "-", " ", ""):
            out.append(line[1:])
    return "\n".join(out)


def diff_text(target: Path, run_id: str) -> str | None:
    """The change under review, or None when there is not one yet.

    None and "" are different answers and stay apart: no diff at all means P4
    has not delivered, which `stage.verify` reports by name; an EMPTY diff
    means it delivered nothing, which is this module's business.
    """
    folder = ledger.run_dir(Path(target), run_id) / stages.IMPL_DIR.rstrip("/")
    patch = folder / stages.CHANGE_PATCH
    if not patch.is_file():
        return None
    try:
        return patch.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise DefectError(f"{patch} could not be read: {exc}") from None


def report(target: Path, run_id: str) -> dict:
    """Everything this module knows about one run, as data."""
    target = Path(target)
    kind = kind_of(target, run_id)
    folder = ledger.run_dir(target, run_id)
    repro = folder / stages.REPRO
    text = ""
    if repro.is_file():
        try:
            text = repro.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise DefectError(f"{repro} could not be read: {exc}") from None
    diff = diff_text(target, run_id)
    # Grounded against the patch's CHANGED CONTENT, never its metadata.
    body = hunks(diff) if diff is not None else None
    tests = named_tests(text)
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "kind": kind,
        "applies": kind == KIND,
        "repro": str(repro),
        "repro_present": repro.is_file() and bool(text.strip()),
        "recorded": stages.REPRO in (ledger.load(target, run_id).get("artifacts") or {}),
        "diff_present": diff is not None,
        "named_tests": tests,
        "grounded": [t for t in tests if body and t in body],
        "ungrounded": [t for t in tests if body is not None and t not in body],
    }


def obligations(target: Path, run_id: str) -> list[str]:
    """What P4 still owes because this run is a defect. Sentences, not codes."""
    try:
        found = report(Path(target), run_id)
    except DefectError as exc:
        return [str(exc)]
    if not found["applies"]:
        return []
    if not found["diff_present"]:
        # Two different absences, and folding them would hide one. If P4 has
        # not delivered `40-impl/` at all, `stage.verify` already says so by
        # name and repeating it teaches people to skim both. But a delivered
        # `40-impl/` with no diff in it is THIS module's business: the check
        # cannot look, and for a defect that is not a pass.
        folder = ledger.run_dir(Path(target), run_id) / stages.IMPL_DIR.rstrip("/")
        if not folder.is_dir():
            return []
        return [
            f"this run was opened as a {KIND} and {stages.IMPL_DIR} carries no "
            f"{stages.CHANGE_PATCH}, so there is nothing to check a regression "
            f"test against - the reproduction cannot be verified, which is not "
            f"the same as verified clean"]

    problems: list[str] = []
    if not found["repro_present"]:
        problems.append(
            f"this run was opened as a {KIND} and owes {stages.REPRO}, which is "
            f"absent or empty - a fix with no reproduction behind it is a claim "
            f"that something was broken, and nothing behind the claim")
        return problems
    if not found["recorded"]:
        problems.append(
            f"{stages.REPRO} exists but was never recorded, so its bytes are not "
            f"pinned and a yes about them covers nothing "
            f"({stages.record_command(stages.REPRO)})")
    if not found["named_tests"]:
        problems.append(
            f"{stages.REPRO} names no regression test. Name each one in "
            f"backticks, exactly as it is spelled in the change - the harness "
            f"does not guess what a test is called in your language, it checks "
            f"that what you called it is really in the diff")
    for name in found["ungrounded"]:
        problems.append(
            f"{stages.REPRO} names `{name}` as a regression test and the change "
            f"does not contain it - a test named only in prose is a claim about "
            f"work nobody did")
    return problems


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", required=True, metavar="DIR")
    ap.add_argument("--run", required=True, metavar="ID")
    ap.add_argument("--check", action="store_true",
                    help="what this run still owes because it is a defect")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    import json

    target = Path(args.target).resolve()
    try:
        found = report(target, args.run)
        owed = obligations(target, args.run)
    except DefectError as exc:
        print(f"!! {exc}", file=sys.stderr)
        return 3
    if args.json:
        print(json.dumps({**found, "obligations": owed}, indent=2, sort_keys=True))
        return 1 if owed else 0
    if not found["applies"]:
        print(f"run {args.run} is a {found['kind'] or '(unreadable)'}, not a "
              f"{KIND} - nothing is owed here")
        return 0
    if not found["diff_present"] and owed:
        # T505: a delivered 40-impl/ with no change.patch is owed. This branch
        # said "no change yet" at exit 0 while `--json` and `obligations()`
        # reported the same run as owing.
        print(f"run {args.run}: {KIND}, {stages.IMPL_DIR} delivered with no "
              f"{stages.CHANGE_PATCH}")
        for problem in owed:
            print(f"  !! {problem}")
        return 1
    if not found["diff_present"]:
        print(f"run {args.run} is a {KIND} and P4 has produced no change yet - "
              f"the phase contract says so by name, and this says it once")
        return 0
    print(f"run {args.run}: {KIND}, {len(found['named_tests'])} regression "
          f"test(s) named, {len(found['grounded'])} of them in the change")
    for problem in owed:
        print(f"  !! {problem}")
    return 1 if owed else 0


def main(argv: list[str] | None = None) -> int:
    """The door. `3` is could-not-look, which a checker must never report as
    a clean answer - `adr.py` states the same rule for the same reason."""
    return cli.door(_main, argv, "the defect check", 3)


if __name__ == "__main__":
    raise SystemExit(main())
