"""The door and the console rule, tested where they now live.

Both were copied code before: `use_utf8` existed twice and was missing from
the three CLIs that needed it most, and the door was written out twice
verbatim in the commit that introduced it — the same restate-the-rule defect
that commit was otherwise about. A shared implementation is only an
improvement if it is held to the behaviour both copies were supposed to have,
so it is pinned here rather than in either caller.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools import cli  # noqa: E402


class _Console:
    """A stream that refuses to be reconfigured and cannot spell everything —
    a cp1252 console, which is what a Windows runner actually has."""

    encoding = "cp1252"

    def __init__(self):
        self.written = []

    def reconfigure(self, **_):
        raise OSError("this stream cannot be reconfigured")

    def write(self, text):
        text.encode(self.encoding)      # raises exactly as the real one does
        self.written.append(text)
        return len(text)

    def flush(self):
        pass


def test_the_door_turns_an_unexpected_failure_into_the_callers_code():
    def boom(argv):
        raise RuntimeError("a disk error, say")

    assert cli.door(boom, None, "the pre-pass", 3) == 3


def test_the_door_says_what_failed(monkeypatch):
    """Deliberately not a blanket except that hides — item 37 recorded where
    that leads. Only the exit code is decided; the failure is named."""
    def boom(argv):
        raise KeyError("a-key")

    out = []
    monkeypatch.setattr(cli, "say", lambda msg, stream=None: out.append(msg))
    cli.door(boom, None, "the SCA gate", 3)
    assert "KeyError" in out[0] and "the SCA gate" in out[0]


def test_a_usage_exit_passes_straight_through():
    """argparse refuses an unknown flag with SystemExit(2). A door that
    swallowed it would answer a typo with "the gate could not look"."""
    def usage(argv):
        raise SystemExit(2)

    with pytest.raises(SystemExit) as exit_info:
        cli.door(usage, None, "the pre-pass", 3)
    assert exit_info.value.code == 2


def test_an_interrupt_is_could_not_look_not_a_clean_answer(monkeypatch):
    """KeyboardInterrupt is a BaseException, so it escaped the first version
    of this guard — and an escaping one exits 1 on CPython, which in every
    caller here means a substantive answer. A Ctrl-C during a 300 s scanner
    subprocess was delivered as the benign findings exit."""
    def interrupted(argv):
        raise KeyboardInterrupt

    out = []
    monkeypatch.setattr(cli, "say", lambda msg, stream=None: out.append(msg))
    assert cli.door(interrupted, None, "the pre-pass", 3) == 3
    assert "interrupted" in out[0]


def test_the_body_gets_the_arguments_and_its_answer_is_returned():
    """The negative control: the door must be transparent when nothing fails."""
    seen = []

    def body(argv):
        seen.append(argv)
        return 1

    assert cli.door(body, ["--x"], "the pre-pass", 3) == 1
    assert seen == [["--x"]]


def test_say_survives_a_console_that_cannot_spell_the_message():
    """The door's whole job is to report a failure, so the report must not be
    able to fail. This is the case that made the guard forge the exit it was
    added to prevent: an exception message carrying a character cp1252 has no
    encoding for, printed by a module that never reconfigured stdout."""
    console = _Console()
    cli.say("!! the pre-pass did not finish: OSError: cannot read 中文/repo",
            stream=console)
    assert console.written and "did not finish" in "".join(console.written)


def test_use_utf8_never_raises_on_a_stream_that_refuses():
    """Idempotent and never fatal: a stream already wrapped, or a test's
    capture object, is left alone rather than taking the CLI down at import."""
    cli.use_utf8(_Console())


@pytest.mark.parametrize("module", [
    "tools/review/prepass.py", "tools/review/sca.py", "tools/review/hitl.py",
    "tools/review/loop.py", "tools/provider_smoke.py",
])
def test_every_cli_that_prints_a_failure_reconfigures_its_console(module):
    """CLAUDE.md: "Windows is a first-class target ... Reconfigure stdout to
    UTF-8." Three of these five had no such call, and all five print
    arbitrary text — an exception message, a runner's output, a review row —
    so on a cp1252 console the print itself raised and Python exited 1, which
    each of them defines as a substantive answer."""
    src = (ROOT / module).read_text(encoding="utf-8")
    assert "cli.use_utf8()" in src, f"{module} does not reconfigure its console"


# ── the record reader ────────────────────────────────────────────────────────


def test_a_record_that_is_not_there_is_empty_not_an_error(tmp_path):
    """Every caller already treated "nothing recorded yet" as ordinary, and a
    reader that raised would put an `is_file()` guard back at all nine."""
    assert cli.jsonl_rows(tmp_path / "never-written.jsonl") == []


def test_blank_lines_are_skipped_and_numbering_is_the_file_s_own(tmp_path):
    """1-based, counting blanks: the number has to be the one a person can go
    and look at, or naming a damaged line is worse than saying nothing."""
    p = tmp_path / "r.jsonl"
    p.write_text('\n{"a": 1}\n\n   \n{"b": 2}\n', encoding="utf-8")
    got = cli.jsonl_rows(p)
    assert [(ln.number, ln.row) for ln in got] == [(2, {"a": 1}), (5, {"b": 2})]


def test_a_line_that_does_not_parse_is_kept_in_place_and_named(tmp_path):
    """Order is preserved and damage stays where it was, because for
    `approve.read_chain` position IS the meaning: a reader that returned the
    good rows and the bad numbers separately would have re-chained across the
    hole and called the ledger intact."""
    p = tmp_path / "r.jsonl"
    p.write_text('{"a": 1}\n{ broken\n{"c": 3}\n', encoding="utf-8")
    got = cli.jsonl_rows(p)
    assert [ln.row for ln in got] == [{"a": 1}, None, {"c": 3}]
    assert got[1].number == 2 and got[1].problem == cli.UNPARSEABLE


@pytest.mark.parametrize("literal", ['"a string"', "7", "null", "[1, 2]",
                                     "true"])
def test_a_line_that_parses_to_something_that_is_not_an_object_is_damage(
        tmp_path, literal):
    """`guard.read_log` was the one reader that never asked this, so a log line
    holding a bare JSON string came back as a `str` and every caller's `.get`
    raised. Both kinds of damage are damage; they are named apart because
    `provider_smoke.verify` says something different about each."""
    p = tmp_path / "r.jsonl"
    p.write_text(literal + "\n", encoding="utf-8")
    got = cli.jsonl_rows(p)
    assert got[0].row is None and got[0].problem == cli.NOT_AN_OBJECT


def test_one_bad_byte_does_not_take_the_whole_record_down(tmp_path):
    """The decode is INSIDE the reader and `errors="replace"` besides. Written
    the obvious way the decode happens before the loop and outside every
    guard, and `UnicodeDecodeError` is a `ValueError`, so it slipped through
    guards written for bad JSON. It took `provider_smoke.read_record` down
    once and `adr.dismissals` a second time."""
    p = tmp_path / "r.jsonl"
    p.write_bytes(b'{"a": 1}\n\xff\xfe not utf-8\n{"c": 3}\n')
    got = cli.jsonl_rows(p)
    assert [ln.row for ln in got] == [{"a": 1}, None, {"c": 3}]


def test_problem_is_empty_exactly_when_a_row_was_read(tmp_path):
    """`if line.row is None` and `if line.problem` must be the same question,
    or a caller that picked the wrong one would silently disagree with its
    neighbour — which is the whole reason this reader exists."""
    p = tmp_path / "r.jsonl"
    p.write_text('{"a": 1}\n{ broken\n"scalar"\n', encoding="utf-8")
    for ln in cli.jsonl_rows(p):
        assert (ln.row is None) == bool(ln.problem)


# ── the console rule, swept rather than listed ───────────────────────────────
#
# The guard this replaces was a hand-written list of the five modules the
# commit that created `cli.use_utf8` happened to be looking at. It passed, and
# NINETEEN other modules were unswept: fourteen carrying a verbatim copy of the
# block, and five with no console rule at all — `pack.py` among them, which
# renders arbitrary repository paths and code excerpts. A list of what to check
# goes stale the first time something is added; a sweep does not.


def _shipped() -> list[Path]:
    """Every shipped module — the installer and everything under `tools/`,
    tests excluded (they do not print to an operator's console)."""
    out = [ROOT / "sdlc-init.py"]
    out += [p for p in sorted((ROOT / "tools").rglob("*.py"))
            if "tests" not in p.parts and "__pycache__" not in p.parts]
    return out


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _harness_imports(path: Path) -> list[str]:
    """The modules a file imports from the harness, read with `ast`.

    NOT a substring scan. `approve.py` carries a comment EXPLAINING why it may
    not write `from tools import cli`, and a text match found that sentence and
    called it an import — the same false positive `test_dependencies.py` was
    rebuilt around when a regex counted thirteen English words as packages.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.split(".")[0] == "tools":
                out += [f"{module}.{a.name}" for a in node.names]
        elif isinstance(node, ast.Import):
            out += [a.name for a in node.names
                    if a.name.split(".")[0] == "tools"]
    return out


def _cannot_import_cli() -> set[str]:
    """The modules that may NOT reach `tools.cli`, DERIVED from the mechanisms
    that forbid it rather than remembered.

    Three separate architectural reasons, none of which was a gate before this
    pass, and each of which a mechanical sweep would have silently broken:

    * `sdlc-init.py` — Tier 0, stdlib-only by `test_bake_off_decisions`.
    * `reference_runner.py` — an adapter is an OUTSIDE process. Its docstring:
      "one that quietly reached into `tools/` would prove nothing about a
      runner that cannot."
    * every module `validate.closure()` embeds into the emitted
      `.sdlc/ledger_check.py`, whose loader carries `tools.sdlc.*` and nothing
      else. An import outside that package is embedded WITHOUT its dependency
      and raises ImportError in an adopter's CI, where the check is required
      and there is no harness to fall back on.
    """
    from tools.sdlc import validate as ledger_gate
    out = {"sdlc-init.py"} | outside_processes()
    out |= {f"tools/sdlc/{name}.py" for name in ledger_gate.closure()}
    return out


def outside_processes() -> set[str]:
    """Modules that DECLARE themselves outside processes, found by sweeping.

    Was a hand-written set holding one name. A roster is the shape this project
    keeps finding stale - `CLI_SOURCES` went eighteen entries out of date before
    anything noticed - and the second entry is exactly when that starts. A module
    joins by saying so in its own source, which is where somebody adding one is
    already looking.
    """
    found = set()
    for path in _shipped():
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:                      # pragma: no cover - unreadable file
            continue
        if re.search(r"^OUTSIDE_PROCESS\s*=\s*True\s*$", text, re.M):
            found.add(_rel(path))
    return found


def test_the_sweep_finds_a_realistic_number_of_modules():
    """The guard's own guard. A sweep that quietly found nothing — a renamed
    directory, a bad filter — would make every assertion below vacuous, which
    is how the hand-written list it replaces stayed green."""
    found = _shipped()
    assert len(found) > 30, [_rel(p) for p in found]
    assert ROOT / "sdlc-init.py" in found
    assert ROOT / "tools" / "cli.py" in found


def test_the_exempt_set_is_derived_and_not_empty():
    """If `closure()` moved or the import spelling changed, the exemption set
    would silently shrink to two and the next test would demand the impossible
    of `approve.py`. Pinning the mechanism keeps that loud."""
    exempt = _cannot_import_cli()
    assert "tools/sdlc/approve.py" in exempt
    assert "tools/sdlc/validate.py" in exempt
    assert "tools/sdlc/guard.py" not in exempt      # reachable, and folded
    assert len(exempt) >= 8


@pytest.mark.parametrize("rel", [_rel(p) for p in _shipped()
                                 if "__main__" in p.read_text(encoding="utf-8")])
def test_every_cli_reconfigures_its_console_somehow(rel):
    """CLAUDE.md: "Windows is a first-class target, and this has bitten twice.
    Reconfigure stdout to UTF-8."

    Five CLIs had NO console rule of any kind — `impact.py`, `pack.py`,
    `mcp_server.py`, `impact_ab.py`, `corpus_harvest.py` — and `pack.md` is
    rendered from arbitrary repository paths and code, so a path outside the
    console's codepage made the render itself raise.
    """
    src = (ROOT / rel).read_text(encoding="utf-8")
    assert "cli.use_utf8()" in src or "reconfigure" in src, (
        f"{rel} is a CLI with no console rule at all")


@pytest.mark.parametrize("rel", [_rel(p) for p in _shipped()])
def test_only_a_module_that_cannot_import_cli_hand_rolls_the_rule(rel):
    """`cli.use_utf8` exists so this is written once. The commit that created
    it converted five modules and left fourteen carrying the copy verbatim —
    the leave-the-neighbours defect that commit was otherwise about.

    Three of the fourteen were a WEAKER spelling — `sys.stdout.reconfigure(...)`
    with no `try`, no stderr and no `errors=` — and the missing `try` is not
    cosmetic: importing `tools/graph/build.py` under a wrapper whose stdout has
    no `.reconfigure` raised AttributeError before the module finished loading.
    """
    src = (ROOT / rel).read_text(encoding="utf-8")
    if "reconfigure" not in src or rel == "tools/cli.py":
        return                       # nothing to say, or the helper itself
    assert rel in _cannot_import_cli(), (
        f"{rel} hand-rolls the console rule and could just call cli.use_utf8()")
    assert "tools.cli" not in _harness_imports(ROOT / rel), (
        f"{rel} is exempt precisely because it must not import tools.cli")


def test_the_outside_process_sweep_finds_the_ones_that_exist():
    """The guard's own guard. A sweep that found nothing - a changed spelling, a
    moved directory - would make the assertion below vacuous, which is exactly
    how the hand-written roster it replaces stayed green."""
    found = outside_processes()
    assert "tools/sdlc/reference_runner.py" in found
    assert "tools/graph/reference_consumer.py" in found


@pytest.mark.parametrize("rel", sorted(outside_processes()))
def test_an_outside_process_imports_nothing_from_the_harness(rel):
    """**The property that makes either one evidence at all.**

    An adapter that quietly reached into `tools/` would prove nothing about a
    runner that cannot, and a consumer that did would prove nothing about the
    bundle being readable without this harness. Both docstrings say so
    emphatically; before this it was enforced for one of them and remembered for
    the other.
    """
    assert _harness_imports(ROOT / rel) == []


# ── item 115: a forced copy must be welded to its original ──────────────────
#
# An OUTSIDE_PROCESS module may import nothing from the harness — that is the
# property that makes it evidence about a consumer which cannot — so where it
# needs a harness constant it must RESTATE it. A restatement with no tether
# drifts, and one did: `http_runner._SECRET_NAME` dropped `CREDENTIAL` at
# birth, so its failure-path scrub printed a value the original would have
# redacted, and a code review found it rather than a test.
#
# The tie is source-text, not behaviour: change one spelling and the other
# fails BY NAME.


def _top_level_constants(rel: str) -> dict[str, str]:
    """`{NAME: the assignment's source}` for one module's top-level constants."""
    import ast

    text = (ROOT / rel).read_text(encoding="utf-8")
    tree = ast.parse(text)
    out: dict[str, str] = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            out[node.targets[0].id] = ast.get_source_segment(text, node.value) or ""
    return out


def _harness_modules() -> list[str]:
    """Shipped source that is NOT an outside process — what they restate from."""
    outside = outside_processes()
    return sorted(_rel(p) for p in _shipped() if _rel(p) not in outside)


def restatements(outside=None, harness=None) -> list[tuple[str, str, str]]:
    """`(outside module, constant, harness module)` for every collision.

    DERIVED, not listed. An outside process that defines a constant whose name
    a harness module also defines is restating it - measured on the three that
    exist and all three are genuine: the consumer restates the bundle layout
    `export_okf` writes, and the endpoint runner restates the smoke's
    secret-name rule. A roster would go stale the first time a fourth appeared,
    which is the shape this project keeps finding.

    The two module maps are INJECTABLE so the sweep's own discrimination can be
    tested with synthetic input - without that, a mutation comparing outside
    processes against each other survives, because the real collisions are
    found either way.
    """
    outs = {rel: _top_level_constants(rel) for rel in sorted(outside_processes())}         if outside is None else outside
    harn = {rel: _top_level_constants(rel) for rel in _harness_modules()}         if harness is None else harness
    found = []
    for rel, mine in outs.items():
        for name in mine:
            for other, theirs in harn.items():
                if other != rel and name in theirs:
                    found.append((rel, name, other))
    return found


def restatement_problem(mine: str, theirs: str, rel: str = "", name: str = "",
                        other: str = "") -> str:
    """The weld itself, as a function, so it can be shown to FAIL.

    Extracted after a mutation pass: with the comparison inline in the test, a
    mutation neutering it survived, because nothing exercised the assertion
    against a pair that SHOULD fail. A guard nobody can watch fail is a guard
    nobody has checked.
    """
    if mine == theirs:
        return ""
    nl = chr(10)
    return (f"{rel} restates {name} from {other} and the two have DRIFTED." + nl
            + f"  {rel}: {mine}" + nl + f"  {other}: {theirs}" + nl
            + "An OUTSIDE_PROCESS module may not import the harness, so the copy "
            + "stays - change both, or the copy stops meaning what it claims.")


def test_the_restatement_sweep_finds_the_ones_that_exist():
    """**The guard's own guard.** A sweep that found nothing - a renamed
    constant, a moved file - would make the weld below vacuous while staying
    green, which is exactly how a roster rots unnoticed."""
    found = restatements()
    assert found, "no restatements found at all - the sweep is not looking"
    pairs = {(rel, name) for rel, name, _ in found}
    assert ("tools/sdlc/http_runner.py", "_SECRET_NAME") in pairs
    assert ("tools/graph/reference_consumer.py", "NODES_DIR") in pairs


def test_the_sweep_pairs_an_outside_process_with_the_HARNESS_not_itself():
    """Synthetic, because the real collisions are found either way - so a
    mutation comparing outside processes against each other survived until this
    existed. A module is never its own original."""
    outs = {"a_outside.py": {"SHARED": "1"}, "b_outside.py": {"SHARED": "2"}}
    harn = {"h.py": {"SHARED": "1"}}
    found = restatements(outside=outs, harness=harn)
    assert {(rel, other) for rel, _, other in found} == {
        ("a_outside.py", "h.py"), ("b_outside.py", "h.py")}, found
    alone = restatements(outside={"x.py": {"S": "1"}}, harness={"x.py": {"S": "1"}})
    assert alone == [], "a module must not be its own original"
    # Asserted HERE as well as in the sweep's own guard. A guard of a guard has
    # to terminate somewhere, and one level further out is one more mutation
    # that cannot pass: deleting the requirement from either test alone leaves
    # the other standing.
    assert restatements(), "the real sweep found nothing - it is not looking"


@pytest.mark.parametrize("rel, name, other", restatements())
def test_a_restated_constant_matches_its_original(rel, name, other):
    """**The weld.** `http_runner._SECRET_NAME` dropped `CREDENTIAL` and
    nothing said so until a review read both files side by side. The copy is
    legitimate - an OUTSIDE_PROCESS cannot import - so what was missing is the
    tether, and this is it."""
    mine = _top_level_constants(rel)[name]
    theirs = _top_level_constants(other)[name]
    # What is compared is the SOURCE of the assignment, never the identifier -
    # a mutation passing `name, name` made the weld trivially true and survived
    # until this line existed.
    assert mine != name and theirs != name,         "the weld must compare values, not the constant's own name"
    problem = restatement_problem(mine, theirs, rel, name, other)
    assert problem == "", problem


def test_the_weld_reports_a_drift_and_stays_quiet_without_one():
    """**Proven to FAIL, not merely to pass.** The drift that shipped is the
    fixture: a pattern with `CREDENTIAL` against one without."""
    good = 're.compile(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL", re.I)'
    drifted = 're.compile(r"KEY|TOKEN|SECRET|PASSWORD", re.I)'
    assert restatement_problem(good, good) == ""
    problem = restatement_problem(drifted, good, "a.py", "_SECRET_NAME", "b.py")
    assert "DRIFTED" in problem and "_SECRET_NAME" in problem
    assert "may not import the harness" in problem,         "the message must say WHY the copy stays, or somebody deletes it"


# ── a bounded command hands over what it printed before the clock ran out ────

# The interpreter running the tests, never a `python` the PATH may or may not
# have: this file is the helper's own contract and must not fail for a reason
# that is about the runner rather than about `run_bounded`.
PARTIAL = [sys.executable, "-c",
           "import sys,time; print('partial-out'); "
           "print('partial-err', file=sys.stderr); sys.stdout.flush(); "
           "sys.stderr.flush(); time.sleep(5)"]


def test_run_bounded_keeps_what_a_timed_out_command_printed():
    """A hung command's partial output is the evidence its record most needs.
    Discarding it leaves `exit_code: None` and nothing to read beside it."""
    from tools import cli

    code, out, err, note = cli.run_bounded(PARTIAL, timeout=1)
    assert code is None, code
    assert "partial-out" in out, out
    assert "partial-err" in err, err
    assert "did not finish" in note, note


def test_run_bounded_keeps_it_through_a_shell_too(tmp_path):
    """The four call sites all pass `shell=True`, so the branch they take is
    the one that has to hold the partial streams - a helper proved only on the
    argv path would be proved on a path nobody uses."""
    from tools import cli

    script = tmp_path / "hang.py"
    script.write_text(
        "import sys,time\nprint('partial-out')\n"
        "print('partial-err', file=sys.stderr)\n"
        "sys.stdout.flush(); sys.stderr.flush()\ntime.sleep(5)\n",
        encoding="utf-8")
    code, out, err, note = cli.run_bounded(
        f'"{sys.executable}" "{script}"', shell=True, timeout=1)
    assert code is None, code
    assert "partial-out" in out, out
    assert "partial-err" in err, err


def test_run_bounded_reports_a_command_that_ran_with_no_note():
    """The positive control: a command that finished carries its real code and
    no note, so `note` is never a synonym for `something was printed`."""
    from tools import cli

    code, out, err, note = cli.run_bounded(
        [sys.executable, "-c", "print('done')"], timeout=60)
    assert code == 0 and "done" in out and note == "", (code, out, note)


def test_run_bounded_says_a_command_that_could_not_start_is_not_a_timeout():
    """Two ways to have no exit code, told apart in the note rather than
    folded into one sentence."""
    from tools import cli

    code, out, err, note = cli.run_bounded(
        ["a-binary-no-estate-has"], timeout=60)
    assert code is None and "could not be started" in note, note


def test_noted_puts_the_reason_where_a_leading_slice_will_find_it():
    """Every reader of these fields takes the FIRST 200 characters, so the
    note leads and the output follows. Written the other way round, a command
    noisy enough to fill the slice hides the reason it was killed."""
    from tools import cli

    joined = cli.noted("did not finish within 1s", "x" * 400)
    assert joined[:200].startswith("did not finish within 1s"), joined[:60]
    assert joined.endswith("x" * 400), joined[-10:]


def test_noted_never_invents_a_separator_it_has_nothing_to_separate():
    """The controls. A run that finished has no note and must not gain a blank
    line; a run killed before it printed anything is the note alone."""
    from tools import cli

    assert cli.noted("", "only the output") == "only the output"
    assert cli.noted("only the note", "") == "only the note"
    assert cli.noted("", "") == ""
    assert cli.noted("a", "b", joiner="; it printed: ") == "a; it printed: b"
