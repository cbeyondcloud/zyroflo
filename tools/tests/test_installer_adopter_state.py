"""Batch 6: the installer leaves what it cannot safely change alone, and says so.

Four rows from the full-repo review (T482, run 4), each a place where `sdlc-init.py`
acted on an adopter's repository without having understood it:

| row  | what the adopter had                    | what the installer did                  |
|------|-----------------------------------------|-----------------------------------------|
| T513 | a `.claude/settings.json` it could not  | rewrote it from scratch, losing every   |
|      | merge (a comment, a trailing comma, a   | key, and printed "merged, not           |
|      | string where a list belongs)            | clobbered"; one shape was a traceback   |
| T514 | git-crypt's `.git-crypt/` directory     | read it as a second git directory and   |
|      |                                         | denied raw git                          |
| T516 | `--tools claude,cursor,claude`          | a traceback at exit 1                   |
| T517 | a generated file it could not write     | a traceback at exit 1, after writing    |
|      |                                         | other files: exit 1 means "nothing was  |
|      |                                         | installed"                              |

Spawned wherever the question is what a person sees, because a CLI's contract is a
process. A directory, or a file standing where a directory must go, stands in for an
unwritable path: a read-only bit does not stop root, which CI's containers run as.
"""
from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.tests import _project  # noqa: E402

INSTALLER = ROOT / "sdlc-init.py"
SETTINGS = Path(".claude") / "settings.json"


def _si():
    spec = importlib.util.spec_from_file_location("sdlc_init_adopter_state", INSTALLER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _install(root: Path, *args: str) -> subprocess.CompletedProcess:
    _project.seed(root)
    return subprocess.run([sys.executable, str(INSTALLER), "--target", str(root),
                           "--no-graph", *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=600)


def _said(run: subprocess.CompletedProcess) -> str:
    return run.stdout + run.stderr


def _tree(root: Path) -> dict[str, bytes]:
    """Every file under `root`, by relative path: "nothing was written" as a value."""
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


def _git(*args: str) -> None:
    subprocess.run(["git", *args], check=True, capture_output=True, timeout=120)


# ── T513: a settings file it cannot merge is left alone and named ─────────────

#: Each shape, and the words the refusal must use for it.
UNMERGEABLE = {
    "a comment": ('{\n  // chosen by hand\n  "model": "team-model",\n'
                  '  "env": {"TEAM": "platform"}\n}\n', "not valid JSON"),
    "a trailing comma": ('{"model": "team-model", "env": {"TEAM": "platform"},}\n',
                         "not valid JSON"),
    "an allow list written as one string": (
        '{"permissions": {"allow": "Bash(make build:*)"}}\n', "permissions.allow"),
    "permissions that are not an object": (
        '{"permissions": "everything"}\n', "permissions"),
    "a document that is not an object": ('["model"]\n', "an object"),
    "a hook list written as one string": (
        '{"hooks": {"PostToolUse": "lint"}}\n', "hooks.PostToolUse"),
}


@pytest.mark.parametrize("shape", sorted(UNMERGEABLE))
def test_a_settings_file_it_cannot_merge_is_left_alone_and_named(tmp_path, shape):
    """T513. It was rewritten from a blank merge, so every adopter key went - their
    model, their env, their own permission rules - while the run's last line said
    "settings.json merged, not clobbered". A string `allow` was split into characters,
    and a string `permissions` was a traceback at exit 1."""
    text, words = UNMERGEABLE[shape]
    path = tmp_path / SETTINGS
    path.parent.mkdir(parents=True)
    path.write_bytes(text.encode("utf-8"))

    run = _install(tmp_path, "--tools", "claude", "--claude-extras")

    assert run.returncode == 0 and "Traceback" not in _said(run), _said(run)
    assert path.read_bytes() == text.encode("utf-8"), \
        "the adopter's settings were rewritten"
    listed = [line for line in run.stdout.splitlines()
              if line.rstrip().endswith(SETTINGS.as_posix())]
    assert listed and "BLOCKED" in listed[0], run.stdout
    assert f"NOTE: {SETTINGS.as_posix()} was left untouched" in run.stdout, run.stdout
    assert words in run.stdout, run.stdout
    assert "merged, not clobbered" not in run.stdout, \
        "the run must not claim a merge that did not happen"
    assert "NOT merged" in run.stdout, run.stdout


def test_a_settings_file_it_can_read_keeps_every_adopter_key(tmp_path):
    """The control: what T513 must not break. A well-formed file is merged, the
    adopter's keys and rules survive, and the harness's rules are added."""
    path = tmp_path / SETTINGS
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "model": "team-model", "env": {"TEAM": "platform"},
        "permissions": {"allow": ["Bash(make build:*)"], "deny": ["Bash(curl:*)"]},
    }), encoding="utf-8")

    run = _install(tmp_path, "--tools", "claude", "--claude-extras")

    assert run.returncode == 0, _said(run)
    merged = json.loads(path.read_text(encoding="utf-8"))
    assert merged["model"] == "team-model" and merged["env"] == {"TEAM": "platform"}
    assert "Bash(make build:*)" in merged["permissions"]["allow"]
    assert "Bash(curl:*)" in merged["permissions"]["deny"]
    assert "Bash(git status:*)" in merged["permissions"]["allow"]
    assert "merged, not clobbered" in run.stdout, run.stdout


