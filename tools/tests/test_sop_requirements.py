"""Item 121 — the requirements the harness enforced and the SOPs never stated.

Item 114's walk found three of item 33's class in ONE run: a phase instructed to
do something only run creation can do (F1), a merge gate needing the run folder
on the branch with no procedure saying to put it there (F9), and a deploy that
reads configuration from a branch nobody was told to carry it onto (O5).

They live in one file because they are one finding wearing three hats, and
because no existing file owns all of `stage`, `release` and three SOPs.

**Where a mechanism can carry the requirement it does; where only prose can, the
prose says why there is no check.** `6a00bfc` settled the shape: a path a caller
must get right is a path that will eventually be got wrong.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import release as REL  # noqa: E402
from tools.sdlc import stage as ST  # noqa: E402
from tools.sdlc import state as ledger  # noqa: E402


def _git(where: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(where), *args],
                          capture_output=True, text=True, timeout=120)


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".sdlc").mkdir(parents=True)
    (root / ".sdlc" / "profile.yml").write_text(
        "scope: repo" + chr(10) + "gate_profile: solo" + chr(10)
        + 'test_command: "pytest -q"' + chr(10),
        encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    return root


def run_at(ws: Path, phase: str = "P5") -> str:
    """A run folder at `phase`, without going through intake - these tests are
    about what a PROMPT states, not about how a run is opened."""
    rid = "20260902-000000-000000"
    folder = ledger.runs_dir(ws) / rid
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(
        json.dumps({"run_id": rid, "phase": phase, "status": "running"}),
        encoding="utf-8")
    return rid

# ── item 121: three requirements the harness enforced and never passed on ───
#
# All three are item 33's recorded class — *a requirement the runner is never
# told* — found in ONE run by item 114's walk:
#
#   F1  P0's SOP said to run the baseline suite and record it. Only
#       `intake.open_run` writes that block, so the instruction was addressed
#       to somebody who could no longer act on it.
#   F9  The merge gate requires the run folder on the BRANCH. No SOP said so,
#       and it failed after a push and an open pull request.
#   O5  The deploy runs from the run's worktree, so repository config fixed on
#       master never reaches it.
#
# Where a mechanism can carry the requirement it does; where only prose can,
# the prose says so and says why there is no check.


def _intake_json(root: Path, run_id: str, baseline: dict | None) -> None:
    folder = ledger.run_dir(root, run_id)
    folder.mkdir(parents=True, exist_ok=True)
    doc = {} if baseline is None else {"baseline": baseline}
    (folder / "00-intake.json").write_text(json.dumps(doc), encoding="utf-8")


def test_the_baseline_is_resolved_from_the_run_not_asked_of_a_caller(ws):
    """**F1.** The prompt asked a CALLER for P0's recorded pass count, and no
    caller downstream of P0 possesses it — `intake.open_run` is the only writer
    of that block. This is item 109's fix for `worktree`, one value along."""
    baseline = [v for s in ST.STAGES if s.phase == "P5"
                for v in s.values if v.key == "baseline"]
    assert baseline and baseline[0].source == "run", (
        "the prompt still asks a caller for a value only P0 can have written")


def test_supplying_the_baseline_is_refused_rather_than_ignored(ws):
    """The other half of a run-sourced value, which `resolve` already enforces:
    a supplied value nothing reads is the same defect as a value nobody
    supplies, arriving from the other side."""
    rid = run_at(ws, "P5")
    stage = [s for s in ST.STAGES if s.phase == "P5"][0]
    with pytest.raises(ST.StageError) as caught:
        ST.resolve(ws, stage, rid, {"baseline": "41"})
    assert "resolves baseline from the run" in str(caught.value)


def test_a_baseline_that_did_not_run_says_so_in_words_this_phase_can_act_on(ws):
    """**The sharpest half of F1, and it must not be propagated.**

    `intake`'s own note reads *"pass --baseline to execute it"* — sound advice
    at run creation and useless by the time a phase prompt is rendered, which
    is the defect one layer on. What a phase CAN do is stated instead, and
    whose act the remedy is.
    """
    rid = run_at(ws, "P5")
    _intake_json(ws, rid, {"ran": False, "exit_code": None, "tests": None,
                           "note": "recorded but NOT run - pass --baseline to "
                                   "execute it"})
    said = ST._baseline_for(ws, rid)
    assert "NOT measured" in said
    assert "OPERATOR" in said and "not this phase" in said
    assert "inventing a number" in said


def test_a_baseline_that_ran_states_what_it_measured(ws):
    """The control: the value must carry the number when there is one, or the
    phase is told nothing useful in the ordinary case."""
    rid = run_at(ws, "P5")
    _intake_json(ws, rid, {"ran": True, "exit_code": 0, "tests": 41,
                           "note": "baseline green"})
    said = ST._baseline_for(ws, rid)
    assert "exit 0" in said and "41 test(s)" in said and "baseline green" in said


def test_absent_and_unreadable_intake_are_different_sentences(ws):
    """Two different repairs: P0 never recorded one, versus the document is
    damaged. Folding them would hide which of the two happened."""
    rid = run_at(ws, "P5")
    folder = ledger.run_dir(ws, rid)
    (folder / "00-intake.json").unlink(missing_ok=True)
    absent = ST._baseline_for(ws, rid)
    (folder / "00-intake.json").write_text("{not json", encoding="utf-8")
    broken = ST._baseline_for(ws, rid)
    assert "is not there" in absent and "never recorded" in absent
    assert "could not be read" in broken and "repair the document" in broken
    assert absent != broken


