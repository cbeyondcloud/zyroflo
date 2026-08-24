"""Item 42 — two runs on one repository, and an upstream merge landing between them.

The design's ruling is optimistic concurrency: no lock, rebase-before-merge as
the enforcement that actually holds, and overlap detection as advisory notice so
a human can sequence. This file is that ruling under test, and the golden claim
is written as an ordering statement rather than a snapshot:

**Two verdicts about the same bytes must produce the same state whichever order
they arrive in, and the state they produce is "shut".** Until item 42
`gate_state` read `entries[-1]`, so the later writer won — on a shared branch
that is a race, and it resolved the wrong way. The point of asking two people is
that either of them may stop it.

Running two real runs over one real repository is also what surfaced the two
defects that made the single-repository case impossible end to end, both of them
the harness dirtying the tree it was about to check: intake writes the run folder
INTO the repository, and the first worktree left `.sdlc-worktrees/` untracked so
no second run could ever open. Both are asserted here, because a scenario nobody
runs is where that class of defect lives.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import approve as A  # noqa: E402
from tools.sdlc import intake as I  # noqa: E402
from tools.sdlc import state as L  # noqa: E402
from tools.sdlc import validate as V  # noqa: E402
from tools.sdlc import worktree as W  # noqa: E402
from tools.tests import _runs as R  # noqa: E402

ALICE = "alice@example.invalid"
BOB = "bob@example.invalid"
DEV = ["-c", "user.email=d@x.invalid", "-c", "user.name=D"]

PROFILE = ("stack: python\ngate_profile: solo\n"
           "phases: [intake, requirements, design, implementation, test, review, "
           "triage, closeout]\n"
           'test_command: ""\nreview:\n  block_on: [critical, high]\n')


def git(where: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(where), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=120)
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return proc.stdout.strip()


def commit(where: Path, message: str) -> None:
    git(where, "add", "-A")
    subprocess.run(["git", "-C", str(where), *DEV, "commit", "-qm", message],
                   check=True, timeout=120, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """ONE repository, harness installed and committed — the task line's case."""
    r = tmp_path / "repo"
    r.mkdir()
    git(r.parent, "init", "-q", "-b", "main", str(r))
    (r / "app.py").write_text("x = 1\n", encoding="utf-8")
    (r / ".sdlc").mkdir()
    (r / ".sdlc" / "profile.yml").write_text(PROFILE, encoding="utf-8")
    commit(r, "base")
    return r


def open_run(repo: Path, title: str) -> str:
    rec = I.open_run(repo, f"# {title}\n\nA body long enough to clear the length "
                           f"check that intake applies.")
    return rec["run_id"]


def upstream(repo: Path, name: str = "other.py", body: str = "y = 1\n") -> str:
    """A commit landing on main while runs are in flight."""
    (repo / name).write_text(body, encoding="utf-8")
    commit(repo, f"upstream: {name}")
    return git(repo, "rev-parse", "HEAD")


# ── the two defects that made one repository impossible ──────────────────────


def test_a_runs_own_ledger_does_not_block_its_worktree(repo):
    """Intake writes `.sdlc/runs/<id>/` INTO the repository it is about — that is
    deliberate, a run folder is committed and travels with the code. So the
    dirty check that guards worktree creation has to know about it, and until
    item 42 there were two dirty checks and only one did."""
    rid = open_run(repo, "a change")
    assert (repo / ".sdlc" / "runs" / rid).is_dir()
    made = W.create(repo, rid)
    assert list(made["components"]) == [repo.name]


def test_the_first_worktree_does_not_dirty_the_repository_forever(repo):
    """The worse half: `.sdlc-worktrees/` sits inside the tree it is about, so
    the first run's worktree made every LATER run impossible to open, and made
    the developer's own `git status` noisy for good."""
    first = open_run(repo, "the first change")
    W.create(repo, first)
    assert (repo / ".sdlc-worktrees" / ".gitignore").is_file()
    dirty, why = I.dirty_paths(repo)
    assert dirty == [], f"the worktrees root is still visible to git: {dirty}"
    noisy = [line for line in git(repo, "status", "--porcelain").splitlines()
             if ".sdlc-worktrees" in line]
    assert noisy == [], f"the developer's status carries the run's trees: {noisy}"
    second = open_run(repo, "the second change")
    assert second != first


def test_the_self_ignoring_root_is_written_once_and_not_overwritten(repo):
    rid = open_run(repo, "a change")
    W.create(repo, rid)
    marker = repo / ".sdlc-worktrees" / ".gitignore"
    marker.write_text("*\n# edited by the adopter\n", encoding="utf-8")
    W.remove(repo, rid)
    W.create(repo, open_run(repo, "another change"))
    assert "edited by the adopter" in marker.read_text(encoding="utf-8")


