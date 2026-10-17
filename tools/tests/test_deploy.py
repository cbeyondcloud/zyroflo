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
# `exit 0` rather than a quoted command, which kept its YAML escapes until T494
# (batch 5). `team`, because solo cannot be selected once a workspace has two
# components.

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
    # Batch 25 (D25-28, D25-45a): spelled from the repository's root, as coverage matches it.
    assert S.changed_files(repo, run_id)[0] == {"api": [], "web": ["web/app.txt"]}
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
    diffs, notes, unknown = S.changed_files(repo, run_id)
    assert diffs == {"api": [], "web": []} and sorted(unknown) == ["api", "web"], \
        (diffs, unknown)
    assert any("pom.xml" in n and "no component's directory" in n for n in notes), notes
    assert len(D.obligations(repo, run_id)) == 2, D.obligations(repo, run_id)


def test_the_harness_own_record_is_no_components_change(repo):
    """B4-17's control. A run folder is committed with the change it describes,
    so a change to the harness's own record - a run folder, the graph - must not
    make every component sharing the workspace's checkout changed, and owe it a
    deploy."""
    from tools.sdlc import suite as S

    two_deploys(repo)
    run_id = at_p9(repo)
    kept = (f".sdlc/runs/{run_id}/notes.md", ".sdlc/graph/snapshot.json")
    committed_monorepo(repo, run_id, *kept)
    for rel in kept:
        (repo / rel).write_text("v2\n", encoding="utf-8")
    assert S.changed_files(repo, run_id)[2] == []
    assert D.obligations(repo, run_id) == []


def test_a_change_to_the_deploy_config_under_sdlc_is_a_change(repo):
    """Review B4-18. The exclusion took ALL of `.sdlc/`, which dropped
    `.sdlc/deploy/` - what P9's SOP says a deploy reads from the run's branch -
    so a run whose whole change was deploy configuration owed no deploy."""
    two_deploys(repo)
    run_id = at_p9(repo)
    committed_monorepo(repo, run_id, ".sdlc/deploy/values.yaml")
    (repo / ".sdlc" / "deploy" / "values.yaml").write_text("v2\n", encoding="utf-8")
    assert len(D.obligations(repo, run_id)) == 2, D.obligations(repo, run_id)


@pytest.mark.parametrize("name", ["caf" + chr(0xE9) + ".txt", " lead.txt"])
def test_a_changed_file_is_named_as_it_is_spelled(repo, name):
    """T596 (review B11-24). The diffs were read without `-z`, so git QUOTED a non-ASCII path
    and P5 recorded `"caf\\303\\251.txt"` - a spelling no coverage entry matches - which also
    began with no component's directory, so T552's remainder ran every sharer as changed. And
    each line was stripped, which took the leading space off a name (B11-10's class)."""
    from tools.sdlc import suite as S

    two_deploys(repo)
    run_id = at_p9(repo)
    committed_monorepo(repo, run_id, "web/" + name)
    (repo / "web" / name).write_text("v2\n", encoding="utf-8")
    diffs, notes, unknown = S.changed_files(repo, run_id)
    # Batch 25 (D25-28, D25-45a): spelled from the repository's root.
    assert (diffs, unknown) == ({"api": [], "web": ["web/" + name]}, []), (diffs, unknown, notes)
    assert not [n for n in notes if "no component's directory" in n], notes


def test_a_component_whose_directory_begins_with_a_space_claims_its_change(repo):
    """T596's other half. Once the whole diff is read exactly, the prefix each component claims
    must be too: a stripped prefix (`api/` for ` api/`) claims nothing, its own change reads as
    lying in no component's directory, and every sharer runs as changed."""
    from tools.sdlc import suite as S
    from tools.sdlc import worktree as W

    two_deploys(repo)
    run_id = at_p9(repo)
    committed_monorepo(repo, run_id, " api/app.txt")
    record = W.load_record(repo, run_id)
    record["components"]["api"].update({"path": " api", "home": str(repo / " api")})
    W.save_record(repo, run_id, record)
    (repo / " api" / "app.txt").write_text("v2\n", encoding="utf-8")
    diffs, notes, unknown = S.changed_files(repo, run_id)
    # Batch 25 (D25-28, D25-45a): spelled from the repository's root.
    assert (diffs, unknown) == ({"api": [" api/app.txt"], "web": []}, []), (diffs, unknown,
                                                                          notes)
    assert not [n for n in notes if "no component's directory" in n], notes


