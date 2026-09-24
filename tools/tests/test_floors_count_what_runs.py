"""T470 - `required_phases` is a floor on what the profile RUNS, not on what it lists.

`policy.py` defines the floor as the profile's active phase names, and both callers
that resolve it handed in `stage.declared(target)[0]` - the phases the profile
LISTS. A phase listed but skipped for a missing capability therefore satisfied the
floor: `required_phases: [verify]` beside a profile listing `verify` with no probes
(`stage --list`: "listed, but this workspace has no probes capability") approved
G2 at exit 0, while omitting `verify` was refused (found by the T468 review,
re-run by the lead).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import state as L  # noqa: E402
from tools.tests import _runs as R  # noqa: E402

PHASES = "phases: [intake, requirements, design, implementation, test, review, triage, closeout, verify]\n"
NO_PROBES = "stack: python\ngate_profile: solo\n" + PHASES
WITH_PROBES = NO_PROBES + "probes:\n  - http://localhost:8080/health\n"


def _repo(tmp_path: Path, name: str, profile: str) -> Path:
    root = tmp_path / name
    (root / ".sdlc").mkdir(parents=True)
    (root / ".sdlc" / "profile.yml").write_text(profile, encoding="utf-8")
    (root / ".sdlc" / "policy.yml").write_text("required_phases: [verify]\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True, capture_output=True)
    return root


def _spawn(script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ROOT / "tools" / "sdlc" / script), *args],
                          capture_output=True, text=True, timeout=300)


@pytest.mark.parametrize("profile, refused", [(NO_PROBES, True), (WITH_PROBES, False)],
                         ids=["listed-but-skipped", "listed-and-running"])
def test_the_gate_counts_a_required_phase_only_when_it_runs(tmp_path, profile, refused):
    root = _repo(tmp_path, "gate", profile)
    run = L.create(root, "requirement", "a change the org wants verified")["run_id"]
    R.park(root, run, "P3")
    done = _spawn("approve.py", "--target", str(root), "--run", run, "--gate", "G2",
                  "--verdict", "approve", "--approver", "dev@example.invalid")
    said = done.stdout + done.stderr
    if refused:
        assert done.returncode == 2, said
        assert "required_phases" in said and "verify" in said, said
    else:
        assert done.returncode == 0, f"the control: a verify that runs meets the floor\n{said}"


def test_the_wizard_check_finds_the_same_refusal(tmp_path):
    """`sdlc config --check` answers "would a run accept this?" - so it must meet
    the same floor the gate does, from the same resolved steps."""
    root = _repo(tmp_path, "check", NO_PROBES)
    done = _spawn("config.py", "--target", str(root), "--check")
    assert done.returncode == 1, done.stdout + done.stderr
    assert "required_phases" in done.stdout + done.stderr
