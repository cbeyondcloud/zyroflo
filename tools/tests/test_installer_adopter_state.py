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

import errno
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


def test_a_directory_at_the_settings_path_is_named_as_one(tmp_path):
    """T567 (batch-6 review). A directory standing at `.claude/settings.json` was
    reported as a bare `Permission denied` - `Is a directory` off Windows - carrying this
    machine's absolute path, rather than as what it is."""
    (tmp_path / SETTINGS).mkdir(parents=True)

    run = _install(tmp_path, "--tools", "claude", "--claude-extras")

    assert run.returncode == 0 and "Traceback" not in _said(run), _said(run)
    said = [line for line in run.stdout.splitlines()
            if f"NOTE: {SETTINGS.as_posix()} could not be read" in line]
    assert len(said) == 1 and "it is a directory, not a file" in said[0], run.stdout
    assert str(tmp_path) not in said[0] and tmp_path.as_posix() not in said[0], said


def test_an_adopters_broad_allow_rule_is_checked_against_the_deny_lists(tmp_path):
    """T566 (batch-6 review). The installer merges the adopter's own permission rules
    into `.claude/settings.json` and never ran `policy_selfcheck` over the result, so a
    broad rule of theirs - a bare interpreter, a prefix that extends into an operation
    `policy.yml` denies - went unremarked beside the deny lists the same run installs.
    Each finding is a NOTE: the rule is the adopter's, so it is said and kept, never
    removed or refused. One NOTE per rule (review B12-8): `Bash(rm:*)` reaches four
    spellings of a recursive delete, named together, not in four near-identical NOTEs -
    and so does a rule with a space in it, `Bash(git push:*)` (B12-12)."""
    path = tmp_path / SETTINGS
    path.parent.mkdir(parents=True)
    ours = ["Bash(python:*)", "Bash(git branch:*)", "Bash(rm:*)", "Bash(make build:*)",
            "Bash(git push:*)"]
    path.write_text(json.dumps({"permissions": {"allow": ours}}), encoding="utf-8")

    run = _install(tmp_path, "--tools", "claude", "--claude-extras")

    assert run.returncode == 0, _said(run)
    notes = [line for line in run.stdout.splitlines()
             if line.lstrip().startswith(f"NOTE: {SETTINGS.as_posix()}:")]
    assert any("Bash(python:*)" in n and "bare interpreter" in n for n in notes), notes
    assert any("Bash(git branch:*)" in n and "git branch -D" in n for n in notes), notes
    deletes = [n for n in notes if "Bash(rm:*)" in n]
    assert len(deletes) == 1, deletes
    assert "`rm -r`, `rm -R`, `rm -f`, `rm --recursive`" in deletes[0], deletes
    pushes = [n for n in notes if "Bash(git push:*)" in n]
    assert len(pushes) == 1, pushes
    assert ("`git push --force`, `git push --force-with-lease`, `git push -f`, "
            "`git push -f origin`") in pushes[0], pushes
    assert not any("Bash(make build:*)" in n for n in notes), notes
    assert all("it was kept" in n for n in notes), notes
    merged = json.loads(path.read_text(encoding="utf-8"))
    assert all(rule in merged["permissions"]["allow"] for rule in ours), merged


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
    """Delta review R2-1: a write that fails AFTER its open - a disk that fills - has
    already emptied the file, wherever `write_lf` writes in place: before T570
    everywhere, and since then where a replace cannot be used, a file another program
    holds open on Windows (review B12-1), one in a directory Windows takes no new file in
    (T598), or one with other hard links (B12-15).
    Counting only the writes that completed, the run said "nothing was installed" at
    exit 1 over a tree of empty files, including the profile. What reached the disk
    decides: exit 4, and each such file's NOTE says it may be left partly written. The
    stub also creates the files, which no failed write does since T570 (review
    B12-6); what it tests - that the disk, not the count, decides - holds either way."""
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


class _FillingDisk:
    """A file opened for writing whose first write fails as a full disk does: what the
    open has already done to the file - created it, or emptied it - stays done."""

    def __init__(self, handle):
        self.handle = handle

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.handle.close()
        return False

    def write(self, text):
        raise OSError(28, "No space left on device")


