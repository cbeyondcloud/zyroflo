"""The corpus must not score the harness's own bookkeeping. Item 103.

`corpus_harvest` harvests the adopter's merge history. Once the harness is
working in that repository it commits run folders, metrics and generated
workflows into it — and those commits are the NEWEST, so `cases()` sorts them to
the front and a replay picking the first N scores nothing else.

**Measured on a real 132-case corpus: the first FIVE cases the replay picked
were all harness commits**, every one scoring `precision@10 = 0.0` correctly and
meaninglessly. Their truth sets were `.sdlc/` run folders, metrics files and
design documents; no impact engine can rank a design document out of a graph of
source code. Left alone, the nightly replay would have published a meaningless
zero every night — and by this project's own rule that a fall to zero is always
a regression, reported one on the second night too.

**Third instance here of the harness contaminating what it measures**, after the
review telemetry that dirtied the tree it was about to check (item 100) and the
scanners that read the harness's own worktree copy (item 96).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools import corpus_harvest as H  # noqa: E402
from tools import corpus_replay as R  # noqa: E402


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True, timeout=120)


# ── what counts as the harness's own ────────────────────────────────────────


@pytest.mark.parametrize("path", [
    ".sdlc/runs/20260101-000000-abcdef/30-design.md",
    ".sdlc/metrics/reviews.jsonl",
    ".sdlc/profile.yml",
    "./.sdlc/policy.yml",
    ".github/workflows/sdlc-ledger.yml",
    ".claude/skills/sdlc/SKILL.md",
    ".mcp.json",
    "AGENTS.md",
    "CLAUDE.md",
    ".github/copilot-instructions.md",
])
def test_the_harness_own_paths_are_recognised(path):
    assert H.own_artifact(path) is True


@pytest.mark.parametrize("path", [
    "src/main/java/Thing.java",
    "pom.xml",
    "README.md",
    "docs/design.md",
    ".github/workflows/build.yml",          # the adopter's own CI stays a case
    ".claude/settings.json",                 # not a harness-generated skill
    "CLAUDE.md.bak",                         # exact match, not a prefix
    "sdlc/notes.md",                         # no leading dot: not .sdlc/
    "src/.sdlc-ish/x.txt",
])
def test_an_adopters_own_paths_are_not(path):
    assert H.own_artifact(path) is False


def test_a_leading_dot_survives_the_normalisation():
    """`lstrip("./")` takes a character SET and eats the leading dot of `.sdlc`,
    so the prefix never matches and the filter passes everything it exists to
    catch. Measured once already in item 96's worktree filter; pinned here so it
    cannot be reintroduced."""
    assert H.own_artifact("./.sdlc/runs/x/state.json") is True
    assert H.own_artifact(".sdlc/runs/x/state.json") is True
    assert H.own_artifact("sdlc/runs/x/state.json") is False


def test_a_windows_separator_is_read_the_same_way():
    assert H.own_artifact(r".sdlc\runs\x\state.json") is True


# ── a mixed commit keeps its code and loses the bookkeeping ─────────────────


def test_subject_files_keeps_the_code_half():
    """A change that touched real code AND recorded a run is still a real
    change, and its truth set is the code half - so it is trimmed, not
    dropped."""
    got = H.subject_files([
        "src/Thing.java", ".sdlc/runs/r/state.json", "pom.xml",
        ".sdlc/metrics/reviews.jsonl"])
    assert got == ["src/Thing.java", "pom.xml"]


def test_a_commit_that_is_ONLY_bookkeeping_has_no_subject_left():
    assert H.subject_files([".sdlc/runs/r/state.json", "CLAUDE.md"]) == []


def test_an_ordinary_commit_is_untouched():
    files = ["src/A.java", "src/B.java"]
    assert H.subject_files(files) == files


# ── harvest never writes one ────────────────────────────────────────────────


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A history where the harness's own commits are the NEWEST, which is the
    shape that produced the defect: they sort to the front of the corpus."""
    r = tmp_path / "svc"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.invalid")
    git(r, "config", "user.name", "t")
    (r / "seed.txt").write_text("seed\n", encoding="utf-8")
    git(r, "add", "."); git(r, "commit", "-qm", "seed")

    # A real change, first.
    (r / "api").mkdir()
    (r / "api" / "Thing.java").write_text("class Thing {}\n", encoding="utf-8")
    (r / "api" / "Other.java").write_text("class Other {}\n", encoding="utf-8")
    git(r, "add", "."); git(r, "commit", "-qm", "fix: correct the thing")

    # Then the harness moves in, and its commits are newer.
    (r / ".sdlc" / "runs" / "r1").mkdir(parents=True)
    (r / ".sdlc" / "runs" / "r1" / "state.json").write_text("{}\n",
                                                            encoding="utf-8")
    (r / ".sdlc" / "profile.yml").write_text("stack: java\n", encoding="utf-8")
    git(r, "add", "."); git(r, "commit", "-qm", "sdlc: run r1 through G4")

    (r / "CLAUDE.md").write_text("# instructions\n", encoding="utf-8")
    git(r, "add", "."); git(r, "commit", "-qm", "chore: install SDLC harness")
    return r


