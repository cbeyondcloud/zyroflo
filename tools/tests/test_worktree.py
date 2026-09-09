"""Item 37 — the parallel-run model: a run works in its own trees, never yours.

Item 35 shipped `confinement()`, which refuses a write outside a run's
worktree, and nothing had ever made one. These tests are mostly about the three
ways an isolation model stops isolating: it half-succeeds, it silently does
nothing for the component that needed it most, or the branch it creates is not
the branch the merge gate looks for.

The last is the sharpest and gets the most tests. A branch pattern known to the
creator and not to the checker leaves a CI gate installed, green, and never
asking the merge question at all.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import policy as P  # noqa: E402
from tools.sdlc import state as L  # noqa: E402
from tools.sdlc import validate as V  # noqa: E402
from tools.sdlc import worktree as W  # noqa: E402

DEV = ["-c", "user.email=dev@x.invalid", "-c", "user.name=Dev"]


def git(where: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(where), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=120)
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return proc.stdout.strip()


def commit(where: Path, message: str = "base") -> None:
    subprocess.run(["git", "-C", str(where), "add", "-A"], check=True, timeout=120,
                   capture_output=True)
    subprocess.run(["git", "-C", str(where), *DEV, "commit", "-qm", message],
                   check=True, timeout=120, capture_output=True)


def make_repo(path: Path, content: str = "x = 1\n") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(path), "init", "-q", "-b", "main"], check=True,
                   timeout=120, capture_output=True)
    (path / "app.py").write_text(content, encoding="utf-8")
    commit(path)
    return path


def profile(root: Path, body: str) -> None:
    (root / ".sdlc").mkdir(parents=True, exist_ok=True)
    (root / ".sdlc" / "profile.yml").write_text(body, encoding="utf-8")


ONE = 'stack: python\nscope: repo\nvcs:\n  deny_raw_git: false\n'

TWO = '''scope: estate
components:
  - name: "api"
    path: "api"
    role: authoritative
    vcs:
      commit_command: ""
      push_command: ""
      push_remote: ""
      branch_pattern: ""
      deny_raw_git: false
      notes: ""
  - name: "lib"
    path: "lib"
    role: authoritative
    vcs:
      commit_command: ""
      push_command: ""
      push_remote: ""
      branch_pattern: ""
      deny_raw_git: false
      notes: ""
'''


@pytest.fixture
def solo(tmp_path: Path) -> Path:
    root = make_repo(tmp_path / "repo")
    profile(root, ONE)
    commit(root, "harness")
    return root


@pytest.fixture
def estate(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    for name in ("api", "lib"):
        make_repo(root / name)
    profile(root, TWO)
    return root


def run_in(root: Path, rid: str = "r1") -> str:
    L.create(root, "requirement", "a change", run_id=rid)
    return rid


# ── the components reader ────────────────────────────────────────────────────


def test_a_single_repository_is_the_one_component_case(solo):
    (only,) = P.components(solo)
    assert only["path"] == "." and only["synthesised"] is True
    assert only["name"] == solo.name
    assert only["vcs"] == {"deny_raw_git": False}


def test_an_estate_reads_the_shape_the_installer_writes(estate):
    every = P.components(estate)
    assert [c["name"] for c in every] == ["api", "lib"]
    assert [c["path"] for c in every] == ["api", "lib"]
    assert every[0]["vcs"]["deny_raw_git"] is False


def test_deny_raw_git_reads_as_a_boolean_not_a_word(estate):
    profile(estate, TWO.replace("      deny_raw_git: false\n      notes: \"\"\n"
                                "  - name: \"lib\"",
                                "      deny_raw_git: true\n      notes: \"two git dirs\"\n"
                                "  - name: \"lib\"", 1))
    api = P.component(estate, "api")
    assert api["vcs"]["deny_raw_git"] is True and api["vcs"]["notes"] == "two git dirs"
    assert P.component(estate, "lib")["vcs"]["deny_raw_git"] is False


def test_an_unknown_component_is_refused_with_the_list(estate):
    with pytest.raises(P.PolicyError, match="'api', 'lib'"):
        P.component(estate, "web")


# ── the branch pattern, and the two sides that must share it ─────────────────


def test_the_default_pattern_is_the_policys_prefix_and_the_run(solo):
    rules = P.load(solo)
    assert P.branch_pattern(rules) == "sdlc/<run-id>"
    assert P.branch_for(P.branch_pattern(rules), "r1") == "sdlc/r1"


def test_making_a_branch_and_reading_it_back_are_inverses():
    for pattern in ("sdlc/<run-id>", "sdlc/run-<run-id>", "sdlc/<run-id>/work",
                    "sdlc/v1.2+<run-id>"):
        assert P.run_of_branch(pattern, P.branch_for(pattern, "abc-123")) == "abc-123"


def test_a_branch_that_is_not_a_runs_reads_as_no_run():
    for branch in ("main", "sdlc/", "feature/x", ""):
        assert P.run_of_branch("sdlc/<run-id>", branch) == ""


def test_a_pattern_with_regex_characters_is_not_read_as_a_regex():
    """Splitting on the token rather than compiling one keeps `v1.2+` meaning
    itself; a regex would read `.` and `+` as instructions."""
    assert P.run_of_branch("sdlc/v1.2+<run-id>", "sdlc/vXXY<run-id>") == ""
    assert P.run_of_branch("sdlc/v1.2+<run-id>", "sdlc/v1.2+r9") == "r9"


@pytest.mark.parametrize("pattern, why", [
    ("sdlc/fixed", "does not contain"),
    ("release/<run-id>", "push_branch_prefix"),
    ("sdlc/<run-id>-<run-id>", "more than once"),
])
def test_a_pattern_that_cannot_work_is_refused_where_it_is_declared(solo, pattern, why):
    assert any(why in p for p in P.pattern_problems(P.load(solo), pattern))


def test_the_ledger_gate_reads_the_repositorys_own_pattern(estate):
    """The item's sharpest edge. `validate.py` hardcoded `sdlc/`, so a
    repository that renamed its run branches kept the gate installed, green,
    and never asking the merge question."""
    profile(estate, TWO.replace('      branch_pattern: ""',
                                '      branch_pattern: "sdlc/change/<run-id>"'))
    assert V.patterns_of(estate) == ["sdlc/change/<run-id>"]
    assert V.run_of_branch("sdlc/change/r1", V.patterns_of(estate)) == "r1"
    assert V.run_of_branch("sdlc/r1", V.patterns_of(estate)) == "", \
        "the old hardcoded shape still matched, so the pattern was not read"


def test_the_gate_and_the_creator_agree_on_the_branch_by_construction(estate):
    profile(estate, TWO.replace('      branch_pattern: ""',
                                '      branch_pattern: "sdlc/change/<run-id>"'))
    rid = run_in(estate)
    made = W.create(estate, rid)
    for entry in made["components"].values():
        assert V.run_of_branch(entry["branch"], V.patterns_of(estate)) == rid


def test_an_unreadable_profile_falls_back_to_the_conventional_pattern(tmp_path):
    """The integrity checks must keep running whatever else is wrong, and
    asking the merge question about the conventional branch is strictly better
    than asking it about none."""
    assert V.patterns_of(tmp_path / "nowhere") == ["sdlc/<run-id>"]


# ── the push wall, per component ─────────────────────────────────────────────


def test_a_component_may_choose_a_remote_the_policy_allows_and_no_other(solo):
    rules = {**P.DEFAULTS, "push_remote": ["origin", "fork"]}
    assert P.push_target(rules, "fork", "sdlc/r1", "r1") == []
    assert any("one of" in p for p in P.push_target(rules, "elsewhere", "sdlc/r1", "r1"))


def test_a_single_remote_still_reads_as_it_always_did(solo):
    assert P.push_target(P.DEFAULTS, "origin", "sdlc/r1", "r1") == []
    assert any("'origin'" in p for p in
               P.push_target(P.DEFAULTS, "upstream", "sdlc/r1", "r1"))


def test_a_fork_is_read_from_and_never_pushed_to():
    problems = P.push_target(P.DEFAULTS, "origin", "sdlc/r1", "r1", {"role": "fork"})
    assert any("never pushed to" in p for p in problems)


def test_a_component_pattern_moves_the_wall_with_it():
    vcs = {"branch_pattern": "sdlc/change/<run-id>"}
    assert P.push_target(P.DEFAULTS, "origin", "sdlc/change/r1", "r1", vcs) == []
    assert P.push_target(P.DEFAULTS, "origin", "sdlc/r1", "r1", vcs) != []


def test_a_branch_from_another_run_is_still_refused():
    assert any("another run" in p for p in
               P.push_target(P.DEFAULTS, "origin", "sdlc/r2", "r1"))


def test_a_component_naming_a_remote_the_policy_forbids_is_named(estate):
    """Found by the mutation pass: the DECLARATION check had no test, only the
    push wall did. Catching it at the declaration is the point — an operator is
    sent to the file, rather than meeting it later as a push that will not go."""
    profile(estate, TWO.replace('      push_remote: ""',
                                '      push_remote: "somewhere-else"'))
    problems = P.vcs_problems(P.load(estate), P.component(estate, "api"))
    assert any("component api" in p and "somewhere-else" in p for p in problems)
    assert any("origin" in p for p in problems), "the refusal never says what IS allowed"


def test_a_component_choosing_an_allowed_remote_is_accepted(estate):
    (estate / ".sdlc" / "policy.yml").write_text(
        "schema_version: 1\npush_remote: [origin, fork]\n", encoding="utf-8")
    profile(estate, TWO.replace('      push_remote: ""', '      push_remote: "fork"'))
    assert P.vcs_problems(P.load(estate), P.component(estate, "api")) == []


def test_a_component_declaring_a_role_that_is_not_one_is_named(estate):
    profile(estate, TWO.replace("    role: authoritative", "    role: mirror", 1))
    problems = P.vcs_problems(P.load(estate), P.component(estate, "api"))
    assert any("role 'mirror'" in p for p in problems)


def test_a_bad_declaration_stops_the_trees_being_made(estate):
    """`create` reads the same predicate, so a workspace that cannot publish
    never gets as far as branches nobody could push."""
    profile(estate, TWO.replace('      push_remote: ""',
                                '      push_remote: "somewhere-else"'))
    rid = run_in(estate)
    with pytest.raises(W.WorktreeError, match="somewhere-else"):
        W.create(estate, rid)


def test_a_component_declaring_an_unusable_pattern_is_named(estate):
    profile(estate, TWO.replace('      branch_pattern: ""',
                                '      branch_pattern: "release/<run-id>"'))
    problems = P.vcs_problems(P.load(estate), P.component(estate, "api"))
    assert any("component api" in p and "push_branch_prefix" in p for p in problems)


# ── making the trees ─────────────────────────────────────────────────────────


def test_a_run_gets_one_tree_per_component_under_the_policys_root(estate):
    rid = run_in(estate)
    made = W.create(estate, rid)
    for name in ("api", "lib"):
        entry = made["components"][name]
        assert entry["mode"] == "worktree"
        assert Path(entry["home"]).is_dir()
        assert Path(entry["home"]).parent.name == rid
        assert Path(entry["home"]).parent.parent.name == ".sdlc-worktrees"
        assert git(Path(entry["home"]), "rev-parse", "--abbrev-ref", "HEAD") == f"sdlc/{rid}"


def test_the_developers_own_checkout_is_untouched(estate):
    rid = run_in(estate)
    before = git(estate / "api", "rev-parse", "--abbrev-ref", "HEAD")
    W.create(estate, rid)
    assert git(estate / "api", "rev-parse", "--abbrev-ref", "HEAD") == before
    assert git(estate / "api", "status", "--porcelain") == ""


def test_the_worktrees_root_comes_from_the_policy_and_is_not_a_constant(estate):
    (estate / ".sdlc" / "policy.yml").write_text(
        'schema_version: 1\nworktrees_root: ".runs"\n', encoding="utf-8")
    rid = run_in(estate)
    made = W.create(estate, rid)
    assert Path(made["components"]["api"]["home"]).parent.parent.name == ".runs"


def test_the_base_commit_is_recorded_at_creation(estate):
    rid = run_in(estate)
    head = git(estate / "api", "rev-parse", "HEAD")
    W.create(estate, rid)
    assert W.base_of(estate, rid, "api") == head
    (estate / "api" / "app.py").write_text("x = 2\n", encoding="utf-8")
    commit(estate / "api", "moved on")
    assert W.base_of(estate, rid, "api") == head, \
        "the base followed upstream, so every diff would be against a moving point"


def test_a_dirty_component_refuses_the_whole_set(estate):
    rid = run_in(estate)
    (estate / "api" / "app.py").write_text("x = 99\n", encoding="utf-8")
    with pytest.raises(W.WorktreeError, match="uncommitted change"):
        W.create(estate, rid)
    assert not (estate / ".sdlc-worktrees").exists(), \
        "lib was made anyway, so the run's diffs come from two different worlds"


def test_a_component_that_is_not_a_repository_is_named(estate):
    (estate / "web").mkdir()
    profile(estate, TWO + '  - name: "web"\n    path: "web"\n    role: authoritative\n')
    rid = run_in(estate)
    with pytest.raises(W.WorktreeError, match="not a git repository"):
        W.create(estate, rid)


def test_a_branch_that_already_exists_refuses_rather_than_reusing_it(estate):
    rid = run_in(estate)
    git(estate / "api", "branch", f"sdlc/{rid}")
    with pytest.raises(W.WorktreeError, match="already exists"):
        W.create(estate, rid)


def test_dry_run_makes_nothing(estate):
    rid = run_in(estate)
    planned = W.create(estate, rid, dry_run=True)
    assert planned["dry_run"] is True and len(planned["components"]) == 2
    assert not (estate / ".sdlc-worktrees").exists()
    assert W.load_record(estate, rid)["components"] == {}


def test_only_the_named_components_are_made(estate):
    rid = run_in(estate)
    made = W.create(estate, rid, ["lib"])
    assert list(made["components"]) == ["lib"]


def test_naming_a_component_that_does_not_exist_is_refused(estate):
    rid = run_in(estate)
    with pytest.raises(W.WorktreeError, match="no component named 'web'"):
        W.create(estate, rid, ["web"])


# ── the component that must not have one ─────────────────────────────────────


def test_deny_raw_git_gets_no_worktree_and_says_why(estate):
    """A worktree carries only the primary `.git`. For a repository with a
    second one, making it is how half the state disappears quietly."""
    profile(estate, TWO.replace('      deny_raw_git: false\n      notes: ""\n'
                                '  - name: "lib"',
                                '      deny_raw_git: true\n      notes: "two git dirs"\n'
                                '  - name: "lib"', 1))
    rid = run_in(estate)
    made = W.create(estate, rid)
    api, lib = made["components"]["api"], made["components"]["lib"]
    assert api["mode"] == "in-place" and "deny_raw_git" in api["why"]
    assert "two git dirs" in api["why"]
    assert not (Path(estate) / ".sdlc-worktrees" / rid / "api").exists()
    assert lib["mode"] == "worktree" and Path(lib["home"]).is_dir()


def test_an_in_place_component_cannot_be_rebased_by_the_harness(estate):
    profile(estate, TWO.replace('      deny_raw_git: false\n      notes: ""\n'
                                '  - name: "lib"',
                                '      deny_raw_git: true\n      notes: ""\n'
                                '  - name: "lib"', 1))
    rid = run_in(estate)
    W.create(estate, rid)
    with pytest.raises(W.WorktreeError, match="works in place"):
        W.rebase(estate, rid, "api", "main")


# ── removing them ────────────────────────────────────────────────────────────


def test_closure_takes_the_trees_away(estate):
    rid = run_in(estate)
    made = W.create(estate, rid)
    homes = [Path(e["home"]) for e in made["components"].values()]
    W.remove(estate, rid)
    assert not any(h.exists() for h in homes)
    assert all(e.get("removed_at") for e in
               W.load_record(estate, rid)["components"].values())


def test_a_tree_with_unfinished_work_in_it_is_not_deleted(estate):
    rid = run_in(estate)
    made = W.create(estate, rid)
    spot = Path(made["components"]["api"]["home"])
    (spot / "app.py").write_text("half a thought\n", encoding="utf-8")
    with pytest.raises(W.WorktreeError, match="only copy"):
        W.remove(estate, rid)
    assert spot.is_dir()
    W.remove(estate, rid, force=True)
    assert not spot.exists()


def test_removing_twice_is_not_an_error(estate):
    rid = run_in(estate)
    W.create(estate, rid)
    W.remove(estate, rid)
    assert all("already gone" in line for line in W.remove(estate, rid))


# ── two runs at once ─────────────────────────────────────────────────────────


def test_two_runs_in_the_same_component_are_flagged_advisory(estate):
    first, second = run_in(estate, "r1"), run_in(estate, "r2")
    W.create(estate, first)
    W.create(estate, second, ["lib"])
    found = W.overlap(estate, second)
    assert len(found) == 1 and "r1" in found[0] and "lib" in found[0]
    assert "advisory" in found[0]


def test_two_runs_in_different_components_do_not_collide(estate):
    first, second = run_in(estate, "r1"), run_in(estate, "r2")
    W.create(estate, first, ["api"])
    W.create(estate, second, ["lib"])
    assert W.overlap(estate, second) == []


def test_a_closed_run_no_longer_overlaps_anything(estate):
    first, second = run_in(estate, "r1"), run_in(estate, "r2")
    W.create(estate, first)
    W.create(estate, second, ["lib"])
    assert W.overlap(estate, second)
    L.advance(estate, first, status="closed")
    assert W.overlap(estate, second) == []


def test_two_runs_get_different_branches_and_different_trees(estate):
    first, second = run_in(estate, "r1"), run_in(estate, "r2")
    a = W.create(estate, first, ["lib"])["components"]["lib"]
    b = W.create(estate, second, ["lib"])["components"]["lib"]
    assert a["branch"] != b["branch"] and a["home"] != b["home"]


def test_a_run_folder_with_no_state_does_not_stop_the_overlap_check(estate):
    """An advisory check that dies on one malformed file names no overlap at
    all — item 44's lesson, applied here."""
    first = run_in(estate, "r1")
    W.create(estate, first)
    broken = L.runs_dir(estate) / "wrecked"
    broken.mkdir()
    (broken / "state.json").write_text("{not json", encoding="utf-8")
    assert "wrecked" in W.active_runs(estate)
    second = run_in(estate, "r2")
    W.create(estate, second, ["lib"])
    assert W.overlap(estate, second)