def test_components_on_different_bases_do_not_invent_a_change(repo):
    """Review B4-19. What a component's directory holds was claimed by what its
    diff RETURNED, so two components based on different commits - one whose
    tree was made later, say at a scope-add - saw each other's history as a
    change in nobody's directory, and a run that changed nothing owed two
    deploys."""
    from tools.sdlc import suite as S
    from tools.sdlc import worktree as W

    two_deploys(repo)
    run_id = at_p9(repo)
    committed_monorepo(repo, run_id)
    (repo / "web" / "app.txt").write_text("v2\n", encoding="utf-8")
    for args in (("add", "-A"), ("-c", "user.email=d@x.invalid", "-c", "user.name=D",
                                 "commit", "-qm", "later")):
        subprocess.run(["git", "-C", str(repo), *args], check=True,
                       capture_output=True, timeout=120)
    record = W.load_record(repo, run_id)
    record["components"]["web"]["base"] = W.git(repo, "rev-parse", "HEAD")
    W.save_record(repo, run_id, record)
    diffs, notes, unknown = S.changed_files(repo, run_id)
    assert (diffs, unknown) == ({"api": [], "web": []}, []), (diffs, unknown, notes)
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


# ── batch 11: which changes count, and where a one-act deploy runs ───────────


def _commit_all(where: Path, message: str = "base") -> str:
    from tools.sdlc import worktree as W

    for args in (("add", "-A"), ("-c", "user.email=d@x.invalid", "-c", "user.name=D",
                                 "commit", "-qm", message)):
        subprocess.run(["git", "-C", str(where), *args], check=True,
                       capture_output=True, timeout=120)
    return W.git(where, "rev-parse", "HEAD")


def _squash_merge(where: Path, branch: str) -> None:
    """The way on a refusal names: pull the merged change, as a squash merge leaves
    it - new commits, the same content. With an identity, because a merge that is
    not a fast-forward needs one and CI's container has none."""
    subprocess.run(["git", "-C", str(where), "-c", "user.email=d@x.invalid",
                    "-c", "user.name=D", "merge", "-q", "--squash", branch],
                   check=True, capture_output=True, timeout=120)


def _repository(where: Path, *files: str) -> str:
    """A repository at `where` holding `files` (each `v1`), committed; its base."""
    where.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(where), "init", "-q"], check=True,
                   capture_output=True, timeout=120)
    for rel in files:
        (where / rel).parent.mkdir(parents=True, exist_ok=True)
        (where / rel).write_text("v1\n", encoding="utf-8")
    return _commit_all(where)


def _in_place(target: Path, run_id: str, base: str, paths: dict[str, str]) -> None:
    from tools.sdlc import policy as P
    from tools.sdlc import worktree as W

    W.save_record(target, run_id, {"components": {
        name: {"mode": "in-place", "path": path, "base": base,
               "home": str(P.component_dir(target, path))}
        for name, path in paths.items()}})


def test_a_component_git_cannot_place_is_named_not_dropped(repo, monkeypatch):
    """T553 (review B4-21). `changed_files` asks git where each component sits in
    its checkout, and swallowed a failure: that component's directory then claimed
    nothing, so its own change read as lying in no component's directory and every
    other component sharing the checkout ran as changed - the safe direction, and
    said nowhere. The failure is a note now, with no machine path in it."""
    from tools.sdlc import suite as S
    from tools.sdlc import worktree as W

    two_deploys(repo)
    run_id = at_p9(repo)
    committed_monorepo(repo, run_id)
    (repo / "api" / "app.txt").write_text("v2\n", encoding="utf-8")
    real = W.git

    def git(where, *args, **kwargs):
        if "--show-prefix" in args and Path(where).name == "api":
            raise W.WorktreeError(f"git rev-parse failed in {where}")
        return real(where, *args, **kwargs)

    monkeypatch.setattr(W, "git", git)
    diffs, notes, unknown = S.changed_files(repo, run_id)
    said = [n for n in notes if n.startswith("api: git could not say where it sits")]
    assert len(said) == 1, notes
    assert str(repo) not in said[0] and repo.as_posix() not in said[0], said
    # Batch 25 (D25-28, D25-45a): spelled from the repository's root.
    assert (diffs["api"], unknown) == (["api/app.txt"], ["web"]), (diffs, unknown)


