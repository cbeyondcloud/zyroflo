"""Make a skipped test a failure on a host that was supposed to run every one of them.

A skip prints green. That is the entire problem: pytest reports `3 skipped` in the same
cheerful dot-line as a pass, the process exits 0, and nothing distinguishes *this assertion
does not apply on this platform* from *this assertion has never once executed anywhere*.

This project has already paid for that distinction twice. Item 73 recorded that a skipping
test still prints green. Item 74 then shipped cross-platform binary handling whose three
POSIX exec-bit assertions are `skipif(os.name == "nt")` — so on the only machine that ever
ran them they skipped, and the code they guard sat unexecuted for a whole item while the
suite reported success. Running them once on Linux by hand closed that gap and immediately
began reopening it: a dated gate record decays the moment anyone edits the file it vouches
for.

So the Linux CI job sets `SDLC_FORBID_SKIPS=1`, and any skip there fails the run.

## Why an environment variable rather than always-on

Some skips are legitimate. A filesystem that ignores `chmod` cannot test an exec bit, and
asserting it anyway would pass or fail for reasons unrelated to the code — a meaningless
gate wearing a real one's clothes. An always-on rule would make the developer's own suite
permanently red, and **a permanently-red gate gets disabled**, taking the real protection
with it.

The flag therefore states a claim about the **host**, not about the tests: *this machine was
supposed to satisfy every precondition the suite has.* That claim is false on Windows and
true on the CI runner, which is exactly the distinction worth encoding.

## Why an undeterminable skip count fails rather than passes

If the flag is set and the reporter cannot be reached, the honest answer is *unknown*, and
this file refuses to round that to *fine*. A guard that quietly passes when it cannot see is
the failure it was written to prevent, one level up.

## Why it names what skipped

A red build reporting `1 skipped` and nothing else is a puzzle, and a puzzle standing between
someone and a merge gets bypassed rather than investigated.
"""
from __future__ import annotations

import os
from typing import Any

#: Set to "1" by a job running on a host expected to satisfy every precondition the suite has.
FORBID_SKIPS_ENV = "SDLC_FORBID_SKIPS"


def skipped_report(stats: dict[str, Any]) -> list[str]:
    """`nodeid: reason` for every skipped test, sorted.

    Pure and separate from the hook so the formatting can be tested without running a nested
    pytest session. A skip report carries its reason in `longrepr` as `(path, lineno, reason)`;
    anything else is reported by node id alone rather than dropped, because a skip nobody can
    name is the state this whole file exists to make visible.
    """
    out: list[str] = []
    for report in stats.get("skipped") or []:
        nodeid = getattr(report, "nodeid", "") or "<unknown test>"
        longrepr = getattr(report, "longrepr", None)
        reason = ""
        if isinstance(longrepr, tuple) and len(longrepr) == 3:
            reason = str(longrepr[2]).strip()
        out.append(f"{nodeid}  ({reason})" if reason else nodeid)
    return sorted(out)


def pytest_sessionfinish(session, exitstatus) -> None:  # noqa: ARG001 - pytest hook signature
    if os.environ.get(FORBID_SKIPS_ENV) != "1":
        return

    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is None:
        # Cannot see the skip count, and the flag says this host had to run everything.
        # Unknown is not the same as clean.
        print(f"\n{FORBID_SKIPS_ENV}=1 but no terminalreporter: the skip count could not be "
              f"read, so whether every test ran is unknown. Failing rather than assuming.")
        session.exitstatus = 1
        return

    skipped = skipped_report(getattr(reporter, "stats", {}) or {})
    if not skipped:
        return

    reporter.write_line("")
    reporter.write_line(
        f"{FORBID_SKIPS_ENV}=1: this host was supposed to run every test, and "
        f"{len(skipped)} skipped.", red=True, bold=True)
    reporter.write_line(
        "A skip prints green, so each of these is an assertion that did not execute:")
    for line in skipped:
        reporter.write_line(f"  - {line}")
    reporter.write_line(
        "Fix the missing precondition, or the skip condition is wrong for this platform.")
    if session.exitstatus == 0:
        session.exitstatus = 1
