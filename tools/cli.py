"""What every harness CLI needs at its door, spelled once.

Two things live here, and both are here because copies of them had already
disagreed with each other:

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

This module is stdlib-only and imports nothing from the harness, so the
zero-dependency parity gate is unaffected and any CLI may use it.
"""
from __future__ import annotations

import sys
from typing import Callable, Sequence


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
