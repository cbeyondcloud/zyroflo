"""The door and the console rule, tested where they now live.

Both were copied code before: `use_utf8` existed twice and was missing from
the three CLIs that needed it most, and the door was written out twice
verbatim in the commit that introduced it — the same restate-the-rule defect
that commit was otherwise about. A shared implementation is only an
improvement if it is held to the behaviour both copies were supposed to have,
so it is pinned here rather than in either caller.
"""
from __future__ import annotations

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
