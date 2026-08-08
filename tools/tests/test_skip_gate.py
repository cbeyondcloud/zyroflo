"""The gate that stops a skip from printing green on a host that had to run everything.

Two of these tests are deliberate controls. `test_the_same_skip_is_still_green_without_the
_flag` and `test_a_run_with_nothing_skipped_passes_under_the_flag` pass with the gate removed
as well as with it, and they are here precisely because the risk in a gate like this is not
that it fails to fire — it is that it fires on the developer's own suite, where three skips
are legitimate, and gets switched off. A gate nobody can live with protects nothing.

The nested-pytest tests spawn a real subprocess rather than stubbing the hook, because the
thing CI depends on is the **process exit code**, and a hook that reports beautifully while
returning 0 would satisfy any test that stopped short of it.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from tools import conftest as skip_gate

REPO_ROOT = Path(__file__).resolve().parents[2]

#: A suite with one passing test and one skip standing in for an unmet platform precondition.
_SKIPPING_SUITE = '''
import pytest

def test_that_runs():
    assert True

@pytest.mark.skipif(True, reason="stands in for an unmet platform precondition")
def test_that_skips():
    raise AssertionError("this body must never execute")
'''

_CLEAN_SUITE = '''
def test_that_runs():
    assert True
'''

#: The nested suite borrows the real hook. Re-implementing it here would test a copy.
_CONFTEST = '''
from tools.conftest import pytest_sessionfinish  # noqa: F401
'''


def _run_nested(tmp_path: Path, suite: str, *, forbid: bool) -> subprocess.CompletedProcess:
    (tmp_path / "conftest.py").write_text(_CONFTEST, encoding="utf-8")
    (tmp_path / "test_sample.py").write_text(suite, encoding="utf-8")
    env = dict(os.environ)
    # The nested run imports `tools.conftest` from the repo; it does not inherit this
    # session's sys.path.
    env["PYTHONPATH"] = str(REPO_ROOT)
    env.pop(skip_gate.FORBID_SKIPS_ENV, None)
    if forbid:
        env[skip_gate.FORBID_SKIPS_ENV] = "1"
    # Bounded on purpose. A nested pytest that hangs would hang the whole suite, and in CI
    # that means burning the runner's default six-hour job budget on a deadlock. A timeout
    # raises, which is a loud failure at known cost.
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", str(tmp_path)],
        cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=300)


def test_a_skip_fails_the_run_when_the_host_was_supposed_to_run_everything(tmp_path):
    proc = _run_nested(tmp_path, _SKIPPING_SUITE, forbid=True)
    assert proc.returncode != 0, (
        "the skip exited 0, which is the whole defect: a test that never ran reported "
        f"success\n{proc.stdout}")


def test_the_same_skip_is_still_green_without_the_flag(tmp_path):
    # Control. The developer's own suite has three legitimate skips, and a gate that broke
    # it would be switched off within a day.
    proc = _run_nested(tmp_path, _SKIPPING_SUITE, forbid=False)
    assert proc.returncode == 0, proc.stdout


def test_a_run_with_nothing_skipped_passes_under_the_flag(tmp_path):
    # Control: the flag must gate on skips, not merely on being set.
    proc = _run_nested(tmp_path, _CLEAN_SUITE, forbid=True)
    assert proc.returncode == 0, proc.stdout


def test_the_failure_names_the_test_that_skipped_and_why(tmp_path):
    # A red build saying only "1 skipped" is a puzzle, and a puzzle on a gate gets bypassed.
    proc = _run_nested(tmp_path, _SKIPPING_SUITE, forbid=True)
    assert "test_that_skips" in proc.stdout, proc.stdout
    assert "unmet platform precondition" in proc.stdout, proc.stdout


class _NoPluginManager:
    @staticmethod
    def get_plugin(name):
        return None


class _ConfigWithoutReporter:
    pluginmanager = _NoPluginManager()


class _SessionWithoutReporter:
    """A session whose terminalreporter cannot be reached."""

    def __init__(self) -> None:
        self.config = _ConfigWithoutReporter()
        self.exitstatus = 0


def test_an_unreadable_skip_count_fails_rather_than_passing_quietly(monkeypatch):
    # "I could not tell" must not round to "fine" — that is the failure this file guards
    # against, one level up.
    monkeypatch.setenv(skip_gate.FORBID_SKIPS_ENV, "1")
    session = _SessionWithoutReporter()
    skip_gate.pytest_sessionfinish(session, 0)
    assert session.exitstatus == 1


def test_an_unreadable_skip_count_is_ignored_when_the_flag_is_off(monkeypatch):
    monkeypatch.delenv(skip_gate.FORBID_SKIPS_ENV, raising=False)
    session = _SessionWithoutReporter()
    skip_gate.pytest_sessionfinish(session, 0)
    assert session.exitstatus == 0


class _Report:
    def __init__(self, nodeid, longrepr=None):
        self.nodeid = nodeid
        self.longrepr = longrepr


def test_the_report_carries_the_reason_when_the_skip_has_one():
    stats = {"skipped": [_Report("t.py::a", ("t.py", 3, "Skipped: no grammar"))]}
    assert skip_gate.skipped_report(stats) == ["t.py::a  (Skipped: no grammar)"]


def test_a_skip_with_no_readable_reason_is_named_rather_than_dropped():
    # Dropping it would hide exactly the thing being counted.
    stats = {"skipped": [_Report("t.py::b", longrepr=None)]}
    assert skip_gate.skipped_report(stats) == ["t.py::b"]


def test_no_skips_reports_nothing():
    assert skip_gate.skipped_report({}) == []
    assert skip_gate.skipped_report({"skipped": []}) == []
