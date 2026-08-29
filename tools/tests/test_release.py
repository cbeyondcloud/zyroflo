"""P8 — publish, open the PR, watch the real CI. Item 47.

The lifecycle's own table says the human *"clicks merge — always"*. This module
is everything up to that click and nothing past it.

The properties worth more than the rest of this file:

**The merge click is enforced, not promised.** A prose rule in an SOP is one an
agent can decide to be pragmatic about. `gh pr merge` does not appear in the
module and a test says so.

**Four of the five verdicts are not green**, and every one of them has been a
real defect shape here. `none` matters most: item 40's SCA gate scanned nothing
and reported nothing for months, because *"nothing ran"* and *"nothing failed"*
produce identical output unless something insists they differ.

**Publishing goes through the constitution's own wall.** `policy.push_target`
decides, not a second opinion written here.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import release as R  # noqa: E402
from tools.sdlc import state as ledger  # noqa: E402
from tools.sdlc import worktree as W  # noqa: E402

PROFILE = ("stack: python\ngate_profile: solo\n"
           "phases: [intake, requirements, design, implementation, test, review, "
           "triage, release, closeout]\n"
           'test_command: "pytest -q"\nreview:\n  block_on: [critical, high]\n')


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / ".sdlc").mkdir(parents=True)
    (r / ".sdlc" / "profile.yml").write_text(PROFILE, encoding="utf-8")
    (r / "code.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(r), "init", "-q"], check=True,
                   capture_output=True, timeout=120)
    subprocess.run(["git", "-C", str(r), "add", "-A"], check=True,
                   capture_output=True, timeout=120)
    subprocess.run(["git", "-C", str(r), "-c", "user.email=t@e", "-c",
                    "user.name=t", "commit", "-qm", "base"], check=True,
                   capture_output=True, timeout=120)
    return r


def rows(*specs: tuple[str, str]) -> list[dict]:
    return [{"name": n, "bucket": b, "state": "COMPLETED", "link": ""}
            for n, b in specs]


# ── only one of five answers is a pass ──────────────────────────────────────


def test_all_passing_is_green(repo):
    assert R.verdict(rows(("build", "pass"), ("scan", "pass")))["state"] == R.GREEN


def test_a_failing_check_is_red_and_names_it(repo):
    found = R.verdict(rows(("build", "pass"), ("scan", "fail")))
    assert found["state"] == R.RED and found["failed"] == ["scan"]


def test_a_pending_check_is_not_a_pass(repo):
    """A run that read pending as passing would merge on a promise."""
    found = R.verdict(rows(("build", "pass"), ("slow", "pending")))
    assert found["state"] == R.PENDING and found["pending"] == ["slow"]


def test_no_checks_at_all_is_not_a_pass(repo):
    """**The one this project has paid for twice.** Item 40's SCA gate scanned
    nothing and reported nothing for months. A pull request with no checks is
    not a passing pull request; it is one nobody is checking."""
    found = R.verdict([])
    assert found["state"] == R.NONE
    assert "nothing ran" in found["why"] and "nothing failed" in found["why"]


def test_a_skipped_check_is_not_a_pass(repo):
    """A skipped job ran nothing. Whether that is fine is a question for a
    person, not an answer this phase may give itself."""
    assert R.verdict(rows(("build", "skipping")))["state"] == R.PENDING


def test_a_cancelled_check_is_red(repo):
    assert R.verdict(rows(("build", "cancel")))["state"] == R.RED


def test_a_bucket_this_phase_has_never_seen_is_not_green(repo):
    """The vocabulary gap that let a leaked credential out of item 36's review
    loop: a value no rule anticipated must not fall through to a pass."""
    found = R.verdict(rows(("build", "pass"), ("odd", "some-new-bucket")))
    assert found["state"] == R.UNKNOWN
    assert any("odd" in u for u in found["unknown"])


def test_red_outranks_pending(repo):
    """A failure is actionable now; waiting for the rest would delay the loop
    back to P4 for no gain."""
    assert R.verdict(rows(("a", "fail"), ("b", "pending")))["state"] == R.RED


# ── the merge click is a human's, and that is enforced ──────────────────────


def test_the_module_cannot_merge():
    """**Enforced rather than promised.** A prose rule in an SOP is one an agent
    can decide to be pragmatic about; a call the code does not contain is not."""
    import ast

    src = (ROOT / "tools" / "sdlc" / "release.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    # An AST sweep, not a word search - item 94's idiom. The first cut banned
    # the WORD and failed on a section header reading "never its merge", which
    # is prose doing its job. What must not exist is an ARGUMENT: a merge is
    # `_gh(["pr", "merge", ...])`, so a string literal "merge" is the thing to
    # refuse, wherever it appears outside a docstring.
    # Docstring nodes are found STRUCTURALLY - the first statement of a module,
    # function or class. Comparing against `ast.get_docstring` does not work:
    # it returns the CLEANED text, which never equals the raw Constant, so the
    # module docstring stayed in the sweep and its own sentence about `gh pr
    # merge` failed the assertion it was describing.
    skip = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)):
            continue
        first = (node.body or [None])[0]
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            skip.add(id(first.value))
    literals = [n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and id(n) not in skip]
    assert "merge" not in literals, "a `merge` argument is reachable from this module"
    assert not any("pr merge" in s for s in literals)
    # and the sweep must be looking at something, or it passes vacuously
    assert "checks" in literals and "create" in literals


def test_nothing_green_leaves_the_phase(repo):
    for state in (R.RED, R.PENDING, R.NONE, R.UNKNOWN):
        owed = R.problems(repo, "r1", {"state": state, "why": "because"})
        assert len(owed) == 1 and state in owed[0]
        assert "merge click is a human" in owed[0]


def test_green_owes_nothing(repo):
    assert R.problems(repo, "r1", {"state": R.GREEN, "why": "all passed"}) == []


def test_no_result_yet_owes_nothing_here(repo):
    """`stage.verify` says P8 owes its artifact by name. Saying it twice in
    different words teaches people to skim both."""
    assert R.problems(repo, "r1", None) == []


# ── publishing goes through the constitution's wall ─────────────────────────


def test_a_branch_that_is_not_a_run_branch_is_refused(repo):
    owed = R.push_problems(repo, "r1", "my-feature", "origin")
    assert owed and "agents push only to" in owed[0]


def test_a_remote_the_constitution_does_not_name_is_refused(repo):
    owed = R.push_problems(repo, "r1", "sdlc/r1", "somewhere-else")
    assert owed and "somewhere-else" in owed[0]


def test_a_run_branch_to_the_allowed_remote_is_permitted(repo):
    assert R.push_problems(repo, "r1", "sdlc/r1", "origin") == []


def test_a_branch_belonging_to_another_run_is_refused(repo):
    owed = R.push_problems(repo, "r2", "sdlc/r1", "origin")
    assert owed and "another run" in owed[0]


def test_the_branch_comes_from_the_run_record_not_a_caller(repo):
    """The same seam item 37 built and `6a00bfc` made run-sourced: a path a
    caller must get right is one that will eventually be got wrong."""
    run_id = ledger.create(repo, "requirement", "a change")["run_id"]
    W.create(repo, run_id)
    assert R.branch_of(repo, run_id) == f"sdlc/{run_id}"


def test_a_run_with_no_worktree_record_names_no_branch(repo):
    """Absent, not guessed. Inventing `sdlc/<id>` here would push a branch that
    may not exist."""
    run_id = ledger.create(repo, "requirement", "a change")["run_id"]
    assert R.branch_of(repo, run_id) == ""


# ── could-not-look is never a green CI ──────────────────────────────────────


def test_an_absent_gh_is_refused_not_read_as_green(repo, monkeypatch):
    monkeypatch.setattr(R, "binary", lambda: (None, "not pinned on this platform"))
    with pytest.raises(R.ReleaseError) as exc:
        R.find_pr(repo, "sdlc/r1")
    assert "not the same as CI being green" in str(exc.value)


def test_the_cli_refuses_checks_without_a_pr(repo, capsys):
    assert R.main(["--target", str(repo), "--checks"]) == 2
    assert "needs --pr" in capsys.readouterr().err


def test_the_cli_reports_a_refused_push(repo, capsys):
    assert R.main(["--target", str(repo), "--branch", "not-a-run-branch"]) == 1
    assert "REFUSED" in capsys.readouterr().out


def test_the_cli_reports_an_allowed_push_without_making_one(repo, capsys):
    """Reporting is not publishing. The CLI's default path answers the question
    and pushes nothing."""
    before = subprocess.run(["git", "-C", str(repo), "branch", "-a"],
                            capture_output=True, text=True, timeout=120).stdout
    assert R.main(["--target", str(repo), "--branch", "sdlc/r1"]) == 0
    after = subprocess.run(["git", "-C", str(repo), "branch", "-a"],
                           capture_output=True, text=True, timeout=120).stdout
    assert before == after
    assert "allowed" in capsys.readouterr().out


# ── wired into the phase contract, not merely available ────────────────────


def test_p8_owes_its_verdict_through_the_conditional_seam(repo):
    """A CLI nobody runs is a CLI nobody runs. `stage.verify` is what a gate
    stands on, and it must carry this."""
    from tools.sdlc import stage as ST

    assert ST.CI_RESULT in ST.by_phase("P8").conditional
    assert ST.CONDITIONAL_JUDGE["P8"][0] == "tools.sdlc.release"
    assert ST.CI_RESULT == R.RESULT      # one name, asked for rather than spelled


def test_a_run_at_p8_with_no_verdict_owes_one(repo):
    """A branch nobody watched is not a branch whose checks passed."""
    run_id = ledger.create(repo, "requirement", "a change")["run_id"]
    ledger.advance(repo, run_id, phase="P8", note="releasing")
    owed = R.obligations(repo, run_id)
    assert len(owed) == 1 and R.RESULT in owed[0]
    assert "nobody watched" in owed[0]


def test_an_earlier_phase_owes_nothing_here(repo):
    """A run that has not reached release has no CI to watch, and saying it
    owes one would fire this judge on every run in the pipeline."""
    run_id = ledger.create(repo, "requirement", "a change")["run_id"]
    assert R.obligations(repo, run_id) == []


def test_a_recorded_green_verdict_owes_nothing(repo):
    run_id = ledger.create(repo, "requirement", "a change")["run_id"]
    ledger.advance(repo, run_id, phase="P8", note="releasing")
    (ledger.run_dir(repo, run_id) / R.RESULT).write_text(
        json.dumps({"state": R.GREEN, "why": "all passed"}), encoding="utf-8")
    assert R.obligations(repo, run_id) == []


def test_a_recorded_red_verdict_holds_the_phase(repo):
    run_id = ledger.create(repo, "requirement", "a change")["run_id"]
    ledger.advance(repo, run_id, phase="P8", note="releasing")
    (ledger.run_dir(repo, run_id) / R.RESULT).write_text(
        json.dumps({"state": R.RED, "why": "1 check failed: build"}),
        encoding="utf-8")
    owed = R.obligations(repo, run_id)
    assert len(owed) == 1 and "red" in owed[0]


def test_an_unreadable_verdict_is_refused_not_read_as_green(repo):
    run_id = ledger.create(repo, "requirement", "a change")["run_id"]
    ledger.advance(repo, run_id, phase="P8", note="releasing")
    (ledger.run_dir(repo, run_id) / R.RESULT).write_text("{not json",
                                                         encoding="utf-8")
    owed = R.obligations(repo, run_id)
    assert len(owed) == 1 and "could not be read" in owed[0]


def test_verify_surfaces_the_owed_verdict(repo):
    """The obligation has to reach the phase machinery, or it is a CLI nobody
    runs."""
    from tools.sdlc import stage as ST

    run_id = ledger.create(repo, "requirement", "a change")["run_id"]
    ledger.advance(repo, run_id, phase="P8", note="releasing")
    (ledger.run_dir(repo, run_id) / "80-release.md").write_text("released\n",
                                                                encoding="utf-8")
    ledger.record_artifact(repo, run_id, "80-release.md")
    problems = ST.verify(repo, run_id, "P8")
    assert any(R.RESULT in p for p in problems), problems


def test_the_lazy_import_is_declared_in_the_bundle_map(repo):
    """`stage.py` is embedded in the emitted ledger check, so a sibling it
    names must be declared or `render_check` refuses to emit."""
    from tools.sdlc import validate as V

    assert "release" in V.LAZY_OUTSIDE_THE_BUNDLE


def test_push_asks_the_wall_before_it_touches_git(repo):
    """**Found by a surviving mutation.** `push_problems` was tested and `push`
    was tested and nothing composed them, so dropping the wall out of `push`
    reddened nothing - the producer/consumer-never-composed class this project
    keeps meeting.

    The refusal must come from the constitution, not from git failing on a
    missing remote: those are different sentences, and only one of them means
    "you may not publish that".
    """
    with pytest.raises(R.ReleaseError) as exc:
        R.push(repo, "r1", "not-a-run-branch", "origin")
    assert "agents push only to" in str(exc.value)
    assert "git push" not in str(exc.value)
    # and nothing was published: the repository has no remote at all, so a
    # push that reached git would have failed with a different message.
    remotes = subprocess.run(["git", "-C", str(repo), "remote"],
                             capture_output=True, text=True, timeout=120).stdout
    assert remotes.strip() == ""


def test_push_refuses_a_remote_outside_the_constitution(repo):
    with pytest.raises(R.ReleaseError) as exc:
        R.push(repo, "r1", "sdlc/r1", "somewhere-else")
    assert "somewhere-else" in str(exc.value)