def test_a_byte_order_mark_does_not_cost_the_adopter_their_settings(tmp_path):
    """A BOM is how Windows PowerShell writes UTF-8, and `json.loads` refuses it. It
    read as unparseable, so the file was replaced and its keys were lost. It is read
    past, and the merged file keeps them."""
    path = tmp_path / SETTINGS
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps({"model": "team-model"}).encode())

    run = _install(tmp_path, "--tools", "claude", "--claude-extras")

    assert run.returncode == 0, _said(run)
    merged = json.loads(path.read_text(encoding="utf-8-sig"))
    assert merged["model"] == "team-model"
    assert "Bash(git status:*)" in merged["permissions"]["allow"]


# ── T514: a directory named .git-* is a git directory only if it is one ───────

def test_a_git_crypt_directory_is_not_a_second_git_directory(tmp_path):
    """T514. `git-crypt init` commits `.git-crypt/` at the repository root. The name
    matched `.git-*`, so the install denied raw git, lost worktree isolation and
    asked for a check-in script the repository does not have."""
    _git("init", "-q", str(tmp_path))
    keys = tmp_path / ".git-crypt" / "keys" / "default" / "0"
    keys.mkdir(parents=True)
    (keys / "0123456789ABCDEF.gpg").write_bytes(b"not a real key")
    (tmp_path / ".git-crypt" / ".gitattributes").write_text("* !filter !diff\n",
                                                           encoding="utf-8")

    run = _install(tmp_path, "--tools", "claude", "--claude-extras")

    assert run.returncode == 0, _said(run)
    assert "raw git DENIED" not in run.stdout, run.stdout
    profile = (tmp_path / ".sdlc" / "profile.yml").read_text(encoding="utf-8")
    assert "deny_raw_git: false" in profile
    allow = json.loads((tmp_path / SETTINGS).read_text(encoding="utf-8"))
    assert "Bash(git commit:*)" in allow["permissions"]["allow"]


def test_a_real_second_git_directory_still_denies_raw_git(tmp_path):
    """The control. An open-core layout keeps a second repository beside `.git`;
    a raw push can reach the wrong remote, and a worktree carries only `.git`."""
    _git("init", "-q", str(tmp_path))
    _git("init", "-q", "--bare", str(tmp_path / ".git-commercial"))

    run = _install(tmp_path, "--tools", "claude")

    assert run.returncode == 0, _said(run)
    assert "raw git DENIED" in run.stdout, run.stdout
    assert ".git-commercial" in (tmp_path / ".sdlc" / "profile.yml").read_text(
        encoding="utf-8")


