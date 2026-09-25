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


# ── T503: a workspace deploys each component by name ────────────────────────
#
# `exit 0` rather than a quoted command: component commands are still read
# through `_unquote` (T494, a later batch), which leaves YAML escapes in. `team`,
# because solo cannot be selected once a workspace has two components.

TEAM = PROFILE.replace("gate_profile: solo", "gate_profile: team")
TWO_DEPLOYS = TEAM + (   'components:\n'
                         '  - name: "api"\n    path: "api"\n    deploy_command: "exit 0"\n'
                         '  - name: "web"\n    path: "web"\n    deploy_command: "exit 0"\n')


def two_deploys(repo: Path, profile: str = TWO_DEPLOYS) -> None:
    """The profile, and the two checkouts a named deploy runs in (review B4-2:
    a component deploys from its own tree or path, never the workspace root)."""
    (repo / ".sdlc" / "profile.yml").write_text(profile, encoding="utf-8")
    for name in ("api", "web"):
        (repo / name).mkdir(exist_ok=True)


def test_a_workspace_of_deploying_components_is_deployed_by_name(repo):
    """T503. With no --component, the FIRST component's command ran from the
    workspace root and the rest were never deployed, recorded as
    `deployed: true`. Refused before anything runs; a named component deploys
    and the record says which one it was."""
    two_deploys(repo)
    run_id = at_p9(repo)
    with pytest.raises(D.DeployError, match="--component"):
        D.run(repo, run_id)
    assert D.recorded(repo, run_id) is None, "a refused deploy wrote a record"
    row = D.run(repo, run_id, "web")
    assert row["deployed"] and row["component"] == "web", row


def test_a_component_nobody_declared_is_refused_not_replaced(repo):
    """Found beside T503: a mistyped --component matched nothing and fell through
    to the top-level command - the wrong deploy, recorded as the one asked for."""
    two_deploys(repo)
    run_id = at_p9(repo)
    with pytest.raises(D.DeployError, match="no component named 'nope'"):
        D.run(repo, run_id, "nope")


def test_the_preview_says_a_component_is_needed_before_anything_runs(repo, capsys):
    """The same refusal on the no-`--deploy` screen, so the operator learns it
    before running the deploy rather than from it."""
    two_deploys(repo)
    run_id = at_p9(repo)
    assert D.main(["--target", str(repo), "--run", run_id]) == 1
    out = capsys.readouterr().out
    assert "--component" in out, out
    assert f"{D.GATE}: stands" in out, f"G5 stands; only the component is missing: {out}"


def test_each_component_is_owed_until_it_has_deployed(repo):
    """T549. The record is one file that each deploy rewrote, so deploying ONE
    of two components discharged P9. It keeps every named deploy now, and P9
    owes the rest by name."""
    two_deploys(repo)
    run_id = at_p9(repo)
    D.run(repo, run_id, "api")
    owed = D.obligations(repo, run_id)
    assert len(owed) == 1 and "web has no recorded deploy" in owed[0], owed
    D.run(repo, run_id, "web")
    assert D.obligations(repo, run_id) == []


def test_a_later_success_does_not_hide_an_earlier_failure(repo):
    """T549. P9 and P10 read the record's top-level fields, which are only the
    LAST deploy - so web succeeding after api failed read as a deployed
    workspace to both."""
    from tools.sdlc import verify as V

    two_deploys(repo, TWO_DEPLOYS.replace('deploy_command: "exit 0"',
                                          'deploy_command: "exit 3"', 1))
    run_id = at_p9(repo)
    assert not D.run(repo, run_id, "api")["deployed"]
    assert D.run(repo, run_id, "web")["deployed"]
    assert D.obligations(repo, run_id) == ["the deploy of api exited 3 - api is not out"]
    with pytest.raises(V.VerifyError, match="api exited 3"):
        V.deployed(repo, run_id)


def test_a_record_nobody_can_read_refuses_the_next_component_before_it_runs(repo):
    """T549. Rewriting it would drop the components it holds, and P9 would owe
    them again with nobody told why."""
    two_deploys(repo)
    run_id = at_p9(repo)
    record = ledger.run_dir(repo, run_id) / D.RESULT
    record.write_text("{not json", encoding="utf-8")
    with pytest.raises(D.DeployError, match="could not be read"):
        D.run(repo, run_id, "api")
    assert record.read_text(encoding="utf-8") == "{not json"