def nested_workspace(tmp_path: Path, *extra: str) -> tuple[Path, str]:
    """T552's shape: a workspace installed in a SUBDIRECTORY of a larger
    repository - `mono`, with the workspace at `mono/tools/ws` and api and web in
    it - holding whatever else the repository holds (`extra`, from its root)."""
    ws = tmp_path / "mono" / "tools" / "ws"
    (ws / ".sdlc").mkdir(parents=True)
    two_deploys(ws)
    base = _repository(tmp_path / "mono", "tools/ws/api/app.txt",
                       "tools/ws/web/app.txt", *extra)
    run_id = at_p9(ws)
    _in_place(ws, run_id, base, {"api": "api", "web": "web"})
    return ws, run_id


#: Beside the workspace in its repository: a sibling project, a build input in a
#: subfolder of the root, and a directory beside the workspace's own.
ELSEWHERE = ("sibling/app.py", "buildSrc/conv.gradle", "tools/other/x.py")


def test_a_sibling_projects_change_is_named_not_every_sharers_change(tmp_path):
    """T552 (review B4-20; the owner's ruling A, 2026-09-28). In a workspace
    installed in a subdirectory of a larger repository, a change to a sibling
    project elsewhere in that repository made every sharing component changed, and
    owed each a deploy. Such a file is named in P5's notes and not counted."""
    from tools.sdlc import suite as S

    ws, run_id = nested_workspace(tmp_path, *ELSEWHERE)
    for rel in ELSEWHERE:
        (tmp_path / "mono" / rel).write_text("v2\n", encoding="utf-8")
    diffs, notes, unknown = S.changed_files(ws, run_id)
    assert (diffs, unknown) == ({"api": [], "web": []}, []), (diffs, unknown, notes)
    named = [n for n in notes if "not counted" in n]
    assert len(named) == 1 and all(rel in named[0] for rel in ELSEWHERE), notes
    assert D.obligations(ws, run_id) == []


@pytest.mark.parametrize("rel", ["pom.xml", "tools/build.gradle", "tools/ws/shared/lib.txt"])
def test_a_parent_build_file_and_the_workspaces_own_files_still_count(tmp_path, rel):
    """T552's other half, and why the bound is not the workspace alone: a file
    directly in a directory between the repository root and the workspace - where
    parent build files live - counts, as does anything inside the workspace. (This
    held before the bound too; it is what the bound must keep.)"""
    from tools.sdlc import suite as S

    ws, run_id = nested_workspace(tmp_path, rel, *ELSEWHERE)
    (tmp_path / "mono" / rel).write_text("v2\n", encoding="utf-8")
    diffs, notes, unknown = S.changed_files(ws, run_id)
    assert sorted(unknown) == ["api", "web"], (unknown, notes)
    assert any(rel in n and "no component's directory" in n for n in notes), notes
    assert len(D.obligations(ws, run_id)) == 2, D.obligations(ws, run_id)


APART = TEAM + ('components:\n'
                '  - name: "api"\n    path: "mono/services/api"\n'
                '    deploy_command: "exit 0"\n'
                '  - name: "web"\n    path: "mono/services/web"\n'
                '    deploy_command: "exit 0"\n')


@pytest.mark.parametrize("rel, counted", [
    ("pom.xml", True), ("services/pom.xml", True),
    ("sibling/app.py", False), ("services/tools/x.txt", False)])
