"""The open-core boundary holds (T689; the owner's decisions 58 to 61).

This repository is the free edition. The paid layer lives in a private repository overlaid on
the same folder as `.git-commercial/`, and the two must hold disjoint files: nothing private is
tracked here, every private path is ignored here, and no free file imports or names the paid
layer. `open_core.py` holds the checks; each runs against this tree and, as a positive
control, against a synthetic repository that plants the defect - so a check that quietly
answers `[]` fails here rather than passing everywhere.

The twin is present only in the main checkout once the overlay exists. Where it is absent (a
worktree, a fresh clone, CI's), `twin_overlap` answers `None`, and the cell asserts THAT rather
than skipping: a skip on the Linux gate fails CI, and a check that never runs looks like one
that passed.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import open_core  # noqa: E402
from ban import CouldNotLook  # noqa: E402

ROOT = open_core.ROOT


def _env() -> dict:
    env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(k, None)
    return env


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, env=_env(), check=True, capture_output=True,
                          text=True).stdout


def repo(tmp: Path, files: dict[str, str], ignore: str = "") -> Path:
    """A committed repository holding `files`, with `ignore` as its .gitignore."""
    root = tmp / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.invalid")
    git(root, "config", "user.name", "t")
    (root / ".gitignore").write_text(ignore, encoding="utf-8")
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    git(root, "add", "-A", "-f")
    git(root, "commit", "-qm", "base")
    return root


BOUNDARY = "enterprise/\ninternal-docs/\n.claude/\n.git-commercial/\n"


# ── nothing private is tracked here ──────────────────────────────────────────


def test_no_private_path_is_tracked_here():
    assert open_core.private_paths_tracked(ROOT) == []


def test_a_tracked_private_path_is_found(tmp_path: Path):
    root = repo(tmp_path, {"enterprise/svc.py": "x", "Internal-Docs/n.md": "y",
                           ".claude/agents/r.md": "z", "tools/ok.py": "w"}, BOUNDARY)
    assert open_core.private_paths_tracked(root) == [
        ".claude/agents/r.md", "Internal-Docs/n.md", "enterprise/svc.py"]


# ── every private path is ignored here ───────────────────────────────────────


def test_every_private_path_is_ignored_here():
    assert open_core.private_paths_unignored(ROOT) == []


def test_a_private_root_the_gitignore_misses_is_found(tmp_path: Path):
    root = repo(tmp_path, {"README.md": "x"}, "enterprise/\ninternal-docs/\n")
    assert open_core.private_paths_unignored(root) == [".claude/", ".git-commercial/"]


def test_a_negated_file_under_an_ignored_root_is_found(tmp_path: Path):
    root = repo(tmp_path, {"README.md": "x"}, BOUNDARY.replace(
        "enterprise/\n", "enterprise/*\n!enterprise/leak.py\n"))
    (root / "enterprise").mkdir()
    (root / "enterprise" / "leak.py").write_text("x", encoding="utf-8")
    assert open_core.private_paths_unignored(root) == ["enterprise/leak.py"]


# ── the barricade: no free file imports or names the paid layer ──────────────


def test_no_free_file_imports_or_names_the_paid_layer():
    assert open_core.barricade_breaches(ROOT) == []


@pytest.mark.parametrize("rel, text", [
    ("tools/sdlc/a.py", "import enterprise\n"),
    ("tools/sdlc/b.py", "from enterprise.console import app\n"),
    ("tools/c.py", "mod = importlib.import_module('enterprise.billing')\n"),
    ("sdlc-init.py", "PAID = ROOT / 'enterprise/' \n"),
    ("sdlc_templates/x.md", "see enterprise/README.md\n"),
    ("sdlc/LIFECYCLE.md", "the console lives in enterprise/console\n"),
])
def test_a_reference_to_the_paid_layer_is_found(tmp_path: Path, rel: str, text: str):
    root = repo(tmp_path, {rel: text})
    found = open_core.barricade_breaches(root)
    assert len(found) == 1 and found[0].startswith(f"{rel}:1:"), found


@pytest.mark.parametrize("rel, text", [
    ("tools/sdlc/a.py", "# an enterprise litellm proxy is an endpoint like any other\n"),
    ("sdlc_templates/policy.yml", "# An enterprise fills this floor\n"),
    ("docs/notes.md", "the paid layer is in enterprise/\n"),
    ("tools/sdlc/b.py", "import enterprise_tools\n"),
])
def test_the_word_and_the_rest_of_the_tree_are_not_breaches(tmp_path: Path, rel: str,
                                                              text: str):
    """The word "enterprise" is a customer's, and `docs/` may describe the split; only an
    import of the package or the path `enterprise/` inside the shipped trees is a breach."""
    assert open_core.barricade_breaches(repo(tmp_path, {rel: text})) == []


# ── the twin, where it is overlaid ───────────────────────────────────────────


def test_the_twin_and_this_repository_hold_disjoint_files():
    if (ROOT / open_core.TWIN_DIR).is_dir():
        assert open_core.twin_overlap(ROOT) == []
    else:
        assert open_core.twin_overlap(ROOT) is None


def _twin(root: Path, files: dict[str, str]) -> None:
    twin = root / open_core.TWIN_DIR
    git(root, "init", "-q", "--bare", "-b", "main", str(twin))
    t = [f"--git-dir={twin}", f"--work-tree={root}"]
    git(root, *t, "config", "core.bare", "false")
    git(root, *t, "config", "user.email", "t@example.invalid")
    git(root, *t, "config", "user.name", "t")
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    git(root, *t, "add", "-f", *files)
    git(root, *t, "commit", "-qm", "private")


def test_a_clean_twin_overlaps_nothing(tmp_path: Path):
    root = repo(tmp_path, {"README.md": "x"}, BOUNDARY)
    _twin(root, {"enterprise/svc.py": "y", "internal-docs/n.md": "z"})
    assert open_core.twin_overlap(root) == []


def test_a_file_both_repositories_track_is_found(tmp_path: Path):
    root = repo(tmp_path, {"README.md": "x"}, BOUNDARY)
    _twin(root, {"README.md": "x", "enterprise/svc.py": "y"})
    assert open_core.twin_overlap(root) == ["README.md  (tracked by both repositories)",
                                            "README.md  (not ignored by this repository)"]


def test_a_twin_file_this_repository_would_publish_is_found(tmp_path: Path):
    root = repo(tmp_path, {"README.md": "x"}, BOUNDARY)
    _twin(root, {"notes/plan.md": "commercial", "enterprise/svc.py": "y"})
    assert open_core.twin_overlap(root) == ["notes/plan.md  (not ignored by this repository)"]


def test_no_twin_answers_none_not_an_empty_list(tmp_path: Path):
    assert open_core.twin_overlap(repo(tmp_path, {"README.md": "x"}, BOUNDARY)) is None


def test_a_twin_nobody_can_list_is_could_not_look(tmp_path: Path):
    root = repo(tmp_path, {"README.md": "x"}, BOUNDARY)
    (root / open_core.TWIN_DIR).mkdir()
    with pytest.raises(CouldNotLook):
        open_core.twin_overlap(root)


# ── the public face ──────────────────────────────────────────────────────────


def test_this_repository_carries_its_public_files():
    """A public repository says how to contribute, where to report a vulnerability and what
    conduct it expects - tracked, not merely present on one machine."""
    assert open_core.required_public_files(ROOT) == []


def test_a_missing_or_untracked_public_file_is_found(tmp_path: Path):
    root = repo(tmp_path, {"README.md": "x", "LICENSE": "MIT"})
    (root / "SECURITY.md").write_text("untracked", encoding="utf-8")
    assert open_core.required_public_files(root) == [
        "CODE_OF_CONDUCT.md", "CONTRIBUTING.md", "SECURITY.md"]


# ── could not look ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("check, said", [("private_paths_tracked", "ls-files exited"),
                                         ("barricade_breaches", "ls-files exited"),
                                         ("required_public_files", "ls-files exited"),
                                         ("private_paths_unignored", "check-ignore exited")])
def test_a_tree_git_cannot_read_is_could_not_look_not_clean(tmp_path: Path, check: str,
                                                             said: str):
    """A failed listing is a tree nobody could read, never a clean one - and it is named as the
    failure it is, not as a later step's empty answer."""
    with pytest.raises(CouldNotLook, match=said):
        getattr(open_core, check)(tmp_path)


def test_an_empty_listing_at_exit_0_is_could_not_look_not_clean(tmp_path: Path):
    """A repository with nothing committed answers `git ls-files` with nothing, at exit 0 -
    CLAUDE.md's rule: that is not evidence of absence."""
    git(tmp_path, "init", "-q")
    with pytest.raises(CouldNotLook):
        open_core.private_paths_tracked(tmp_path)