# ── review round 1 (batch 4): which deploys a run owes, and from where ──────


def trees_for(repo: Path, run_id: str, *names: str) -> None:
    """A worktree record holding these components, each in its own checkout -
    the record is what the deploy reads, and a real tree buys nothing here."""
    from tools.sdlc import worktree as W

    W.save_record(repo, run_id, {"components": {
        name: {"mode": "in-place", "path": name, "base": "",
               "home": str(repo / name)} for name in names}})


def test_a_first_component_with_no_deploy_does_not_excuse_the_rest(repo):
    """Review B4-1. P9's judge asked `command_of(target, "")`, which reads only
    the FIRST component - so a first component with no deploy answered "nothing
    to deploy" for a workspace whose second one deploys, and P9 closed with no
    deploy at all."""
    two_deploys(repo, TEAM + ('components:\n'
                              '  - name: "web"\n    path: "web"\n'
                              '  - name: "api"\n    path: "api"\n'
                              '    deploy_command: "exit 0"\n'))
    run_id = at_p9(repo)
    owed = D.obligations(repo, run_id)
    assert len(owed) == 1 and owed[0].startswith("api has no recorded deploy"), owed


def test_a_named_component_deploys_from_its_own_tree_never_another(repo):
    """Review B4-2. A component the record held no tree for fell back to "the
    only tree in the record", so `--component api` deployed web's tree and the
    record called it api's - item 37's failure, with a label on it."""
    two_deploys(repo)
    run_id = at_p9(repo)
    trees_for(repo, run_id, "web")
    assert D.run(repo, run_id, "api")["where"] == "api"


def test_a_worktree_record_nobody_can_read_is_refused_not_read_as_no_trees(repo):
    """Review B4-2's other half. An unreadable record read as "no trees", and
    the deploy ran from the root - which is where the change is NOT."""
    from tools.sdlc import worktree as W

    declare(repo, "exit 0")
    run_id = at_p9(repo)
    W.record_path(repo, run_id).write_text("{not json", encoding="utf-8")
    with pytest.raises(D.DeployError, match="worktree record could not be read"):
        D.run(repo, run_id)
    assert D.recorded(repo, run_id) is None, "the deploy ran from the wrong tree"


def test_a_component_that_inherits_the_top_level_deploy_is_deployed_and_owed(repo):
    """Review B4-3. "blank = inherit" is the installer's rule for every
    per-component command. With api overriding the top-level deploy, web - which
    inherits it - was neither owed nor deployable as itself, and the refusal
    claimed the first component's command would run at the root."""
    two_deploys(repo, TEAM + 'deploy_command: "exit 0"\n' + (
        'components:\n'
        '  - name: "api"\n    path: "api"\n    deploy_command: "exit 0"\n'
        '  - name: "web"\n    path: "web"\n'))
    run_id = at_p9(repo)
    with pytest.raises(D.DeployError, match="--component NAME: api, web"):
        D.run(repo, run_id)
    D.run(repo, run_id, "api")
    owed = D.obligations(repo, run_id)
    assert len(owed) == 1 and owed[0].startswith("web has no recorded deploy"), owed
    row = D.run(repo, run_id, "web")
    assert (row["command_source"], row["where"]) == ("top-level", "web"), row
    assert D.obligations(repo, run_id) == []


def test_only_the_components_this_run_changed_are_owed(repo):
    """Review B4-4. P9 owed every component declaring a deploy, whatever this
    run changed - manufacturing a deploy of an untouched component, which is
    the cost G5 exists to make somebody say yes to."""
    from tools.sdlc import verify as V

    two_deploys(repo)
    run_id = at_p9(repo)
    trees_for(repo, run_id, "api")
    assert D.obligations(repo, run_id)[0].startswith("api has no recorded deploy")
    D.run(repo, run_id, "api")
    assert D.obligations(repo, run_id) == []
    assert V.deployed(repo, run_id)["deployed"]


