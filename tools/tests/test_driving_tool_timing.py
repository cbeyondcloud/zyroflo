"""T462: a step the DRIVING TOOL runs in-session is timed too.

`drive` times the steps it spawns (T446b). A step Claude Code, Cursor or Codex
runs itself was never spawned: it had no start and no duration. Now entering a
step through `stage.advance` opens its clock and leaving it closes the clock -
and a step `drive` spawned, or one `--report-usage` recorded, is never counted
twice. The double count was measured, not imagined: `drive` advances the run
BEFORE it writes its own finish, and the first cut closed the driver's start.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import drive as D  # noqa: E402
from tools.sdlc import events as E  # noqa: E402
from tools.sdlc import intake as entry  # noqa: E402
from tools.sdlc import stage as S  # noqa: E402
from tools.sdlc import state as L  # noqa: E402
from tools.tests import _runs as R  # noqa: E402

RUNNER_CMD = '"' + sys.executable + '" -c pass'


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / ".sdlc").mkdir(parents=True)
    (r / ".sdlc" / "profile.yml").write_text(
        "stack: python\ngate_profile: solo\n"
        "phases: [intake, requirements, design, implementation, test, review, "
        "triage, closeout]\n"
        "runners:\n  - name: writer\n"
        f"    command: '{RUNNER_CMD}'\n    capabilities: chat\n"
        "phase_runners:\n  intake: writer\n", encoding="utf-8")
    for args in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "i"]):
        subprocess.run(["git", "-C", str(r), *args], check=True, capture_output=True)
    return r


def _run(repo: Path) -> str:
    return entry.open_run(repo, "title: a change\n\nkind: requirement\n\n"
                                "## What\n\nA small change.\n")["run_id"]


def _steps(repo: Path, rid: str, phase: str) -> list[dict]:
    return [e for e in E.read(repo, rid)
            if e["event"] in ("step_started", "step_finished") and e["step"] == phase]


def test_a_driving_tool_step_is_timed_on_advance(repo):
    rid = _run(repo)
    R.deliver(repo, rid, "P0")
    S.advance(repo, rid, "P1")
    started, finished = _steps(repo, rid, "P0")
    assert (started["event"], started["actor"]) == ("step_started", "driving-tool")
    assert (finished["event"], finished["actor"], finished["outcome"]) == (
        "step_finished", "driving-tool", "advanced")
    assert isinstance(finished["duration_ms"], int) and finished["duration_ms"] >= 0
    assert finished["started_at"] == started["at"]
    assert finished["tokens"]["source"] == "unavailable", "never an invented 0"
    (opened,) = _steps(repo, rid, "P1")
    assert (opened["event"], opened["attempt"]) == ("step_started", 1)
    assert E.verify(repo, rid) == []


def test_a_loop_back_closes_as_looped_back_and_reopens_as_attempt_two(repo):
    rid = _run(repo)
    R.deliver(repo, rid, "P0")
    S.advance(repo, rid, "P1")
    R.deliver(repo, rid, "P1")        # a backward move still owes the step's artifact
    S.advance(repo, rid, "P0")                          # back to intake
    p1 = _steps(repo, rid, "P1")
    assert p1[-1]["event"] == "step_finished" and p1[-1]["outcome"] == "looped-back"
    reopened = _steps(repo, rid, "P0")[-1]
    assert (reopened["event"], reopened["attempt"]) == ("step_started", 2)


def test_a_step_drive_spawned_is_finished_exactly_once(repo):
    """The regression: the driver's own open start is the driver's to close."""
    rid = _run(repo)
    got = D.step(repo, rid, {}, run=lambda command, prompt, cwd, timeout=3600: (0, "doc\n"))
    assert got["advanced_to"] == "P1", got
    finishes = [e for e in _steps(repo, rid, "P0") if e["event"] == "step_finished"]
    assert len(finishes) == 1 and finishes[0]["actor"] == "runner", finishes


def test_a_self_report_is_not_followed_by_a_second_finish(repo):
    rid = _run(repo)
    done = subprocess.run([sys.executable, str(ROOT / "tools" / "sdlc" / "stage.py"),
                           "--target", str(repo), "--run", rid, "--phase", "P0",
                           "--report-usage", "input=10,output=2"], capture_output=True,
                          text=True, encoding="utf-8", timeout=300)
    assert done.returncode == 0, done.stdout
    R.deliver(repo, rid, "P0")
    S.advance(repo, rid, "P1")
    finishes = [e for e in _steps(repo, rid, "P0") if e["event"] == "step_finished"]
    assert len(finishes) == 1 and finishes[0]["outcome"] == "self-reported"


def test_the_metrics_count_a_driving_tool_step(repo):
    """The point of the row: the audit trail's per-step numbers now include a
    step nobody spawned, with its duration measured and its tokens unmeasured."""
    from tools.sdlc import metrics as M
    rid = _run(repo)
    R.deliver(repo, rid, "P0")
    S.advance(repo, rid, "P1")
    intake = M.compute(repo)["steps"]["by_step"]["intake"]
    assert intake["attempts"]["value"] == 1
    assert isinstance(intake["duration_ms"]["p50"]["value"], int)
    assert intake["tokens"]["input"]["value"] is None