def _fill_disk_on(si, monkeypatch, name: str) -> None:
    """Every write-mode open the installer makes of a file whose name holds `name` -
    "w", or "x" for the file it creates beside its target - fails at its first write,
    wherever the installer writes it."""
    real = open

    def opening(file, mode="r", *args, **kwargs):
        handle = real(file, mode, *args, **kwargs)
        writes = "w" in mode or "x" in mode
        return _FillingDisk(handle) if writes and name in Path(file).name else handle

    monkeypatch.setattr(si, "open", opening, raising=False)


def test_a_write_that_fails_midway_leaves_the_file_as_it_was(tmp_path, monkeypatch,
                                                             capsys):
    """T570 (batch-6 review R3-1). `write_lf` opened its target with "w", so a write that
    failed midway - a disk that fills - left the file emptied, and the re-run the message
    asked for could not repair a preserve-capable one: with no digest of an empty
    profile, the next run kept it as a difference it could not attribute, wrote `.new`
    beside it and signed off at exit 0. A write now goes to a file beside the target and
    replaces it only once complete: the file is as it was, nothing is left beside it, and
    the NOTE does not say the file may be partly written."""
    si = _project.filled_gate(_si())
    assert si.install(tmp_path, ["claude"], force=False, dry_run=False, graph=False) == 0
    profile = tmp_path / ".sdlc" / "profile.yml"
    profile.write_text(profile.read_text(encoding="utf-8") + "# an adopter's line\n",
                       encoding="utf-8", newline="\n")
    before = profile.read_bytes()
    capsys.readouterr()
    _fill_disk_on(si, monkeypatch, "profile.yml")
    si.install(tmp_path, ["claude"], force=True, dry_run=False, graph=False)
    out = capsys.readouterr().out
    assert profile.read_bytes() == before, "the failed write changed the file"
    beside = sorted(p.name for p in profile.parent.iterdir()
                    if "profile.yml" in p.name and p.name != "profile.yml")
    assert beside == [], beside
    assert ".sdlc/profile.yml" in out and "may be left partly written" not in out, out


def test_a_write_keeps_a_symlinked_target_a_link(tmp_path):
    """T570's fix replaces a file rather than writing into it, and replacing the LINK
    would turn an adopter's `AGENTS.md -> CLAUDE.md` into a copy that no longer follows
    `CLAUDE.md`. The link is resolved first and its target written. A control: `main`
    wrote through the link with its "w" open. Windows refuses an unprivileged symlink
    (WinError 1314), and that is the only way this test may not make one."""
    si = _si()
    (tmp_path / "CLAUDE.md").write_text("ours\n", encoding="utf-8")
    link = tmp_path / "AGENTS.md"
    try:
        os.symlink("CLAUDE.md", link)
    except OSError as exc:
        assert os.name == "nt" and getattr(exc, "winerror", None) == 1314, exc
        return
    si.write_lf(link, "written through the link\n")
    assert link.is_symlink(), "the write replaced the link with a file"
    assert (tmp_path / "CLAUDE.md").read_text(encoding="utf-8") == "written through the link\n"


def test_a_file_another_program_holds_open_is_still_written(tmp_path):
    """Review B12-1 (the owner's ruling A, 2026-10-01). T570's replace is refused on
    Windows while any program holds the target open, even only to read it, where an
    in-place write succeeds - and the pre-check could not see it. The write falls back to
    writing in place there, so the file is written and nothing is left beside it. POSIX
    lets the replace through, with the same outcome."""
    si = _si()
    target = tmp_path / "held.md"
    target.write_text("old\n", encoding="utf-8")
    with open(target, "r", encoding="utf-8"):
        si.write_lf(target, "new\n")
    assert target.read_text(encoding="utf-8") == "new\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["held.md"]