# ── two runs, side by side ───────────────────────────────────────────────────


def test_two_runs_on_one_repository_get_their_own_everything(repo):
    a, b = open_run(repo, "change A"), open_run(repo, "change B")
    made_a, made_b = W.create(repo, a), W.create(repo, b)
    branch_a = made_a["components"][repo.name]["branch"]
    branch_b = made_b["components"][repo.name]["branch"]
    assert branch_a != branch_b
    assert made_a["components"][repo.name]["home"] != \
        made_b["components"][repo.name]["home"]
    patterns = V.patterns_of(repo)
    assert V.run_of_branch(branch_a, patterns) == a
    assert V.run_of_branch(branch_b, patterns) == b
    assert A.approvals_path(repo, a) != A.approvals_path(repo, b)


def test_each_run_sees_the_other_as_an_overlap(repo):
    a, b = open_run(repo, "change A"), open_run(repo, "change B")
    W.create(repo, a)
    W.create(repo, b)
    assert any(a in line for line in W.overlap(repo, b))
    assert any(b in line for line in W.overlap(repo, a))
    assert all("advisory" in line for line in W.overlap(repo, a))


def test_a_verdict_in_one_run_does_not_touch_the_other(repo):
    a, b = open_run(repo, "change A"), open_run(repo, "change B")
    for rid in (a, b):
        R.park(repo, rid, "P7")
    A.record(repo, a, "G4", "reject", ALICE, notes="not this one")
    assert A.gate_state(repo, a, "G4")[0] is False
    A.record(repo, b, "G4", "approve", ALICE)
    assert A.gate_state(repo, b, "G4")[0] is True, "one run's no reached the other"
    assert V.merge_ready(repo, b) == []
    assert V.merge_ready(repo, a) != []


# ── an upstream merge, landing mid-run ───────────────────────────────────────


def test_the_delta_re_check_names_what_landed_after_the_base(repo):
    a = open_run(repo, "change A")
    W.create(repo, a)
    head = upstream(repo)
    arrived = W.upstream_commits(repo, a, repo.name, "main")
    assert len(arrived) == 1 and head[:7] in " ".join(arrived)


def test_both_runs_see_the_same_upstream_delta(repo):
    a, b = open_run(repo, "change A"), open_run(repo, "change B")
    W.create(repo, a)
    W.create(repo, b)
    upstream(repo)
    assert W.upstream_commits(repo, a, repo.name, "main") == \
        W.upstream_commits(repo, b, repo.name, "main")


def test_rebasing_one_run_leaves_the_other_still_behind(repo):
    """The point of per-run worktrees: catching up is a per-run act, and one
    run's rebase must not silently move another's base."""
    a, b = open_run(repo, "change A"), open_run(repo, "change B")
    W.create(repo, a)
    W.create(repo, b)
    base_b = W.base_of(repo, b, repo.name)
    upstream(repo)
    result = W.rebase(repo, a, repo.name, "main")
    assert result["rebased"] is True and len(result["upstream"]) == 1
    assert W.upstream_commits(repo, a, repo.name, "main") == [], \
        "the delta re-check still reports work the rebase already took"
    assert W.upstream_commits(repo, b, repo.name, "main"), "B was moved too"
    assert W.base_of(repo, b, repo.name) == base_b


def test_a_conflicting_upstream_merge_sends_the_work_back_to_g3(repo):
    a = open_run(repo, "change A")
    made = W.create(repo, a)
    spot = Path(made["components"][repo.name]["home"])
    (spot / "app.py").write_text("the run's version\n", encoding="utf-8")
    commit(spot, "the run's change")
    upstream(repo, "app.py", "upstream's version\n")
    result = W.rebase(repo, a, repo.name, "main")
    assert result["conflicts"] == ["app.py"] and result["rebased"] is False
    assert "G3" in result["note"]
    assert git(spot, "status", "--porcelain") == "", "left mid-rebase"


# ── the concurrent-verdict race: the golden claim ────────────────────────────


def parked(repo: Path, title: str) -> str:
    rid = open_run(repo, title)
    R.park(repo, rid, "P7")
    return rid


def test_a_reject_beats_an_approve_that_arrives_after_it(repo):
    """THE race. Two reviewers read the same diff, one says no and one says yes,
    and until item 42 whoever wrote second decided it."""
    rid = parked(repo, "a contested change")
    A.record(repo, rid, "G4", "reject", ALICE, notes="this is not right")
    A.record(repo, rid, "G4", "approve", BOB, notes="looks fine to me")
    ok, why = A.gate_state(repo, rid, "G4")
    assert ok is False
    assert ALICE in why and "whichever arrived last" in why