# ── catching up with upstream ────────────────────────────────────────────────


def test_a_clean_rebase_reports_what_arrived(estate):
    rid = run_in(estate)
    W.create(estate, rid, ["api"])
    (estate / "api" / "other.py").write_text("y = 1\n", encoding="utf-8")
    commit(estate / "api", "upstream work")
    result = W.rebase(estate, rid, "api", "main")
    assert result["rebased"] is True and len(result["upstream"]) == 1
    assert "impact check" in result["note"]


def test_nothing_to_rebase_says_so_rather_than_doing_it(estate):
    rid = run_in(estate)
    W.create(estate, rid, ["api"])
    result = W.rebase(estate, rid, "api", "main")
    assert result["upstream"] == [] and result["rebased"] is False
    assert "already on the latest" in result["note"]


def test_a_conflict_is_aborted_and_named_as_work_that_re_enters_at_g3(estate):
    """A repository left mid-rebase is a state no later phase knows how to
    read, and an agent that finds one will improvise."""
    rid = run_in(estate)
    made = W.create(estate, rid, ["api"])
    spot = Path(made["components"]["api"]["home"])
    (spot / "app.py").write_text("mine\n", encoding="utf-8")
    commit(spot, "the run's change")
    (estate / "api" / "app.py").write_text("theirs\n", encoding="utf-8")
    commit(estate / "api", "upstream change")
    result = W.rebase(estate, rid, "api", "main")
    assert result["rebased"] is False and result["conflicts"] == ["app.py"]
    assert "G3" in result["note"] and "ABORTED" in result["note"]
    assert git(spot, "status", "--porcelain") == "", "the tree was left mid-rebase"
    assert (spot / "app.py").read_text(encoding="utf-8") == "mine\n"


