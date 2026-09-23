"""T450b: `status` names the gate a run waits at from the RESOLVED pipeline, and
`status --run` draws the run's place on the same picture `stage.py --list` draws.

The defect this closes was measured while planning T450: `status.PHASE_GATE` was
derived from the builtin `approve.GATE_PHASE`, so a run halted at an
`extra_gates` stop (`G-test`) or at a builtin the profile had re-anchored (G2 at
`requirements` once `design` is deleted) was reported as waiting on "a gate" —
in the one command whose job is saying who a run is waiting on.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import approve as A  # noqa: E402
from tools.sdlc import state as L  # noqa: E402
from tools.tests import _runs as R  # noqa: E402

STATUS = ROOT / "tools" / "sdlc" / "status.py"
WHO = "reviewer@example.com"
NO_DESIGN = ("phases: [intake, requirements, implementation, test, review, "
             "triage, closeout]\n")


def _repo(tmp_path: Path, profile: str) -> Path:
    r = tmp_path / "repo"
    (r / ".sdlc").mkdir(parents=True)
    (r / ".sdlc" / "profile.yml").write_text(
        "stack: python\ngate_profile: solo\n" + profile, encoding="utf-8")
    subprocess.run(["git", "-C", str(r), "init", "-q"], check=True,
                   capture_output=True)
    return r


def _parked(repo: Path, *phases: str) -> str:
    """A run walked through `phases`, parked awaiting approval at the last."""
    rid = L.create(repo, "requirement", "a change worth gating")["run_id"]
    for p in phases[:-1]:
        R.park(repo, rid, p, status="running")
    R.park(repo, rid, phases[-1])
    return rid


def _status(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(STATUS), "--target", str(repo), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          timeout=300, cwd=str(ROOT))


@pytest.mark.parametrize("profile, phases, gate", [
    ("extra_gates: [test]\n", ("P1", "P3", "P4", "P5"), "G-test"),
    (NO_DESIGN, ("P1",), "G2"),
], ids=["extra-gate", "re-anchored-builtin"])
def test_awaiting_names_the_gate_the_run_actually_waits_at(tmp_path, profile, phases, gate):
    """Spawned as a person meets it. Against the static map both read
    `waiting a gate`."""
    repo = _repo(tmp_path, profile)
    rid = _parked(repo, *phases)
    done = _status(repo, "--awaiting")
    assert done.returncode == 0, done.stdout + done.stderr
    assert f"waiting {gate} " in done.stdout, done.stdout
    assert "waiting a gate" not in done.stdout, done.stdout
    assert rid in done.stdout


def test_run_view_draws_the_run_on_the_pipeline(tmp_path):
    """`status --run` marks done steps, the current one, and each gate's live
    verdict or wait — on the same ASCII picture `--list` draws."""
    repo = _repo(tmp_path, "extra_gates: [test]\n")
    rid = _parked(repo, "P1", "P3")
    A.record(repo, rid, "G2", "approve", WHO)
    for p in ("P4", "P5"):
        R.park(repo, rid, p, status="running")
    L.advance(repo, rid, status="awaiting_approval")
    done = _status(repo, "--run", rid)
    assert done.returncode == 0, done.stdout + done.stderr
    out = done.stdout
    assert "P0 intake [done] --> P1 requirements [done]" in out, out
    assert "[G2 hard: approved]" in out, out
    assert "P5 test [here]" in out, out
    assert "[G-test hard: waiting]" in out, out
    assert "P6 review [" not in out, "a step not reached carries no mark"
    diagram = out.split("approval(s)", 1)[1]
    assert diagram.isascii(), [c for c in diagram if not c.isascii()]


def test_run_view_counts_loop_backs_from_history(tmp_path):
    """A run sent back from review to implementation is rework, and the
    picture says so from the run's own history."""
    repo = _repo(tmp_path, "")
    rid = _parked(repo, "P1", "P3", "P4", "P5", "P6")
    R.park(repo, rid, "P4", status="running")
    done = _status(repo, "--run", rid)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "loop-backs: P6 review -> P4 implementation (x1)" in done.stdout, done.stdout
    assert "P4 implementation [here]" in done.stdout, done.stdout


def test_an_unresolvable_pipeline_is_said_not_hidden(tmp_path):
    """A profile the resolver refuses leaves gates unnamed, and status SAYS why
    once rather than silently printing "a gate"."""
    repo = _repo(tmp_path, "")
    rid = _parked(repo, "P1", "P3")
    (repo / ".sdlc" / "profile.yml").write_text(
        "stack: python\ngate_profile: solo\nextra_gates: [nonsense]\n", encoding="utf-8")
    done = _status(repo, "--awaiting")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "gates shown unnamed" in done.stdout and "nonsense" in done.stdout, done.stdout
    assert rid in done.stdout
