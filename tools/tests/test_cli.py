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