# ── the record ───────────────────────────────────────────────────────────────


def test_a_corrupt_record_is_a_refusal_not_a_fresh_start(estate):
    rid = run_in(estate)
    W.record_path(estate, rid).write_text("{not json", encoding="utf-8")
    with pytest.raises(W.WorktreeError, match="not readable JSON"):
        W.load_record(estate, rid)


def test_the_record_names_the_pattern_that_made_the_branch(estate):
    rid = run_in(estate)
    made = W.create(estate, rid, ["api"])
    assert made["components"]["api"]["pattern"] == "sdlc/<run-id>"


def test_asking_about_a_component_this_run_never_made_is_refused(estate):
    rid = run_in(estate)
    W.create(estate, rid, ["api"])
    with pytest.raises(W.WorktreeError, match="no tree recorded"):
        W.base_of(estate, rid, "lib")


# ── the CLI ──────────────────────────────────────────────────────────────────


def test_cli_creates_lists_and_removes(estate, capsys):
    rid = run_in(estate)
    assert W.main(["--target", str(estate), "--run", rid, "--create"]) == 0
    assert "2 component(s) ready" in capsys.readouterr().out
    assert W.main(["--target", str(estate), "--run", rid, "--list"]) == 0
    out = capsys.readouterr().out
    assert "api" in out and f"sdlc/{rid}" in out
    assert W.main(["--target", str(estate), "--run", rid, "--remove"]) == 0
    assert "removed" in capsys.readouterr().out


