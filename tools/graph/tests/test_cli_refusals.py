"""T163: the CLI refusal branches, SPAWNED.

`build.py`'s argument-combination refusals and `mcp_server.py`'s startup refusals each
`print(...)` and `return 2`, but the suite only ever spawned `build.py` once (asserting
`code == 0`) and never spawned `mcp_server.py` at all - it was only imported. Item 117's
lesson (`policy.py --validate` handing a traceback for the one document class it exists
to refuse, while every in-process test passed) is what an unspawned refusal costs when
it is finally paid: calling `main()` in-process cannot tell a clean `return 2` from a
traceback, because pytest renders both as a failing test. The contract a person meets is
only proven by a process that reads its exit code AND its first printed line.

These are coverage tests: every branch below behaves correctly today. They assert the
FIRST LINE of each refusal, not merely that something was refused, so a message moved to
the wrong branch (a "!! ..." line that refuses the wrong thing) is caught rather than
passed.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _spawn(module: str, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", module, *args],
                          cwd=str(ROOT), capture_output=True, text=True)


def _first_line(proc: subprocess.CompletedProcess) -> str:
    lines = proc.stdout.splitlines()
    return lines[0] if lines else ""


# (argv, distinctive substring of the refusal's first line). Each pair names exactly ONE
# pre-flight branch - all fire before the workspace is resolved, so no fixture is needed.
BUILD_REFUSALS = [
    (["--publish-snapshot", "--from-snapshot"], "cannot be combined: publishing, loading"),
    (["--from-snapshot", "--only", "x"], "--from-snapshot runs no extractors"),
    (["--incremental", "--from-snapshot"], "--incremental is a way of RUNNING the build"),
    (["--incremental", "--only", "x"], "--incremental decides what to run from the stamps"),
    (["--propose-canonical", "--check-fresh"], "--propose-canonical drafts from the build"),
    (["--check-fresh", "--only", "x"], "--check-fresh compares the whole committed snapshot"),
    (["--target", "a", "--workspace", "b"], "--target and --workspace cannot be combined"),
]


@pytest.mark.parametrize("argv, first_line", BUILD_REFUSALS)
def test_build_refuses_argument_combinations_by_exit_and_first_line(argv, first_line):
    proc = _spawn("tools.graph.build", argv)
    assert proc.returncode == 2, f"exit {proc.returncode}\n{proc.stdout}\n{proc.stderr}"
    assert first_line in _first_line(proc), proc.stdout or proc.stderr


@pytest.mark.parametrize("kind, first_line", [
    ("no-graph-and-no-target", "one of --graph-dir and --target"),
    ("graph-jsonl-absent", "does not exist"),
])
def test_mcp_server_refuses_startup_by_exit_and_first_line(kind, first_line, tmp_path):
    # The graph-dir path is built under tmp_path at runtime; no machine path is written
    # into the test, and the directory genuinely has no graph.jsonl.
    argv = [] if kind == "no-graph-and-no-target" else ["--graph-dir", str(tmp_path / "nope")]
    proc = _spawn("tools.graph.mcp_server", argv)
    assert proc.returncode == 2, f"exit {proc.returncode}\n{proc.stdout}\n{proc.stderr}"
    assert first_line in _first_line(proc), proc.stdout or proc.stderr
