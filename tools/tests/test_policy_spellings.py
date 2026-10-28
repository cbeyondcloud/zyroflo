"""T695 (Z5): a destroying command behind a spelling the shell removes is still refused.

`policy.destructive` read a command the way it is typed. A shell reads it after
removing what hides the program from that reading: a control keyword at the
command position, a leading redirect, quotes around or inside the program word,
cmd's caret and doubled quotes, a backslash escape, a carriage return between two
commands. agentic-sdlc, which pins this harness as a library, judged every
command in several spellings to cover these; the fix belongs here, so every
caller gets it. Each case below was MEASURED ALLOWED on `main` before this row.

The rule the fix keeps: the command as typed is still judged exactly as before,
and the other spellings only ADD refusals. So the false-positive controls at the
end are the cases that must stay allowed, and every message names the command as
typed, never one of its spellings.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tools.sdlc import policy as P

D = dict(P.DEFAULTS)
ROOT = Path(__file__).resolve().parents[2]


def refused(command: str, policy: dict | None = None) -> bool:
    return bool(P.destructive(policy or D, command))


# ── where the program stands ─────────────────────────────────────────────────


@pytest.mark.parametrize("command", [
    "if rm -rf x; then :; fi", "true; then rm -rf x", "false; else rm -rf x",
    "true; elif rm -rf x; then :; fi", "for d in a b; do rm -rf $d; done",
    "while true; do rm -rf x; done", "until rm -rf x; do :; done", "! rm -rf x",
    "coproc rm -rf x", "if sudo rm -rf x; then :; fi", "then FOO=1 rm -rf x",
])
def test_a_control_keyword_at_the_command_position_does_not_hide_the_program(command):
    assert refused(command), command


@pytest.mark.parametrize("command", [
    ">log rm -rf x", "> log rm -rf x", ">>log rm -rf x", "2>err rm -rf x",
    "2> /dev/null rm -rf x", "2>&1 rm -rf x", ">&2 rm -rf x", "<in rm -rf x",
    "&>log rm -rf x", ">&log rm -rf x", "sudo >log rm -rf x",
    "<<<data rm -rf x", "true; 2>&1 rm -rf x",
])
def test_a_leading_redirect_does_not_hide_the_program(command):
    assert refused(command), command


@pytest.mark.parametrize("command", [
    '>"my log" rm -rf x', "> 'my log' rm -rf x", '2> "err file" rm -rf x',
    '>>"my log" rm -rf x', "<'in put' rm -rf x", 'true && >"my log" rm -rf x',
    'if >"a b" rm -rf x; then :; fi', ">my\\ log rm -rf x", '2>&1 >"my log" rm -rf x',
    '>"\\My Logs\\run log.txt" rm -rf x', '>my" "log rm -rf x',
])
def test_a_redirect_target_with_a_space_in_it_does_not_hide_the_program(command):
    """A quoted or escaped target is one word to the shell; split at its space,
    its second half was read as the program and `rm` as an argument. Paths with
    spaces are common on Windows."""
    assert refused(command), command


@pytest.mark.parametrize("command", [
    'echo done >"my log"', "make 2> 'err file' >out.txt", 'sort <"in put" >"out put"',
    'echo "a > b c" rm', "cat <(echo rm -rf x)",
])
def test_a_redirect_target_with_a_space_in_it_is_still_only_a_file(command):
    assert not refused(command), command


@pytest.mark.parametrize("command", [
    "builtin eval rm -rf x", "chroot /mnt rm -rf x", "chroot --userspec=u:g /mnt rm -rf x",
    "watch rm -rf x", "watch -n 5 rm -rf x",
])
def test_the_builtin_chroot_and_watch_wrappers_are_seen_through(command):
    assert refused(command), command


# ── how the program is spelled ───────────────────────────────────────────────


@pytest.mark.parametrize("command", [
    '"\\Program Files\\Git\\usr\\bin\\rm.exe" -rf x',
    "'/opt/my tools/rm' -rf x",
    '\\"Program Files"\\Git\\usr\\bin\\rm.exe -rf x',
    '""\\Program Files\\Git\\usr\\bin\\rm.exe" -rf x"',
    'cmd /c ""\\Program Files\\Git\\usr\\bin\\rm.exe" -rf x"',
    'true && "/usr/local/my bin/rm" -r build',
])
def test_a_quoted_program_path_is_read_as_its_program(command):
    assert refused(command), command


@pytest.mark.parametrize("command", [
    'r""m -rf x', "r'm' -rf x", "r^m -rf x", "r\\m -rf x", 'rm -r""f x',
    "rm -r^f x", "Remove-It''em -Recurse x",
])
def test_a_quote_caret_or_backslash_inside_a_word_does_not_hide_it(command):
    assert refused(command), command


@pytest.mark.parametrize("command", [
    "bash <<< 'rm -rf x'", "sh <<<'rm -r build'", 'bash -e <<< "rm -rf x"',
    "bash -s <<< 'rm -rf x'", "zsh <<<\"rm -rf x\"",
])
def test_a_here_string_handed_to_a_shell_is_judged_as_its_script(command):
    """A shell with no command string and no script file reads its script from
    stdin, and a here-string IS its stdin: `bash <<< 'rm -rf x'` runs `rm`."""
    assert refused(command), command


@pytest.mark.parametrize("command", [
    "cat <<< 'rm -rf x'", "grep -c rm <<< 'rm -rf x'", "bash run.sh <<< 'rm -rf x'",
])
def test_a_here_string_to_anything_else_is_data(command):
    assert not refused(command), command


def test_a_carriage_return_separates_two_commands():
    """PowerShell ends a statement at a carriage return as at a newline."""
    assert refused("git status\rrm -rf x")


# ── deny_git, by tokens as well as by substring ──────────────────────────────


@pytest.mark.parametrize("command", [
    'git reset "--hard"', "git reset --ha''rd", "git reset --ha^rd",
    "git reset -q --hard", "git reset --ha HEAD~1", "git clean -df", "git clean -d -f",
    "git clean -xdf", "git push origin -f", "git push -uf origin main",
    "git branch -vD topic", "git gc --aggressive --prune=now",
    "if git reset -q --hard; then :; fi", "sudo git clean -df",
    'bash -c "git clean -df"', '"\\Program Files\\Git\\cmd\\git.exe" clean -df',
])
def test_a_denied_git_operation_is_found_by_its_words(command):
    assert refused(command), command


@pytest.mark.parametrize("command", [
    "git branch -d merged", "git clean -n", "git clean -nd", "git reset --soft HEAD~1",
    "git reset -q HEAD file", "git push origin sdlc/r1", "git push -u origin sdlc/r1",
    "git log --format=%H", "git checkout -f main", "git fetch --prune",
    "git commit -m 'reset the cache'",
    'git commit -m "docs: explain push and the -f flag"',
    'git commit -m "clean up temp files, drop -f"', 'git commit -m "remove the origin -f use"',
    'git commit -am "push it with -f"', 'git commit --message="clean all, -f"',
    'git commit -m "x" -m "push, then -f"', 'git log --grep "push.*-f"',
    'git log -S "branch x -D" --oneline', 'git tag -a v1 -m "no -f to origin"',
    'git commit -F "notes/clean up -f.txt"', 'git log --author "reset a --hard"',
])
def test_ordinary_git_is_not_refused_by_the_token_reading(command):
    """A commit message, a search string or a file name is free text: words in
    it that name a denied operation are not that operation (an agent writes
    commit messages constantly)."""
    assert not refused(command), command


@pytest.mark.parametrize("command", [
    "git clean -df -e keep.txt", "git push origin -f && git commit -m done",
    'git commit -m "x" && git push origin -f', "git reset -q --hard HEAD~1",
])
def test_a_denied_git_operation_beside_a_message_is_still_found(command):
    assert refused(command), command


# ── a rule that names a wrapper ──────────────────────────────────────────────


WRAPPERS = {"deny_git": [], "deny_commands": ["sudo", "env", "nice", "timeout -k"]}


@pytest.mark.parametrize("command", [
    "sudo ls", "sudo", "timeout 5 sudo ls", "if sudo ls; then :; fi", "env X=1 ls",
    "X=1 env ls", "nice -n 5 ls", "true && nice ls", "timeout -k 5 60 ls",
    "bash -c 'sudo ls'",
])
def test_a_rule_naming_a_wrapper_refuses_the_wrapper_where_it_runs(command):
    """The wrapper was dropped before any rule was read, so a rule naming one
    could never match: an org that denies `sudo` was denying nothing."""
    assert P.denied_command(WRAPPERS, command), command


@pytest.mark.parametrize("command", [
    "echo sudo", "grep -r env src", "make nice", "timeout 60 ls", "git commit -m sudo",
])
def test_a_rule_naming_a_wrapper_does_not_refuse_the_word_elsewhere(command):
    assert P.denied_command(WRAPPERS, command) == [], command


def test_a_wrapper_rule_still_lets_the_wrapped_command_be_judged():
    found = P.denied_command({"deny_commands": ["sudo", "rm -r"]}, "sudo rm -rf x")
    assert len(found) == 2, found


# ── the CLI, as a process ────────────────────────────────────────────────────


@pytest.mark.parametrize("command, code", [
    ("if rm -rf x; then :; fi", 1), ("2>&1 rm -rf x", 1), ("git clean -df", 1),
    ('"\\Program Files\\Git\\usr\\bin\\rm.exe" -rf x', 1), ("git clean -n", 0),
])
def test_the_policy_door_refuses_these_spellings_as_a_process(tmp_path, command, code):
    """A CLI's contract is a process (CLAUDE.md): `policy.py --git` is the door
    every runner asks, and it inherits the fix through `destructive`."""
    done = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "sdlc" / "policy.py"),
         "--target", str(tmp_path), "--git", command],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(ROOT), timeout=120)
    assert done.returncode == code, (command, done.stdout, done.stderr)
    assert ("REFUSED" in done.stdout) is bool(code), done.stdout


# ── what must not change ─────────────────────────────────────────────────────


@pytest.mark.parametrize("command", [
    'echo "rm -rf x"', "echo 'if rm -rf x; then'", "make >build.log 2>&1",
    "cat <in.txt", "if [ -f x ]; then echo ok; fi", "for f in a b; do black $f; done",
    '"\\Program Files\\Git\\cmd\\git.exe" status', '"\\Program Files\\nodejs\\npm.cmd" ci',
    "ls 'my dir/sub'", "grep -F \"a/b\" notes.txt", "echo then rm", "npm test 2>&1 | tee log",
])
def test_ordinary_work_in_these_spellings_is_not_refused(command):
    assert not refused(command), command


def test_a_message_names_the_command_as_typed_never_a_spelling():
    command = 'r""m -rf x && git reset "--hard"'
    found = P.destructive(D, command)
    assert len(found) == 2, found
    assert all(reason.startswith(repr(command) + ":") for reason in found), found


def test_the_spellings_start_with_the_command_as_typed_and_repeat_none():
    found = P.spellings('r""m -rf "\\a b\\x"')
    assert found[0] == 'r""m -rf "\\a b\\x"'
    assert len(found) == len(set(found))
    assert "rm -rf x" in found


#: Named, because pytest puts a test's id in an environment variable and
#: Windows refuses one longer than 32,767 characters.
LONG = {
    "400 quoted paths": " && ".join(
        f'"\\Program Files\\t{i}\\tool.exe" --flag {i}' for i in range(400)),
    "unpaired leading quote": '"' + "a" * 16000,
    "unpaired trailing quote": "a" * 16000 + '"',
    "unpaired middle quote": "a" * 8000 + "'" + "a" * 8000,
    "a quote per word": "a'" * 8000,
    "one long quoted path": '"' + "/a" * 8000 + '"',
    "100 KB of sudo": "sudo " * 20000,
    "100 KB of if": "if " * 33000,
    "100 KB of leading redirects": ">a " * 33000,
    "100 KB of assignments": "A=1 " * 25000,
    "100 KB of timeout -k": "timeout -k 5 60 " * 6000 + "ls",
    "100 KB of env -u": "env -u X " * 11000,
    "100 KB of quoted targets": '>"a b" ' * 14000,
    "100 KB of a>": "a>" * 50000,
    "100 KB of >quote": '>"' * 50000,
}


@pytest.mark.parametrize("command", list(LONG.values()), ids=list(LONG))
def test_a_long_command_is_still_judged_quickly(command):
    """Every spelling costs one more reading, and the readings must stay linear:
    a quote with no partner must not make a scan restart at every character
    (a first draft took 15 seconds over 16,000 characters behind one quote), and
    a run of wrappers, keywords, assignments or leading redirects must not make
    every word copy or shift the words after it (100 KB of `sudo ` took 30
    seconds, and of `env -u X ` 60, once each wrapper was also handed back as a
    command - review F1)."""
    import time

    started = time.perf_counter()
    P.destructive(D, command)
    assert time.perf_counter() - started < 2