def test_a_directory_shaped_like_a_git_directory_is_recognised(tmp_path):
    """The predicate, both ways: HEAD, objects/ and refs/ are what git itself looks
    for before it treats a directory as one; any one missing is not a repository."""
    si = _si()
    whole = tmp_path / ".git-other"
    (whole / "objects").mkdir(parents=True)
    (whole / "refs").mkdir()
    (whole / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    assert si.detect_vcs(tmp_path)["deny_raw_git"] is True
    (whole / "HEAD").unlink()
    assert si.detect_vcs(tmp_path)["deny_raw_git"] is False
    # And a FILE by such a name - git's own blame-ignore list - is never one.
    (tmp_path / ".git-blame-ignore-revs").write_text("0" * 40 + "\n", encoding="utf-8")
    assert si.detect_vcs(tmp_path)["deny_raw_git"] is False


def test_a_second_repository_kept_as_a_working_clone_still_counts(tmp_path):
    """Batch-6 review B6-6: a `.git-*` directory that is an ordinary clone - its git
    directory one level down, in `.git-private/.git/` - carries the same hazard as a
    bare one beside `.git`, and T514's narrowing had stopped counting it."""
    _git("init", "-q", str(tmp_path))
    _git("init", "-q", str(tmp_path / ".git-private"))
    assert _si().detect_vcs(tmp_path)["deny_raw_git"] is True


def test_a_directory_it_cannot_look_into_still_counts_as_one():
    """A look that failed is not evidence of absence. Every `.git-*` directory counted
    before T514, and denying raw git is the direction that cannot push through a
    wall somebody put up, so one that cannot be checked keeps counting."""

    class Unreadable:
        def __truediv__(self, name):
            return self

        def is_file(self):
            raise PermissionError(13, "Permission denied")

        is_dir = is_file

    assert _si()._laid_out_as_git_dir(Unreadable()) is True


# ── T516: a tool named twice is one tool ───────────────────────────────────────

@pytest.mark.parametrize("tools,expected", [
    ("claude,cursor,claude", "claude, cursor"),
    ("cursor,cursor", "claude, cursor"),
])
def test_a_tool_named_twice_is_installed_once(tmp_path, tools, expected):
    """T516: two ops on one file tripped the fan-out's own guard, a RuntimeError
    that reached the person as a traceback at exit 1."""
    run = _install(tmp_path, "--tools", tools)
    assert run.returncode == 0 and "Traceback" not in _said(run), _said(run)
    assert f"tools          : {expected}\n" in run.stdout, run.stdout
    assert (tmp_path / ".cursor" / "rules" / "sdlc.mdc").is_file()


# ── T517: a file it cannot write is refused before anything is written ───────

def _block_the_skill(root: Path) -> Path:
    """A FILE where the skill's directory must go, so the skill cannot be written."""
    blocker = root / ".claude" / "skills" / "sdlc"
    blocker.parent.mkdir(parents=True)
    blocker.write_text("a file, where a directory must go\n", encoding="utf-8")
    return blocker


def test_a_path_it_cannot_write_refuses_the_install_before_anything_is_written(
        tmp_path):
    """T517. The write raised midway: `.sdlc/` was already written when the skill
    failed, and the traceback's exit 1 is the code this installer documents as
    "nothing was installed". It is now asked before the first write."""
    _project.seed(tmp_path)
    _block_the_skill(tmp_path)
    before = _tree(tmp_path)

    run = _install(tmp_path, "--tools", "claude")

    assert run.returncode == 1 and "Traceback" not in _said(run), _said(run)
    assert _tree(tmp_path) == before, "exit 1 is 'nothing was installed'"
    assert ".claude/skills/sdlc/SKILL.md" in run.stdout, run.stdout
    assert "nothing was written" in run.stdout.lower(), run.stdout


def test_a_dry_run_reports_the_refusal_the_real_run_would_give(tmp_path):
    """A dry run that says "N files would change" about a run that refuses
    describes a different command from the one it stands in for."""
    _project.seed(tmp_path)
    _block_the_skill(tmp_path)
    before = _tree(tmp_path)

    run = _install(tmp_path, "--tools", "claude", "--dry-run")

    assert run.returncode == 1 and "Traceback" not in _said(run), _said(run)
    assert _tree(tmp_path) == before
    assert ".claude/skills/sdlc/SKILL.md" in run.stdout, run.stdout


@pytest.mark.skipif(os.name != "nt" and os.geteuid() == 0,
                    reason="root writes through a read-only bit")
def test_a_read_only_generated_file_refuses_the_install_before_anything_is_written(
        tmp_path):
    """T517's own shape: an existing generated file marked read-only (an editor's
    lock answers the same way on Windows). Only a re-run meets it, so the first
    install lays the tree down."""
    assert _install(tmp_path, "--tools", "claude").returncode == 0
    rubric = tmp_path / ".sdlc" / "review-rubric.md"
    rubric.write_text("an older rubric\n", encoding="utf-8")
    # A write the run owes BEFORE it reaches the rubric, so a run that stops at the
    # rubric has already changed the tree.
    (tmp_path / ".sdlc" / "providers.toml").unlink()
    os.chmod(rubric, stat.S_IREAD)
    try:
        before = _tree(tmp_path)
        run = _install(tmp_path, "--tools", "claude")
        assert run.returncode == 1 and "Traceback" not in _said(run), _said(run)
        assert _tree(tmp_path) == before, "exit 1 is 'nothing was installed'"
        assert ".sdlc/review-rubric.md" in run.stdout, run.stdout
    finally:
        os.chmod(rubric, stat.S_IREAD | stat.S_IWRITE)


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0,
                    reason="POSIX mode bits, which bind only below root")
