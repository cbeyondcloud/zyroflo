"""P9 — deploy, and never before a human has said yes. Item 46.

G5 is the only gate that repeats. Every other gate approves a CHANGE once; this
one approves an ACT with a bill attached, so the approval is about this deploy,
of this build, to this place.

The properties worth more than the rest of this file:

**Nothing runs without that yes**, and the yes must still speak for what it
approved — a deploy riding on a gate whose artifacts moved is a yes about a
different build.

**A blank `deploy_command` is not a deployment that succeeded.** The shape item
40's SCA gate wore for months and item 47 met again at P8: *nothing ran* and
*nothing failed* are identical output unless something insists they differ.

**Evidence is recorded whether it worked or not.** A failed deploy is the state
P10 most needs to know about, and a phase that only wrote evidence on success
would hide the one case somebody has to act on.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import approve  # noqa: E402
from tools.sdlc import deploy as D  # noqa: E402
from tools.sdlc import stage as ST  # noqa: E402
from tools.sdlc import state as ledger  # noqa: E402

PROFILE = ("stack: python\ngate_profile: solo\n"
           "phases: [intake, requirements, design, implementation, test, review, "
           "triage, release, deploy, closeout]\n"
           'test_command: "pytest -q"\nreview:\n  block_on: [critical, high]\n')


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / ".sdlc").mkdir(parents=True)
    (r / ".sdlc" / "profile.yml").write_text(PROFILE, encoding="utf-8")
    subprocess.run(["git", "-C", str(r), "init", "-q"], check=True,
                   capture_output=True, timeout=120)
    return r


def declare(repo: Path, command: str) -> None:
    p = repo / ".sdlc" / "profile.yml"
    p.write_text(p.read_text(encoding="utf-8") + f'deploy_command: "{command}"\n',
                 encoding="utf-8")


def at_p9(repo: Path, approved: bool = True) -> str:
    """A run sitting at P9, with G5 taken for real when asked."""
    run_id = ledger.create(repo, "deploy-change", "a config change")["run_id"]
    folder = ledger.run_dir(repo, run_id) / "40-impl"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "README.md").write_text("the change\n", encoding="utf-8")
    ledger.record_artifact(repo, run_id, "40-impl/")
    # P9 owes its plan BEFORE G5: the gate approves a documented deploy, not an
    # intention. `approve.record` refuses a yes about work that does not exist,
    # which is item 32's rule reaching the one gate that costs money.
    (ledger.run_dir(repo, run_id) / "90-deploy.md").write_text(
        "what will be deployed, where, and how to undo it\n", encoding="utf-8")
    ledger.record_artifact(repo, run_id, "90-deploy.md")
    ledger.advance(repo, run_id, phase="P9", note="ready to deploy")
    if approved:
        approve.record(repo, run_id, D.GATE, "approve", "owner@example.test",
                       notes="deploy approved for this build")
    return run_id


# ── nothing runs without the yes ────────────────────────────────────────────


def test_no_G5_refuses_the_deploy(repo):
    declare(repo, "echo deployed")
    run_id = at_p9(repo, approved=False)
    owed = D.gate_problems(repo, run_id)
    assert len(owed) == 1 and "no live G5" in owed[0]
    assert "costs something" in owed[0]


def test_a_live_G5_permits_it(repo):
    declare(repo, "echo deployed")
    assert D.gate_problems(repo, at_p9(repo)) == []


def test_a_G5_whose_artifacts_moved_no_longer_speaks_for_them(repo):
    """A deploy riding on a gate whose bytes changed is a yes about a different
    build. Item 32's void, arriving where it costs money."""
    declare(repo, "echo deployed")
    run_id = at_p9(repo)
    (ledger.run_dir(repo, run_id) / "40-impl" / "README.md").write_text(
        "something else entirely\n", encoding="utf-8")
    owed = D.gate_problems(repo, run_id)
    assert owed and "no longer speaks for what it approved" in owed[0]