def test_a_repository_that_does_not_hold_the_workspace_is_bounded_by_its_components(
        tmp_path, rel, counted):
    """T552, the reading the owner's go on batch 11 added (2026-09-30): where the
    sharing components live in a repository that does not contain the workspace -
    here one inside it - the bound is taken from their own directories. A file
    directly in a directory between that repository's root and a component counts;
    a sibling project's file, or one in a subfolder beside the components, is named
    and not counted. (The two counted cases were counted before the bound too:
    they are controls, what the bound must keep.)"""
    from tools.sdlc import suite as S

    root = tmp_path / "root"
    (root / ".sdlc").mkdir(parents=True)
    (root / ".sdlc" / "profile.yml").write_text(APART, encoding="utf-8")
    base = _repository(root / "mono", "services/api/app.txt", "services/web/app.txt",
                       rel)
    run_id = at_p9(root)
    _in_place(root, run_id, base, {"api": "mono/services/api", "web": "mono/services/web"})
    (root / "mono" / rel).write_text("v2\n", encoding="utf-8")
    diffs, notes, unknown = S.changed_files(root, run_id)
    assert sorted(unknown) == (["api", "web"] if counted else []), (unknown, notes)
    assert any(rel in n and ("not counted" in n) != counted for n in notes), notes


ONE_ACT = TEAM + 'deploy_command: "exit 0"\n' + ('components:\n'
                                                 '  - name: "api"\n    path: "api"\n'
                                                 '  - name: "web"\n    path: "web"\n')


def one_act_with_trees(repo: Path) -> str:
    """T551's shape: a workspace that deploys as ONE act - no component declares a
    deploy_command of its own - and a run with a tree for each component: a real
    worktree on the run's branch, apart from the root checkout, as P4 leaves it."""
    from tools.sdlc import worktree as W

    two_deploys(repo, ONE_ACT)
    run_id = at_p9(repo)
    record = {}
    for name in ("api", "web"):
        base = _repository(repo / name, "app.txt")
        home = repo / ".trees" / name
        W.git(repo / name, "worktree", "add", "-q", "-b", f"run-{name}", str(home), base)
        record[name] = {"mode": "worktree", "path": name, "base": base, "home": str(home)}
    W.save_record(repo, run_id, {"components": record})
    return run_id


def change_in_tree(repo: Path, name: str) -> None:
    """The run's change to one component, committed on its branch as P4 does."""
    (repo / ".trees" / name / "app.txt").write_text("v2\n", encoding="utf-8")
    _commit_all(repo / ".trees" / name, "the change")


def test_a_one_act_deploy_of_several_trees_waits_for_the_root_to_hold_the_change(repo):
    """T551 (the owner's ruling A, 2026-09-28). A workspace that deploys as one act
    ran its one command at the workspace root whenever the run had more than one
    tree - where the change is NOT until it is merged and pulled, item 37's hazard:
    the base deployed and recorded as the change. It runs there once the root
    checkout of every component the run changed holds the change, compared by
    content - a squash merge rewrites the commits - and refuses until then, naming
    the checkout and the two ways on. Following the first is accepted."""
    run_id = one_act_with_trees(repo)
    change_in_tree(repo, "api")
    with pytest.raises(D.DeployError) as refused:
        D.run(repo, run_id)
    said = str(refused.value)
    assert "api" in said and "app.txt" in said and "web" not in said, said
    assert "pull the merged change" in said and "deploy_command" in said, said
    assert D.recorded(repo, run_id) is None, "a refused deploy ran"
    # The way on, as a squash merge leaves it: new commits, the same content.
    _squash_merge(repo / "api", "run-api")
    _commit_all(repo / "api", "squashed")
    row = D.run(repo, run_id)
    assert row["deployed"] and row["where"] == ".", row


def test_a_changed_component_whose_change_cannot_be_read_is_named_not_assumed(repo):
    """T551. Whether the root holds a change nobody can read cannot be checked, so
    it is never assumed held: a component whose tree is gone is named."""
    import shutil

    run_id = one_act_with_trees(repo)
    shutil.rmtree(repo / ".trees" / "web")
    with pytest.raises(D.DeployError, match="web") as refused:
        D.run(repo, run_id)
    assert "could not be read" in str(refused.value), refused.value
    assert D.recorded(repo, run_id) is None