def test_the_write_check_asks_for_write_alone_and_names_the_target_plainly(tmp_path):
    """Review round 1's suspected case: probing with `open(path, "r+b")` also needs
    READ, so a write-only file the real write would reach was refused. And a target
    directory that takes no new file is named as one, not as "./"."""
    si = _si()
    write_only = tmp_path / "generated.md"
    write_only.write_text("x\n", encoding="utf-8")
    os.chmod(write_only, stat.S_IWUSR)
    try:
        assert si.cannot_write_reason(tmp_path, write_only) is None
    finally:
        os.chmod(write_only, stat.S_IRUSR | stat.S_IWUSR)
    os.chmod(tmp_path, stat.S_IRUSR | stat.S_IXUSR)
    try:
        why = si.cannot_write_reason(tmp_path, tmp_path / "new.md")
    finally:
        os.chmod(tmp_path, stat.S_IRWXU)
    assert why and why.startswith("the target directory"), why


@pytest.mark.parametrize("dry", [False, True])
def test_a_project_file_it_cannot_write_is_refused_by_name(tmp_path, dry):
    """T565, T517's class at the one write before the plan: with no project file,
    INIT writes the template and stops for it to be filled - and a directory standing
    at that name answered with a traceback."""
    (tmp_path / "sdlc-project.toml").mkdir()
    before = _tree(tmp_path)

    run = _install(tmp_path, "--tools", "claude", *(["--dry-run"] if dry else []))

    assert run.returncode == 1 and "Traceback" not in _said(run), _said(run)
    assert _tree(tmp_path) == before and not (tmp_path / ".sdlc").exists()
    assert "sdlc-project.toml" in run.stdout and "nothing was written" in run.stdout, \
        run.stdout


def _failing_writes(si, monkeypatch, fails: str) -> None:
    """`write_lf` raises for one file: what no look ahead can see."""
    real = si.write_lf

    def failing(path, content):
        if Path(path).as_posix().endswith(fails):
            raise PermissionError(13, "Permission denied", str(path))
        return real(path, content)

    monkeypatch.setattr(si, "write_lf", failing)


