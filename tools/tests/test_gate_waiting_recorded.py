"""T464: a run that STOPS at a hard gate records that it is waiting.

`gate_waiting` was written only on a status move to `awaiting_approval`, and
nothing in the pipeline ever made that move - `drive` stopped at a gate with
the run still `running`, and `stage.advance` refused the move and left it so.
The audit trail therefore never held a wait, `gate_decided.waited_ms` was
always None, and `status --awaiting` listed nothing. Now the stop itself marks
the run `awaiting_approval`, once, and the approval answers that wait.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import approve as A  # noqa: E402
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
        "phase_runners:\n  intake: writer\n  design: writer\n", encoding="utf-8")
    for args in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "i"]):
        subprocess.run(["git", "-C", str(r), *args], check=True, capture_output=True)
    return r


def _at_the_gate(repo: Path) -> str:
    rid = entry.open_run(repo, "title: a change\n\nkind: requirement\n\n"
                               "## What\n\nA small change.\n")["run_id"]
    for phase, nxt in (("P0", "P1"), ("P1", "P3")):
        R.deliver(repo, rid, phase)
        S.advance(repo, rid, nxt)
    R.deliver(repo, rid, "P3")       # design delivered; G2 is hard under solo
    return rid


def _waits(repo: Path, rid: str) -> list[dict]:
    return [e for e in E.read(repo, rid) if e["event"] == "gate_waiting"]


def _the_gate(repo: Path) -> str:
    (gate,) = [g.id for g in S.pipeline(repo).gates if g.after_phase == "P3"]
    return gate


def test_a_refused_move_past_a_hard_gate_records_the_wait_once(repo):
    rid = _at_the_gate(repo)
    for _ in range(2):                       # asking twice is still one wait
        with pytest.raises(S.StageError):
            S.advance(repo, rid, "P4")
    (wait,) = _waits(repo, rid)
    assert (wait["step"], wait["gate"], wait["hard"]) == ("P3", _the_gate(repo), True)
    assert L.load(repo, rid)["status"] == "awaiting_approval"


def test_the_approval_answers_the_wait_with_a_measured_duration(repo):
    rid = _at_the_gate(repo)
    with pytest.raises(S.StageError):
        S.advance(repo, rid, "P4")
    A.record(repo, rid, _the_gate(repo), "approve", approver="t@e")
    (decided,) = [e for e in E.read(repo, rid) if e["event"] == "gate_decided"]
    assert isinstance(decided["waited_ms"], int), decided
    assert L.load(repo, rid)["status"] == "running"
    S.advance(repo, rid, "P4")                                    # and it moves on


def test_drive_stopping_at_the_gate_records_the_wait(repo):
    rid = _at_the_gate(repo)
    got = D.step(repo, rid, {}, run=lambda command, prompt, cwd, timeout=3600: (0, "doc\n"))
    assert got["stop"] == D.GATE, got
    (wait,) = _waits(repo, rid)
    assert wait["gate"] == _the_gate(repo)
    assert L.load(repo, rid)["status"] == "awaiting_approval"


def test_a_forced_move_is_not_a_wait(repo):
    rid = _at_the_gate(repo)
    S.advance(repo, rid, "P4", force=True)
    assert _waits(repo, rid) == []