def committed_trees(repo: Path, run_id: str) -> None:
    """api and web as real repositories, each recorded with its base - the
    record P4 leaves by default, a tree for EVERY component."""
    from tools.sdlc import worktree as W

    bases = {}
    for name in ("api", "web"):
        where = repo / name
        (where / "app.txt").write_text("v1\n", encoding="utf-8")
        for args in (("init", "-q"), ("add", "-A"),
                     ("-c", "user.email=d@x.invalid", "-c", "user.name=D",
                      "commit", "-qm", "base")):
            subprocess.run(["git", "-C", str(where), *args], check=True,
                           capture_output=True, timeout=120)
        bases[name] = W.git(where, "rev-parse", "HEAD")
    W.save_record(repo, run_id, {"components": {
        name: {"mode": "worktree", "path": name, "base": bases[name],
               "home": str(repo / name)} for name in ("api", "web")}})


def test_a_tree_with_no_change_in_it_is_not_owed_a_deploy(repo):
    """Review B4-4, as P4 actually leaves it: a tree for EVERY component, which
    is the default. The record alone would owe them all, so "changed" is P5's
    answer - the diff against each recorded base."""
    two_deploys(repo)
    run_id = at_p9(repo)
    committed_trees(repo, run_id)
    (repo / "api" / "app.txt").write_text("v2\n", encoding="utf-8")
    owed = D.obligations(repo, run_id)
    assert len(owed) == 1 and owed[0].startswith("api has no recorded deploy"), owed


def committed_monorepo(repo: Path, run_id: str, *extra: str) -> None:
    """api and web as directories of ONE committed repository - the workspace's
    own - each recorded in place with that commit as its base."""
    from tools.sdlc import worktree as W

    for rel in ("api/app.txt", "web/app.txt", *extra):
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text("v1\n", encoding="utf-8")
    for args in (("add", "-A"), ("-c", "user.email=d@x.invalid", "-c", "user.name=D",
                                 "commit", "-qm", "base")):
        subprocess.run(["git", "-C", str(repo), *args], check=True,
                       capture_output=True, timeout=120)
    base = W.git(repo, "rev-parse", "HEAD")
    W.save_record(repo, run_id, {"components": {
        name: {"mode": "in-place", "path": name, "base": base,
               "home": str(repo / name)} for name in ("api", "web")}})


def test_components_sharing_one_checkout_are_diffed_one_by_one(repo):
    """Review B4-14. With no pathspec, components inside one repository each got
    the WHOLE repository's diff, so one component's change owed a paid deploy of
    every other - the shape a monorepo's declared service directories take."""
    from tools.sdlc import suite as S

    two_deploys(repo)
    run_id = at_p9(repo)
    committed_monorepo(repo, run_id)
    (repo / "web" / "app.txt").write_text("v2\n", encoding="utf-8")
    assert S.changed_files(repo, run_id)[0] == {"api": [], "web": ["app.txt"]}
    owed = D.obligations(repo, run_id)
    assert len(owed) == 1 and owed[0].startswith("web has no recorded deploy"), owed


def test_a_change_in_no_components_directory_is_every_sharers_change(repo):
    """Review B4-17. Scoping each diff to its component (B4-14) left a file
    changed OUTSIDE every component's directory - a monorepo's root build file -
    belonging to nobody: P5 ran nothing and P9 owed nothing, at exit 0. It is
    named, and every component sharing that checkout is run as changed."""
    from tools.sdlc import suite as S

    two_deploys(repo)
    run_id = at_p9(repo)
    committed_monorepo(repo, run_id, "pom.xml", "shared/lib.txt")
    for rel in ("pom.xml", "shared/lib.txt"):
        (repo / rel).write_text("v2\n", encoding="utf-8")
    diffs, notes, unread = S.changed_files(repo, run_id)
    assert diffs == {"api": [], "web": []} and sorted(unread) == ["api", "web"], \
        (diffs, unread)
    assert any("pom.xml" in n and "no component's directory" in n for n in notes), notes
    assert len(D.obligations(repo, run_id)) == 2, D.obligations(repo, run_id)


def test_the_harness_own_directory_is_no_components_change(repo):
    """B4-17's control. A run folder is committed with the change it describes,
    so a change under `.sdlc/` - here the profile - must not make every
    component sharing the workspace's checkout changed, and owe it a deploy."""
    from tools.sdlc import suite as S

    two_deploys(repo)
    run_id = at_p9(repo)
    committed_monorepo(repo, run_id)
    profile = repo / ".sdlc" / "profile.yml"
    profile.write_text(profile.read_text(encoding="utf-8") + "# touched\n",
                       encoding="utf-8")
    assert S.changed_files(repo, run_id)[2] == []
    assert D.obligations(repo, run_id) == []