def test_cli_overlap_exits_one_so_a_script_can_read_it(estate, capsys):
    first, second = run_in(estate, "r1"), run_in(estate, "r2")
    W.create(estate, first)
    W.create(estate, second, ["lib"])
    assert W.main(["--target", str(estate), "--run", "r2", "--overlap"]) == 1
    assert "advisory" in capsys.readouterr().out
    assert W.main(["--target", str(estate), "--run", "r1", "--overlap"]) == 1


def test_cli_rebase_exits_one_on_conflict(estate, capsys):
    rid = run_in(estate)
    made = W.create(estate, rid, ["api"])
    spot = Path(made["components"]["api"]["home"])
    (spot / "app.py").write_text("mine\n", encoding="utf-8")
    commit(spot, "the run's change")
    (estate / "api" / "app.py").write_text("theirs\n", encoding="utf-8")
    commit(estate / "api", "upstream change")
    assert W.main(["--target", str(estate), "--run", rid, "--component", "api",
                   "--rebase", "main"]) == 1
    assert "conflict: app.py" in capsys.readouterr().out


def test_cli_says_what_it_cannot_do_rather_than_tracing_back(estate, capsys):
    assert W.main(["--target", str(estate), "--create"]) == 2
    assert "needs --run" in capsys.readouterr().out
    assert W.main(["--target", str(estate), "--run", "nope", "--create"]) == 2
    assert "no run folder" in capsys.readouterr().out