def test_the_p0_sop_no_longer_instructs_a_baseline_it_cannot_record(ws):
    """The instruction was the part that was wrong, and the missing mechanism
    is missing ON PURPOSE: `intake` measures the baseline on the clean tree at
    run creation, so one recorded later describes a different tree."""
    text = (ST.HARNESS / ST.SOP_DIR / "P0-intake.md").read_text(encoding="utf-8")
    assert "Do NOT run the suite here" in text
    assert "describes a *different\n   tree*" in text or "different\n   tree" in text
    assert "re-open it with `--baseline`" in text


# ── F9: the run folder belongs on the branch ────────────────────────────────


def _repo_with_run(tmp_path: Path, run_id: str) -> Path:
    root = tmp_path / "pub"
    (root / ".sdlc").mkdir(parents=True)
    (root / ".sdlc" / "profile.yml").write_text("scope: repo\n", encoding="utf-8")
    (root / "code.py").write_text("x = 1\n", encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=d@x.invalid", "-c", "user.name=D",
         "commit", "-qm", "base")
    folder = ledger.runs_dir(root) / run_id
    folder.mkdir(parents=True)
    (folder / "state.json").write_text(json.dumps({"run_id": run_id}),
                                       encoding="utf-8")
    _git(root, "checkout", "-q", "-b", f"sdlc/{run_id}")
    (root / "code.py").write_text("x = 2\n", encoding="utf-8")
    _git(root, "add", "code.py")
    _git(root, "-c", "user.email=d@x.invalid", "-c", "user.name=D",
         "commit", "-qm", "the change, without the run folder")
    return root


def test_a_branch_without_its_run_folder_is_refused_before_the_push(tmp_path):
    """**F9, and the point is WHERE it is caught.**

    The gate was already correct; it fired in CI, after a branch was published
    and a pull request opened on a public fork. Asking the same question here
    moves the refusal to before anything leaves the machine.
    """
    rid = "20260902-000000-000000"
    root = _repo_with_run(tmp_path, rid)
    said = REL.unpublished_run(root, rid, f"sdlc/{rid}")
    assert said and ".sdlc/runs/" + rid in said[0]
    assert "committed with the code it describes" in said[0]


def test_committing_the_run_folder_clears_the_refusal(tmp_path):
    """The control. A check that never passes is one somebody deletes."""
    rid = "20260902-000000-000000"
    root = _repo_with_run(tmp_path, rid)
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=d@x.invalid", "-c", "user.name=D",
         "commit", "-qm", "carry the run folder")
    assert REL.unpublished_run(root, rid, f"sdlc/{rid}") == []


def test_the_check_is_silent_when_it_cannot_tell(tmp_path):
    """Could-not-look is not a refusal. A directory that is not a checkout, or
    a branch git does not know, is somebody else's error — blocking a push on
    it would be this check inventing one."""
    rid = "20260902-000000-000000"
    root = _repo_with_run(tmp_path, rid)
    assert REL.unpublished_run(root, rid, "no-such-branch") == []
    assert REL.unpublished_run(tmp_path / "not-a-repo", rid, "main") == []


def test_push_problems_asks_the_question_before_the_push(tmp_path):
    """Composed, not merely present. `push` refuses first and pushes second, so
    the check has to be reachable THROUGH `push_problems` — the producer and
    the consumer, which is this project's most-found gap."""
    rid = "20260902-000000-000000"
    root = _repo_with_run(tmp_path, rid)
    said = REL.push_problems(root, rid, f"sdlc/{rid}")
    assert any("does not carry" in p for p in said), said


def test_the_release_sop_states_the_step_and_names_the_hole_in_the_check(tmp_path):
    """The mechanism has a hole a test cannot close: P8 permits a
    `vcs.push_command` the runner invokes itself, which never reaches
    `release.push`. Both the step and the reason are in the SOP."""
    text = (ST.HARNESS / ST.SOP_DIR / "P8-release.md").read_text(encoding="utf-8")
    assert "carries the run folder before publishing" in text
    assert "goes around that check" in text
    assert "git ls-tree" in text


def test_the_implementation_sop_says_what_belongs_in_the_commit(tmp_path):
    """P4 committed code per tracker row and never said the run folder is part
    of the change. Discovering a forgotten `git add` at CI costs a published
    branch."""
    text = (ST.HARNESS / ST.SOP_DIR / "P4-implementation.md").read_text(
        encoding="utf-8")
    assert "The run folder is part of the change" in text


def test_the_deploy_sop_states_the_branch_requirement_and_why_nothing_checks(tmp_path):
    """**O5.** Prose, deliberately: a branch differing from its base is the
    NORMAL case, so a check would fire on every run and be switched off — the
    shape `prepass.py`'s own docstring names. The SOP says the requirement and
    says why there is no mechanism."""
    text = (ST.HARNESS / ST.SOP_DIR / "P9-deploy.md").read_text(encoding="utf-8")
    assert "carries the deploy configuration you expect" in text
    assert "Nothing checks this, deliberately" in text
    assert "switched off" in text