def test_a_read_only_file_is_refused_by_its_own_name_with_nothing_beside_it(tmp_path):
    """Review B12-2. T570 copied a read-only target's mode onto the file beside it, so on
    Windows the replace failed, the clean-up could not delete the read-only temp, and the
    error named the temp - left in the adopter's repository - while on POSIX the replace
    went straight through the flag. A read-only file is refused before anything is
    written, by its own name. Whether the flag binds is measured: root writes through it."""
    si = _si()
    target = tmp_path / "ro.md"
    target.write_text("old\n", encoding="utf-8")
    target.chmod(stat.S_IREAD)
    try:
        if os.access(target, os.W_OK):
            si.write_lf(target, "new\n")
            assert target.read_text(encoding="utf-8") == "new\n"
        else:
            with pytest.raises(PermissionError) as refused:
                si.write_lf(target, "new\n")
            assert refused.value.filename == str(target), refused.value
            assert target.read_text(encoding="utf-8") == "old\n"
    finally:
        target.chmod(stat.S_IREAD | stat.S_IWRITE)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["ro.md"]


def test_a_directory_that_takes_no_new_file_is_named_before_anything_is_written(tmp_path):
    """Review B12-4. T570's write makes a file beside its target, so a writable file in a
    directory that takes no new entry could no longer be written - while the pre-check,
    which only opened the file, said nothing stood in the way. It names the directory
    now, as it already did for a file not yet there. POSIX only: a directory's mode bits
    do not do this on Windows, and root writes anyway - measured, not assumed."""
    si = _si()
    sealed = tmp_path / "sealed"
    sealed.mkdir()
    kept = sealed / "kept.md"
    kept.write_text("old\n", encoding="utf-8")
    sealed.chmod(0o555)
    try:
        binds = os.name != "nt" and not os.access(sealed, os.W_OK | os.X_OK)
        said = si.cannot_write_reason(tmp_path, kept)
    finally:
        sealed.chmod(0o755)
    assert said == ("sealed does not accept a new file" if binds else None), said


def test_a_dangling_link_is_written_where_it_points_and_makes_nothing_there(tmp_path):
    """Review B12-5. T570 resolved a symlinked target and then made its directories, so a
    dangling link pointing outside the install made directories and a file out there,
    where `main` refused. Directories are made only for a path that is not a link.
    Windows refuses an unprivileged symlink (WinError 1314), the only way this test may
    not make one."""
    si = _si()
    (tmp_path / "escape").mkdir()
    link = tmp_path / "escape" / "link.md"
    try:
        os.symlink(os.path.join("..", "outside", "nested", "x.md"), link)
    except OSError as exc:
        assert os.name == "nt" and getattr(exc, "winerror", None) == 1314, exc
        return
    with pytest.raises(FileNotFoundError):
        si.write_lf(link, "x\n")
    assert not (tmp_path / "outside").exists() and link.is_symlink()


def _opens_for_writing(path: Path) -> bool:
    try:
        os.close(os.open(path, os.O_WRONLY))
    except PermissionError:
        return False
    return True


def test_a_file_the_system_will_not_open_for_writing_is_not_replaced(tmp_path):
    """Review round 2 (suspected there; measured 2026-10-01). The check before the
    replace asked `os.access`, which on Windows sees the read-only flag alone, so a file
    an ACL denies writing to was replaced - a replace needs only the right to delete
    it - and T570 got round the adopter's protection, where `main`'s in-place write was
    refused. The check opens the file for writing now, as `cannot_write_reason` does,
    and a file the system will not open is refused by its own name. POSIX has no ACL
    here, so its mode bits stand in; whether a protection binds is measured, since root
    writes through the mode bits."""
    si = _si()
    target = tmp_path / "guarded.md"
    target.write_text("old\n", encoding="utf-8")
    everyone = "*S-1-1-0"
    if os.name == "nt":
        guard = subprocess.run(["icacls", str(target), "/deny", f"{everyone}:(WD)"],
                               capture_output=True, text=True)
        assert guard.returncode == 0, guard.stdout + guard.stderr
    else:
        target.chmod(stat.S_IREAD)
    try:
        if _opens_for_writing(target):
            si.write_lf(target, "new\n")
            assert target.read_text(encoding="utf-8") == "new\n"
        else:
            with pytest.raises(PermissionError) as refused:
                si.write_lf(target, "new\n")
            assert refused.value.filename == str(target), refused.value
            assert target.read_text(encoding="utf-8") == "old\n"
    finally:
        if os.name == "nt":
            subprocess.run(["icacls", str(target), "/remove:d", everyone],
                           capture_output=True, text=True)
        else:
            target.chmod(stat.S_IREAD | stat.S_IWRITE)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["guarded.md"]