def test_cli_json_is_machine_readable(estate, capsys):
    rid = run_in(estate)
    W.main(["--target", str(estate), "--run", rid, "--create", "--json"])
    parsed = json.loads(capsys.readouterr().out)
    assert set(parsed["components"]) == {"api", "lib"}


# ── the seam the field run found ─────────────────────────────────────────────


def test_the_reader_reads_what_the_real_generator_writes(tmp_path):
    """The hand-written fixtures above are a guess at the installer's output,
    and a guess is exactly what let this ship broken: the unit tests passed
    against my shape while the CI gate, reading a real installed profile, fell
    back to the hardcoded pattern and said nothing.

    So this one runs `render_components` itself and reads its output back.
    Producer and consumer, composed — the seam item 29's review named.
    """
    spec = importlib.util.spec_from_file_location("sdlc_init", ROOT / "sdlc-init.py")
    init = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(init)
    rendered = init.render_components([
        {"name": "api", "path": "api", "role": "authoritative", "stack": "python",
         "vcs": {"push_remote": "fork", "branch_pattern": "sdlc/change/<run-id>",
                 "deny_raw_git": False, "notes": "n"}},
        {"name": "lib", "path": "lib", "role": "fork", "stack": "python",
         "vcs": {"deny_raw_git": True, "notes": "two git dirs"}},
    ])
    root = tmp_path / "ws"
    profile(root, "scope: estate\n" + rendered + "\n")
    api, lib = P.components(root)
    assert api["name"] == "api" and api["path"] == "api"
    assert api["vcs"]["push_remote"] == "fork"
    assert api["vcs"]["branch_pattern"] == "sdlc/change/<run-id>"
    assert api["vcs"]["deny_raw_git"] is False
    assert lib["role"] == "fork" and lib["vcs"]["deny_raw_git"] is True
    assert V.patterns_of(root) == ["sdlc/change/<run-id>", "sdlc/<run-id>"]


