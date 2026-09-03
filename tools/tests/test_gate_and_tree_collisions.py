"""Item 122 - two lifecycle steps that collide with a LIVE deploy.

Item 114's walk reached both only because it went P0 to P11 in one piece.

F10 is structural: P9 writes the deploy PLAN, stops for G5, then deploys and
records the outcome, so the phase's artifact map spans its own gate. Pinning all
of it meant following the SOP exactly voided the approval that permitted the
act - and the only ways past were re-taking G5 over bytes nobody re-read, or
forcing a deploy gate, which item 98 argues is indistinguishable from what the
gate exists to catch.

F11 is a guard that destroys what it guards, for the second time here after item
44's `emit_check`. `git worktree remove` is not atomic: pointed at a tree holding
an open file it deleted every tracked file it could - INCLUDING the run's own
deploy script, which knew how to stop the service still serving from that
directory - deregistered the worktree, and only then failed with `Invalid
argument`.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import stage as ST  # noqa: E402
from tools.sdlc import state as ledger  # noqa: E402
from tools.sdlc import worktree as W  # noqa: E402


def _git(where: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(where), *args],
                          capture_output=True, text=True, timeout=120)


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir(parents=True)
    (root / ".sdlc").mkdir()
    (root / "f.txt").write_text("x" + chr(10), encoding="utf-8")
    # What `worktree.claim_root` writes for real: the run trees live INSIDE the
    # repository and git is told not to see them. Without it the repo reads as
    # dirty and the uncommitted-work refusal fires before the one under test.
    (root / ".gitignore").write_text(
        ".sdlc-worktrees/" + chr(10) + ".sdlc/runs/" + chr(10), encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=d@x.invalid", "-c", "user.name=D",
         "commit", "-qm", "base")
    return root


def _tree(root: Path, run_id: str, name: str) -> Path:
    spot = root / ".sdlc-worktrees" / run_id / name
    _git(root, "worktree", "add", "-q", "-b", "sdlc/" + run_id + "-" + name,
         str(spot))
    return spot


def _record(root: Path, run_id: str, homes: dict) -> None:
    ledger.run_dir(root, run_id).mkdir(parents=True, exist_ok=True)
    W.save_record(root, run_id, {
        "schema_version": W.SCHEMA_VERSION, "run_id": run_id,
        "components": {
            name: {"mode": "worktree", "path": ".",
                   "branch": "sdlc/" + run_id + "-" + name,
                   "pattern": "sdlc/<run-id>", "base": "", "home": str(spot),
                   "role": "authoritative", "why": "", "at": ""}
            for name, spot in homes.items()}})


RUN = "20260902-000000-000000"


@pytest.fixture
def deployed(tmp_path: Path):
    root = _repo(tmp_path)
    spot = _tree(root, RUN, "svc")
    _record(root, RUN, {"svc": spot})
    _deployed_from(root, RUN, spot)
    return root, RUN, spot


@pytest.fixture
def two_trees(tmp_path: Path):
    """One real worktree, and one recorded home git will refuse to remove.

    **The failure is induced differently from the way the walk hit it, and
    deliberately.** There, a live JVM held a jar open inside the tree and
    Windows refused the delete. Holding a file open blocks deletion on Windows
    and NOT on Linux, so a test built that way passes on one platform's file
    semantics and proves nothing on the other - which is exactly what happened:
    it went green locally and red in CI.

    What is under test is the harness's behaviour when `git worktree remove`
    fails, not the reason git failed. A recorded home that is a committed
    directory rather than a registered worktree fails on every platform, for a
    reason git states, and reaches the same code path.
    """
    root = _repo(tmp_path)
    first = _tree(root, RUN, "alpha")
    second = root / "beta-not-a-worktree"
    second.mkdir()
    (second / "keep.txt").write_text("y" + chr(10), encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=d@x.invalid", "-c", "user.name=D",
         "commit", "-qm", "a directory that is not a worktree")
    _record(root, RUN, {"alpha": first, "beta": second})
    return root, RUN, first, second


# ── item 122: two lifecycle steps that collide with a LIVE deploy ───────────
#
# F10  P9 writes the deploy PLAN, stops for G5, then deploys and records the
#      outcome — so the phase's artifact map spans its own gate, and pinning
#      all of it made following the SOP exactly void the approval that
#      permitted the act.
# F11  Close-out removes the run's trees. `git worktree remove` is not atomic:
#      pointed at a tree holding an open file it deletes every tracked file it
#      can, deregisters the worktree, and only then fails.
#
# Both are about a deploy that is still running, which is why they are one item.


def test_a_gate_does_not_pin_the_outcome_of_the_act_it_authorises():
    """**F10.** Item 46 ruled that a gate cannot REQUIRE the thing it
    authorises; this extends the same argument to PINNING.

    Measured before it was fixed: `governs("G5", "90-deploy.json")` answered
    True, and the outcome escaped only by not existing yet at approval time —
    which stops being true the moment a gate is RE-TAKEN, as one was on the
    first whole-lifecycle walk.
    """
    assert ST.governs("G5", ST.DEPLOY_RESULT) is False
    assert ST.DEPLOY_RESULT in ST.artifacts_after("G5")


def test_the_deploy_PLAN_is_still_pinned_and_that_is_the_boundary():
    """**The control, and it is the security-relevant half.** Unpinning
    `90-deploy.md` would let the approved command change after the yes, which
    is exactly the property item 32 exists for. The plan is what G5 is a
    verdict about."""
    assert ST.governs("G5", "90-deploy.md") is True


def test_only_the_gates_own_phase_contributes_what_it_writes_after():
    """A phase with no `after_gate` is unaffected, so this cannot quietly
    unpin anything else. G4 stands at P7 and P7 writes nothing after it."""
    for stage in ST.STAGES:
        if stage.gate and stage.phase != "P9":
            assert stage.after_gate == (), (
                f"{stage.phase} declares after_gate; the pin exclusion widened "
                f"beyond the case it was built for")
    assert ST.governs("G4", "40-impl/") is True


def test_after_gate_does_not_unpin_the_phases_own_owed_artifacts():
    """`produces` is what the phase owes and what the gate judges. Only the
    named post-gate artifact is excluded, never the whole phase."""
    p9 = ST.by_phase("P9")
    for owed in p9.produces:
        assert ST.governs("G5", owed) is True, owed


# ── F11: the guard must not destroy what it guards ──────────────────────────


def _deployed_from(root: Path, run_id: str, where: Path, ok: bool = True) -> None:
    (ledger.run_dir(root, run_id) / ST.DEPLOY_RESULT).write_text(json.dumps({
        "schema_version": 1, "run": run_id, "where": str(where),
        "deployed": ok, "exit_code": 0 if ok else 1,
        "command": "java -jar target/app.jar"}), encoding="utf-8")


def test_a_tree_a_deploy_ran_from_is_refused_before_anything_is_deleted(deployed):
    """**F11's first half, and the ordering is the item.**

    On the walk the removal deleted the run's own `deploy.py` — the script that
    knew how to stop the service still serving from that directory — then
    failed with `Invalid argument`. The refusal has to come BEFORE the delete,
    which is the promise `--remove` already keeps for uncommitted work.
    """
    root, rid, spot = deployed
    with pytest.raises(W.WorktreeError) as caught:
        W.remove(root, rid)
    said = str(caught.value)
    assert "reported success" in said and "still be serving" in said
    assert "--force" in said and "You do not undeploy at close-out" in said
    assert spot.is_dir(), "the tree was deleted by a call that refused"


def test_force_overrides_the_deploy_refusal(deployed):
    """The control. A refusal with no way past it is one somebody deletes."""
    root, rid, spot = deployed
    assert W.remove(root, rid, force=True)
    assert not spot.is_dir()


def test_a_deploy_that_FAILED_does_not_hold_the_tree(deployed):
    """`deployed: false` means nothing was started, so there is nothing to be
    serving. Refusing on it would make close-out impossible after every failed
    deploy — the shape that gets a check switched off."""
    root, rid, spot = deployed
    _deployed_from(root, rid, spot, ok=False)
    assert W.remove(root, rid)
    assert not spot.is_dir()


def test_a_deploy_from_a_DIFFERENT_tree_does_not_hold_this_one(deployed):
    """The identity is the tree, not the run. A run that deployed from
    somewhere else says nothing about this directory."""
    root, rid, spot = deployed
    _deployed_from(root, rid, root / "somewhere-else")
    assert W.remove(root, rid)
    assert not spot.is_dir()


def test_no_deploy_record_at_all_is_not_a_reason_to_refuse(deployed):
    """Could-not-look is not a refusal here: most runs never deploy, and a
    close-out that stopped on a missing document would stop on every one."""
    root, rid, spot = deployed
    (ledger.run_dir(root, rid) / ST.DEPLOY_RESULT).unlink()
    assert W.remove(root, rid)
    assert not spot.is_dir()


def test_an_unreadable_deploy_record_does_not_block_close_out(deployed):
    """Same argument one step along, and stated because the opposite reading is
    tempting: a damaged document is not evidence that a service is running."""
    root, rid, spot = deployed
    (ledger.run_dir(root, rid) / ST.DEPLOY_RESULT).write_text(
        "{not json", encoding="utf-8")
    assert W.remove(root, rid)
    assert not spot.is_dir()


def test_a_partial_removal_keeps_the_record_of_what_it_removed(two_trees):
    """**F11's second half, found by reading and proved by running.**

    `save_record` sat after the loop the exception leaves, so a removal that
    failed part-way left the record disagreeing with the disk: whatever went
    first was gone AND unrecorded.
    """
    root, rid, first, second = two_trees
    with pytest.raises(W.WorktreeError) as caught:
        W.remove(root, rid)
    said = str(caught.value)
    assert "may be PART removed" in said
    assert "Removed before this" in said or "Nothing was removed" in said
    stamped = W.load_record(root, rid)["components"]
    assert stamped["alpha"].get("removed_at"), (
        "the tree that WAS removed is unrecorded, so the record disagrees "
        "with the disk")
    assert not stamped["beta"].get("removed_at")


def test_the_failure_names_what_to_do_rather_than_raw_git(two_trees):
    """The walk got `error: failed to delete '...': Invalid argument`, which
    says nothing about a running process, a port or a pid. Item 117's standard,
    one phase along: a refusal nobody can act on is one nobody acts on."""
    root, rid, first, second = two_trees
    with pytest.raises(W.WorktreeError) as caught:
        W.remove(root, rid)
    said = str(caught.value)
    assert "held open by a running process" in said
    assert "check it before reusing it" in said


def test_the_refusal_names_no_pid_because_the_harness_records_none(deployed):
    """**A correction to this item's own filing.** The row claimed the harness
    already records the serving pid in `90-deploy.json`. It does not — the
    record carries the command, the tree and the exit code, and the walk
    operator read the pid out of the deploy's stdout. The refusal says the true
    thing instead: a deploy ran from here and reported success."""
    root, rid, spot = deployed
    row = json.loads((ledger.run_dir(root, rid) / ST.DEPLOY_RESULT)
                     .read_text(encoding="utf-8"))
    assert "pid" not in row
    with pytest.raises(W.WorktreeError) as caught:
        W.remove(root, rid)
    # The PATH is stripped before the check: pytest derives tmp_path from the
    # test's own name, so this very test's directory contains "pid" and a bare
    # substring sweep matches it. Item 94 paid for that lesson once already.
    said = str(caught.value).lower().replace(str(root).lower(), "<root>")
    assert "pid" not in said, said
    assert "reported success" in said


# ── the SOPs that told the operator to do the colliding thing ───────────────


def test_the_deploy_sop_sends_the_outcome_to_the_unpinned_document():
    text = (ST.HARNESS / ST.SOP_DIR / "P9-deploy.md").read_text(encoding="utf-8")
    assert "you do not write\n   the outcome into `90-deploy.md`" in text
    assert "voids the gate that permitted the deploy" in text
    assert "and nothing added after the gate" in text


def test_the_closeout_sop_states_both_refusals_and_the_part_removal():
    text = (ST.HARNESS / ST.SOP_DIR / "P11-closeout.md").read_text(
        encoding="utf-8")
    assert "before it deletes anything" in text
    assert "you do not undeploy at close-out" in text.lower()
    assert "PART removed" in text


# ── item 129: could-not-look is not "nothing was serving" ───────────────────
#
# `deploy.py` records `exit_code: None` when the command could not be run to
# completion - under a comment saying could-not-look is its own answer here as
# everywhere else - and then writes `deployed: code == 0` on the NEXT LINE,
# which folds that answer into False. `serving_from` read the folded boolean.
#
# A timeout is the one shape a still-serving foreground process produces:
# `subprocess.run` gave up waiting, which is the opposite of knowing nothing is
# there. The item's own fixture command, `java -jar target/app.jar`, is exactly
# such a process - so the walk this item came from could delete the tree out
# from under the live service a second time.


def _deploy_row(root: Path, run_id: str, where: Path, code, deployed: bool,
                note: str = "") -> None:
    """A deploy record with the exit code stated separately from the verdict,
    which is the distinction this item turns on."""
    (ledger.run_dir(root, run_id) / ST.DEPLOY_RESULT).write_text(json.dumps({
        "schema_version": 1, "run": run_id, "where": str(where),
        "deployed": deployed, "exit_code": code, "stderr": note,
        "command": "java -jar target/app.jar"}), encoding="utf-8")


def test_a_deploy_that_never_reported_an_exit_code_holds_the_tree(deployed):
    """**The defect, in one case.** A timed-out deploy records exit_code None
    and deployed false; before this item the second was read and the first was
    not, so the tree was removed while something may well have been serving."""
    root, rid, spot = deployed
    _deploy_row(root, rid, spot, None, False,
                "the deploy did not finish within 600s")
    with pytest.raises(W.WorktreeError) as caught:
        W.remove(root, rid)
    assert "never reported an exit code" in str(caught.value)
    assert spot.is_dir(), "the tree was deleted while it may still be serving"


def test_the_unknown_case_says_so_rather_than_borrowing_the_success_sentence(deployed):
    """Two different repairs. A deploy that SUCCEEDED is stopped by whoever
    knows how; one nobody watched finish has to be LOOKED at before anybody can
    say what is running - so it does not claim the deploy reported success."""
    root, rid, spot = deployed
    _deploy_row(root, rid, spot, None, False, "timed out")
    said = W.serving_from(root, rid, spot)
    assert "unknown" in said and "look before you delete" in said
    assert "reported success" not in said, (
        "an unwatched deploy must not be described as a successful one")
    assert "--force" in said, "a refusal must name the way past it"


def test_a_real_non_zero_exit_still_does_not_hold_the_tree(deployed):
    """**The control, and it is the reason this is three states and not two.**
    Refusing on every failed deploy makes close-out impossible, which is the
    shape that gets a check switched off. An exit code the tool actually
    returned is evidence; None is the absence of it."""
    root, rid, spot = deployed
    _deploy_row(root, rid, spot, 1, False, "the deploy failed")
    assert W.serving_from(root, rid, spot) == ""
    assert W.remove(root, rid)
    assert not spot.is_dir()


def test_force_still_removes_a_tree_held_by_an_unknown_outcome(deployed):
    """Holding on could-not-look cannot paralyse close-out, which is the
    objection the old docstring raised against refusing at all."""
    root, rid, spot = deployed
    _deploy_row(root, rid, spot, None, False, "timed out")
    assert W.remove(root, rid, force=True)
    assert not spot.is_dir()


def test_a_record_with_no_exit_code_at_all_behaves_as_it_did_before():
    """`serving_from` reads records it did not write. A document from before
    `exit_code` existed must keep answering the way it used to - `deployed`
    decides - rather than becoming permanently unknown, which would hold every
    old run's tree forever. The default is 0 for exactly that reason."""
    root = Path(__import__("tempfile").mkdtemp()) / "r"
    (root / ".sdlc").mkdir(parents=True)
    rid = "20260902-000000-000000"
    folder = ledger.run_dir(root, rid)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ST.DEPLOY_RESULT).write_text(json.dumps({
        "where": str(root), "deployed": False,
        "command": "java -jar app.jar"}), encoding="utf-8")
    assert W.serving_from(root, rid, root) == ""


def test_the_deploy_record_still_states_the_exit_code_separately():
    """The fix reads `exit_code`, so it depends on `deploy.py` continuing to
    record could-not-look as None rather than folding it into the verdict. That
    is a claim about another module and is held here by name."""
    source = (ST.HARNESS / "tools" / "sdlc" / "deploy.py").read_text(
        encoding="utf-8")
    assert '"exit_code": code,' in source
    assert '"deployed": code == 0,' in source, (
        "deployed changed meaning; serving_from's three states assume it is "
        "still exactly 'the deploy reported success'")