def _change_as(tree: Path, shape: str, scratch: Path) -> None:
    """The run's change to api, of a kind that tests how it is compared: a symlink,
    a submodule pointer or the executable bit alone, staged as git records each; or
    a file whose name is a pathspec pattern (`[ab].txt`) or begins with a space.
    Committed on the run's branch."""
    from tools.sdlc import worktree as W

    if shape in ("pattern", "space"):
        (tree / ("[ab].txt" if shape == "pattern" else " lead.txt")).write_text(
            "new\n", encoding="utf-8")
        _commit_all(tree, f"a {shape} change")
        return
    if shape == "mode":
        # On disk too, as a real `chmod +x` and commit leave it: where git records the
        # bit (Linux, CI), the index alone reads as an uncommitted change (B11-12).
        (tree / "app.txt").chmod(0o755)
        W.git(tree, "update-index", "--chmod=+x", "app.txt")
    else:
        if shape == "symlink":
            spelled = scratch / "link-target.txt"
            spelled.write_bytes(b"no-such-directory")
            mode, oid, rel = "120000", W.git(tree, "hash-object", "-w", str(spelled)), "current"
        else:
            mode, oid, rel = "160000", W.git(tree, "rev-parse", "HEAD"), "sub"
        W.git(tree, "update-index", "--add", "--cacheinfo", f"{mode},{oid},{rel}")
    W.git(tree, "-c", "user.email=d@x.invalid", "-c", "user.name=D", "commit", "-qm",
          f"a {shape} change")
    if shape == "symlink":
        W.git(tree, "checkout", "--", "current")    # a link on Linux, a file on Windows
    elif shape == "submodule":
        (tree / "sub").mkdir()      # as a checkout leaves a submodule nobody cloned


@pytest.mark.parametrize("shape", ["symlink", "submodule", "mode", "pattern", "space"])
def test_a_change_is_compared_as_git_records_it(repo, tmp_path, shape):
    """Reviews B11-1, B11-3, B11-8 and B11-10. Whether the root held the change was
    judged by the bytes of regular files, so a symlink or a submodule pointer - the
    same in both checkouts after the merge - was never held, and the way on the
    refusal named could not clear it; while the executable bit alone was always
    held, and the one act ran at a root that lacked it. git compares each path as it
    records it, each taken literally - `[ab].txt` as a pattern would also match the
    root's own `a.txt`, a difference no pull settles - and read as git wrote it, a
    leading space kept: refused before the way on, held after. (A symlink is a plain
    file in a Windows checkout, where the byte compare happened to hold; CI's Linux
    is where it did not. The submodule here is a bare pointer, which git judges by
    its recorded commit; one checked out at the root is judged at the commit it is
    at, so the way on names updating it.)"""
    run_id = one_act_with_trees(repo)
    _change_as(repo / ".trees" / "api", shape, tmp_path)
    if shape == "pattern":      # the root's own file, which no part of the change names
        (repo / "api" / "a.txt").write_text("the root's own\n", encoding="utf-8")
        _commit_all(repo / "api", "unrelated")
    with pytest.raises(D.DeployError, match="lacks 1 of api's changed file"):
        D.run(repo, run_id)
    _squash_merge(repo / "api", "run-api")
    _commit_all(repo / "api", "squashed")
    row = D.run(repo, run_id)
    assert row["deployed"] and row["where"] == ".", row


def test_a_change_a_tree_never_committed_is_named_not_held(repo):
    """Review B11-7. The run's change is the branch's commits, so a tracked edit P4
    left uncommitted in a tree changed nothing that was compared: the root was held,
    and the one act deployed the base while P5 said the component had changed. No
    merge can bring such an edit to the root, so it is named; committing it on the
    branch and merging it clears the refusal."""
    run_id = one_act_with_trees(repo)
    (repo / ".trees" / "api" / "app.txt").write_text("v2\n", encoding="utf-8")
    with pytest.raises(D.DeployError, match=r"api's tree holds 1 tracked change\(s\) never "
                                            r"committed \(app\.txt\)"):
        D.run(repo, run_id)
    _commit_all(repo / ".trees" / "api", "the change, committed")
    _squash_merge(repo / "api", "run-api")
    _commit_all(repo / "api", "squashed")
    row = D.run(repo, run_id)
    assert row["deployed"] and row["where"] == ".", row


def test_discarding_a_change_a_tree_never_committed_clears_the_refusal(repo):
    """Review B11-19. The refusal's first way on for an uncommitted change is the
    cheaper one - discard it in the tree - and following it is accepted: the run's
    change is then what the branch committed, which the root holds."""
    from tools.sdlc import worktree as W

    run_id = one_act_with_trees(repo)
    (repo / ".trees" / "api" / "app.txt").write_text("v2\n", encoding="utf-8")
    with pytest.raises(D.DeployError, match="discard them there, or commit them with the "
                                            "run's change and merge that"):
        D.run(repo, run_id)
    W.git(repo / ".trees" / "api", "checkout", "--", "app.txt")
    row = D.run(repo, run_id)
    assert row["deployed"] and row["where"] == ".", row