@pytest.mark.parametrize("reader", ["components", "top_vcs", "component_count",
                                    "gate_overrides", "profile_dials", "locate"])
def test_every_profile_reader_takes_a_string_target(estate, reader):
    """`Path(target)` at the boundary. A reader that raises TypeError on a str
    is a reader whose caller falls back to a default, and the CI gate's default
    is the hardcoded pattern this item exists to stop using."""
    fn = getattr(P, reader)
    fn(str(estate)) if reader != "components" else fn(str(estate))


def test_a_profile_that_cannot_be_read_is_a_finding_not_a_quiet_fallback(estate):
    """The except clause reintroduced the very defect the item removed: the
    gate went on asking the merge question about a branch shape the repository
    may have stopped publishing to, and said nothing about it."""
    (estate / ".sdlc" / "policy.yml").write_text("this: [is: not: readable\n",
                                                 encoding="utf-8")
    patterns, note = V._patterns(estate)
    assert patterns == ["sdlc/<run-id>"] and note
    assert "may not be what this repository publishes" in note
    report = V.check(estate)
    assert note in report["problems"]


def test_no_profile_at_all_is_not_a_finding(tmp_path):
    """A repository with no harness install has no runs for the merge question
    to be about, so defaulting there is silence rather than a shrug."""
    patterns, note = V._patterns(tmp_path)
    assert patterns == ["sdlc/<run-id>"] and note == ""


# ── the harness's own trees, and the two gates that were scanning them ───────
#
# `claim_root` puts the worktrees INSIDE the repository on purpose, and the
# `.gitignore` it writes only stops git from seeing them. Any scanner told to
# ignore git's exclusions walks straight in, and a worktree is a full COPY - so
# every finding in one is a second report of a fact already reported at its real
# path, at an address the repository does not have. Measured on a real
# repository: 36 of 72 manifests and 386 of 789 findings, three-fold with two
# runs open, and the count moves when a run is opened or closed.


def test_own_trees_is_read_from_the_constitution_not_a_constant(tmp_path):
    root = tmp_path / "repo"
    (root / ".sdlc").mkdir(parents=True)
    (root / ".sdlc" / "policy.yml").write_text(
        "worktrees_root: .my-trees\n", encoding="utf-8")
    assert W.own_trees(root) == ".my-trees"


def test_a_path_inside_the_harness_own_tree_is_recognised(tmp_path):
    root = tmp_path / "repo"
    (root / ".sdlc").mkdir(parents=True)
    (root / ".sdlc" / "policy.yml").write_text(
        "worktrees_root: .sdlc-worktrees\n", encoding="utf-8")
    assert W.is_own_tree(root, ".sdlc-worktrees/run-1/comp/pom.xml")
    # A scanner on Windows prints backslashes, which is the whole reason
    # `is_own_tree` normalises before it compares.
    assert W.is_own_tree(root, r".sdlc-worktrees\run-1\comp\pom.xml")
    assert W.is_own_tree(root, "./.sdlc-worktrees/run-1/comp/pom.xml")