def test_run_refuses_before_it_executes_anything(repo, tmp_path):
    """The refusal must come first. A deploy that ran and was then judged has
    already spent whatever it costs."""
    marker = tmp_path / "ran.txt"
    declare(repo, f"python -c \"open(r'{marker}','w').write('x')\"")
    run_id = at_p9(repo, approved=False)
    with pytest.raises(D.DeployError) as exc:
        D.run(repo, run_id)
    assert "no live G5" in str(exc.value)
    assert not marker.exists(), "the deploy command ran despite the refusal"


# ── a blank command is not a deploy that worked ─────────────────────────────


def test_a_blank_deploy_command_is_refused_not_reported_clean(repo):
    """*Nothing ran* and *nothing failed* are identical output unless something
    insists they differ - item 40's shape, and item 47 met it again at P8."""
    run_id = at_p9(repo)
    with pytest.raises(D.DeployError) as exc:
        D.run(repo, run_id)
    assert "nothing to run" in str(exc.value)
    assert "green nobody earned" in str(exc.value)


def test_a_declared_command_is_found_with_its_source(repo):
    declare(repo, "echo deployed")
    command, source = D.command_of(repo)
    assert command == "echo deployed" and source == "top-level"


def test_no_declaration_is_an_empty_command_not_a_guess(repo):
    assert D.command_of(repo) == ("", "")


# ── evidence, whether it worked or not ──────────────────────────────────────


def test_a_successful_deploy_is_recorded(repo):
    declare(repo, "echo deployed")
    run_id = at_p9(repo)
    row = D.run(repo, run_id)
    assert row["deployed"] is True and row["exit_code"] == 0
    assert row["approved_by"] == "owner@example.test"
    assert (ledger.run_dir(repo, run_id) / D.RESULT).is_file()


def test_a_FAILED_deploy_is_recorded_too(repo):
    """The state P10 most needs to know about. A phase that only wrote evidence
    on success would hide the one case somebody has to act on."""
    declare(repo, "python -c \"import sys; sys.exit(3)\"")
    run_id = at_p9(repo)
    row = D.run(repo, run_id)
    assert row["deployed"] is False and row["exit_code"] == 3
    assert D.recorded(repo, run_id)["exit_code"] == 3


def test_a_deploy_that_could_not_start_is_neither_pass_nor_fail(repo):
    """`exit_code: None` is could-not-look. Not 0, and not 1."""
    declare(repo, "this-command-does-not-exist-anywhere")
    run_id = at_p9(repo)
    row = D.run(repo, run_id)
    assert row["deployed"] is False
    assert row["exit_code"] not in (0,)          # either None or a shell's code


# ── wired into the phase contract ───────────────────────────────────────────


def test_p9_owes_its_record_through_the_conditional_seam(repo):
    assert ST.DEPLOY_RESULT in ST.by_phase("P9").conditional
    assert ST.CONDITIONAL_JUDGE["P9"][0] == "tools.sdlc.deploy"
    assert ST.DEPLOY_RESULT == D.RESULT          # one name, asked for


def test_a_run_at_p9_with_no_record_owes_one(repo):
    declare(repo, "echo deployed")
    run_id = at_p9(repo)
    owed = D.obligations(repo, run_id)
    assert len(owed) == 1 and D.RESULT in owed[0]
    assert "cannot verify" in owed[0] and "roll back" in owed[0]


def test_an_earlier_phase_owes_nothing_here(repo):
    declare(repo, "echo deployed")
    run_id = ledger.create(repo, "deploy-change", "a change")["run_id"]
    assert D.obligations(repo, run_id) == []


def test_a_repository_with_no_deploy_command_owes_nothing(repo):
    """A repository that declares no deploy is not one whose deploy failed.
    `blank = no deploy phase` is the profile's own wording."""
    run_id = at_p9(repo)
    assert D.obligations(repo, run_id) == []