def test_the_run_folder_a_branch_carries_is_the_harness_record_not_the_change(repo):
    """Review B11-8. A run's branch carries its own run folder with the change, and
    the root's copy moves on after the merge, as the ledger records each later step.
    Compared as part of the change it could never be held, whatever was pulled; it
    is the harness's record, left out here as P5 leaves it out. (This passes on
    `main`, which never refused a one-act deploy; it defends the filter, and fails
    without it.)"""
    run_id = one_act_with_trees(repo)
    kept = Path(".sdlc") / "runs" / "r1" / "state.json"
    (repo / ".trees" / "api" / kept).parent.mkdir(parents=True)
    (repo / ".trees" / "api" / kept).write_text('{"phase": "P8"}\n', encoding="utf-8")
    change_in_tree(repo, "api")
    _squash_merge(repo / "api", "run-api")
    _commit_all(repo / "api", "squashed")
    (repo / "api" / kept).write_text('{"phase": "P9"}\n', encoding="utf-8")
    row = D.run(repo, run_id)
    assert row["deployed"] and row["where"] == ".", row


def test_a_component_changed_in_place_is_held_where_it_changed(repo):
    """Review B11-2. A component changed in place has no tree of its own: its
    checkout at the root is where it changed, so the root holds that change
    whatever can be read of it. Taken for a tree, one with no base - as a component
    denied a tree can leave it - was named as unreadable, and every one-act deploy
    of such a run was refused for it."""
    from tools.sdlc import worktree as W

    run_id = one_act_with_trees(repo)
    change_in_tree(repo, "api")
    record = W.load_record(repo, run_id)
    record["components"]["web"] = {"mode": "in-place", "path": "web", "base": "",
                                   "home": str(repo / "web")}
    W.save_record(repo, run_id, record)
    with pytest.raises(D.DeployError) as refused:
        D.run(repo, run_id)
    assert "api" in str(refused.value) and "web" not in str(refused.value), refused.value


def test_a_tree_the_root_checkout_cannot_compare_is_named_not_assumed(repo):
    """T551. A tree whose commit the checkout at the root does not have - here a
    clone of it, not a worktree - cannot be compared there, so it is named, never
    held by default."""
    from tools.sdlc import worktree as W

    run_id = one_act_with_trees(repo)
    clone = repo / ".trees" / "api-clone"
    subprocess.run(["git", "clone", "-q", str(repo / "api"), str(clone)], check=True,
                   capture_output=True, timeout=120)
    (clone / "app.txt").write_text("v2\n", encoding="utf-8")
    _commit_all(clone, "the change")
    record = W.load_record(repo, run_id)
    record["components"]["api"]["home"] = str(clone)
    W.save_record(repo, run_id, record)
    with pytest.raises(D.DeployError, match="the checkout at the root of api could not "
                                            "be compared"):
        D.run(repo, run_id)
    assert D.recorded(repo, run_id) is None


def test_a_component_below_its_repositorys_root_is_compared_where_its_tree_is(repo):
    """T551, found while building it: a worktree is of the WHOLE repository, so a
    component declared below its repository's root has the tree's top as its home,
    and its changed files are relative to that top. Compared against the
    component's own directory at the root, a pulled change never matched, and the
    refusal's way on could not be followed. They are compared in the checkout at
    the root, which the refusal names with its paths (review B11-5)."""
    from tools.sdlc import worktree as W

    two_deploys(repo, TEAM + 'deploy_command: "exit 0"\n' + (
        'components:\n'
        '  - name: "api"\n    path: "api"\n'
        '  - name: "web"\n    path: "mono/web"\n'))
    run_id = at_p9(repo)
    record = {}
    for name, rel, where in (("api", "app.txt", repo / "api"),
                             ("web", "web/app.txt", repo / "mono")):
        base = _repository(where, rel, "README.md")
        home = repo / ".trees" / name
        start = where / "web" if name == "web" else where
        W.git(start, "worktree", "add", "-q", "-b", f"run-{name}", str(home), base)
        record[name] = {"mode": "worktree", "path": "mono/web" if name == "web" else name,
                        "base": base, "home": str(home)}
    W.save_record(repo, run_id, {"components": record})
    (repo / ".trees" / "web" / "web" / "app.txt").write_text("v2\n", encoding="utf-8")
    _commit_all(repo / ".trees" / "web", "the change")
    with pytest.raises(D.DeployError, match=r"the checkout at mono lacks 1 of web's "
                                            r"changed file\(s\): web/app\.txt"):
        D.run(repo, run_id)
    _squash_merge(repo / "mono", "run-web")
    _commit_all(repo / "mono", "squashed")
    row = D.run(repo, run_id)
    assert row["deployed"] and row["where"] == ".", row