def test_a_whole_deploy_keeps_the_trees_earlier_deploys_ran_from(repo):
    """Review B4-15. The whole deploy stands in for the named attempts before it
    for what is OWED, but it wrote over them, and close-out's serving guard - the
    third reader of this record - stopped holding a tree one of them had
    deployed from: item 129's hazard, which removed a live service's tree."""
    from tools.sdlc import worktree as W

    two_deploys(repo, TEAM + 'deploy_command: "exit 0"\n' + (
        'components:\n'
        '  - name: "api"\n    path: "api"\n'
        '  - name: "web"\n    path: "web"\n'))
    run_id = at_p9(repo)
    assert D.run(repo, run_id, "api")["where"] == "api"
    assert D.run(repo, run_id)["where"] == "."
    assert "may still be serving" in W.serving_from(repo, run_id, repo / "api")
    assert D.obligations(repo, run_id) == []


def test_a_run_that_changed_nothing_is_told_so(repo):
    """Review B4-11. With nothing changed, the refusal said none of the changed
    components declares a deploy_command - when both declare one, and the run
    changed neither.

    And a file git does not track is not a change - the harness's change is
    what P4 commits - so a report P5 wrote into a tree that does not ignore it
    owes nobody a deploy."""
    two_deploys(repo)
    run_id = at_p9(repo)
    committed_trees(repo, run_id)
    (repo / "api" / "junit.xml").write_text("<testsuite/>\n", encoding="utf-8")
    why = D.needs_component(repo, run_id)
    assert "this run changed none of its components" in why, why
    assert "declares a deploy_command" not in why, why
    assert D.obligations(repo, run_id) == []


def _command(repo: Path, command: str) -> None:
    p = repo / ".sdlc" / "profile.yml"
    text = p.read_text(encoding="utf-8")
    start = text.index("deploy_command:")
    end = text.index("\n", start)
    p.write_text(text[:start] + f'deploy_command: "{command}"' + text[end:],
                 encoding="utf-8")


def test_a_later_named_success_does_not_speak_for_a_whole_deploy_that_failed(repo):
    """Review B4-12, the hole it points at. A deploy naming no component was not
    kept under `components`, so a failed whole deploy followed by one
    component's success read as a deployed workspace: the top-level fields
    are only the LAST deploy."""
    from tools.sdlc import policy as guardrails

    declare(repo, "exit 3")
    run_id = at_p9(repo)
    assert not D.run(repo, run_id)["deployed"]
    _command(repo, "exit 0")
    (only,) = guardrails.components(repo)
    assert D.run(repo, run_id, only["name"])["deployed"]
    assert D.obligations(repo, run_id) == [
        "the deploy of the workspace exited 3 - the workspace is not out"]


def test_a_whole_deploy_stands_in_for_the_named_attempts_before_it(repo):
    """Review B4-12's control. In a workspace that deploys as one act, the whole
    deploy IS the deploy: once it has worked, an earlier failed attempt at one
    component is not owed a second, paid deploy of something already out."""
    from tools.sdlc import policy as guardrails

    declare(repo, "exit 3")
    run_id = at_p9(repo)
    (only,) = guardrails.components(repo)
    assert not D.run(repo, run_id, only["name"])["deployed"]
    _command(repo, "exit 0")
    assert D.run(repo, run_id)["deployed"]
    assert D.obligations(repo, run_id) == []


def test_the_p9_prompt_tells_the_runner_to_deploy_by_name(repo):
    """Review B4-8. The runner's prompt named `--deploy` alone - the command a
    workspace that deploys by name now refuses. The SOP was told; the prompt,
    which is what a runner actually reads first, was not."""
    two_deploys(repo)
    run_id = at_p9(repo)
    assert "--component NAME" in ST.render_prompt(repo, run_id, "P9")


def test_the_door_refuses_a_missing_component_as_an_argument(repo):
    """SPAWNED. A missing `--component` is the caller's to fix, like any refused
    argument: exit 2 and a sentence, not 3, which says the deploy could not look
    (the door's word for every DeployError)."""
    two_deploys(repo)
    run_id = at_p9(repo)
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "sdlc" / "deploy.py"), "--target",
         str(repo), "--run", run_id, "--deploy"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180)
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
    assert "--component NAME: api, web" in proc.stderr, proc.stderr
    assert D.recorded(repo, run_id) is None


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