def test_a_failed_deploy_holds_the_phase(repo):
    declare(repo, "python -c \"import sys; sys.exit(3)\"")
    run_id = at_p9(repo)
    D.run(repo, run_id)
    owed = D.obligations(repo, run_id)
    assert len(owed) == 1 and "exited 3" in owed[0]
    assert "nothing healthy to verify" in owed[0]


def test_a_successful_deploy_owes_nothing(repo):
    declare(repo, "echo deployed")
    run_id = at_p9(repo)
    D.run(repo, run_id)
    assert D.obligations(repo, run_id) == []


def test_an_unreadable_record_is_refused_not_read_as_deployed(repo):
    declare(repo, "echo deployed")
    run_id = at_p9(repo)
    (ledger.run_dir(repo, run_id) / D.RESULT).write_text("{not json",
                                                          encoding="utf-8")
    owed = D.obligations(repo, run_id)
    assert len(owed) == 1 and "could not be read" in owed[0]


def test_the_lazy_import_is_declared_in_the_bundle_map(repo):
    from tools.sdlc import validate as V

    assert "deploy" in V.LAZY_OUTSIDE_THE_BUNDLE


# ── the CLI ─────────────────────────────────────────────────────────────────


def test_the_cli_executes_nothing_without_the_flag(repo, tmp_path, capsys):
    marker = tmp_path / "ran2.txt"
    declare(repo, f"python -c \"open(r'{marker}','w').write('x')\"")
    run_id = at_p9(repo)
    assert D.main(["--target", str(repo), "--run", run_id]) == 0
    assert not marker.exists()
    assert "nothing was executed" in capsys.readouterr().out


def test_the_cli_reports_a_refused_gate(repo, capsys):
    declare(repo, "echo deployed")
    run_id = at_p9(repo, approved=False)
    assert D.main(["--target", str(repo), "--run", run_id]) == 1
    assert "REFUSED" in capsys.readouterr().out


# ── the run's tree, not the repository root ────────────────────────────────


def test_the_deploy_runs_in_the_runs_own_tree(repo, tmp_path):
    """**The defect the first real deploy produced, and the worst kind.**

    Since item 37 a run's change never lives in the repository root - it is on
    the branch, in the worktree. P9 ran the deploy command with the ROOT as its
    working directory, so it built the BASE and recorded `deployed: true`: a
    config change of 15000 to 30000 was deployed as 15000, and the health check
    passed because the previous process was still serving.

    This is `6a00bfc`'s defect one phase further on, and it costs more. A review
    of the wrong tree misreports; a DEPLOY of the wrong tree ships.
    """
    from tools.sdlc import worktree as W

    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True,
                   capture_output=True, timeout=120)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@e", "-c",
                    "user.name=t", "commit", "-qm", "base"], check=True,
                   capture_output=True, timeout=120)
    run_id = at_p9(repo)
    W.create(repo, run_id)
    home = Path(W.load_record(repo, run_id)["components"][
        next(iter(W.load_record(repo, run_id)["components"]))]["home"])
    assert home != repo, "the fixture must actually have a separate tree"

    # A command that reports WHERE it ran, in FULL. Its basename is not enough
    # and that is not a detail: the worktree's leaf directory is named for the
    # component, a single-component repository names its component after the
    # repository, and so `home.name == target.name` - an assertion on the
    # basename passes with the deploy running in either tree, which is what the
    # first cut of this test did.
    declare(repo, "python -c \"import os; print(os.getcwd())\"")
    row = D.run(repo, run_id)
    assert row["deployed"] is True
    assert row["where"] == str(home), row["where"]
    ran_in = Path(row["stdout"].strip()).resolve()
    assert ran_in == home.resolve(), f"{ran_in} != {home}"
    assert ran_in != repo.resolve(), "the deploy built the BASE, not the change"


