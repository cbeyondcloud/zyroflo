"""Test-only bootstrap for T397's spawned-pipeline test (see test_prepass.py).

Spawned as its OWN process via `sys.executable`, per this repo's CLI-testing
rule: `main()` called in-process cannot tell a refusal from a traceback, so a
test asking what a person sees must spawn it and read the exit code and the
output.

It fakes exactly one thing: a producer that emits an ast-grep-shaped
`"severity": null` row. The real pinned ast-grep 0.45.1 cannot be made to do
this (measured for T397: omitting `severity` from a rule makes the tool
supply its OWN default, `"hint"`, never a null; an explicit `severity: null`
in the rule YAML fails the tool's own rule-config parse before any row is
produced at all) and the vendored Windows binary does not even resolve under
the name this module looks for (T385 - a separate, unfixed row) - so there is
no real, spawnable path that reaches `parse_astgrep` with a null severity on
this machine, or apparently on any machine running this ast-grep version.
This stub stands in for that shape so the PARSER's contract is proven against
the JSON shape it must survive, not only against what today's pinned tool
happens to send.

Everything else in the pipeline is real: argument parsing, `prepass()`,
`parse_astgrep`, report assembly, the exit code and the stdout/stderr a
person would actually see.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import tools.review.prepass as PP  # noqa: E402


def _fake_binary(name: str):
    return Path(f"fake-{name}"), ""


def _fake_run_tool(cmd, timeout=300, env=None):
    exe = cmd[0]
    if "ast-grep" in exe:
        target = cmd[-1]
        row = {
            "file": target,
            "range": {"start": {"line": 0, "column": 0}},
            "ruleId": "t397-probe",
            "message": "a present-null severity probe",
            "severity": None,
        }
        return 0, json.dumps([row]), ""
    return 0, "", ""  # gitleaks: a clean run, exit 0, nothing to parse


if __name__ == "__main__":
    # The seam is faked ONLY in this spawned process, and ONLY under __main__.
    # At module IMPORT these two lines used to run unconditionally, so any
    # in-process import of this file (a stray collection, a future refactor, a
    # sys.path collision under pytest's prepend import-mode) permanently
    # replaced `tools.review.prepass.binary`/`.run_tool` process-wide with no
    # restore - poisoning every later test in the same session:
    # `binary("gh")` then returned the nonexistent `fake-gh` and every gate ran
    # the canned `_fake_run_tool`. This module is spawned as its own process
    # (module docstring), so the mutation belongs here, never at import.
    PP.binary = _fake_binary
    PP.run_tool = _fake_run_tool
    raise SystemExit(PP.main(sys.argv[1:]))