def relabel(repo: Path, run_id: str, how: str) -> None:
    """Make the run's phase label unreadable while its state still loads."""
    path = ledger.run_dir(repo, run_id) / ledger.STATE_NAME
    state = json.loads(path.read_text(encoding="utf-8"))
    if how == "absent":
        state.pop("phase")
    else:
        state["phase"] = {"null": None, "list": [D.PHASE], "unknown": "P12",
                          "lower": D.PHASE.lower(), "trailing": D.PHASE + " ",
                          "leading": " " + D.PHASE}[how]
    path.write_text(json.dumps(state), encoding="utf-8")


@pytest.mark.parametrize("how", ["null", "list", "unknown", "absent",
                                 "lower", "trailing", "leading"])
def test_a_phase_label_nobody_can_read_is_not_a_phase_that_owes_nothing(repo, how):
    """T336. P9 owes its record only once the run is AT P9, so the label is a
    GATE INPUT, and one that is not a phase used to compare unequal to P9 and
    read as *not there yet*. `unknown` is the valid-looking wrong one: a string
    in a phase's shape that is not a phase, which a check asking only for a
    string would still have read as some other phase. `lower`, `trailing` and
    `leading` are near misses, held to the WRITER's contract: `state.advance`
    accepts only an exact phase name, so a reader that normalised them would
    call a run AT P9 that advancing the same run says is not."""
    declare(repo, "echo deployed")
    run_id = at_p9(repo)
    relabel(repo, run_id, how)
    owed = D.obligations(repo, run_id)
    assert len(owed) == 1 and "could not tell" in owed[0], owed
    assert D.RESULT in owed[0] and "not one of" in owed[0]


def test_a_state_nobody_can_read_is_not_a_phase_that_owes_nothing(repo):
    """The other fold at the same site: a state file that does not load."""
    declare(repo, "echo deployed")
    run_id = at_p9(repo)
    (ledger.run_dir(repo, run_id) / ledger.STATE_NAME).write_text(
        "{not json", encoding="utf-8")
    owed = D.obligations(repo, run_id)
    assert len(owed) == 1 and "could not tell" in owed[0], owed
    assert "not readable JSON" in owed[0]


def test_the_gate_holds_p9_over_a_label_nobody_can_read(repo):
    """The process a person runs. Before T336 this printed `P9: clean` and
    exited 0 over a run whose readable twin owed its deploy record."""
    declare(repo, "echo deployed")
    run_id = at_p9(repo)
    relabel(repo, run_id, "null")
    done = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "sdlc" / "stage.py"), "--target",
         str(repo), "--run", run_id, "--phase", "P9", "--verify"],
        capture_output=True, text=True, timeout=120)
    assert done.returncode == 1, done.stdout + done.stderr
    assert "could not tell" in done.stdout and "P9: 1 owed" in done.stdout
    assert "P9: clean" not in done.stdout


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
    # T127: `where` is stored RELATIVE (item 120), resolving to the live tree.
    assert not Path(row["where"]).is_absolute(), row["where"]
    assert (repo / row["where"]).resolve() == home.resolve()
    ran_in = Path(row["stdout"].strip()).resolve()
    assert ran_in == home.resolve(), f"{ran_in} != {home}"
    assert ran_in != repo.resolve(), "the deploy built the BASE, not the change"


def test_a_run_with_no_tree_deploys_from_the_root(repo):
    """The truth in that case rather than a default: a component denied a
    worktree works in its own checkout, and that is where its change is."""
    declare(repo, "echo deployed")
    run_id = at_p9(repo)
    row = D.run(repo, run_id)
    # T127: no tree -> the run root itself, stored relative (".") and resolving
    # back to the repo. Not the absolute machine path it used to be.
    assert not Path(row["where"]).is_absolute(), row["where"]
    assert (repo / row["where"]).resolve() == repo.resolve()


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


def test_deploy_where_is_recorded_relative_to_the_target(repo):
    """T127: 90-deploy.json must carry `where` RELATIVE to the run root, not an
    absolute machine path (item 120), and it must still resolve to the live tree
    via target/where."""
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
    declare(repo, "echo deployed")
    row = D.run(repo, run_id)
    assert not Path(row["where"]).is_absolute(), row["where"]
    assert (repo / row["where"]).resolve() == home.resolve()
