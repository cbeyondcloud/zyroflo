"""T485: a gate is read where it STANDS in the pipeline, not where `Stage.gate` names it.

`pipeline()` re-anchors a builtin gate whose step the profile removed onto the nearest
active step before it, and adds a profile's `extra_gates` stops beside the builtin - so a
step can hold a gate that is not its `Stage.gate`, or several. `halted_at` asked the
pipeline and halted there, while the runner's prompt, `standing_rejection` and `unanswered`
read `Stage.gate`. Two shapes, each asked of all three readers:

| shape                                   | the step          | what `Stage.gate` said |
|-----------------------------------------|-------------------|------------------------|
| `design` removed: G2 re-anchors         | P1 (requirements) | no gate at all         |
| `extra_gates: [design]`: G-design added | P3 (design)       | G2 only                |
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import approve as A  # noqa: E402
from tools.sdlc import stage as ST  # noqa: E402
from tools.sdlc import state as L  # noqa: E402
from tools.tests import _runs  # noqa: E402

WITHOUT_DESIGN = ("stack: python\ngate_profile: solo\n"
                  "phases: [intake, requirements, implementation, test, review, "
                  "triage, closeout]\n")
EXTRA_AT_DESIGN = "stack: python\ngate_profile: solo\nextra_gates: [design]\n"
WHY = "the error path is not specified at all"


def _run(tmp_path: Path, profile: str, phase: str) -> tuple[Path, str]:
    target = tmp_path / "repo"
    (target / ".sdlc").mkdir(parents=True)
    (target / ".sdlc" / "profile.yml").write_text(profile, encoding="utf-8")
    subprocess.run(["git", "-C", str(target), "init", "-q"], check=True,
                   capture_output=True, timeout=120)
    rid = L.create(target, "requirement", "a change worth making")["run_id"]
    _runs.park(target, rid, phase)
    return target, rid


def test_the_pipeline_is_the_premise(tmp_path):
    """The two shapes really are what this file says they are."""
    target, _ = _run(tmp_path / "a", WITHOUT_DESIGN, "P1")
    assert ST.gates_at(target, "P1") == (["G2"], "") and not ST.by_phase("P1").gate
    target, _ = _run(tmp_path / "b", EXTRA_AT_DESIGN, "P3")
    assert ST.gates_at(target, "P3") == (["G2", "G-design"], "")


def test_the_prompt_names_a_re_anchored_gate(tmp_path):
    """The run halts at G2 after requirements, and the runner was never told:
    P1's `Stage.gate` is empty, so the prompt said nothing about a gate."""
    target, rid = _run(tmp_path, WITHOUT_DESIGN, "P1")
    assert any("G2" in p for p in ST.halted_at(target, rid, "P1"))
    prompt = ST.render_prompt(target, rid, "P1")
    assert "G2 stands at the end of this phase" in prompt, prompt


def test_the_prompt_names_every_gate_at_a_step(tmp_path):
    target, rid = _run(tmp_path, EXTRA_AT_DESIGN, "P3")
    prompt = ST.render_prompt(target, rid, "P3")
    assert "G2 and G-design stand at the end of this phase" in prompt, prompt
    assert "You do not take them" in prompt, prompt


@pytest.mark.parametrize("profile, phase, gate", [
    (WITHOUT_DESIGN, "P1", "G2"), (EXTRA_AT_DESIGN, "P3", "G-design")])
def test_a_rejection_of_a_gate_standing_here_reaches_the_runner(tmp_path, profile,
                                                                phase, gate):
    """Item 136's fix, reached for the gates `Stage.gate` did not name: the
    reason a human gave was in the ledger and not in the next prompt."""
    target, rid = _run(tmp_path, profile, phase)
    A.record(target, rid, gate, "reject", "who@x.invalid", notes=WHY, force=True)
    assert ST.standing_rejection(target, rid, phase)[0] == gate
    prompt = ST.render_prompt(target, rid, phase)
    assert f"{gate} WAS REJECTED" in prompt and WHY in prompt, prompt


@pytest.mark.parametrize("profile, phase, gate, owed", [
    (WITHOUT_DESIGN, "P1", "G2", "10-requirements.md"),
    (EXTRA_AT_DESIGN, "P3", "G-design", "30-design.md")])
def test_the_driver_sees_a_rejection_that_was_not_answered(tmp_path, profile, phase,
                                                           gate, owed):
    """Item 138's UNANSWERED stop, for the same gates: the phase came back with
    the very bytes the human refused, and the driver reported GATE instead."""
    target, rid = _run(tmp_path, profile, phase)
    A.record(target, rid, gate, "reject", "who@x.invalid", notes=WHY, force=True)
    said = ST.unanswered(target, rid, phase)
    assert len(said) == 1 and f"standing {gate} rejection" in said[0], said
    assert owed in said[0], said


def test_a_step_with_no_gate_still_says_nothing(tmp_path):
    """Rule-28 control: P1 under the solo default holds no gate, and the prompt
    must not invent one."""
    target, rid = _run(tmp_path, "stack: python\ngate_profile: solo\n", "P1")
    assert ST.gates_at(target, "P1") == ([], "")
    assert "stands at the end of this phase" not in ST.render_prompt(target, rid, "P1")
