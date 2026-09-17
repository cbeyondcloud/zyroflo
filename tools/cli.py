"""What every harness CLI needs at its door, spelled once.

Three things live here, and all three are here because copies of them had
already disagreed with each other:

**Console encoding.** `provider_smoke.py` and `review/loop.py` each carried
their own three-line `reconfigure` block; `review/prepass.py`, `review/sca.py`
and `review/hitl.py` carried none. CLAUDE.md is not ambiguous about this —
*"Windows is a first-class target, and this has bitten twice. Reconfigure
stdout to UTF-8"* — and the omission was not cosmetic: the crash handler in
`prepass.main` prints an arbitrary exception message, so on a cp1252 console a
path with a non-Latin character made the handler itself raise, and the process
exited 1, which that module's own contract calls *"every gate ran, findings —
not a failure"*. The guard against a forged answer forged one.

**The door.** A CLI whose exit 1 means something substantive cannot let an
uncaught exception produce exit 1, because Python's default for an escaping
exception IS 1. Each such CLI needs a top-level guard that says what failed and
returns the code its own vocabulary already owns for *could not look*. That
guard was written twice, verbatim, in the commit that introduced it — the same
copy-the-rule defect the rest of that commit was about — and a third CLI with
the identical collision (`review/loop.py`, where 1 means *iterate again*) was
left without one.

**A command run under a clock.** Four phases bounded a subprocess and, on
the timeout branch, threw away the partial output `TimeoutExpired` was
holding for them - see `run_bounded`.

**Reading a record this process did not write.** Eleven sites read a JSONL
record line by line, and they had settled on four different answers to "what
about the line I cannot read": count it, mark it inline, name it as a stated
problem, or die — plus a fifth that was not a convention at all, a silent
`continue` shared with a row that was merely somebody else's. Reading is not where that choice belongs — *what to do about
damage* is the caller's vocabulary, *finding* the damage is one rule — and
splitting them let the rule be got wrong three ways at once. `guard.read_log`
was the one reader that never asked whether a row was an object, so a log line
holding a bare JSON string came back as a `str` and `guard --show` died on
`.get`. `feedback.review_corrections` folded a corrupt row into the same
`continue` as a row belonging to another run, leaving an `unreadable` counter
that could not see it. And `impact.load_graph`, `graph/validate._rows` and
`snapshot.load` decoded strictly, so they could not reach any convention at
all — they raised first.

This module is stdlib-only and imports nothing from the harness, so the
zero-dependency parity gate is unaffected and any CLI may use it.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Callable, NamedTuple, Sequence


def use_utf8(*streams) -> None:
    """Make the console able to carry anything a message might hold.

    Idempotent, and never fatal: a stream that cannot be reconfigured (a pipe
    already wrapped, a test's capture object) is left alone. Call it at module
    import, next to the other module-level constants, the way the two CLIs
    that already had it do.
    """
    for stream in (streams or (sys.stdout, sys.stderr)):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def run_bounded(command, *, cwd=None, env=None, timeout=None, shell=False,
                timed_out: str = "the command did not finish within {timeout}s",
                could_not_start: str = "the command could not be started: "
                                       "{error}",
                ) -> tuple[int | None, str, str, str]:
    """Run a command under a clock and **keep what it printed either way**.

    Four call sites had written the same three-branch idiom, and all four made
    the same mistake in the middle branch:

        except subprocess.TimeoutExpired:
            code, out, err = None, "", "did not finish within Ns"

    `TimeoutExpired` carries `.stdout` and `.stderr` — everything the child
    printed before it was killed — and that line overwrote both with the empty
    string. A deploy or a release step that HUNG is precisely the case where
    what it managed to say is the evidence somebody needs, and the record it
    left said `exit_code: None` with nothing beside it. That is this project's
    ABSENT-vs-COULD-NOT-LOOK rule turned on its head: the answer was kept and
    the evidence for it thrown away.

    Returns `(code, stdout, stderr, note)`. `code` is None when no exit status
    exists — a timeout or a child that never started — and `note` is empty
    exactly when the process ran to completion, so a caller can tell "it ran
    and printed nothing" from "it never finished". The note wording belongs to
    the caller, because each phase already had its own sentence for this and a
    shared one would have changed four records' prose to save a parameter.

    **Decoding is the same on both paths.** The normal path asks `subprocess`
    for text with `errors="replace"`; `TimeoutExpired.stdout` is str on that
    same setting on Windows but is not guaranteed to be anywhere else, so
    bytes are decoded here with the identical policy rather than being handed
    on as a `bytes` repr inside a record that claims to hold output.
    """
    try:
        proc = subprocess.run(command, shell=shell, cwd=cwd, env=env,
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout)
        return proc.returncode, _as_text(proc.stdout), _as_text(proc.stderr), ""
    except subprocess.TimeoutExpired as expired:
        return (None, _as_text(expired.stdout), _as_text(expired.stderr),
                timed_out.format(timeout=timeout))
    except OSError as exc:
        return None, "", "", could_not_start.format(error=exc)


def noted(note: str, text: str, *, joiner: str = "\n") -> str:
    """The reason FIRST, then whatever the command printed.

    Every caller of `run_bounded` has to put two things into one field: the
    note saying the command never finished, and the partial output it left.
    The ORDER is not cosmetic, because every reader of those fields takes a
    LEADING slice - `stderr[:200]` in P9's judge, the first 200 characters of
    the artifact check's `why`. Joined the other way round, a command that
    printed 400 characters before it hung pushes "did not finish within Ns"
    out of the only sentence an operator is shown, and a run the CLOCK ended
    becomes indistinguishable from one that answered and failed.

    So the order is decided here, once. Four sites had written this join by
    hand and three of them had written it backwards; a rule that has to be
    remembered at four call sites is a rule that holds at three.

    Callers slice `text` BEFORE handing it over. The tail of what a hung
    command printed is the part worth keeping, and prepending afterwards is
    what stops the note from being what that tail cuts off.
    """
    return joiner.join(part for part in (note, text) if part)


def _as_text(value) -> str:
    """Whatever a stream handed back, as the string a record can hold.

    None is the absence of a stream, not an empty one, but a record has only
    one way to spell "nothing was captured" - so both arrive as "" and the
    distinction that matters (whether the process finished) is carried by the
    exit code and the note instead.
    """
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def say(message: str, stream=None) -> None:
    """Print a message that must survive a console that cannot spell it.

    The door's whole job is to report a failure, so the report must not be
    able to fail. `use_utf8` normally makes this moot; this is the belt for
    the case where the stream refused to be reconfigured — which is exactly
    the case the door is running in.
    """
    stream = sys.stdout if stream is None else stream
    try:
        print(message, file=stream)
    except UnicodeEncodeError:
        encoding = getattr(stream, "encoding", None) or "ascii"
        print(message.encode(encoding, "replace").decode(encoding, "replace"),
              file=stream)


def door(run: Callable[..., int], argv: Sequence[str] | None, subject: str,
         failed: int) -> int:
    """Run a CLI's body and convert any unexpected failure into `failed`.

    `failed` is passed by the caller rather than defaulted here, because the
    code that means *could not look* is the caller's vocabulary, not this
    module's — and because writing it literally at the call site keeps it
    visible to the contract's derived exit table.

    Deliberately NOT a blanket except that hides: item 37 recorded where that
    leads. The exception's type and message are always said out loud; only the
    exit code is decided here.

    What is caught, and what is not:

    * `Exception` — the unexpected failure this exists for.
    * `KeyboardInterrupt` — separately, and for the same reason. It is a
      BaseException, so it escaped the first version of this guard, and an
      escaping KeyboardInterrupt exits 1 on CPython: a Ctrl-C during a 300 s
      scanner subprocess was delivered to the caller as the benign findings
      answer. An interrupted gate did not look, which is what `failed` says.
    * `SystemExit` passes straight through, so argparse keeps its own exit 2
      for a usage error. A door that swallowed it would answer a typo with
      "the gate could not look".
    """
    try:
        return run(argv)
    except KeyboardInterrupt:
        say(f"!! {subject} was interrupted before it finished")
        return failed
    except Exception as exc:
        say(f"!! {subject} did not finish: {type(exc).__name__}: {exc}")
        return failed


def missing_target(target) -> str:
    """Why `target` is not a directory to look in, or "" when it is one.

    **Absent is not measured-zero, and the record readers cannot say so.** A
    missing profile or an empty run directory is an ordinary state — "no profile
    yet", "nothing recorded yet" — so `policy._profile_source` returns an empty
    profile for a path that is not there and every ledger reader returns an empty
    record. A door that counted that answered `0 declared, 0 recorded` at exit 0
    for a `--target` that does not exist: the ABSENT-vs-MEASURED-ZERO confusion
    this project keeps finding, on the input easiest to get wrong.

    The could-not-look is at the WHOLE target — above any one record — so it is
    caught here once (a single weld rather than four copies) and each door raises
    its own vocabulary's error with this reason, the same shape those doors
    already spend on an UNREADABLE record. This never reads the target's
    contents; it only asks whether there is one to read, so it cannot itself be
    the thing that could-not-look.

    Two branches, because the reason must NAME which case it is: a path that is
    not there, and a path that is a file where a repository or workspace was
    meant. `Path(target)`, not `target`, because a door reaches this holding
    whatever argparse gave it — a string as readily as a `Path`.
    """
    p = Path(target)
    if not p.exists():
        return (f"{p} does not exist, so there is nothing here to look at - an "
                f"absent target is could-not-look, not a repository that has "
                f"declared and recorded nothing")
    if not p.is_dir():
        return (f"{p} is a file, not a directory - a target is the repository or "
                f"workspace to look in, so this is could-not-look rather than an "
                f"empty one")
    return ""


#: Why a line came back with no row. Both are damage; they are named apart
#: because `provider_smoke.verify` says something different about each, and a
#: shared reader that could not carry that distinction would have forced its
#: caller to give one of the two messages up.
UNPARSEABLE = "did not parse as JSON"
NOT_AN_OBJECT = "is not a JSON object"


class Line(NamedTuple):
    """One line of a JSONL record: where it is, and what it held.

    `row` is None when the line could not be read as a JSON object — it did
    not parse, or it parsed to something that is not a mapping. Both are
    damage, and the caller says what damage means to it; neither is ever
    silently dropped, because a record that quietly loses lines reads exactly
    like a record that is simply short (CLAUDE.md: zero silent drops).

    `number` is 1-based, so it is the number the file itself would show and
    the number a person can go and look at.

    `problem` is empty exactly when `row` is not None, so `if line.row is
    None` and `if line.problem` are the same question asked two ways — the
    caller may use whichever reads better where it stands.
    """
    number: int
    row: dict | None
    problem: str = ""


def jsonl_rows(path: Path) -> list[Line]:
    """Every non-blank line of a JSONL record, in file order.

    Order is preserved and damage is kept in place rather than collected to
    one side, because for some readers position IS the meaning. `feedback.read`
    substitutes a marker at the damaged index; the shape comes from
    `approve.read_chain`, which hashes each entry against its predecessor and
    would silently re-chain across a hole if a reader handed back the good rows
    and the bad line numbers separately. (That function cannot call this one —
    see its own comment — but it is why this returns what it returns.)

    A missing file is an empty record, not an error — every caller already
    treats "nothing recorded yet" as an ordinary state, and a reader that
    raised on it would make each of them guard the call. A file that is there
    and cannot be read is NOT folded into that: it raises, and the door names
    it, because "nothing has been recorded" and "the record is unreadable" are
    the two answers this whole module exists to keep apart.

    The decode is inside the guard, and `errors="replace"` besides. Written
    the obvious way — `for line in path.read_text(...).splitlines()` — the
    decode happens BEFORE the loop and outside every try in the function, so
    one non-UTF-8 byte raises `UnicodeDecodeError` past all of it. That is not
    hypothetical here: it took `provider_smoke.read_record` down once and
    `adr.dismissals` a second time, and `UnicodeDecodeError` is a `ValueError`
    subclass, so it slipped through guards written to catch bad JSON.
    """
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    out: list[Line] = []
    for number, raw in enumerate(text.splitlines(), 1):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except ValueError:
            out.append(Line(number, None, UNPARSEABLE))
            continue
        out.append(Line(number, row) if isinstance(row, dict)
                   else Line(number, None, NOT_AN_OBJECT))
    return out