def test_a_run_with_no_tree_deploys_from_the_root(repo):
    """The truth in that case rather than a default: a component denied a
    worktree works in its own checkout, and that is where its change is."""
    declare(repo, "echo deployed")
    run_id = at_p9(repo)
    row = D.run(repo, run_id)
    assert row["where"] == str(repo)


def test_a_deploy_that_never_finished_is_could_not_look(repo):
    """`exit_code: None` for a timeout, and NOT 1. A deploy still running when
    the harness stopped waiting has not failed - nobody knows what it did, and
    saying `failed` claims knowledge the harness does not have."""
    declare(repo, "python -c \"import time; time.sleep(30)\"")
    run_id = at_p9(repo)
    row = D.run(repo, run_id, timeout=2)
    assert row["exit_code"] is None and row["deployed"] is False
    assert "did not finish" in row["stderr"]
    owed = D.obligations(repo, run_id)
    assert len(owed) == 1 and "did not run to completion" in owed[0]


def test_a_deploy_the_os_refused_to_start_is_could_not_look(repo, monkeypatch):
    """The same answer from the other side. On Windows a bad command reaches a
    SHELL, which returns its own code, so this branch needs the OS to refuse -
    and it is the branch where reporting 1 would be a deploy that never ran
    being recorded as one that ran and failed."""
    declare(repo, "echo deployed")
    run_id = at_p9(repo)

    def refuse(*a, **k):
        raise OSError("the shell could not be executed")

    monkeypatch.setattr(subprocess, "run", refuse)
    row = D.run(repo, run_id)
    assert row["exit_code"] is None and row["deployed"] is False
    assert "could not be started" in row["stderr"]


def test_the_cli_exits_nonzero_when_the_deploy_failed(repo, capsys):
    """The exit code is what a runner reads. A CLI that returns 0 for a deploy
    that did not happen is the green nobody earned, reached by the one route
    every automated caller takes."""
    declare(repo, "python -c \"import sys; sys.exit(3)\"")
    run_id = at_p9(repo)
    assert D.main(["--target", str(repo), "--run", run_id, "--deploy"]) == 1
    assert "NOT deployed" in capsys.readouterr().out


def test_the_cli_exits_zero_when_it_did(repo):
    declare(repo, "echo deployed")
    run_id = at_p9(repo)
    assert D.main(["--target", str(repo), "--run", run_id, "--deploy"]) == 0


# ── the two gaps the mutation pass found ────────────────────────────────────


def test_a_component_deploy_command_beats_the_top_level_one(tmp_path):
    """A component that declares its own deploy wins, and the source SAYS so.

    An estate deploys per component; a top-level command silently overriding
    one is the whole workspace deployed by whatever the first component
    declared. Found by mutation: every existing test declared the top-level
    command alone, so the branch that prefers a component's could be deleted
    outright with the suite still green.
    """
    root = tmp_path / "estate"
    (root / ".sdlc").mkdir(parents=True)
    (root / "api").mkdir()
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True,
                   capture_output=True, timeout=120)
    (root / ".sdlc" / "profile.yml").write_text(
        "scope: estate\ngate_profile: team\n"
        "phases: [intake, requirements, design, implementation, test, review, "
        "triage, release, deploy, closeout]\n"
        "review:\n  block_on: [critical, high]\n"
        'deploy_command: "echo the whole estate"\n'
        "components:\n"
        '  - name: "api"\n    path: "api"\n    role: authoritative\n'
        '    deploy_command: "echo just the api"\n',
        encoding="utf-8")

    assert D.command_of(root, "api") == ("echo just the api", "component")
    # And with no component named, the top-level one is what a caller gets.
    assert D.command_of(root) == ("echo just the api", "component")


