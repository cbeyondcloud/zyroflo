"""T719: a run whose temp root sits inside a git repository stops once, saying so.

The builder refuses to write a graph inside a repository the graph is not about, and a test
that needs a folder outside every repository makes one under `tmp_path`. Both are right, and
both assume pytest's temp root is in no repository. On a developer machine whose home folder
is itself a repository that is false: measured 2026-11-17, 415 failures and 118 setup errors,
each looking like a real defect, all passing once `--basetemp` was moved outside. So
`tools/conftest.py` checks the temp root the first time a test asks for it, and ends the
session with ONE message naming the cause and the way on.

The nested tests spawn a real pytest, as `test_skip_gate` does, because the contract is the
process exit code and the message a person reads. Two are controls: a temp root outside
every repository still runs, and a test that never asks for a temp folder is not stopped.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools.conftest import temp_root_refusal

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The phrase a refused run prints; the tests read it rather than the whole message, so the
#: wording around it can change without a test going stale.
REFUSED = "is inside the git repository"

#: The nested suite borrows the real fixture. Re-implementing it here would test a copy.
_CONFTEST = '''
from tools.conftest import tmp_path_factory  # noqa: F401
'''

_TEMP_SUITE = '''
import os
from pathlib import Path

def test_that_needs_a_temp_folder(tmp_path):
    Path(os.environ["T719_MARKER"]).write_text("ran", encoding="utf-8")
'''

_NO_TEMP_SUITE = '''
def test_that_needs_no_temp_folder():
    assert True
'''


def _throwaway_repo(where: Path) -> Path:
    where.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=where, check=True, capture_output=True,
                   text=True)
    return where


def _run_nested(tmp_path: Path, suite: str, basetemp: Path) -> subprocess.CompletedProcess:
    here = tmp_path / "suite"
    here.mkdir()
    (here / "conftest.py").write_text(_CONFTEST, encoding="utf-8")
    (here / "test_sample.py").write_text(suite, encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["T719_MARKER"] = str(tmp_path / "ran")
    # Bounded, as test_skip_gate's nested runs are: a hang raises instead of eating the run.
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider",
         f"--basetemp={basetemp}", str(here)],
        cwd=str(here), env=env, capture_output=True, text=True, timeout=300)


def test_a_temp_root_inside_a_repository_is_refused_by_name(tmp_path):
    repo = _throwaway_repo(tmp_path / "repo")
    refusal = temp_root_refusal(repo / "bt")
    assert refusal is not None, "a temp root inside a repository was let through"
    assert REFUSED in refusal and str(repo.resolve()) in refusal, refusal
    assert "--basetemp" in refusal, f"the refusal gives no way on: {refusal!r}"


def test_this_sessions_own_temp_root_is_allowed(tmp_path):
    """True by construction: the gate let this session reach a test that asks for
    `tmp_path`. Asserted so the pure answer and the fixture cannot disagree."""
    assert temp_root_refusal(tmp_path) is None


def test_a_run_that_needs_a_temp_folder_in_a_repository_stops_with_the_message(tmp_path):
    repo = _throwaway_repo(tmp_path / "repo")
    proc = _run_nested(tmp_path, _TEMP_SUITE, repo / "bt")
    out = proc.stdout + proc.stderr
    assert proc.returncode == pytest.ExitCode.USAGE_ERROR, out
    assert REFUSED in out and "--basetemp" in out, f"the run did not say why:\n{out}"
    assert not (tmp_path / "ran").exists(), "the test body ran under a refused temp root"


def test_a_temp_root_outside_every_repository_still_runs(tmp_path):
    # Control: the gate must fire on where the temp root is, not on being asked.
    free = tmp_path / "free"
    free.mkdir()
    proc = _run_nested(tmp_path, _TEMP_SUITE, free / "bt")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (tmp_path / "ran").exists(), "the control's test body never ran"


def test_a_test_that_needs_no_temp_folder_is_not_stopped(tmp_path):
    # Control: a run that never asks for a temp folder cannot be hurt by where it is.
    repo = _throwaway_repo(tmp_path / "repo")
    proc = _run_nested(tmp_path, _NO_TEMP_SUITE, repo / "bt")
    assert proc.returncode == 0, proc.stdout + proc.stderr