def test_a_writable_file_in_a_directory_that_takes_no_new_file_is_written(tmp_path):
    """T598 (review B12-18). On Windows a directory whose ACL refuses a new file - while the
    file in it stays writable, a shape corporate hardening uses - refused the file `main`
    wrote in place: T570 could not make the file beside it, the pre-check cannot see a
    directory's ACL there, and the refusal named the file, which is writable. It is written
    in place, as B12-1 does for a file another program holds open; a new file there is
    still refused, by its own name. POSIX names such a directory in the pre-check (B12-4),
    kept; whether its mode bits bind is measured, since root writes through them."""
    si = _si()
    sealed = tmp_path / "sealed"
    sealed.mkdir()
    kept = sealed / "kept.md"
    kept.write_text("old\n", encoding="utf-8")
    everyone = "*S-1-1-0"
    if os.name == "nt":
        guard = subprocess.run(["icacls", str(sealed), "/deny", f"{everyone}:(WD)"],
                               capture_output=True, text=True)
        assert guard.returncode == 0, guard.stdout + guard.stderr
    else:
        sealed.chmod(0o555)
    try:
        if os.name == "nt":
            # The ACE binds: a new file is refused there, and the file in it stays writable.
            with pytest.raises(PermissionError):
                (sealed / "probe.md").write_text("x\n", encoding="utf-8")
            assert _opens_for_writing(kept)
            assert si.cannot_write_reason(tmp_path, kept) is None
            si.write_lf(kept, "new\n")
            assert kept.read_text(encoding="utf-8") == "new\n"
            new = sealed / "new.md"
            with pytest.raises(PermissionError) as refused:
                si.write_lf(new, "x\n")
            assert refused.value.filename == str(new), refused.value
            assert sorted(p.name for p in sealed.iterdir()) == ["kept.md"]
        else:
            binds = not os.access(sealed, os.W_OK | os.X_OK)
            said = si.cannot_write_reason(tmp_path, kept)
            assert said == ("sealed does not accept a new file" if binds else None), said
            if binds:
                # The write refuses what the pre-check refuses, on POSIX as before.
                with pytest.raises(PermissionError):
                    si.write_lf(kept, "new\n")
                assert kept.read_text(encoding="utf-8") == "old\n"
    finally:
        if os.name == "nt":
            subprocess.run(["icacls", str(sealed), "/remove:d", everyone],
                           capture_output=True, text=True)
        else:
            sealed.chmod(0o755)


def test_a_failed_write_names_the_target_alone(tmp_path, monkeypatch):
    """Review B12-13. An error naming two paths - the file beside the target and the
    target - was made to name the target by clearing its second path, and an OSError
    renders a cleared second path as `-> None`. The error is raised afresh, naming the
    target alone, and the file beside it is still removed."""
    si = _si()
    target = tmp_path / "new.md"

    def moved_nowhere(src, dst):
        raise OSError(errno.EXDEV, os.strerror(errno.EXDEV), str(src), None, str(dst))

    monkeypatch.setattr(si.os, "replace", moved_nowhere)
    with pytest.raises(OSError) as failed:
        si.write_lf(target, "x\n")
    said = f"[Errno {errno.EXDEV}] {os.strerror(errno.EXDEV)}: {str(target)!r}"
    assert str(failed.value) == said, failed.value
    assert list(tmp_path.iterdir()) == []