def test_a_run_PAST_p9_no_longer_owes_the_record(repo):
    """P9's obligation belongs to P9 and is discharged when the run moves on.

    The phase guard looked tested and was not: every existing case put the run
    at P0, where the G5 check refuses first and returns the same empty list for
    a different reason. A run at P10 with a LIVE G5 is the state that tells the
    two apart - and without the guard, a deploy record deleted after the fact
    would hold P10 and P11 shut on P9's behalf.
    """
    declare(repo, "echo deployed")
    run_id = at_p9(repo)
    assert D.obligations(repo, run_id), "P9 must owe its record while AT P9"
    ledger.advance(repo, run_id, phase="P10", note="deployed, verifying")
    assert D.gate_problems(repo, run_id) == [], "G5 must still stand at P10"
    assert D.obligations(repo, run_id) == []


def test_an_estate_that_deploys_PER_COMPONENT_can_run_p9(tmp_path):
    """`deploy_command` set is what activates this phase, and an estate sets it
    per component - which is the shape `sdlc-init.py` writes.

    Reading only the top-level scalar reported P9 as a phase the workspace
    CANNOT run, for exactly the workspaces that deploy component by component:
    `stage.active` folds it out, `skipped` says the capability is missing, and
    the deploy never happens with nobody having decided that.
    """
    root = tmp_path / "estate"
    (root / ".sdlc").mkdir(parents=True)
    (root / ".sdlc" / "profile.yml").write_text(
        "scope: estate\ngate_profile: team\n"
        "components:\n"
        '  - name: "api"\n    path: "api"\n    role: authoritative\n'
        '    deploy_command: "helm upgrade api"\n',
        encoding="utf-8")
    assert ST.capable(root, "deploy") is True


def test_a_workspace_that_declares_no_deploy_anywhere_cannot_run_p9(tmp_path):
    """The control. A capability that answers yes for everything reports
    nothing, and `blank = no deploy phase` is the profile's own wording."""
    root = tmp_path / "estate"
    (root / ".sdlc").mkdir(parents=True)
    (root / ".sdlc" / "profile.yml").write_text(
        "scope: estate\ngate_profile: team\n"
        "components:\n"
        '  - name: "api"\n    path: "api"\n    role: authoritative\n',
        encoding="utf-8")
    assert ST.capable(root, "deploy") is False


def test_a_timed_out_deploy_still_records_what_it_printed(repo):
    """The deploy that hung is exactly the one whose output P10 needs. The
    handler used to overwrite both streams with the empty string, so the
    record said `exit_code: None` and showed nothing that had been printed."""
    declare(repo, "python -c \"import sys,time; print('partial-out'); "
                  "print('partial-err', file=sys.stderr); sys.stdout.flush(); "
                  "sys.stderr.flush(); time.sleep(5)\"")
    run_id = at_p9(repo)
    row = D.run(repo, run_id, timeout=1)
    assert row["exit_code"] is None and row["deployed"] is False
    assert "partial-out" in row["stdout"], row["stdout"]
    assert "partial-err" in row["stderr"], row["stderr"]
    assert "did not finish" in row["stderr"], row["stderr"]


def test_a_noisy_hung_deploy_still_names_the_timeout_to_the_operator(repo):
    """Keeping the partial output is only half of it: the judge quotes the
    FIRST 200 characters of the recorded stderr, so a command that printed 400
    of them before it hung decides what the operator is shown. With the reason
    joined AFTER that output it fell off the end of the sentence, and a deploy
    the clock ended became indistinguishable from one that merely failed."""
    declare(repo, "python -c \"import sys,time; "
                  "print('x' * 400, file=sys.stderr); sys.stderr.flush(); "
                  "time.sleep(5)\"")
    run_id = at_p9(repo)
    row = D.run(repo, run_id, timeout=1)
    assert row["exit_code"] is None, row
    assert len(row["stderr"]) > 200, row["stderr"]
    owed = D.obligations(repo, run_id)
    assert len(owed) == 1, owed
    assert "did not finish" in owed[0], owed[0]