def test_the_workspaces_own_checkout_is_named_as_such(repo, tmp_path):
    """Review B11-15. A component declared at the workspace's root has the
    workspace's own checkout as its checkout there, which the refusal names as such
    rather than as "the checkout at ."."""
    from tools.sdlc import worktree as W

    two_deploys(repo, TEAM + 'deploy_command: "exit 0"\n' + (
        'components:\n'
        '  - name: "api"\n    path: "."\n'
        '  - name: "web"\n    path: "web"\n'))
    (repo / "app.txt").write_text("v1\n", encoding="utf-8")
    base = _commit_all(repo)
    run_id = at_p9(repo)
    home = tmp_path / "trees" / "api"
    W.git(repo, "worktree", "add", "-q", "-b", "run-api", str(home), base)
    W.save_record(repo, run_id, {"components": {
        "api": {"mode": "worktree", "path": ".", "base": base, "home": str(home)},
        "web": {"mode": "in-place", "path": "web", "base": base, "home": str(repo / "web")}}})
    (home / "app.txt").write_text("v2\n", encoding="utf-8")
    _commit_all(home, "the change")
    with pytest.raises(D.DeployError, match=r": the workspace checkout lacks 1 of api's "
                                            r"changed file\(s\): app\.txt"):
        D.run(repo, run_id)


def test_the_one_act_refusal_is_the_callers_to_fix_and_the_preview_says_so(repo):
    """T551, as a process: the refusal is the operator's to act on, so `--deploy`
    exits 2 with it (not 3, could-not-look), and the preview names it first, so
    nobody learns it from the deploy."""
    run_id = one_act_with_trees(repo)
    change_in_tree(repo, "api")
    door = [sys.executable, str(ROOT / "tools" / "sdlc" / "deploy.py"),
            "--target", str(repo), "--run", run_id]
    preview = subprocess.run(door, capture_output=True, text=True, timeout=300)
    assert preview.returncode == 1, (preview.stdout, preview.stderr)
    assert "!! this workspace deploys as one act" in preview.stdout, preview.stdout
    deploy = subprocess.run(door + ["--deploy"], capture_output=True, text=True,
                            timeout=300)
    assert deploy.returncode == 2, (deploy.stdout, deploy.stderr)
    assert deploy.stderr.startswith("refused: this workspace deploys as one act"), \
        deploy.stderr
    assert "Traceback" not in preview.stderr + deploy.stderr
    assert D.recorded(repo, run_id) is None


def test_a_one_act_preview_over_a_record_nobody_can_read_could_not_look(repo):
    """Review B11-6. The preview of a one-act deploy now reads the worktree record,
    so a record nobody can read is could-not-look there (3), as it already was for
    `--deploy` - never a preview that says the deploy may run."""
    from tools.sdlc import worktree as W

    two_deploys(repo, ONE_ACT)
    run_id = at_p9(repo)
    W.record_path(repo, run_id).write_text("{not json", encoding="utf-8")
    door = [sys.executable, str(ROOT / "tools" / "sdlc" / "deploy.py"),
            "--target", str(repo), "--run", run_id]
    for extra in ([], ["--deploy"]):
        run = subprocess.run(door + extra, capture_output=True, text=True, timeout=300)
        said = run.stdout + run.stderr
        assert run.returncode == 3, (extra, said)
        assert "worktree record could not be read" in said and "Traceback" not in said
    assert D.recorded(repo, run_id) is None


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