@pytest.mark.parametrize("fails", [".sdlc/review-rubric.md", ".sdlc/profile.yml"])
def test_a_write_that_fails_after_the_check_is_named_and_the_run_says_incomplete(
        tmp_path, monkeypatch, capsys, fails):
    """What no check can see in advance - a disk that fills, a file locked after it
    was probed - is reported as that file's own outcome, and the rest land: by then
    other files are written, so exit 1 ("nothing was installed") would be false.
    Batch-6 review B6-1: nor is it a success. It ended at 0 with "Next: commit what
    was just installed", and drew the pipeline from a profile that was not there. It
    is exit 4, the scaffold is incomplete, and nothing is built on it."""
    si = _project.filled_gate(_si())
    _failing_writes(si, monkeypatch, fails)
    code = si.install(tmp_path, ["claude"], force=False, dry_run=False, graph=False)
    out = capsys.readouterr().out

    assert code == 4, out
    listed = [line for line in out.splitlines() if line.rstrip().endswith(fails)]
    assert listed and "BLOCKED (could not write)" in listed[0], out
    assert f"NOTE: {fails} could not be written" in out, out
    assert not (tmp_path / fails).exists()
    others = {".sdlc/review-rubric.md", ".sdlc/profile.yml"} - {fails}
    assert all((tmp_path / other).is_file() for other in others), "the rest must land"
    assert "INCOMPLETE" in out, out
    assert "Next: commit" not in out, "no sign-off over a scaffold with a hole in it"
    assert "pipeline diagram:\n" not in out, "nothing is built on it"


def test_a_run_whose_every_write_fails_is_exit_1_not_incomplete(
        tmp_path, monkeypatch, capsys):
    """Delta review R1-1: exit 4 says a file failed AFTER others were written. When
    every write failed nothing was, which is what exit 1 means, and the run says so
    rather than "the files that were written stay"."""
    si = _project.filled_gate(_si())

    def failing(path, content):
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(si, "write_lf", failing)
    code = si.install(tmp_path, ["claude"], force=False, dry_run=False, graph=False)
    out = capsys.readouterr().out

    assert code == 1, out
    assert not (tmp_path / ".sdlc" / "profile.yml").exists()
    assert "so nothing was installed" in out, out
    assert "the files that were written stay" not in out, out
    assert "Next: commit" not in out, out


def test_a_write_that_began_before_it_failed_is_incomplete_not_nothing(
        tmp_path, monkeypatch, capsys):
    """Delta review R2-1: `write_lf` opens with "w", so a write that fails AFTER its
    open - a disk that fills - has already created or emptied the file. Counting only
    the writes that completed, the run said "nothing was installed" at exit 1 over a
    tree of empty files, including the profile. What reached the disk decides: exit
    4, and each such file's NOTE says it may be left partly written."""
    si = _project.filled_gate(_si())

    def begun_then_failed(path, content):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8"):
            pass
        raise OSError(28, "No space left on device", str(path))

    monkeypatch.setattr(si, "write_lf", begun_then_failed)
    code = si.install(tmp_path, ["claude"], force=False, dry_run=False, graph=False)
    out = capsys.readouterr().out

    assert code == 4, out
    assert (tmp_path / ".sdlc" / "profile.yml").stat().st_size == 0, "the fixture's premise"
    assert "nothing was installed" not in out, out
    assert "may be left partly written" in out, out


def test_a_failed_write_is_never_recorded_as_installed(tmp_path, monkeypatch, capsys):
    """Review B6-7: the digest half of the test above held for the rubric only
    vacuously, since the rubric is not preserve-capable. For the profile, which is:
    a first install records its digest, and a forced re-run whose write of it fails
    must leave that digest as it was, because the file on disk is still the one it
    describes."""
    si = _project.filled_gate(_si())
    assert si.install(tmp_path, ["claude"], force=False, dry_run=False,
                      graph=False) == 0
    sidecar = tmp_path / si.DIGESTS_REL

    def recorded() -> dict[str, str]:
        rows = (line.split("\t") for line in sidecar.read_text(encoding="utf-8")
                .splitlines() if "\t" in line)
        return dict(rows)

    before = recorded()
    assert ".sdlc/profile.yml" in before, "the fixture must start from a recorded digest"
    profile = (tmp_path / ".sdlc" / "profile.yml").read_bytes()

    _failing_writes(si, monkeypatch, ".sdlc/profile.yml")
    code = si.install(tmp_path, ["claude", "cursor"], force=True, dry_run=False,
                      graph=False)                   # a different profile, forced
    out = capsys.readouterr().out

    assert code == 4, out
    assert (tmp_path / ".sdlc" / "profile.yml").read_bytes() == profile
    assert recorded()[".sdlc/profile.yml"] == before[".sdlc/profile.yml"], \
        "the digest of a write that failed replaced the one describing the file"