def test_a_link_at_the_name_beside_the_target_is_not_written_through(tmp_path):
    """Review B12-14. The file beside the target has a fixed name, and T570 opened it with
    "w", which follows a link: a link standing at that name had the content written
    wherever it pointed, and was then itself moved over the target. Whatever stands at
    that name is the installer's own leftover, so it is removed, never followed, and the
    file is created afresh. Windows refuses an unprivileged symlink (WinError 1314), the
    only way this test may not make one; a temp a killed run left stands in there."""
    si = _si()
    target = tmp_path / "ours.md"
    target.write_text("old\n", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere.md"
    elsewhere.write_text("theirs\n", encoding="utf-8")
    beside = tmp_path / ".ours.md.sdlc-tmp"
    try:
        os.symlink(elsewhere, beside)
    except OSError as exc:
        assert os.name == "nt" and getattr(exc, "winerror", None) == 1314, exc
        beside.write_text("left by a killed run\n", encoding="utf-8")
    si.write_lf(target, "new\n")
    assert target.read_text(encoding="utf-8") == "new\n" and not target.is_symlink()
    assert elsewhere.read_text(encoding="utf-8") == "theirs\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["elsewhere.md", "ours.md"]


def test_a_read_only_leftover_at_the_temp_name_is_cleared_and_removed(tmp_path):
    """T599 (review B12-19). A file with the Windows read-only flag at the temp name - which
    no run leaves itself - was one `os.unlink` cannot remove there, so the file beside it
    could never be written, and "re-run once it can be" could never be followed. Its flag
    is cleared and it is removed, as any leftover at that name is (B12-14). POSIX removes a
    read-only file from a writable directory as it stands."""
    si = _si()
    target = tmp_path / "ours.md"
    target.write_text("old\n", encoding="utf-8")
    beside = tmp_path / ".ours.md.sdlc-tmp"
    beside.write_text("left by something else\n", encoding="utf-8")
    beside.chmod(stat.S_IREAD)
    try:
        si.write_lf(target, "new\n")
    finally:
        if beside.exists():
            beside.chmod(stat.S_IREAD | stat.S_IWRITE)
    assert target.read_text(encoding="utf-8") == "new\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["ours.md"]


@pytest.mark.parametrize("existing", [True, False], ids=["existing", "new"])
def test_a_directory_at_the_temp_name_is_refused_by_its_own_name(tmp_path, existing):
    """T599 (review B12-19), the other occupant `os.unlink` cannot remove: a directory at
    the temp name, on either platform. Its error was renamed to the target, the pre-check
    said nothing stood in the way, and "re-run once it can be" could never be followed. It
    is not the installer's to remove, so the write and the pre-check alike refuse it by its
    own name, and nothing is written."""
    si = _si()
    target = tmp_path / "ours.md"
    if existing:
        target.write_text("old\n", encoding="utf-8")
    beside = tmp_path / ".ours.md.sdlc-tmp"
    beside.mkdir()
    (beside / "theirs.txt").write_text("kept\n", encoding="utf-8")
    said = si.cannot_write_reason(tmp_path, target)
    assert said is not None and ".ours.md.sdlc-tmp" in said, said
    with pytest.raises(IsADirectoryError) as refused:
        si.write_lf(target, "new\n")
    assert refused.value.filename == str(beside), refused.value
    assert ".ours.md.sdlc-tmp" in refused.value.strerror, refused.value
    assert (target.read_text(encoding="utf-8") if existing else target.exists()) == (
        "old\n" if existing else False)
    assert (beside / "theirs.txt").read_text(encoding="utf-8") == "kept\n"


def test_a_directory_link_at_the_temp_name_is_removed_not_refused(tmp_path):
    """T599's other side: a link at the temp name is a leftover, removed and never followed
    (B12-14) - a link to a directory too, which is not a directory of the adopter's. A
    junction on Windows (made without privilege), a symlink on POSIX."""
    si = _si()
    target = tmp_path / "ours.md"
    target.write_text("old\n", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "theirs.txt").write_text("kept\n", encoding="utf-8")
    beside = tmp_path / ".ours.md.sdlc-tmp"
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(elsewhere), str(beside))
    else:
        os.symlink(elsewhere, beside, target_is_directory=True)
    assert si.cannot_write_reason(tmp_path, target) is None
    si.write_lf(target, "new\n")
    assert target.read_text(encoding="utf-8") == "new\n"
    assert (elsewhere / "theirs.txt").read_text(encoding="utf-8") == "kept\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["elsewhere", "ours.md"]