def test_harvest_writes_no_case_for_a_harness_only_commit(repo):
    """**The regression.** Red on the pre-fix source, where these two commits
    became the two newest cases in the corpus."""
    cases, err = H.harvest_repo(repo, "svc", limit=50)
    assert err == ""
    titles = [c["title"] for c in cases]
    assert "sdlc: run r1 through G4" not in titles
    assert "chore: install SDLC harness" not in titles
    assert "fix: correct the thing" in titles, \
        "the adopter's own change must still be a case"


def test_a_harvested_case_carries_only_subject_files(repo):
    cases, _ = H.harvest_repo(repo, "svc", limit=50)
    real = [c for c in cases if c["title"] == "fix: correct the thing"][0]
    assert all(not H.own_artifact(f) for f in real["files"])


# ── an existing corpus is filtered on READ ──────────────────────────────────


def _case(folder: Path, sha: str, files: list[str], date: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{sha[:12]}.json").write_text(json.dumps({
        "schema_version": 1, "repo": "svc", "sha": sha, "parent": "0" * 40,
        "date": date, "title": "t", "classes": ["scoping"], "files": files,
        "synthetic": False}), encoding="utf-8")


def test_a_stored_harness_case_is_set_aside_not_replayed(tmp_path):
    """An adopter who already HAS a corpus cannot be told to re-harvest it, and
    until they do every night scores the harness's own bookkeeping."""
    folder = tmp_path / ".sdlc" / "corpus"
    _case(folder, "a" * 40, [".sdlc/runs/r/state.json"], "2026-08-30T00:00:00Z")
    _case(folder, "b" * 40, ["src/Thing.java"], "2026-01-01T00:00:00Z")

    got = R.cases(tmp_path)
    assert [c["sha"] for c in got] == ["b" * 40]
    assert R.set_aside(tmp_path) == ["aaaaaaaaaaaa.json"]


def test_a_stored_mixed_case_keeps_its_code_and_is_replayed(tmp_path):
    folder = tmp_path / ".sdlc" / "corpus"
    _case(folder, "c" * 40, ["src/A.java", ".sdlc/metrics/x.jsonl"],
          "2026-08-30T00:00:00Z")
    got = R.cases(tmp_path)
    assert len(got) == 1 and got[0]["files"] == ["src/A.java"]
    assert R.set_aside(tmp_path) == []


def test_the_newest_first_sort_is_what_made_this_bite(tmp_path):
    """The defect was not that contaminated cases EXISTED - it was that they
    sorted to the FRONT, because the harness's commits are the most recent in
    any repository it is working in, and the replay picks the first N."""
    folder = tmp_path / ".sdlc" / "corpus"
    _case(folder, "d" * 40, [".sdlc/runs/r/state.json"], "2026-08-30T00:00:00Z")
    _case(folder, "e" * 40, [".sdlc/runs/r/other.json"], "2026-08-29T00:00:00Z")
    _case(folder, "f" * 40, ["src/Old.java"], "2020-01-01T00:00:00Z")

    got = R.cases(tmp_path)
    assert [c["sha"][:1] for c in got] == ["f"], \
        "with both harness cases set aside, the real one is what a replay picks"


def test_set_aside_is_per_repository(tmp_path):
    """Two repositories read in one process must not report each other's."""
    one, two = tmp_path / "one", tmp_path / "two"
    _case(one / ".sdlc" / "corpus", "a" * 40, [".sdlc/x.json"], "2026-01-01T00:00:00Z")
    _case(two / ".sdlc" / "corpus", "b" * 40, ["src/B.java"], "2026-01-01T00:00:00Z")
    R.cases(one)
    R.cases(two)
    assert R.set_aside(one) == ["aaaaaaaaaaaa.json"]
    assert R.set_aside(two) == []


def test_the_floors_count_only_scoreable_cases(tmp_path):
    """A floor counted over cases nothing can score is a floor nobody checked."""
    folder = tmp_path / ".sdlc" / "corpus"
    for i in range(4):
        _case(folder, f"{i}" * 40, [".sdlc/runs/r/x.json"], "2026-08-30T00:00:00Z")
    _case(folder, "9" * 40, ["src/A.java"], "2026-01-01T00:00:00Z")
    assert R.floors(R.cases(tmp_path))["cases"] == 1


def test_classify_needs_files_which_is_why_the_empty_guard_looks_redundant():
    """**Pinning a dependency, not a behaviour.**

    `harvest_repo` drops a case whose subject files are all gone, and a mutation
    removing that `continue` SURVIVES the suite - because `classify` happens to
    require files for every class it can return, so an emptied case is dropped
    one line later anyway.

    Equivalent today, and fragile: the guard's safety rests on a property of a
    DIFFERENT function that nothing stated. A title-only class - say a rule that
    reads `revert:` - would make the empty case real. This says so if that day
    comes, which is the composition this project keeps finding it did not have.
    """
    for title in ("fix: something", "revert: something", "sdlc: bookkeeping"):
        assert H.classify(title, [], is_merge=False) == []
        assert H.classify(title, [], is_merge=True) == []
