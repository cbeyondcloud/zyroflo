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
    cases, err = H.harvest_repo(repo, "svc", limit=50)
    assert err == ""
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
    newest_only, err = H.harvest_repo(repo, "svc", limit=1)
    assert err == ""
    assert {c["title"] for c in newest_only} == {"Merge branch feature"}


def test_a_directory_that_is_not_a_repository_degrades_to_a_row(tmp_path):
    cases, err = H.harvest_repo(tmp_path, "plain", limit=10)
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
