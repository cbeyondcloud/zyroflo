"""Corpus harvest v1: mechanical selection from real history, adopter-resident by rule.

Item 17. The classifier is pinned sentence by sentence against a synthetic repository
whose history is BUILT here, commit by commit — so every rule ("a docs-only fix is not
a defect case") fails its own test rather than a reading of the code. The probe default
writes nothing; `--write` lands cases in the repository they came from, never anywhere
else.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools import corpus_harvest as H  # noqa: E402


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


@pytest.fixture(scope="module")
def repo(tmp_path_factory) -> Path:
    """Four commits, one per rule: a wide change, a fix, a docs-only fix, and a merge."""
    r = tmp_path_factory.mktemp("hist") / "svc"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.invalid")
    git(r, "config", "user.name", "t")

    (r / "seed.txt").write_text("seed\n")
    git(r, "add", "."); git(r, "commit", "-qm", "seed")

    (r / "api").mkdir(); (r / "worker").mkdir()
    (r / "api" / "a.py").write_text("a\n"); (r / "worker" / "b.py").write_text("b\n")
    git(r, "add", "."); git(r, "commit", "-qm", "add api and worker halves")

    (r / "api" / "a.py").write_text("a2\n")
    git(r, "add", "."); git(r, "commit", "-qm", "fix the null response bug")

    (r / "README.md").write_text("docs\n")
    git(r, "add", "."); git(r, "commit", "-qm", "fix typo in readme")

    git(r, "checkout", "-qb", "feature")
    (r / "worker" / "b.py").write_text("b2\n")
    git(r, "add", "."); git(r, "commit", "-qm", "feature work")
    git(r, "checkout", "-q", "main")
    git(r, "merge", "-q", "--no-ff", "-m", "Merge branch feature", "feature")
    return r


def cases_of(repo: Path) -> list[dict]:
    cases, err, dropped = H.harvest_repo(repo, "svc", limit=50)
    assert err == "" and dropped == []
    return cases


# ── the classifier, one rule per test ─────────────────────────────────────────


def test_a_change_spreading_across_directories_is_a_scoping_case(repo):
    by_title = {c["title"]: c for c in cases_of(repo)}
    assert "scoping" in by_title["add api and worker halves"]["classes"]


def test_a_fix_touching_code_is_a_defect_case(repo):
    by_title = {c["title"]: c for c in cases_of(repo)}
    case = by_title["fix the null response bug"]
    assert case["classes"] == ["defect"], "one file, one dir — a defect and nothing else"
    assert case["files"] == ["api/a.py"]


def test_a_docs_only_fix_is_not_a_defect_case(repo):
    titles = {c["title"] for c in cases_of(repo)}
    assert "fix typo in readme" not in titles, \
        "a typo fixed in a README is a fix, not a case to replay a localizer against"


def test_a_merge_with_changes_is_a_review_case(repo):
    by_title = {c["title"]: c for c in cases_of(repo)}
    assert "review" in by_title["Merge branch feature"]["classes"]


def test_every_case_is_stamped_real_and_carries_its_provenance(repo):
    for c in cases_of(repo):
        assert c["synthetic"] is False
        assert c["sha"] and c["parent"] and c["date"] and c["files"]


def test_the_history_window_is_a_recorded_bound(repo):
    newest_only, err, _ = H.harvest_repo(repo, "svc", limit=1)
    assert err == ""
    assert {c["title"] for c in newest_only} == {"Merge branch feature"}


def test_a_directory_that_is_not_a_repository_degrades_to_a_row(tmp_path):
    cases, err, _ = H.harvest_repo(tmp_path, "plain", limit=10)
    assert cases == [] and err, "stated, never skipped silently"


# ── probe vs write: the adopter-resident line ─────────────────────────────────


def test_probe_is_the_default_and_writes_nothing(repo, tmp_path, capsys):
    out = tmp_path / "probe.json"
    assert H.main(["--repo", str(repo), "--out", str(out)]) == 0
    text = capsys.readouterr().out
    assert "probe only" in text and "nothing written" in text
    assert not (repo / ".sdlc").exists()
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["classes"]["defect"] == 1
    assert report["floor_met"]["defect"] is False, "1 < the M5 floor of 10, and v1 " \
                                                   "reports rather than enforces"


def test_write_lands_cases_in_the_repository_they_came_from(repo, capsys):
    assert H.main(["--repo", str(repo), "--write"]) == 0
    files = sorted((repo / ".sdlc" / "corpus").glob("*.json"))
    assert files, "the adopter's act puts the cases in the adopter's tree"
    case = json.loads(files[0].read_text(encoding="utf-8"))
    assert case["schema_version"] == H.SCHEMA_VERSION and case["synthetic"] is False


def test_exactly_one_source_is_required(capsys):
    assert H.main([]) == 2
    assert "exactly one" in capsys.readouterr().out


# ── could-not-look is not measured-zero (A3c-14 / W156) ──────────────────────


ROOT = Path(__file__).resolve().parents[2]


def spawn_harvest(*extra: str):
    """The real door, spawned - a CLI's contract is a process, and this one had
    never been spawned by a test (W156 A3c-14, class 7)."""
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "corpus_harvest.py"), *extra],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180)
    return proc, (proc.stdout or "") + (proc.stderr or "")


def _one_commit_repo(root: Path) -> Path:
    """A real git repo with only a root commit - READABLE, but no case qualifies
    (a root commit has no 'before'), so it is a genuine MEASURED zero."""
    r = root / "root_only"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.invalid")
    git(r, "config", "user.name", "t")
    (r / "seed.txt").write_text("seed\n")
    git(r, "add", "."); git(r, "commit", "-qm", "seed")
    return r


def test_a_repo_that_cannot_be_read_is_could_not_look_not_measured_zero(tmp_path):
    """**SPAWNED. A3c-14 (W156).** A `--repo` that is not a git repository and a
    real repo that genuinely yields no case both printed the floors 'below' at
    exit 0, so a caller reading only the exit code could not tell could-not-look
    from measured-zero - this project's most-repeated distinction. Could-not-look
    is exit 3, the code `corpus_replay` already uses at its door."""
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    proc, said = spawn_harvest("--repo", str(not_a_repo))
    assert proc.returncode == 3, \
        f"a component that could not be read is could-not-look (3); got " \
        f"{proc.returncode}: {said!r}"
    assert "!!" in said, said


def test_a_real_repo_with_no_qualifying_case_is_measured_zero_exit_0(tmp_path):
    """**SPAWNED.** The other half: a repo that WAS read and holds no case is a
    measured zero and stays exit 0. Without it, exit 3 would only prove 3 means
    'anything', not 'could not look'."""
    r = _one_commit_repo(tmp_path)
    proc, said = spawn_harvest("--repo", str(r))
    assert proc.returncode == 0, \
        f"measured-zero is exit 0; got {proc.returncode}: {said!r}"


def test_floor_met_is_none_when_no_component_could_be_read(tmp_path):
    """**A3c-14, the report half.** `floor_met: false` is a MEASURED claim - we
    counted and it was below the floor. On a run where nothing was readable the
    count never happened, so the honest answer is None, not False."""
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    out = tmp_path / "probe.json"
    code = H.main(["--repo", str(not_a_repo), "--out", str(out)])
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["unharvestable"], "the premise: the component was unreadable"
    assert report["floor_met"]["scoping"] is None, \
        "a floor nobody could measure is could-not-look (None), not below (False)"
    assert code == 3, "and the process says could-not-look too"


# ── a dropped commit is counted, not skipped silently (A3c-15 / W156) ────────


def _show_always_fails(monkeypatch):
    real = H._git

    def fake(r, *args):
        if args and args[0] == "show":
            return "", "fatal: bad object"
        return real(r, *args)

    monkeypatch.setattr(H, "_git", fake)


def test_a_commit_whose_show_fails_is_counted_not_dropped(repo, monkeypatch):
    """**A3c-15 (W156).** `harvest_repo` did `if err: continue` on a commit whose
    `git show` failed - no counter, no record - so a dropped commit lowered the
    counts the M5 floors are judged against and the lowered count was reported as
    measured. The module docstring keeps this rule at the COMPONENT level
    ('stated, never skipped silently') and broke it at the commit level."""
    _show_always_fails(monkeypatch)
    cases, err, dropped = H.harvest_repo(repo, "svc", limit=50)
    assert err == "", "the component itself was readable"
    assert cases == [], "every commit's show failed, so no case survives"
    assert dropped, "a dropped commit must be recorded, not skipped silently"
    assert all(d.get("sha") and d.get("why") for d in dropped), \
        "each drop carries which commit and why - provenance, not a bare count"


def test_dropped_commits_are_reported_and_make_the_run_could_not_look(
        repo, tmp_path, monkeypatch, capsys):
    """The report and the exit code, composed. A drop that only lowered a count
    is exactly could-not-look wearing measured-zero's clothes, so the report
    records it the way it records an unharvestable component and the process
    stops reading as a clean measured zero."""
    _show_always_fails(monkeypatch)
    out = tmp_path / "probe.json"
    code = H.main(["--repo", str(repo), "--out", str(out)])
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report.get("dropped_commits"), \
        "commits git could not show must be recorded in the report"
    said = capsys.readouterr().out
    assert "!!" in said, "the drop is printed, not silent"
    assert code == 3, \
        "a run that could not read some of the history it was asked to is " \
        "could-not-look, not a clean measured zero"


# ── a harness-only commit is recorded, not silently dropped (T318 / W391) ────


def _repo_with_harness_only_commit(root: Path) -> Path:
    """A real git repo whose newest non-root commit touches ONLY harness files.

    The commit qualifies structurally (has a parent, git show succeeds) but
    after subject_files() filters the harness bookkeeping, nothing remains.
    Previously this was a silent ``continue`` with no record — the commit
    vanished from the case list AND from the dropped list, lowering the
    total the M5 floors rest on with nobody the wiser.
    """
    r = root / "harness_only"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.invalid")
    git(r, "config", "user.name", "t")
    # Commit 1 (root): creates a real file.
    (r / "src").mkdir()
    (r / "src" / "app.py").write_text("# app\n")
    git(r, "add", "."); git(r, "commit", "-qm", "initial")
    # Commit 2: touches a real file in two dirs (scoping-eligible).
    (r / "lib").mkdir()
    (r / "lib" / "util.py").write_text("# util\n")
    (r / "src" / "app.py").write_text("# app v2\n")
    git(r, "add", "."); git(r, "commit", "-qm", "add lib and update app")
    # Commit 3: touches ONLY harness bookkeeping.
    (r / ".sdlc").mkdir()
    (r / ".sdlc" / "run.json").write_text("{}\n")
    git(r, "add", "."); git(r, "commit", "-qm", "record harness run")
    return r


def test_harness_only_commit_is_recorded_not_silently_dropped(tmp_path):
    """T318.  ``harvest_repo`` lists commits from ``git log``, then calls
    ``git show`` on each.  When show succeeds but every changed file is
    harness bookkeeping, ``subject_files()`` returns ``[]`` and the loop
    used to ``continue`` with no record.  The commit vanished from BOTH
    the case list AND the dropped list — zero silent drops is a
    load-bearing convention, and this broke it."""
    r = _repo_with_harness_only_commit(tmp_path)
    cases, err, dropped = H.harvest_repo(r, "harness_only", limit=50)
    assert err == "", "the component itself was readable"
    # The harness-only commit must appear in `dropped`, not vanish.
    harness_drops = [d for d in dropped if d["sha"]]
    assert harness_drops, \
        "a commit whose files are all harness bookkeeping must be " \
        "recorded in dropped, not silently skipped"
    # The reason must name WHY — specifically, that it was harness bookkeeping.
    for d in harness_drops:
        assert d.get("why"), \
            f"dropped commit {d['sha'][:12]} carries no reason"
        assert "harness" in d["why"].lower(), \
            f"the reason must name harness bookkeeping, not a generic " \
            f"message; got: {d['why']!r}"


def test_harness_only_drop_is_visible_in_cli_output(tmp_path):
    """T318, spawned.  A person running the probe must SEE that commits
    were examined and excluded — absent is not zero, and a count that
    silently omits entries it tried and failed to process is worse than
    one that admits the gap."""
    r = _repo_with_harness_only_commit(tmp_path)
    proc, said = spawn_harvest("--repo", str(r))
    # The CLI must mention the dropped commit(s).
    assert "!!" in said, \
        f"the harness-only drop must be visible in the output; got:\n{said}"
    assert proc.returncode == 3, \
        f"a run with unscored commits is could-not-look (3), " \
        f"not a clean zero; got {proc.returncode}"
