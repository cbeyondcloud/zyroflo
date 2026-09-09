"""P5's engine — item 38: build and test what the change can reach, and judge
it against what was already true.

**The commands are not here.** Core owns four things: which components a change
reaches, what order to run them in, whether a result is a regression, and what
the result contract says. Everything a stack knows — how to build, how to test,
how to measure coverage, which failures were already red — is DATA in
`profile.yml: components[]`, filled in by a developer and read from there.

That split is not aesthetic. `sdlc-init.py` has been writing per-component
`build_command`, `test_command`, `coverage_command`, `coverage_threshold` and
`known_failures` into every estate profile since item 15, and **nothing has
ever read one of them.** The only reader anywhere was `profile_value(target,
"test_command")` — the TOP-LEVEL key, which a multi-component workspace does
not have. So a workspace with a dozen components and a dozen declared suites
recorded `baseline: (none) - no test_command`, and P5 had nothing to compare
against while the comparison sat in the same file, one indent level down.

**Reach is computed, not declared.** `DG_BUILT_FROM` says which module a
component publishes, `DG_DEPENDS_ON` says which modules a component consumes,
and the join of those two over the graph is "who breaks if this changes". A
third-party module has no publisher in the workspace and correctly drops out.
The closure is transitive and ordered dependencies-first; a cycle is REPORTED
and then broken by a stable rule, because a build order that hangs is worse
than one that is arguably wrong.

**A verdict is relative to a baseline, and a baseline nobody took is not zero.**
The bar is *no NEW failures*, so a repository that was already red stays
workable. What this cannot do is name which test newly failed: that needs a
report format nobody has declared, per stack, and guessing at one is how a
green run gets reported for a red suite. So the quarantine is DECLARED —
`known_failures` — and a red baseline with an empty quarantine is a REFUSAL
rather than a pass, because "no new failures" cannot be judged against a
failure set nobody wrote down.

**Coverage is measured or it is a finding.** Three report formats are read
(Cobertura, JaCoCo, LCOV), matched against the files this run actually changed.
A report that predates the coverage command did not come from it, and is
refused rather than believed — item 29's stale-artifact lesson, which is how a
runner that wrote nothing got vouched for.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools import cli  # noqa: E402
from tools.sdlc import adr as records  # noqa: E402
from tools.sdlc import policy as guardrails  # noqa: E402
from tools.sdlc import stage as stages  # noqa: E402
from tools.sdlc import state as ledger  # noqa: E402
from tools.sdlc import worktree as trees  # noqa: E402

cli.use_utf8()

SCHEMA_VERSION = 1
PHASE = "P5"

#: The steps, in the order they run for one component. Build before test
#: because a component that will not compile has no test result worth reading.
STEPS = ("build", "test", "coverage")

#: The profile key each step's command lives under, per component. These are
#: the keys `render_components` has been emitting all along.
COMMAND_KEY = {"build": "build_command", "test": "test_command",
               "coverage": "coverage_command"}

#: Where the engine's answer is written, inside the run folder.
#: Asked of the phase contract rather than spelled again. `stage.py` is the
#: only module every writer can import - it is embedded in the
#: dependency-free ledger check, so it can import almost nothing itself.
#: One home per name; the import graph decides which module gets to be it.
RESULT = stages.SUITE_RESULT

#: The proposal a breakage outside the approved change set produces. Owed
#: exactly when the engine found breakage nobody scoped, and not otherwise.
#:
#: The NAME lives in `stage.py`, which declares it as P5's conditional artifact,
#: and this module asks for it. That direction is forced rather than chosen:
#: this module imports `stage`, so `stage` cannot import back, and `stage` is
#: embedded in the dependency-free ledger check while this module is not. The
#: rule is one home per name; which module that is, the import graph decides.
SCOPE_ADD = stages.SCOPE_ADD

#: The SCA gate's document is written by `sca.py`, a separate CLI on purpose:
#: it queries a network database, and an engine that runs suites should not
#: have a remote service in its hot path. Its NAME is not restated here or
#: there - both ask `stage.py`, for the reason `RESULT` above states.

#: advisory < hard. A missed threshold is a finding either way; under `hard` it
#: is also something the phase owes. Aliased rather than repeated: the ordering
#: is what `tighten_only` compares against, and a second copy of it here could
#: disagree with the constitution's own.
ENFORCEMENT = guardrails.ENFORCEMENT_MODES

#: A step's outcome. Kept apart on purpose: `red` and `new-failures` are
#: different sentences, and so are `not-declared` and `unmeasurable`.
VERDICTS = ("green", "red", "new-failures", "quarantined", "not-declared",
            "unmeasurable", "blocked")

#: Long enough for a real suite; `intake.baseline` already uses this number and
#: two timeouts that disagree would make a baseline and its comparison
#: incomparable.
TIMEOUT = 7200

#: Report basenames, and the reader each one needs. Discovery is by NAME rather
#: than by a path a stack is assumed to use — `target/site/jacoco/jacoco.xml`,
#: `build/reports/.../jacoco.xml` and `coverage/lcov.info` are all conventions
#: that vary by plugin version, and a hardcoded path finds none of them.
REPORT_READERS = {
    "coverage.xml": "cobertura", "cobertura.xml": "cobertura",
    "jacoco.xml": "jacoco", "jacocoTestReport.xml": "jacoco",
    "lcov.info": "lcov", "lcov.dat": "lcov",
}

#: How deep under a component the search goes. Bounded because an unbounded
#: walk of a workspace with node_modules in it is measured in minutes.
REPORT_DEPTH = 6

#: JUnit-XML report basenames. Same argument as `REPORT_READERS` above —
#: discovery is by NAME, because `target/surefire-reports/`,
#: `build/test-results/test/` and whatever `--junitxml` was pointed at are
#: conventions that vary by plugin, and a hardcoded path finds none of them.
#: Maven and Gradle both write `TEST-*.xml`; `jest-junit`, `pytest --junitxml`
#: and `gotestsum --junitfile` are conventionally one of the others.
TEST_REPORT_NAMES = ("TEST-*.xml", "junit*.xml", "test-results*.xml",
                     "*-junit.xml")


def tests_reported(where: Path, not_before: float | None = None,
                   depth: int = REPORT_DEPTH) -> int | None:
    """How many test cases a FRESH JUnit-XML report says ran.

    `None` means no report was found, which is a different answer from `0` and
    must stay different: *nobody told us* and *a report says nothing ran* call
    for different sentences, and folding them is how "empty reads as clean"
    gets one more foothold.

    This exists because of a field run. A real repository's `mvn test` exited
    0 having executed **0 of 59** tests — the build's own Dockerfile passes
    `-DskipTests` and surefire discovers nothing locally either — and the
    harness recorded `baseline green` on the strength of the exit code alone.
    That is CLAUDE.md's cardinal rule one level up: *an external binary
    returning nothing at exit 0 is not evidence of absence.* A test command
    exiting 0 having run no tests is not evidence of passing tests.

    Freshness is the same rule the coverage reader applies, and for the same
    reason: a report that predates the command did not come from it. Without
    it, a `target/` left over from last week would vouch for a suite that did
    not run today.

    Counting sums `tests` MINUS `skipped` across every `<testsuite>`: a suite
    that reports 59 tests and skips 59 of them ran nothing, and saying "59"
    there would reintroduce the defect wearing a number.
    """
    where = Path(where)
    if not where.is_dir():
        return None
    total: int | None = None
    for pattern in TEST_REPORT_NAMES:
        for found in where.rglob(pattern):
            try:
                if len(found.relative_to(where).parts) > depth:
                    continue
                if not_before is not None and found.stat().st_mtime < not_before:
                    continue
                root = ET.parse(found).getroot()
            except (OSError, ValueError, ET.ParseError):
                # An unreadable report is not a report. It leaves the answer
                # None rather than 0, so it reads as "could not tell".
                continue
            suites = ([root] if root.tag == "testsuite"
                      else root.iter("testsuite"))
            for suite_el in suites:
                ran = _int_of(suite_el.get("tests", "0"), 0)
                skipped = _int_of(suite_el.get("skipped", "0"), 0)
                total = (total or 0) + max(ran - skipped, 0)
    return total


def failure_names(case: ET.Element) -> set[str]:
    """Every spelling one failing test case can be declared by.

    **A SET, for `sca.names_of`'s reason.** OSV lists whichever advisory id it
    likes first while the person writing the declaration has the CVE in front
    of them; the same is true here, where a runner prints
    `pkg.FooTest.testBar`, a report holds `classname` and `name` separately,
    and the human copies whichever their terminal showed them. A mechanism
    only spellable one way is one nobody can use.

    **`classname` alone is deliberately NOT a spelling.** One declaration
    covering a whole class is an off switch with extra steps - item 98's rule
    about a quarantine written loosely enough to cover findings nobody has
    read - and the same argument rules out matching a parametrised id by its
    stem, so `test_foo` never covers `test_foo[b]`.

    No ecosystem is named. The separators are the ones a JUnit-XML consumer
    already has to handle, not a table of test runners.
    """
    name = (case.get("name") or "").strip()
    where = (case.get("classname") or "").strip()
    if not name:
        return set()
    spellings = {name}
    if where:
        spellings |= {f"{where}.{name}", f"{where}#{name}", f"{where}::{name}"}
    return spellings


def failing_reported(where: Path, not_before: float | None = None,
                     depth: int = REPORT_DEPTH) -> dict[str, set[str]] | None:
    """Which tests a FRESH JUnit-XML report says FAILED: `{id: spellings}`.

    Keyed by ONE canonical id per failing case, because the spellings must
    stay grouped: a flat set cannot say whether `testBar` and
    `pkg.FooTest.testBar` are one failure or two, and a caller reporting
    undeclared failures has to name cases rather than strings.

    The counting sibling of `tests_reported`, walking the same reports under
    the same freshness rule for the same reason - a report that predates the
    command did not come from it.

    **`None` is no report; an EMPTY SET is a report saying nothing failed.**
    That distinction is the whole point of the function, because the caller
    must not read *could not look* as *nothing failed* - which is the fold
    this package keeps having to undo, at P5 in item 110 and one step over in
    `tests_reported`'s own docstring.

    Errors count as failures. A test that errored did not pass, and a
    quarantine that covered failures but not errors would let the same test
    through by changing how it broke.
    """
    where = Path(where)
    if not where.is_dir():
        return None
    found_any = False
    cases: dict[str, set[str]] = {}
    for pattern in TEST_REPORT_NAMES:
        for found in where.rglob(pattern):
            try:
                if len(found.relative_to(where).parts) > depth:
                    continue
                if not_before is not None and found.stat().st_mtime < not_before:
                    continue
                root = ET.parse(found).getroot()
            except (OSError, ValueError, ET.ParseError):
                # Unreadable is not empty, exactly as in `tests_reported`.
                continue
            found_any = True
            for case in root.iter("testcase"):
                if not any(child.tag in ("failure", "error") for child in case):
                    continue
                spellings = failure_names(case)
                if not spellings:
                    continue
                name = (case.get("name") or "").strip()
                holder = (case.get("classname") or "").strip()
                cases[f"{holder}.{name}" if holder else name] = spellings
    return cases if found_any else None


def undeclared(failures: dict[str, set[str]] | None,
               declared: list[str]) -> list[str] | None:
    """The failing tests no declaration names, or None if nothing can be read.

    A case is declared when ANY of its spellings is, because the person
    writing the profile copied whichever spelling their runner printed.

    None rather than `[]` for an unreadable run, which is the same refusal the
    reader above makes: *nobody could look* is not *nothing was found*.
    """
    if failures is None:
        return None
    named = {str(d).strip() for d in declared if str(d).strip()}
    return sorted(ident for ident, spellings in failures.items()
                  if not spellings & named)


def stale_declarations(failures: dict[str, set[str]] | None,
                       declared: list[str]) -> list[str]:
    """Declared names that no failing test answers to.

    Item 98's rule, which `sca_known_findings` has had since it was built and
    this key never did: **a declaration that outlives its finding is a standing
    exception nobody re-examines**, which is exactly what the declaration is
    supposed to replace. Empty when nothing can be read, because an unreadable
    run cannot show a declaration to be stale.
    """
    if failures is None:
        return []
    every = set().union(*failures.values()) if failures else set()
    return sorted({str(d).strip() for d in declared if str(d).strip()} - every)


def baseline_note(exit_code: int | None, tests: int | None) -> str:
    """What a baseline is entitled to claim, given what it actually saw.

    Lives here because `tests_reported` lives here, and the two are one
    statement: an exit code is evidence about the COMMAND, and "green" is a
    claim about TESTS. `intake.baseline` calls this for the single-repository
    case and `baselines` below calls it for every component, so the two cannot
    drift — which they had. The top-level baseline learned to count tests and
    the per-component one, in this file, went on writing `baseline green` off
    the exit code alone: the same defect, one indent level down, in the
    function whose own docstring says a baseline taken one way and compared
    another way is not a baseline.

    Four outcomes, kept apart because folding any two of them is the point:

    * the command did not finish — it decided nothing, and says so;
    * a non-zero exit — red, and P5 compares against it, so the failures are
      pre-existing rather than caused by the change;
    * exit 0 with a fresh report saying **0 tests ran** — NOT green. The
      command succeeded at doing nothing, which is the loudest thing a
      baseline can be asked to notice;
    * exit 0 with no report at all — the count is unknown, and the note says
      so rather than promoting the exit code into a claim about tests.
    """
    if exit_code is None:
        return "the baseline command did not finish, so it decided nothing"
    if exit_code != 0:
        return (f"baseline is RED (exit {exit_code}) before any change - P5 "
                f"compares against this, so the failures are pre-existing")
    if tests is None:
        return ("the test command exited 0, but no JUnit-XML report was "
                "written where this could find one, so NOTHING here shows a "
                "test ran. Point the command at a report (surefire/gradle "
                "write one already; pytest --junitxml, jest-junit and "
                "gotestsum --junitfile all can) or read this as untested")
    if tests == 0:
        return ("the test command exited 0 and its own report says 0 tests "
                "ran - a command that succeeds at doing nothing is not a "
                "green baseline, and P5 would have nothing to compare against")
    return f"baseline green - {tests} test(s) ran"


class SuiteError(RuntimeError):
    """A run the engine cannot make, or a result it cannot judge."""


class Step(NamedTuple):
    component: str
    step: str
    command: str
    #: Where the command came from: "component", "top-level", or "" when none
    #: is declared. A blank command whose source nobody states reads as
    #: detection having failed rather than as nobody having filled it in.
    source: str
    exit_code: int | None
    seconds: float
    verdict: str
    note: str


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ── what a component declares ────────────────────────────────────────────────


def _int_of(value: str, fallback: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return fallback


def _int_or_none(value: str) -> int | None:
    """The same read, with UNREADABLE kept apart from a number.

    `_int_of(x, 0)` folds "there is no floor" into "the floor is zero", and for
    a threshold those are opposite instructions: one has nothing to miss and the
    other passes everything. The two callers stay separate because the JUnit
    attribute readers above genuinely want a default — a `tests=""` attribute is
    a suite that reported no count, and 0 there is measured by the report rather
    than invented by the reader.
    """
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _flow_list(value: str) -> list[str]:
    """`["a", "b"]` — the shape `render_components` writes — as a list.

    The profile reader is line-based by necessity (the harness must read this
    file with no packages installed), so a flow list arrives as its own text.
    """
    text = str(value).strip()
    if not (text.startswith("[") and text.endswith("]")):
        return [text] if text else []
    inner = text[1:-1].strip()
    if not inner:
        return []
    out = []
    for part in inner.split(","):
        item = part.strip().strip('"').strip("'").strip()
        if item:
            out.append(item)
    return out


def declared(target: Path, name: str) -> dict:
    """Everything one component says about building and testing itself.

    A per-component value wins; the top-level key is the fallback and the
    SOURCE is carried, because "this component declares nothing and inherited
    the repository's command" and "someone filled this in" are different facts
    to anyone reading a red result.
    """
    target = Path(target)
    comp = guardrails.component(target, name)
    out: dict = {"component": name, "path": comp.get("path") or ".",
                 "role": comp.get("role", "authoritative")}
    for step, key in COMMAND_KEY.items():
        own = str(comp.get(key, "") or "").strip()
        if own:
            out[step] = (own, "component")
            continue
        top = guardrails.profile_scalar(target, key)
        top = str(top or "").strip()
        out[step] = (top, "top-level" if top else "")
    threshold = comp.get("coverage_threshold")
    if threshold is None or str(threshold).strip() == "":
        threshold = guardrails.profile_scalar(target, "coverage_threshold")
    # **A metric nobody can compute is NEVER zero**, and `_int_of(threshold, 0)`
    # said it was. A `coverage_threshold` the reader cannot turn into a number
    # fell back to 0, `met` became `percent >= 0`, and the coverage gate passed
    # everything — a silent pass in the gate the value exists to arm. Absent and
    # blank stay 0 (nobody declared a floor, so there is nothing to miss); a
    # value that is THERE and unreadable is `None`, which `changed_coverage`'s
    # consumers already read as unmeasurable.
    declared = "" if threshold is None else str(threshold).strip()
    out["coverage_threshold"] = 0 if not declared else _int_or_none(declared)
    if out["coverage_threshold"] is None:
        out["threshold_note"] = (
            f"coverage_threshold {declared!r} is not a number, so the floor it "
            f"declares cannot be applied - unmeasurable, which is a finding "
            f"rather than a threshold of zero")
    # Both quarantines, through ONE reader. They were one field and a second
    # was added; writing the fallback out twice is how this project's most
    # expensive class starts - every sweep hardening the line it is looking at
    # and leaving its neighbour. `sca_known_findings` inherits all three
    # lessons below because it cannot do otherwise.
    for key in (KNOWN_FAILURES, sca_quarantine_key()):
        out[key] = quarantined(target, comp, key)
    return out


#: The test gate's quarantine. The supply-chain gate's name is `sca.py`'s and is
#: asked for rather than spelled here - one home per name.
KNOWN_FAILURES = "known_failures"


def sca_quarantine_key() -> str:
    from tools.review import sca
    return sca.QUARANTINE_KEY


def quarantined(target: Path, comp: dict, key: str) -> list[str]:
    """One component's declared quarantine for `key`, per-component first.

    **The top-level fallback**, which `declared`'s docstring promises and one
    field did not do. `profile_list` rather than `profile_scalar` because a
    quarantine is a LIST and the scalar reader returns None for anything
    starting with `[` - deliberately, and it has a sibling for exactly this
    shape that nothing was calling.

    A single-repository install declares no `components:`, so the component
    `policy.components` synthesises carries name/path/role/vcs and nothing
    else. Without this fallback the key is UNREACHABLE there - measured on a
    real repository, where a red baseline with an unreachable `known_failures`
    meant a repository with one pre-existing failure could never leave P5.

    **Three shapes reach the assignment and each is handled where a reader can
    see it.** A LIST is what `profile_list` returns and is taken as it stands;
    handing it to `_flow_list` would work only by round-tripping through
    `repr`, which is an accident rather than a decision. A STRING is what a
    component block holds, since the profile reader is line-based. And `None`
    is "nobody declared one", which must stay the EMPTY quarantine:
    `_flow_list(None)` answers `["None"]` - one declared entry named None - so
    a red result would read as quarantined against a name nothing can ever
    match. **Absent is not a declaration.**
    """
    own = comp.get(key)
    if own is None or (isinstance(own, str) and not own.strip()):
        own = guardrails.profile_list(target, key)
    if own is None:
        return []
    if isinstance(own, list):
        # **Item 150: a null ITEM is not a declaration either, and this filter
        # could not see one.** `str(None)` is the four-character string
        # `"None"` - truthy - so a bare `-` under the key walked straight
        # through the very comprehension written to drop blanks. The hazard is
        # named eight lines above, in this function's own docstring, and was
        # fixed for the KEY and left for the ITEM: a rule hardened where
        # somebody was looking, which is this project's dominant shape.
        #
        # **MEASURED, and it is a gate defeated by a typo.** A `known_failures`
        # holding one bare `-` read as `['None']`, which is not empty - so item
        # 38's refusal of a red baseline nobody has accounted for never fired,
        # and `judge` fell through to `unmeasurable`, which `stopped` and
        # `_verdict` BOTH ignore. Truly empty, the same baseline answers
        # `new-failures` and P5 halts. One stray hyphen turned a hard refusal
        # into a pass, which is what the sibling test calls a silent pass
        # wearing a verdict.
        #
        # An empty STRING was already dropped here, so this makes the rule
        # uniform rather than introducing a loss: `- ""` and `-` are both
        # somebody writing nothing, and neither is a name anything can match.
        return [str(item).strip() for item in own
                if item is not None and str(item).strip()]
    return _flow_list(own)


def enforcement(target: Path) -> str:
    """The coverage dial, resolved. The constitution names the LOOSEST it may
    be and the profile may only tighten — `policy.tighten_only` refuses the
    other direction, so this only has to resolve, never to police."""
    chosen = str(guardrails.profile_scalar(target, "coverage_enforcement") or "").strip()
    floor = guardrails.load(target).get("coverage_enforcement_floor", ENFORCEMENT[0])
    if chosen not in ENFORCEMENT:
        return str(floor)
    return max((chosen, str(floor)), key=ENFORCEMENT.index)


# ── who breaks if this changes ───────────────────────────────────────────────


def _module_map(edges: list[dict], edge_type: str) -> dict[str, set[str]]:
    """`module id -> the components on the other end of `edge_type`."""
    out: dict[str, set[str]] = {}
    for edge in edges:
        if edge.get("type") != edge_type:
            continue
        owner = edge["src"].split("@", 1)[1] if "@" in edge["src"] else ""
        if owner:
            out.setdefault(edge["dst"], set()).add(owner)
    return out


def direct_dependents(target: Path) -> dict[str, set[str]]:
    """`component -> the components that consume a module it publishes`.

    The join is the module node and nothing else. A module with no publisher
    inside this workspace is third-party and drops out on its own, which is
    why there is no list of vendor prefixes anywhere in this file.
    """
    if not records.graph_ready(target):
        raise SuiteError(
            f"there is no built graph at {records.graph_dir(target)}, so nothing "
            f"knows which component publishes what - no dependent can be computed. "
            f"Build one, or name the components with --changed and accept that only "
            f"they are run")
    _, edges = records.load(target)
    published = _module_map(edges, "DG_BUILT_FROM")
    consumed = _module_map(edges, "DG_DEPENDS_ON")
    out: dict[str, set[str]] = {}
    for module, publishers in published.items():
        for publisher in publishers:
            for user in consumed.get(module, set()):
                if user != publisher:
                    out.setdefault(publisher, set()).add(user)
    return out


def closure(target: Path, changed: list[str]) -> tuple[list[str], list[str]]:
    """Every component the change reaches, dependencies first, plus the cycles.

    A cycle is reported and then broken by name order. A build order that hangs
    is worse than one somebody can argue with, and the argument is printed.
    """
    graph = direct_dependents(target)
    reached: set[str] = set()
    frontier = list(dict.fromkeys(changed))
    while frontier:
        one = frontier.pop()
        if one in reached:
            continue
        reached.add(one)
        frontier += sorted(graph.get(one, set()) - reached)
    cycles: list[str] = []
    ordered: list[str] = []
    state: dict[str, int] = {}

    def visit(node: str, trail: tuple[str, ...]) -> None:
        if state.get(node) == 2:
            return
        if state.get(node) == 1:
            cycles.append(" -> ".join(trail[trail.index(node):] + (node,)))
            return
        state[node] = 1
        for nxt in sorted(graph.get(node, set()) & reached):
            visit(nxt, trail + (node,))
        state[node] = 2
        ordered.append(node)

    for node in sorted(reached):
        visit(node, ())
    # Post-order over publisher -> consumer edges yields consumers first; the
    # build order is the reverse of that, and a dependent built before what it
    # depends on is not a dependent build at all.
    ordered.reverse()
    return ordered, sorted(set(cycles))


# ── what actually changed ────────────────────────────────────────────────────


def changed_files(target: Path, run_id: str) -> tuple[dict[str, list[str]], list[str]]:
    """Per component, the files this run changed, and every reason one could
    not be answered for.

    Derived from the worktree record: the base commit P4 pinned, diffed against
    the tree the work happened in. A component with no recorded base cannot be
    diffed, and that is SAID rather than reported as "nothing changed" — an
    empty answer and an unanswerable one look identical downstream.
    """
    record = trees.load_record(target, run_id).get("components", {})
    out: dict[str, list[str]] = {}
    notes: list[str] = []
    for name, entry in sorted(record.items()):
        where = Path(entry.get("home") or (Path(target) / (entry.get("path") or ".")))
        base = str(entry.get("base") or "")
        if not base:
            notes.append(f"{name}: no base commit was recorded ({entry.get('mode', '?')} "
                         f"mode), so its changed files cannot be determined")
            continue
        if not where.is_dir():
            notes.append(f"{name}: {where} is gone, so nothing can be diffed there")
            continue
        listing = trees.git(where, "diff", "--name-only", base, check=False)
        out[name] = [line.strip().replace("\\", "/")
                     for line in listing.splitlines() if line.strip()]
    if not record:
        notes.append("this run recorded no worktrees, so the engine has no diff to "
                     "work from - name the changed components with --changed")
    return out, notes


# ── running one thing ────────────────────────────────────────────────────────


def run_command(where: Path, command: str,
                timeout: int = TIMEOUT) -> tuple[int | None, float, str]:
    """Execute one declared command. The exit code comes from the process.

    Never through a pipe: reading the exit status of something a suite was
    piped INTO reports the pipe's success, which this harness has done to
    itself and which reads exactly like a green run.

    **A step's record has no stdout key, so the note is where the evidence of
    a killed step has to go.** A suite that hung used to leave `exit_code:
    None` and the sentence "timed out after Ns" - true, and useless to the
    person who has to find out WHY it hung, when the partial output naming
    the test it stopped on was in hand and discarded.
    """
    started = time.time()
    code, out, err, note = cli.run_bounded(
        command, shell=True, cwd=str(where), timeout=timeout,
        timed_out="timed out after {timeout}s",
        could_not_start="did not start: {error}")
    if note and (printed := (err or out).strip()):
        note = f"{note}; it printed: {printed[-2000:]}"
    return code, round(time.time() - started, 1), note


def judge(step: str, exit_code: int | None, baseline_exit: int | None,
          known_failures: list[str], tests: int | None = None,
          baseline_tests: int | None = None,
          failures: dict[str, set[str]] | None = None) -> tuple[str, str]:
    """One step's verdict, relative to what was true before the change.

    **An exit code is evidence about the COMMAND; green is a claim about
    TESTS.** `baseline_note` has said that since item 38's amendment and this
    function - the one that decides whether P5 is passed - went on reading the
    exit code alone, so a suite that collected ZERO tests and exited 0 was a
    green test step. That is item 40's rule (*nothing ran and nothing failed
    are identical output unless something insists they differ*), met at P8 by
    item 47, at P9 by 46 and at P10 by 48, and still open at P5, where suites
    actually run.

    **The verdict is `unmeasurable`, never `red`.** A suite that reports no
    count is not failing; it is unreadable, and `unmeasurable` is already this
    module's word for exactly that one step over, at coverage. It is REPORTED
    and does not stop the phase - `_verdict` and `stopped` escalate only on
    red/new-failures/blocked - because reddening every repository whose runner
    writes no JUnit XML is how a real check gets switched off, which is the
    risk item 40's own module docstring names.

    `tests` and `baseline_tests` default to None so every existing caller keeps
    its meaning: None is *nobody counted*, which is the honest state for a step
    that is not the test step. `failures` follows the same rule for the same
    reason - None is *nobody read a report*, and item 119's whole finding is
    what happens when a caller reads that as *nothing new failed*.
    """
    if exit_code is None:
        return "blocked", "the command did not finish, so it decided nothing"
    if exit_code == 0:
        if step != "test":
            return "green", ""
        if tests == 0:
            return "unmeasurable", (
                "the command exited 0 and its own report says 0 tests ran - it "
                "succeeded at doing nothing, which is not the same as nothing "
                "having failed")
        if tests is None:
            return "unmeasurable", (
                "the command exited 0 and no JUnit-XML report was found, so "
                "nothing here shows a test ran. Point the command at a report "
                "(surefire/gradle write one already; pytest --junitxml, "
                "jest-junit and gotestsum --junitfile all can), or read this "
                "as untested rather than as passed")
        return "green", ""
    if step == "build":
        if baseline_exit is None:
            return "red", (f"exit {exit_code}, and no build baseline was recorded "
                           f"for this component - with nothing to compare "
                           f"against, a red build is a red build")
        if baseline_exit == 0:
            return "new-failures", (f"exit {exit_code} against a build baseline "
                                    f"that was GREEN - this change broke the build")
        # **The baseline IS the declaration, and that is the whole difference
        # from `known_failures`.** A test suite is a SET, so naming WHICH
        # failures are pre-existing is the only way to tell a new one from an
        # old one - which is why item 38 refuses a red test baseline with an
        # empty quarantine. A build is atomic: it built or it did not, and the
        # baseline's own exit code says everything a list could. Inventing a
        # `known_build_failures` here would be a third spelling of one idea
        # (`known_failures`, `sca_known_findings`, and now this) for no
        # information gained; the field can ask for one later if it needs it.
        return "quarantined", (f"exit {exit_code}, and the build was already red "
                               f"at the baseline (exit {baseline_exit}) - this "
                               f"change did not break it. Nothing declares a "
                               f"build quarantine because the baseline is the "
                               f"declaration")
    if step != "test":
        return "red", f"exit {exit_code}"
    if baseline_exit is None:
        return "red", (f"exit {exit_code}, and no baseline was recorded for this "
                       f"component - with nothing to compare against, a red suite "
                       f"is a red suite")
    if baseline_exit == 0:
        if baseline_tests == 0:
            # ZERO, never None. None means nobody passed a count - unknown - and
            # reading it as "the baseline measured nothing" would fold absent
            # into invalid, which is the distinction this package keeps having
            # to make. Only a baseline that COUNTED and counted zero has been
            # shown not to be evidence.
            #
            # The comparison still fires - the suite IS red now - but the
            # sentence must not claim the baseline proved something it never
            # measured.
            return "new-failures", (
                f"exit {exit_code}, and the baseline exited 0 having run 0 tests "
                f"- so it is not evidence that this worked before. Fix the "
                f"baseline's reporting before reading this as a regression")
        return "new-failures", (f"exit {exit_code} against a baseline that was GREEN "
                                f"- this change broke something that worked")
    if not known_failures:
        return "new-failures", (
            f"exit {exit_code}, and the baseline was red too - but `known_failures` "
            f"is empty, so there is no failure set to compare against and 'no NEW "
            f"failures' cannot be judged. Name the pre-existing failures in the "
            f"profile, or fix them")

    # **The declaration is MATCHED, not counted.** Until item 119 this branch
    # returned `quarantined` for any non-empty list, so `known_failures: ["x"]`
    # passed a red suite that failed for entirely different reasons - and a
    # regression the change introduced was structurally invisible, because
    # `judge` was never told WHICH tests failed. One failure and fifty were the
    # same input. Its sibling `sca_known_findings` has matched by full identity
    # since item 98, through the same reader; only the judging diverged.
    missing = undeclared(failures, known_failures)
    if missing is None:
        return "unmeasurable", (
            f"exit {exit_code}, the baseline was red, and "
            f"{len(known_failures)} failure(s) are declared - but no JUnit-XML "
            f"report names which tests failed, so 'no NEW failures' cannot be "
            f"judged. The declaration is counted, not matched, which is not "
            f"evidence. Point the command at a report, or read this as unjudged "
            f"rather than as quarantined")
    stale = stale_declarations(failures, known_failures)
    rot = (f". {len(stale)} declaration(s) matched nothing and may be stale: "
           f"{', '.join(stale[:5])}" if stale else "")
    if missing:
        return "new-failures", (
            f"exit {exit_code}, the baseline was red, and {len(missing)} "
            f"failing test(s) are NOT declared: {', '.join(missing[:5])}"
            f"{rot}")
    return "quarantined", (f"exit {exit_code}, the baseline was red, and every "
                           f"failing test is declared pre-existing: "
                           f"{', '.join(sorted(failures)[:5])}{rot}")


# ── coverage, or the reason there is none ────────────────────────────────────


def _read_cobertura(path: Path) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    for cls in ET.parse(path).getroot().iter("class"):
        name = str(cls.get("filename") or "").replace("\\", "/")
        if not name:
            continue
        covered = total = 0
        for line in cls.iter("line"):
            total += 1
            covered += 1 if _int_of(line.get("hits", "0"), 0) > 0 else 0
        prev = out.get(name, (0, 0))
        out[name] = (prev[0] + covered, prev[1] + total)
    return out


def _read_jacoco(path: Path) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    for package in ET.parse(path).getroot().iter("package"):
        prefix = str(package.get("name") or "").replace(".", "/").strip("/")
        for source in package.iter("sourcefile"):
            name = str(source.get("name") or "")
            if not name:
                continue
            key = f"{prefix}/{name}" if prefix else name
            for counter in source.iter("counter"):
                if counter.get("type") != "LINE":
                    continue
                missed = _int_of(counter.get("missed", "0"), 0)
                covered = _int_of(counter.get("covered", "0"), 0)
                prev = out.get(key, (0, 0))
                out[key] = (prev[0] + covered, prev[1] + covered + missed)
    return out


def _read_lcov(path: Path) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    name = ""
    covered = total = 0
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if line.startswith("SF:"):
            name, covered, total = line[3:].replace("\\", "/").strip(), 0, 0
        elif line.startswith("DA:") and name:
            _, _, rest = line.partition(":")
            parts = rest.split(",")
            if len(parts) >= 2:
                total += 1
                covered += 1 if _int_of(parts[1], 0) > 0 else 0
        elif line == "end_of_record" and name:
            prev = out.get(name, (0, 0))
            out[name] = (prev[0] + covered, prev[1] + total)
            name = ""
    return out


READERS = {"cobertura": _read_cobertura, "jacoco": _read_jacoco, "lcov": _read_lcov}


def find_report(where: Path, not_before: float = 0.0) -> tuple[Path | None, str, str]:
    """The newest coverage report under a component, its format, and why not.

    A report older than the coverage command's start did not come from it.
    Believing one is how item 29's smoke gate vouched for a runner that wrote
    nothing, and a stale coverage figure is the same failure with a percentage
    attached.
    """
    where = Path(where)
    if not where.is_dir():
        return None, "", f"{where} is not a directory"
    best: tuple[float, Path, str] | None = None
    stale = 0
    for name, kind in REPORT_READERS.items():
        for found in where.rglob(name):
            try:
                depth = len(found.relative_to(where).parts)
            except ValueError:
                continue
            if depth > REPORT_DEPTH:
                continue
            try:
                stamp = found.stat().st_mtime
            except OSError:
                continue
            if not_before and stamp < not_before:
                stale += 1
                continue
            if best is None or stamp > best[0]:
                best = (stamp, found, kind)
    if best:
        return best[1], best[2], ""
    if stale:
        return None, "", (f"{stale} coverage report(s) under {where.name} all predate "
                          f"the coverage command, so none of them came from it")
    return None, "", (f"no coverage report under {where.name} - looked for "
                      + ", ".join(sorted(REPORT_READERS)))


def match_file(changed: str, reported: dict[str, tuple[int, int]]) -> str:
    """The report entry that is this changed file, or "".

    Matched by path SUFFIX, because the three formats root their paths
    differently — JaCoCo at the package root, Cobertura at the source root,
    LCOV wherever the tool ran. An ambiguous match returns nothing: a coverage
    figure attributed to the wrong file is worse than a missing one.
    """
    want = changed.replace("\\", "/").strip("/")
    hits = [key for key in reported
            if key == want or key.endswith("/" + want) or want.endswith("/" + key)]
    return hits[0] if len(hits) == 1 else ""


def changed_coverage(where: Path, files: list[str],
                     not_before: float = 0.0) -> dict:
    """The percentage of changed lines covered, or the reason there is none."""
    if not files:
        return {"percent": None, "matched": 0, "of": 0,
                "why": "this component's changed files are not known, so there is "
                       "nothing to measure coverage OF"}
    report, kind, why = find_report(where, not_before)
    if report is None:
        return {"percent": None, "matched": 0, "of": len(files), "why": why}
    try:
        reported = READERS[kind](report)
    except (ET.ParseError, OSError, ValueError) as exc:
        return {"percent": None, "matched": 0, "of": len(files),
                "why": f"{report.name} is a {kind} report that will not parse: {exc}"}
    covered = total = matched = 0
    missing: list[str] = []
    for one in files:
        key = match_file(one, reported)
        if not key:
            missing.append(one)
            continue
        matched += 1
        covered += reported[key][0]
        total += reported[key][1]
    row = {"report": str(report), "format": kind, "matched": matched,
           "of": len(files), "missing": missing[:10], "why": ""}
    if not matched:
        row["percent"] = None
        row["why"] = (f"{report.name} names none of the {len(files)} changed file(s), "
                      f"so this change is unmeasured rather than uncovered")
        return row
    if not total:
        row["percent"] = None
        row["why"] = (f"{report.name} reports {matched} changed file(s) with no "
                      f"measurable lines at all")
        return row
    row["percent"] = round(100.0 * covered / total, 1)
    if missing:
        row["why"] = (f"{len(missing)} changed file(s) appear in no report and are "
                      f"excluded from the figure rather than counted as zero")
    return row


# ── the whole phase ──────────────────────────────────────────────────────────


def baselines(target: Path, names: list[str] | None = None,
              run: bool = False) -> dict:
    """Each component's suite, recorded, and executed on request.

    P0 calls this; P5 compares against what it wrote. One definition, because a
    baseline taken one way and compared another way is not a baseline.
    """
    target = Path(target)
    out: dict = {"schema": SCHEMA_VERSION, "at": _now(), "components": {}}
    for comp in guardrails.components(target):
        name = comp["name"]
        if names is not None and name not in names:
            continue
        spec = declared(target, name)
        command, source = spec["test"]
        row = {"command": command, "source": source, "ran": False,
               "exit_code": None, "seconds": None, "tests": None,
               "known_failures": spec["known_failures"],
               # Item 111. P0 recorded the TEST command and never the build, so
               # P5 judged the build absolutely: a repository whose build was
               # already red had every future change blamed for it, forever, and
               # the only way past was `--force`. The build is the last step in
               # this engine that judged without a baseline to judge against.
               "build": _build_baseline(target, spec, run)}
        if not command:
            row["note"] = (f"{name} declares no test_command and the profile has no "
                           f"top-level one - P5 has nothing to compare against")
        elif not run:
            row["note"] = "recorded but NOT run - pass --run to execute it"
        else:
            where = Path(target) / (spec["path"] or ".")
            started = time.time()
            code, seconds, problem = run_command(where, command)
            counted = tests_reported(where, not_before=started)
            row.update(ran=problem == "", exit_code=code, seconds=seconds,
                       tests=counted,
                       note=problem or baseline_note(code, counted))
        out["components"][name] = row
    return out


def _build_baseline(target: Path, spec: dict, run: bool) -> dict:
    """One component's BUILD baseline, recorded always and executed on request.

    Same shape and same rules as the test baseline beside it, minus the count:
    an exit code IS the whole evidence about a build, because a build is atomic
    - it either produced its artifacts or it did not - which is exactly why
    item 110's test count has no analogue here and asking for one would report
    every green build as unmeasurable.
    """
    command, source = spec["build"]
    row = {"command": command, "source": source, "ran": False,
           "exit_code": None, "seconds": None}
    if not command:
        row["note"] = ("no build_command declared - P5 has nothing to compare "
                       "a build failure against")
        return row
    if not run:
        row["note"] = "recorded but NOT run - pass --run to execute it"
        return row
    where = Path(target) / (spec["path"] or ".")
    code, seconds, problem = run_command(where, command)
    row.update(ran=problem == "", exit_code=code, seconds=seconds,
               note=problem or ("baseline build is GREEN" if code == 0 else
                                f"baseline build is RED (exit {code}) before any "
                                f"change - P5 compares against this, so the "
                                f"failure is pre-existing rather than caused by "
                                f"the change"))
    return row


def recorded_baselines(target: Path, run_id: str) -> dict:
    """P0's per-component baselines, read back off the intake artifact."""
    # The NAME from the module that writes the file. Spelled here as a literal,
    # this was the SCA drift waiting to happen a second time: `intake.py` owns
    # `INTAKE_JSON` and its own docstring records that the artifact was renamed
    # once already.
    from tools.sdlc import intake as adapter

    path = ledger.run_dir(target, run_id) / adapter.INTAKE_JSON
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    base = data.get("baseline") or {}
    return base.get("components") or {}


def approved_scope(target: Path, run_id: str) -> tuple[set[str], str]:
    """The components the approved design said it would change.

    Read off `30-design.md` through item 64's own resolver, so "what was
    scoped" and "what fired an ADR trigger" cannot be two different readings
    of one document.
    """
    try:
        changes = records.change_set(target, run_id)
    except (records.AdrError, guardrails.PolicyError, RuntimeError) as exc:
        return set(), f"the design's change set could not be read: {exc}"
    if not changes.resolved:
        return set(), ("the design named no file this workspace contains, so there "
                       "is no approved scope to compare breakage against")
    return {component for component, _ in changes.resolved}, ""


def execute(target: Path, run_id: str, changed: list[str] | None = None,
            steps: tuple[str, ...] = STEPS, dry_run: bool = False) -> dict:
    """Run the reached components, dependencies first, and judge each result."""
    target = Path(target)
    diffs, notes = changed_files(target, run_id)
    seeds = list(changed) if changed is not None else \
        sorted(name for name, files in diffs.items() if files)
    known = {c["name"] for c in guardrails.components(target)}
    if unknown := sorted(set(seeds) - known):
        raise SuiteError(f"no component named {', '.join(map(repr, unknown))} - one "
                         f"of {', '.join(sorted(known))}")
    if records.graph_ready(target):
        ordered, cycles = closure(target, seeds)
    else:
        ordered, cycles = sorted(seeds), []
        notes.append("there is no built graph, so no dependent was computed - only "
                     "the components named as changed were run")
    base_rows = recorded_baselines(target, run_id)
    scoped, scope_note = approved_scope(target, run_id)
    if scope_note:
        notes.append(scope_note)
    dial = enforcement(target)

    out: dict = {"schema": SCHEMA_VERSION, "run": run_id, "at": _now(),
                 "seeds": seeds, "order": ordered, "cycles": cycles,
                 "notes": notes, "enforcement": dial, "dry_run": dry_run,
                 "steps": [], "coverage": {}, "unscoped": [], "verdict": "green"}
    if dry_run:
        # Nothing ran, so nothing passed. `green` here is the false green this
        # whole module is written against, printed by the harness itself.
        out["verdict"] = "not-run"
        return out

    for name in ordered:
        spec = declared(target, name)
        entry = trees.load_record(target, run_id)["components"].get(name, {})
        where = Path(entry.get("home") or (target / (spec["path"] or ".")))
        base_exit = (base_rows.get(name) or {}).get("exit_code")
        base_tests = (base_rows.get(name) or {}).get("tests")
        # The BUILD's baseline is its own - reading the test baseline for it
        # would compare a build against a suite, which is how "the build broke"
        # and "a test broke" become one sentence.
        base_build = ((base_rows.get(name) or {}).get("build") or {}).get("exit_code")
        blocked = False
        for step in steps:
            if step == "coverage":
                continue
            command, source = spec[step]
            if not command:
                out["steps"].append(Step(name, step, "", source, None, 0.0,
                                         "not-declared",
                                         f"{name} declares no {COMMAND_KEY[step]}"
                                         )._asdict())
                continue
            if blocked:
                out["steps"].append(Step(name, step, command, source, None, 0.0,
                                         "blocked", f"{name}'s build did not pass, "
                                         f"so its {step} result would mean nothing"
                                         )._asdict())
                continue
            started = time.time()
            code, seconds, problem = run_command(where, command)
            # Counted the same way the baseline was, by the one counter this
            # package has - a baseline taken one way and compared another way
            # is not a baseline, which is this module's own rule.
            counted = (tests_reported(where, not_before=started)
                       if step == "test" else None)
            # Read from the SAME reports, under the same freshness bound, in
            # the same walk the count came from - a declaration matched against
            # a different run's report would not be a comparison.
            broke = (failing_reported(where, not_before=started)
                     if step == "test" else None)
            against = base_build if step == "build" else base_exit
            verdict, note = judge(step, code, against, spec["known_failures"],
                                  tests=counted, baseline_tests=base_tests,
                                  failures=broke)
            out["steps"].append(Step(name, step, command, source, code, seconds,
                                     verdict, problem or note)._asdict())
            if step == "build" and verdict != "green":
                blocked = True

        if "coverage" in steps:
            command, source = spec["coverage"]
            started = time.time()
            if command and not blocked:
                code, seconds, problem = run_command(where, command)
                out["steps"].append(Step(name, "coverage", command, source, code,
                                         seconds, "green" if code == 0 else "red",
                                         problem)._asdict())
            elif not command:
                out["steps"].append(Step(name, "coverage", "", source, None, 0.0,
                                         "not-declared",
                                         f"{name} declares no coverage_command, so "
                                         f"its coverage is unmeasurable - which is a "
                                         f"finding, not a pass")._asdict())
            row = changed_coverage(where, diffs.get(name, []),
                                   started if command and not blocked else 0.0)
            row["threshold"] = spec["coverage_threshold"]
            # A threshold that could not be read leaves `met` UNKNOWN, the same
            # answer an unmeasured percentage gets, and says which of the two it
            # was — `why` is what the P5 reader prints, so a run whose floor is
            # unreadable must not read as a run whose coverage is unreadable.
            if spec["coverage_threshold"] is None:
                row["met"] = None
                row["why"] = spec.get("threshold_note", "") or row.get("why", "")
            else:
                row["met"] = (None if row.get("percent") is None
                              else row["percent"] >= spec["coverage_threshold"])
            out["coverage"][name] = row

    # Build and test only. A coverage command that will not run is a
    # MEASUREMENT failure — it is already reported as unmeasurable, and calling
    # it breakage proposed widening the approved scope because a tool was
    # missing. The field run did exactly that to a component whose build and
    # tests were both green.
    broke = {row["component"] for row in out["steps"]
             if row["step"] != "coverage"
             and row["verdict"] in ("red", "new-failures", "blocked")}
    out["unscoped"] = sorted(broke - scoped) if scoped else []
    out["verdict"] = _verdict(out)
    return out


def stopped(result: dict) -> list[tuple[str, str]]:
    """Each component that did not come back clean, with the verdict that
    stopped it. Kept as a pair because "its tests regressed" and "its build
    failed so its tests never ran" are different sentences, and the field run
    reported the second one as the first."""
    worst: dict[str, str] = {}
    for row in result.get("steps", []):
        if row["step"] == "coverage" or row["verdict"] not in (
                "red", "new-failures", "blocked"):
            continue
        if row["verdict"] == "new-failures" or row["component"] not in worst:
            worst[row["component"]] = row["verdict"]
    return sorted(worst.items())


def _verdict(result: dict) -> str:
    kinds = {row["verdict"] for row in result["steps"]
             if row["step"] != "coverage"}
    if kinds & {"new-failures", "blocked"}:
        return "new-failures"
    if "red" in kinds or any(row["verdict"] == "red" and row["step"] == "coverage"
                             for row in result["steps"]):
        return "red"
    if result["enforcement"] == "hard" and any(
            row.get("met") is not True for row in result["coverage"].values()):
        return "coverage-short"
    return "green"


def save(target: Path, run_id: str, result: dict) -> Path:
    path = ledger.run_dir(target, run_id) / RESULT
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8", newline="\n")
    ledger.record_artifact(Path(target), run_id, RESULT)
    return path


def load_result(target: Path, run_id: str) -> dict:
    path = ledger.run_dir(target, run_id) / RESULT
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise SuiteError(f"{path} is not readable JSON: {exc}") from None


def sca_result(target: Path, run_id: str) -> dict:
    """The SCA gate's document for this run, or {} when it was never run."""
    from tools.review import sca as scanner

    path = ledger.run_dir(target, run_id) / scanner.RESULT_NAME
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise SuiteError(f"{path} is not readable JSON: {exc}") from None


def sca_problems(target: Path, run_id: str) -> list[str]:
    """What the SCA gate makes P5 owe.

    The severity judgement is `loop.assess`'s, against the `block_on` the run is
    actually governed by — one implementation of "is this a blocker", so a CVE
    and an agent finding are weighed by the same rule. A vulnerability OSV has
    not scored arrives ungraded and holds the phase open, which is the same
    answer the review loop gives an ungraded finding.

    A `not-run` gate (no vendored binary) and a component with no manifest are
    REPORTED, never owed: neither is work P5 can do. The report is `sca.py`'s
    own exit 3 at the CLI boundary — NOT a line in this list, and the
    difference is worth stating because a reader looking for the report inside
    this function will not find one. Everything this function returns is owed;
    a gate P5 cannot act on does not belong in it.
    """
    from tools.review import loop as review

    doc = sca_result(target, run_id)
    if not doc:
        return [f"the engine ran and no SCA gate result is recorded - P5 owes one "
                f"(tools/review/sca.py --target . --out <run folder>), and a supply chain nobody "
                f"scanned is not a clean one"]
    from tools.review import prepass

    problems: list[str] = []
    # The shared rule, not a fourth restatement of it: `doc.get("tools", [])`
    # was the present-null pattern `prepass.tool_rows` exists to replace, and
    # the one-status deny-list beside it could not see `not-run`.
    for tool in prepass.unfired(doc):
        if tool.get("status") != "error":
            continue          # not-run / nothing-scanned: sca.py's exit 3 owns it
        why = tool.get("detail") or "no reason recorded"
        problems.append(f"the SCA gate errored: {why} - a scanner that "
                        f"failed has not found nothing")
    # A scan that WROTE to the tree it audited is owed here whatever it found.
    # Recorded on the row by `sca.scan_one` and read here, because a field
    # scan moved two dependency versions in a module the change never touched
    # and a `git add -A` committed them into it. A finding nobody reads is the
    # defect this project keeps meeting; this one has a reader.
    for tool in prepass.tool_rows(doc) or []:
        for row in tool.get("per_component") or []:
            if not isinstance(row, dict) or not row.get("mutated"):
                continue
            paths = ", ".join(str(p) for p in row["mutated"][:5])
            problems.append(
                f"the SCA scan wrote to {row.get('component', '?')}: {paths} - "
                f"restore them, because a gate that edits what it audits puts "
                f"changes nobody made into whatever is committed next")
    from tools.review import sca

    # The declared quarantine, applied BEFORE severity is weighed. A finding
    # the repository has declared is not owed; one it has not is owed whatever
    # else is declared, which is the property that keeps this gate alive. The
    # identity carries no path on purpose - one CVE in one package version is
    # ONE decision however many manifests resolve it, and on the repository
    # that motivated this, 85 blocking findings were 67.
    #
    # **What is silenced here is COUNTED, by `sca_silenced`, and P5's SOP makes
    # the evidence state it.** It cannot be said in this list: everything this
    # function returns is owed, and a count of accepted findings is not work -
    # putting it here would hold the phase open for the ordinary case the
    # declaration exists to allow. But a gate that quietly stops reporting
    # findings is the thing this project treats as worst, so it is not allowed
    # to be invisible either.
    # Read from the PROFILE, never from the document's scope. Keying this to
    # the components an SCA run says it covered would mean a name the profile
    # spells differently silently drops the declaration - and a quarantine that
    # silently does not apply is the same defect as one that silently does,
    # arriving from the other side. `quarantined` already gives each component
    # the top-level fallback, which is the only reachable key in a
    # single-component install and is the shape that motivated this.
    entries = sorted({e for comp in guardrails.components(target)
                      for e in quarantined(target, comp, sca.QUARANTINE_KEY)})

    # Filter the DOCUMENT, then flatten. The other order silently matches
    # nothing: `from_prepass` builds rows with a fixed key set and the identity
    # does not survive it.
    split = sca.filter_document(doc, entries)
    doc = split["doc"]
    for bad in sca.malformed(entries):
        problems.append(
            f"the {sca.QUARANTINE_KEY} entry {bad!r} is not "
            f"{sca.QUARANTINE_ARITY} tokens (ecosystem package version "
            f"advisory), so it names no finding - an entry nobody can match "
            f"looks exactly like one that matched, and the difference is "
            f"whether a vulnerability is being watched")
    verdict = review.assess(review.from_prepass(doc), review.block_on_of(target))
    for row in verdict["open"]:
        grade = row["severity"] or "ungraded"
        problems.append(f"SCA [{grade}] {row['path']}: {row['message'] or row['id']}")
    # Stale entries are REPORTED and never owed. A vulnerability that is gone is
    # not work P5 can do, and reddening the phase for it is how a real check
    # gets switched off - but a declaration outliving its finding is precisely
    # the standing exception this mechanism replaces, so it does not go unsaid.
    for entry in split["stale"]:
        problems.append(
            f"the {sca.QUARANTINE_KEY} entry {entry!r} matched nothing in this "
            f"scan - the finding is fixed, the dependency moved, or the entry "
            f"was never right. Remove it: a quarantine that outlives its "
            f"finding is the standing exception it was meant to replace")
    return problems



def sca_silenced(target: Path, run_id: str) -> dict:
    """What the declared supply-chain quarantine removed from this run's gate.

    Separate from `sca_problems` because the two answer different questions and
    folding them would break the contract that everything `sca_problems`
    returns is OWED. This is the other half of zero-silent-drops: the findings
    a declaration accepted are counted and nameable, so "the gate reported
    nothing" and "the gate was told to report nothing" are never the same
    sentence to whoever reads P5's evidence.
    """
    from tools.review import sca

    doc = sca_result(target, run_id)
    if not doc:
        return {"declared": [], "quarantined": 0, "advisories": [], "stale": []}
    entries = sorted({e for comp in guardrails.components(target)
                      for e in quarantined(target, comp, sca.QUARANTINE_KEY)})
    split = sca.filter_document(doc, entries)
    advisories = sorted({sca.entry_for(row) or "(unnameable)"
                         for row in split["quarantined"]})
    return {"declared": entries, "quarantined": len(split["quarantined"]),
            "advisories": advisories, "stale": split["stale"]}


def scope_add_text(result: dict) -> str:
    """The proposal an un-scoped breakage produces.

    A draft for a human, not a decision: the components are named with what
    broke in them, and the reader takes it back through the design gate. A
    breakage quietly fixed outside the approved scope is a change nobody
    reviewed arriving under a change somebody did.
    """
    lines = [f"# Scope-add proposal — run {result.get('run', '?')}", "",
             "P5 built the closure of the approved change and found breakage in "
             "component(s) the approved design does not cover. This is a proposal "
             "to widen the scope, and it belongs at **G1**: fixing it here would "
             "put unreviewed work inside a reviewed change.", ""]
    for name in result.get("unscoped", []):
        lines.append(f"## {name}")
        for row in result.get("steps", []):
            if row["component"] == name and row["verdict"] in (
                    "red", "new-failures", "blocked"):
                lines.append(f"- **{row['step']}** — {row['verdict']}: "
                             f"{row['note'] or 'exit ' + str(row['exit_code'])}")
        lines.append("")
    lines += ["## Decision", "",
              "- [ ] widen the scope: this component re-enters at P3 with the rest",
              "- [ ] out of scope: record why the breakage is not this run's to fix",
              ""]
    return "\n".join(lines)


def obligations(target: Path, run_id: str) -> list[str]:
    """What P5 still owes on account of what the engine found.

    Reached through `stage.verify`, which is how the conditional artifact stops
    being a suggestion — the same seam item 64 used for the ADR. Silence here
    would mean a run could leave P5 with un-scoped breakage recorded in a JSON
    file nobody is required to act on.
    """
    target = Path(target)
    result = load_result(target, run_id)
    if not result:
        return []
    problems: list[str] = sca_problems(target, run_id)
    folder = ledger.run_dir(target, run_id)
    if result.get("unscoped") and not (folder / SCOPE_ADD).is_file():
        problems.append(
            f"{', '.join(result['unscoped'])} broke and the approved design does not "
            f"cover {'them' if len(result['unscoped']) > 1 else 'it'} - that is a "
            f"scope-add for G1, not a fix to make here. Write {SCOPE_ADD} "
            f"(tools/sdlc/suite.py --scope-add)")
    if result.get("verdict") == "new-failures":
        named = ", ".join(f"{name} ({verdict})" for name, verdict in stopped(result))
        problems.append(
            f"the engine did not get a clean result from {named} - P5 does not finish "
            f"on a regression or on a build nobody fixed; the work goes back to P4 "
            f"and re-enters G3")
    if result.get("enforcement") == "hard":
        for name, row in sorted(result.get("coverage", {}).items()):
            if row.get("met") is True:
                continue
            short = (f"{row['percent']}% against a threshold of {row['threshold']}%"
                     if row.get("percent") is not None
                     and row.get("threshold") is not None
                     else f"unmeasurable: {row.get('why', 'no reason recorded')}")
            problems.append(f"{name}'s changed-file coverage is {short}, and "
                            f"coverage_enforcement is hard")
    return problems


# ── CLI ──────────────────────────────────────────────────────────────────────


def _render(result: dict) -> str:
    lines = [f"seeds: {', '.join(result['seeds']) or '(none)'}",
             f"order: {' -> '.join(result['order']) or '(none)'}"]
    if result["cycles"]:
        lines.append("CYCLES (broken by name order, and worth fixing): "
                     + "; ".join(result["cycles"]))
    for row in result["steps"]:
        mark = f"{row['verdict']:<13}"
        source = f" [{row['source']}]" if row["source"] else ""
        lines.append(f"  {mark} {row['component']} {row['step']}{source}"
                     + (f" ({row['seconds']}s)" if row["seconds"] else ""))
        if row["note"]:
            lines.append(f"      {row['note']}")
    for name, row in sorted(result["coverage"].items()):
        figure = (f"{row['percent']}% of {row['matched']}/{row['of']} changed file(s), "
                  f"threshold {row['threshold']}%"
                  if row.get("percent") is not None else "UNMEASURABLE")
        lines.append(f"  coverage      {name}: {figure}")
        if row.get("why"):
            lines.append(f"      {row['why']}")
    for note in result["notes"]:
        lines.append(f"  note: {note}")
    lines.append(f"verdict: {result['verdict']} (coverage_enforcement: "
                 f"{result['enforcement']})")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", default=".", metavar="DIR")
    ap.add_argument("--run", metavar="ID", help="the run id")
    ap.add_argument("--changed", metavar="NAMES",
                    help="comma-separated components to seed from, instead of the "
                         "worktree diffs")
    ap.add_argument("--baseline", action="store_true",
                    help="record each component's suite (P0's job)")
    ap.add_argument("--run-commands", action="store_true",
                    help="with --baseline, actually execute them")
    ap.add_argument("--plan", action="store_true",
                    help="the closure and the order, running nothing")
    ap.add_argument("--execute", action="store_true", help="build, test, measure")
    ap.add_argument("--scope-add", action="store_true",
                    help=f"write {SCOPE_ADD} from the recorded result")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    target = Path(args.target).resolve()
    seeds = ([s.strip() for s in args.changed.split(",") if s.strip()]
             if args.changed else None)
    try:
        if args.baseline:
            data = baselines(target, run=args.run_commands)
            print(json.dumps(data, indent=2, sort_keys=True) if args.json else
                  "\n".join(f"  {name}: {row['command'] or '(none)'} - {row['note']}"
                            for name, row in sorted(data["components"].items())))
            return 0
        if not args.run:
            ap.error("--run is required for --plan, --execute and --scope-add")
        if args.scope_add:
            result = load_result(target, args.run)
            if not result:
                print(f"refused: run {args.run} has no {RESULT} to propose from",
                      file=sys.stderr)
                return 2
            if not result.get("unscoped"):
                print("nothing to propose: no breakage outside the approved scope")
                return 0
            path = ledger.run_dir(target, args.run) / SCOPE_ADD
            path.write_text(scope_add_text(result), encoding="utf-8", newline="\n")
            ledger.record_artifact(target, args.run, SCOPE_ADD)
            print(f"wrote {path}")
            return 0
        result = execute(target, args.run, seeds, dry_run=args.plan)
        if not args.plan:
            save(target, args.run, result)
        print(json.dumps(result, indent=2, sort_keys=True) if args.json
              else _render(result))
        # A plan succeeded at planning. Only an executed run has a verdict to
        # fail on, which is the same distinction `not-run` makes in the output.
        return 0 if args.plan or result["verdict"] == "green" else 1
    except (SuiteError, stages.StageError, guardrails.PolicyError,
            trees.WorktreeError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    except (RuntimeError, OSError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