def test_the_repositorys_own_files_are_not_mistaken_for_the_harness_trees(tmp_path):
    root = tmp_path / "repo"
    (root / ".sdlc").mkdir(parents=True)
    (root / ".sdlc" / "policy.yml").write_text(
        "worktrees_root: .sdlc-worktrees\n", encoding="utf-8")
    for path in ("pom.xml", "src/main/App.java", ".sdlc/profile.yml",
                 ".sdlc-worktrees-backup/pom.xml",
                 "vendor/.sdlc-worktrees.md"):
        assert not W.is_own_tree(root, path), path


def test_a_repository_with_no_policy_still_knows_its_own_trees(tmp_path):
    """No policy file means the DEFAULTS, and the defaults are where the
    harness would put its trees - so a filter built on them is right rather
    than merely safe."""
    root = tmp_path / "plain"
    root.mkdir()
    default = P.DEFAULTS["worktrees_root"]
    assert W.own_trees(root) == default.replace("\\", "/").strip("/")
    assert W.is_own_tree(root, default + "/r/c/pom.xml")


def test_a_policy_that_cannot_be_read_filters_NOTHING(tmp_path):
    """A filter that cannot answer must not answer yes. `""` rather than a
    guess, so a caller drops nothing rather than everything - between losing a
    duplicate and losing a real finding, only one is worth risking."""
    root = tmp_path / "broken"
    (root / ".sdlc").mkdir(parents=True)
    (root / ".sdlc" / "policy.yml").write_text(
        "worktrees_root: [unclosed\n  and: not a document\n", encoding="utf-8")
    assert W.own_trees(root) == ""
    assert not W.is_own_tree(root, ".sdlc-worktrees/run-1/pom.xml")


def test_the_root_the_gates_exclude_is_the_root_the_model_creates(tmp_path):
    """One address. `claim_root` makes it and `is_own_tree` excludes it, and a
    test rather than a convention holds them together - the wall and the thing
    it guards disagreeing about an address is how this class starts."""
    root = tmp_path / "repo"
    (root / ".sdlc").mkdir(parents=True)
    (root / ".sdlc" / "policy.yml").write_text(
        "worktrees_root: .sdlc-worktrees\n", encoding="utf-8")
    policy = P.load(root)
    made = W.claim_root(policy, root)
    rel = made.relative_to(root).as_posix()
    assert W.is_own_tree(root, rel + "/run-1/comp/pom.xml")


# ── the overlap check answers at the gate it exists for ─────────────────────


def test_overlap_answers_before_the_trees_exist(solo):
    """**The finding.** `overlap`'s own docstring says it exists so "a human
    sees the collision at G1", and the worktree record is written at P4 - two
    phases later. So through the entire window the check is FOR, it read an
    absent record as an empty component set and answered "no other live run
    shares a component", every time.

    Measured on one real run: 0 overlaps before `--create`, 2 immediately
    after, with nothing about the repository having changed in between.
    """
    repo = solo
    first = L.create(repo, "requirement", "one")["run_id"]
    W.create(repo, first)
    second = L.create(repo, "requirement", "two")["run_id"]
    # `second` has no worktree record - exactly the G1 state.
    comps, basis = W.components_of(repo, second)
    assert basis == "profile" and comps
    found = W.overlap(repo, second)
    assert found and first in found[0]
    assert "has not made its trees yet" in found[0]


def test_the_basis_is_the_record_once_the_trees_exist(solo):
    repo = solo
    run_id = L.create(repo, "requirement", "one")["run_id"]
    W.create(repo, run_id)
    comps, basis = W.components_of(repo, run_id)
    assert basis == "record" and comps


def test_a_profile_that_cannot_be_read_says_nothing_rather_than_no_overlap(solo):
    """Could-not-look is not "no overlap"; `unknown` is the caller's signal that
    the two are different answers.

    **Written when it could not happen, and item 117 made it happen.** It used
    to be forced with a monkeypatch, because unreadable YAML, an absent profile
    and an empty one all made `policy.components` synthesise the single
    component named after the directory rather than raise; the guard was kept
    because the alternative was a bare call whose failure mode is a traceback
    out of an ADVISORY check. The profile refuses what it cannot read now, so
    the branch has a live route to it.

    **And the assertion this test used to carry contradicted its own first
    sentence**: it required `overlap` to answer `[]`, which the CLI prints as
    *no other live run shares a component* — could-not-look reported as a clear
    run, which is the finding item 100 made about this very function. The
    property was right and the assertion was wrong, so the assertion moved.

    Bad YAML alone is still not enough, and that stays measured here: PyYAML
    cannot read `{{{ not yaml` either, and item 117 rules an unreadable-by-YAML
    file a stated LIMIT rather than a refusal, so this one is still read.
    """
    repo = solo
    run_id = L.create(repo, "requirement", "one")["run_id"]
    # The measured fact this test rests on: bad YAML alone is not enough.
    (repo / ".sdlc" / "profile.yml").write_text("{{{ not yaml", encoding="utf-8")
    assert W.components_of(repo, run_id)[1] == "profile"

    # An INVISIBLE indent is enough, and needs no monkeypatch: it is refused by
    # the character rule, which is the live route the guard now has.
    (repo / ".sdlc" / "profile.yml").write_text(
        "components:\n" + chr(0xA0) + "  - name: a\n", encoding="utf-8")
    comps, basis = W.components_of(repo, run_id)
    assert comps == set() and basis == "unknown"
    said = W.overlap(repo, run_id)
    assert len(said) == 1 and "could-not-look" in said[0], said
    assert "could not be determined" in said[0]