def test_a_reject_beats_an_approve_that_arrived_before_it(repo):
    rid = parked(repo, "a contested change")
    A.record(repo, rid, "G4", "approve", BOB)
    A.record(repo, rid, "G4", "reject", ALICE, notes="this is not right")
    ok, why = A.gate_state(repo, rid, "G4")
    assert ok is False and ALICE in why


def test_the_two_orders_reach_the_same_state(repo, tmp_path):
    """The golden claim, stated as an ordering property: order must not matter,
    and the state both orders reach is shut."""
    first = parked(repo, "contested one")
    A.record(repo, first, "G4", "reject", ALICE)
    A.record(repo, first, "G4", "approve", BOB)

    second = parked(repo, "contested two")
    A.record(repo, second, "G4", "approve", BOB)
    A.record(repo, second, "G4", "reject", ALICE)

    one, two = A.gate_state(repo, first, "G4"), A.gate_state(repo, second, "G4")
    assert one[0] == two[0] == False  # noqa: E712 - the symmetry is the assertion
    assert V.merge_ready(repo, first) and V.merge_ready(repo, second)


def test_a_fresh_approval_over_new_work_supersedes_the_reject(repo):
    """The other half, and without it a reject would block forever. Work happens
    after a rejection; the approval that follows is pinned to DIFFERENT bytes,
    so it is a verdict about different work and it stands."""
    rid = parked(repo, "a change that got fixed")
    A.record(repo, rid, "G4", "reject", ALICE, notes="fix the thing")
    assert A.gate_state(repo, rid, "G4")[0] is False
    (L.run_dir(repo, rid) / "70-triage.md").write_text("the thing, fixed\n",
                                                       encoding="utf-8")
    L.record_artifact(repo, rid, "70-triage.md")
    A.record(repo, rid, "G4", "approve", ALICE, notes="fixed")
    ok, why = A.gate_state(repo, rid, "G4")
    assert ok is True, why
    assert V.merge_ready(repo, rid) == []


def test_a_reject_over_the_new_work_shuts_it_again(repo):
    rid = parked(repo, "a change rejected twice")
    A.record(repo, rid, "G4", "reject", ALICE)
    (L.run_dir(repo, rid) / "70-triage.md").write_text("attempt two\n",
                                                       encoding="utf-8")
    L.record_artifact(repo, rid, "70-triage.md")
    A.record(repo, rid, "G4", "approve", BOB)
    assert A.gate_state(repo, rid, "G4")[0] is True
    A.record(repo, rid, "G4", "reject", ALICE, notes="still no")
    assert A.gate_state(repo, rid, "G4")[0] is False


def test_duplicate_approvals_are_idempotent(repo):
    rid = parked(repo, "an uncontested change")
    A.record(repo, rid, "G4", "approve", ALICE)
    before = A.gate_state(repo, rid, "G4")
    A.record(repo, rid, "G4", "approve", BOB)
    assert A.gate_state(repo, rid, "G4") == before
    assert len(A.read_chain(repo, rid)) == 2, \
        "the chain records what happened; only the STATE is idempotent"


def test_duplicate_rejections_do_not_compound(repo):
    rid = parked(repo, "a doubly rejected change")
    A.record(repo, rid, "G4", "reject", ALICE)
    A.record(repo, rid, "G4", "reject", BOB)
    (L.run_dir(repo, rid) / "70-triage.md").write_text("fixed\n", encoding="utf-8")
    L.record_artifact(repo, rid, "70-triage.md")
    A.record(repo, rid, "G4", "approve", ALICE)
    assert A.gate_state(repo, rid, "G4")[0] is True, \
        "two rejections of the same bytes needed two fixes to clear"


def test_the_refusal_says_who_said_no_and_what_to_do(repo):
    rid = parked(repo, "a contested change")
    A.record(repo, rid, "G4", "reject", ALICE)
    A.record(repo, rid, "G4", "approve", BOB)
    why = A.gate_state(repo, rid, "G4")[1]
    assert ALICE in why
    assert "Do the work, then approve again" in why


def test_the_chain_still_verifies_through_the_whole_race(repo):
    rid = parked(repo, "a contested change")
    for verdict, who in (("reject", ALICE), ("approve", BOB), ("reject", ALICE)):
        A.record(repo, rid, "G4", verdict, who)
    assert A.verify_chain(repo, rid) == []
    assert V.integrity(repo) == []


def test_a_run_with_only_rejections_names_the_last_one(repo):
    """The pre-existing message shape, kept: with no approval at all the answer
    is still 'rejected by', not the same-bytes sentence."""
    rid = parked(repo, "a rejected change")
    A.record(repo, rid, "G4", "reject", ALICE)
    ok, why = A.gate_state(repo, rid, "G4")
    assert ok is False and f"rejected by {ALICE}" in why
    assert "whichever arrived last" not in why