def test_a_directory_at_the_temp_name_refuses_the_install_and_moving_it_is_followed(
        tmp_path):
    """T599 where a person meets it: the install refuses before anything is written, naming
    the directory - and doing what the refusal says, moving what stands in the way, lets
    the next run install."""
    _project.seed(tmp_path)
    blocker = tmp_path / ".claude" / "skills" / "sdlc" / ".SKILL.md.sdlc-tmp"
    blocker.mkdir(parents=True)
    (blocker / "theirs.txt").write_text("kept\n", encoding="utf-8")
    before = _tree(tmp_path)

    run = _install(tmp_path, "--tools", "claude")

    assert run.returncode == 1 and "Traceback" not in _said(run), _said(run)
    assert _tree(tmp_path) == before, "exit 1 is 'nothing was installed'"
    assert ".claude/skills/sdlc/.SKILL.md.sdlc-tmp" in run.stdout, run.stdout
    assert "move what stands in its way" in run.stdout, run.stdout
    blocker.rename(tmp_path / "moved-aside")
    again = _install(tmp_path, "--tools", "claude")
    assert again.returncode == 0, _said(again)
    assert (tmp_path / ".claude" / "skills" / "sdlc" / "SKILL.md").is_file()


def test_a_file_with_another_hard_link_keeps_it(tmp_path):
    """Review B12-15. T570's replace gives the target a new file, so a hard link to it -
    an adopter's second name for the same file - silently kept the old content. A file
    with other links is written in place, as before T570, so every name keeps it."""
    si = _si()
    target = tmp_path / "ours.md"
    target.write_text("old\n", encoding="utf-8")
    twin = tmp_path / "twin.md"
    os.link(target, twin)
    si.write_lf(target, "new\n")
    assert twin.read_text(encoding="utf-8") == "new\n"
    assert os.stat(target).st_ino == os.stat(twin).st_ino
    assert sorted(p.name for p in tmp_path.iterdir()) == ["ours.md", "twin.md"]


def test_a_hard_linked_file_needs_no_new_file_beside_it(tmp_path):
    """Review B12-15, the pre-check's half: a file with other hard links is written in
    place, so a directory that takes no new entry does not stand in its way, and the
    pre-check - which must refuse exactly what the write would - does not say it does.
    The directory's mode bits bind on POSIX only, and not for root: there this test
    asks nothing the write could refuse."""
    si = _si()
    sealed = tmp_path / "sealed"
    sealed.mkdir()
    kept = sealed / "kept.md"
    kept.write_text("old\n", encoding="utf-8")
    os.link(kept, tmp_path / "twin.md")
    sealed.chmod(0o555)
    try:
        said = si.cannot_write_reason(tmp_path, kept)
        si.write_lf(kept, "new\n")
    finally:
        sealed.chmod(0o755)
    assert said is None, said
    assert (tmp_path / "twin.md").read_text(encoding="utf-8") == "new\n"
    assert sorted(p.name for p in sealed.iterdir()) == ["kept.md"]


def test_a_failed_write_to_a_file_nobody_can_read_says_so(tmp_path, monkeypatch, capsys):
    """T571 (batch-6 review R3-2). A failed write to a file whose bytes cannot be read on
    either side - locked on Windows, write-only on POSIX - was reported as "the write had
    begun, so the file may be left partly written", when nothing may have changed. It
    says the file could not be read before or after the write; exit 4 stays, the safe
    direction."""
    si = _project.filled_gate(_si())
    assert si.install(tmp_path, ["claude"], force=False, dry_run=False, graph=False) == 0
    profile = tmp_path / ".sdlc" / "profile.yml"
    profile.write_text(profile.read_text(encoding="utf-8") + "# an adopter's line\n",
                       encoding="utf-8", newline="\n")
    capsys.readouterr()
    real = si._bytes_or_unknown
    monkeypatch.setattr(si, "_bytes_or_unknown", lambda path: (
        si._UNKNOWN if Path(path).name == "profile.yml" else real(path)))
    _fill_disk_on(si, monkeypatch, "profile.yml")
    code = si.install(tmp_path, ["claude"], force=True, dry_run=False, graph=False)
    out = capsys.readouterr().out
    assert code == 4, out
    assert "could not be read before or after the write" in out, out
    assert "may be left partly written" not in out, out


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