# ── item 156a: a creation that refuses leaves nothing behind ─────────────────


def test_an_occupied_destination_refuses_before_any_tree_is_made(estate):
    """The observed shape: the SECOND component's home is already occupied.

    `git worktree add` refuses an occupied destination, and `plan` never looked
    at one - so the refusal arrived half way through, with the first component's
    tree on disk, its branch registered in `git worktree list`, and no record
    written for `remove` to read. The destination is part of what a plan is
    about, so it is validated with the branch.
    """
    run_in(estate)
    policy = P.load(estate)
    busy = W.home(policy, estate, "r1", "lib")
    busy.mkdir(parents=True)
    (busy / "leftover.txt").write_text("stale\n", encoding="utf-8")

    with pytest.raises(W.WorktreeError) as exc:
        W.create(estate, "r1")
    assert "lib" in str(exc.value) and "not empty" in str(exc.value), exc.value

    first = W.home(policy, estate, "r1", "api")
    assert not first.exists(), f"the first component's tree was left at {first}"
    assert W.git(estate / "api", "rev-parse", "--verify", "--quiet",
                 "sdlc/r1", check=False) == ""
    listed = W.git(estate / "api", "worktree", "list").splitlines()
    assert len(listed) == 1, listed
    assert not W.record_path(estate, "r1").is_file()


def test_a_failure_part_way_unwinds_the_trees_it_already_made(estate, monkeypatch):
    """The failure `plan` cannot foresee, which is why the unwind exists too.

    Validation closes the common case; it cannot close the race, the permission
    or the disk. So the `worktree add` that fails here is made to fail directly,
    and what is asserted is the state afterwards: nothing made, nothing
    registered, and an error that names what it took back.
    """
    run_in(estate)
    policy = P.load(estate)
    real = W.git

    def fail_on_lib(where, *args, **kwargs):
        if args[:2] == ("worktree", "add") and Path(where).name == "lib":
            raise W.WorktreeError("git worktree add in lib failed: simulated")
        return real(where, *args, **kwargs)

    monkeypatch.setattr(W, "git", fail_on_lib)
    with pytest.raises(W.WorktreeError) as exc:
        W.create(estate, "r1")
    said = str(exc.value)
    assert "removed" in said and "api" in said, said

    first = W.home(policy, estate, "r1", "api")
    assert not first.exists(), f"the first component's tree was left at {first}"
    assert real(estate / "api", "rev-parse", "--verify", "--quiet",
                "sdlc/r1", check=False) == ""
    assert len(real(estate / "api", "worktree", "list").splitlines()) == 1
    assert not W.record_path(estate, "r1").is_file()


# ── item 156b: the ignore rule is checked, not merely created ────────────────


def test_an_edited_ignore_file_is_repaired_and_the_adopters_lines_kept(solo):
    """The permanent-dirty failure `claim_root` exists to prevent, restored.

    Writing the file only when it is ABSENT means an edited one is never
    repaired: the single-repo target reads `?? .sdlc-worktrees/` for ever and
    every later run's clean-tree check refuses. The rule is asserted on every
    call now - and the adopter's own lines survive, because a file somebody may
    legitimately extend is not one a tool may overwrite.
    """
    root = W.claim_root(P.load(solo), solo)
    (root / ".gitignore").write_text("# my own notes\n*.tmp\n", encoding="utf-8")

    again = W.claim_root(P.load(solo), solo)
    (again / "r1").mkdir(parents=True, exist_ok=True)
    (again / "r1" / "f.txt").write_text("work\n", encoding="utf-8")

    text = (again / ".gitignore").read_text(encoding="utf-8")
    assert "# my own notes" in text and "*.tmp" in text, text
    dirty = W.git(solo, "status", "--porcelain")
    assert "sdlc-worktrees" not in dirty, dirty
