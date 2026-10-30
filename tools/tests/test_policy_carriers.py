"""T703 (Z10): text a carrier hands to a shell is still read.

T697 taught `policy.destructive` to read a script a shell takes from its stdin:
a here-string, a process substitution, or what `echo`/`printf` print into a
pipe. Three shapes stayed unread, each run by a real shell and each allowed on
`main` (agentic-sdlc's DF-779, DF-780 and DF-782, which its guard refuses with
spellings of its own that also refuse quoted data, DF-723):

* text that any other program passes on into a shell - `cat <<< 'rm -rf x' |
  sh`, `cat <(echo rm -rf x) | sh` - or into a subshell, a group or a compound
  after the pipe, `tee >(sh)`, `source /dev/stdin`, `at now <<< ...`;
* a brace group in the text an interpreter or a carrier runs - `bash -c "{ rm
  -rf x; }"`, `watch`, `ssh`, `unshare`, `kubectl exec`, `tmux`, `xterm -e`,
  `script -qc`, `runuser -c`, `flock -c`;
* for a caller that reads the words of `spellings` (agentic's GIT_* and PATH
  walls), an assignment after a separator inside a command string: every
  spelling kept the quotes around `'ls; GIT_SSH_COMMAND=x git fetch'`.

The rule is T695's and T697's: the readings only ADD refusals, and quoted data
or a keyword in argument position stays data - the controls at the end.
"""
from __future__ import annotations

import re

import pytest

from tools.sdlc import policy as P

D = dict(P.DEFAULTS)


def refused(command: str) -> bool:
    return bool(P.destructive(D, command))


# ── DF-779: text passed on into a shell ──────────────────────────────────────


@pytest.mark.parametrize("command", [
    "cat <<< 'rm -rf x' | sh", "<<< 'rm -rf x' cat | sh", "cat <(echo rm -rf x) | sh",
    "cat < <(echo rm -rf x) | sh", "head <<< 'rm -rf x' | sudo sh",
    "tr a a <<< 'rm -rf x' | bash -s", "cat <<< 'rm -rf x' |& sh",
    "cat <<< 'rm -rf x' | /bin/sh", "cat <<< 'rm -rf x' | env sh",
    "cat <<< 'rm -rf x' | 2>&1 sh", "cat <<< 'rmdir /s /q x' | cmd",
    "cat <<< 'Remove-Item -Recurse x' | powershell -",
    "echo 'rm -rf x' | cat | sh", "echo 'rm -rf x' | grep rm | sh",
    "cat <<< 'rm -rf x' | sh -c 'sh'", "echo 'rm -rf x' | bash -c 'cat | sh'",
])
def test_text_a_program_passes_on_into_a_shell_is_refused(command):
    """Any program but a printer may pass its stdin, or a file it is handed as a
    process substitution, on to its output; a printer prints only its words. A
    command string inherits the stdin of the shell it is handed to."""
    assert refused(command), command


@pytest.mark.parametrize("command", [
    "cat <<< 'rm -rf x' |\n sh", "cat <<< 'rm -rf x' |\r\n sh",
    "cat <<< 'rm -rf x' | # why\n sh", "cat <<< 'rm -rf x' | (sh)",
    "cat <<< 'rm -rf x' | { sh; }", "cat <<< 'rm -rf x' | (cd /tmp; sh)",
    "cat <<< 'rm -rf x' | { cd /tmp; sh; }", "cat <<< 'rm -rf x' | if true; then sh; fi",
    "cat <<< 'rm -rf x' | while read l; do sh; done", "echo 'rm -rf x' | (sh)",
    "echo 'rm -rf x' | { cd /tmp && sh; }", "cat <<< 'rm -rf x' | ( (sh) )",
    "{ echo 'rm -rf x'; } | sh", "{ cat <<< 'rm -rf x'; } | sh",
    "(cat <<< 'rm -rf x') | sh", "if true; then echo 'rm -rf x'; fi | sh",
])
def test_a_pipe_into_a_subshell_a_group_or_a_compound_reaches_its_shell(command):
    """Every command inside a subshell, a group or a compound that a pipe feeds
    reads that pipe, until the compound closes; a line break or a comment after
    the `|` does not end it (T697 review F1)."""
    assert refused(command), command


@pytest.mark.parametrize("command", [
    "cat <<< 'rm -rf x' | tee >(sh)", "echo 'rm -rf x' | tee >(sh) >/dev/null",
    "echo 'rm -rf x' > >(sh)", "cat <<< 'rm -rf x' | source /dev/stdin",
    "cat <<< 'rm -rf x' | . /dev/stdin", "bash /dev/stdin <<< 'rm -rf x'",
    "echo 'rm -rf x' | bash /dev/fd/0", "bash <(cat <<< 'rm -rf x')",
    "source <(cat <<< 'rm -rf x')", "at now <<< 'rm -rf x'",
    "echo 'rm -rf x' | at now", "echo 'rm -rf x' | batch",
    "cat <<< 'rm -rf x' | csh", "echo 'rm -rf x' | tcsh", "echo 'rm -rf x' | fish",
    "echo 'rm -rf x' | ash", "echo 'rm -rf x' | mksh", "csh -c 'rm -rf x'",
    "fish -c 'rm -rf x'",
])
def test_an_output_substitution_a_stdin_file_and_at_read_the_script(command):
    """`>(sh)` reads what its command writes; `/dev/stdin` named as the script
    file is the shell's stdin; `at` and `batch` run what they read on stdin; and
    csh, tcsh, fish, ash and mksh are shells (T699, in part)."""
    assert refused(command), command


# ── DF-780: a brace group in the text an interpreter or carrier runs ─────────


@pytest.mark.parametrize("command", [
    'bash -c "{ rm -rf x; }"', "sh -c '{ rm -rf x; }' # why", 'watch "{ rm -rf x; }"',
    "watch -n 1 '{ rm -rf x; }'", 'watch "{ rm -rf x; }" # why',
    "bash -c \"cat <<< 'rm -rf x' | sh\"", "2>&1 bash -c '{ rm -rf x; }'",
    "2>&1 sh -c 'r''m -r x'", "unshare sh -c '{ rm -rf x; }'",
    "unshare -m sh -c 'rm -rf x'", "ssh host '{ rm -rf x; }'", "ssh -p 22 host 'rm -rf x'",
    "kubectl exec p -- sh -c '{ rm -rf x; }'", "kubectl exec -it p -- rm -rf x",
    "tmux new-session '{ rm -rf x; }'", "tmux new -d 'rm -rf x'",
    "xterm -e '{ rm -rf x; }'", "xterm -e sh -c 'rm -rf x'", "script -qc '{ rm -rf x; }'",
    "script -c 'rm -rf x' log", "runuser -u bob -c '{ rm -rf x; }'",
    "runuser -u bob -- sh -c '{ rm -rf x; }'", "flock /tmp/l -c '{ rm -rf x; }'",
    "flock /tmp/l sh -c '{ rm -rf x; }'", "su -c '{ rm -rf x; }'",
    "su root -c 'rm -rf x'", "eval '{ rm -rf x; }'",
    "docker run img sh -c '{ rm -rf x; }'", "docker exec c sh -c '{ rm -rf x; }'",
    "find . -exec sh -c '{ rm -rf x; }' \\;", "xargs sh -c '{ rm -rf x; }'",
    "sudo bash -c \"ssh h '{ rm -rf x; }'\"", "{ rm -rf x; }",
])
def test_a_group_in_the_text_a_carrier_runs_is_refused(command):
    """The text an interpreter or carrier runs is a command line of its own,
    read with one level of quotes removed, where an unquoted `{` opens a group."""
    assert refused(command), command


# ── DF-782: an assignment inside a command string is a word of a spelling ────

#: The assignments agentic's walls refuse: git's own variables, which can make
#: git run a command, and the variables that choose which program runs.
ASSIGNMENT = re.compile(r"(?:GIT_[A-Z0-9_]*|PATH|PATHEXT|COMSPEC|BASH_ENV|LD_\w*|DYLD_\w*)\+?=",
                        re.IGNORECASE)


def assignments(command: str) -> list[str]:
    """The words of every spelling, read with quotes respected, that assign one
    of `ASSIGNMENT` - what a caller with walls of its own reads."""
    return [item[1] for spelling in P.spellings(command)
            for _, items in P._simple_commands(spelling)
            for item in items if item[0] == "word" and ASSIGNMENT.match(item[1])]


@pytest.mark.parametrize("command", [
    "bash -c 'ls; GIT_SSH_COMMAND=\"touch MARK #\" git ls-remote ssh://h/x'",
    "sh -c 'ls | GIT_EXTERNAL_DIFF=x git diff'", "sh -c 'export GIT_SSH_COMMAND=x; git fetch'",
    "env sh -c 'ls; GIT_DIR=x git status'", "ssh h 'ls; GIT_DIR=x git status'",
    'cmd /c "set GIT_SSH_COMMAND=x && git fetch"', "watch 'ls; env PATH=/tmp ls'",
    'cmd /c "ls & set PATH=x & ls"', "bash -c \"ls; LD_PRELOAD=x.so ls\"",
    "sh -c 'BASH_ENV=x bash run.sh'", "powershell -Command \"ls; $x=1; GIT_DIR=x git status\"",
])
def test_an_assignment_after_a_separator_in_a_command_string_is_a_word(command):
    """The text a shell or carrier runs is itself a spelling, so a caller's word
    reader sees `GIT_SSH_COMMAND=...` as the word it is to that shell."""
    assert assignments(command), P.spellings(command)


def test_the_command_as_typed_is_still_the_first_spelling():
    command = "bash -c 'ls; GIT_DIR=x git status'"
    spelled = P.spellings(command)
    assert spelled[0] == command and "ls; GIT_DIR=x git status" in spelled


def test_a_message_names_the_command_as_typed():
    command = 'bash -c "{ rm -rf x; }"'
    found = P.destructive(D, command)
    assert found and all(reason.startswith(repr(command) + ":") for reason in found), found


def test_the_door_refuses_a_carried_group_as_a_process(tmp_path):
    """A CLI's contract is a process (CLAUDE.md)."""
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    done = subprocess.run(
        [sys.executable, str(root / "tools" / "sdlc" / "policy.py"),
         "--target", str(tmp_path), "--git", "cat <<< 'rm -rf x' | (sh)"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(root), timeout=120)
    assert done.returncode == 1 and "REFUSED" in done.stdout, (done.stdout, done.stderr)


# ── what must stay allowed ───────────────────────────────────────────────────


@pytest.mark.parametrize("command", [
    # DF-723: quoted data and keywords in argument position (agentic over-refuses)
    "cat <<< 'rm -rf x'", "grep -c rm <<< 'rm -rf x'", "bash run.sh <<< 'rm -rf x'",
    "cat <(echo rm -rf x)", 'echo "{ rm -rf x; }"', "echo if do !",
    'git commit -m "if then do"', "grep -e '{ rm -rf x; }' notes.txt",
    # text that reaches no shell, or a shell that does not read it
    "cat <<< 'rm -rf x' | grep rm", "cat <<< 'rm -rf x' | sh run.sh",
    "cat <<< 'rm -rf x' | bash -c 'cat'", "cat <<< 'rm -rf x' | tee out.txt",
    "cat <<< 'rm -rf x' || sh", "cat <<< 'rm -rf x' && sh", "cat <<< 'rm -rf x'; sh",
    "cat <<< 'rm -rf x' | (cd /tmp); sh", "cat <<< 'rm -rf x' | { cat; }; sh",
    "cat <<< 'rm -rf x' | if true; then cat; fi; sh", "echo hi <<< 'rm -rf x' | sh",
    "cat notes.txt | sh", "tee >(cat) <<< 'rm -rf x'", "cat <<< 'rm -rf x' | sh -c 'cat'",
    "at now -f job.sh", "echo ls | at now",
    # command strings holding quoted data
    "bash -c 'echo \"{ rm -rf x; }\"'", "ssh host 'echo \"{ rm -rf x; }\"'",
    "watch 'git log --grep \"rm -rf x\"'", "kubectl exec p -- echo '{ rm -rf x; }'",
    "docker run img echo '{ rm -rf x; }'", "tmux display-message '#{pane_id}'",
    "bash -c 'git commit -m \"GIT_DIR=x is a variable\"'",
    "xterm -e 'echo \"{ rm -rf x; }\"'", "su -c 'echo \"{ rm -rf x; }\"'",
    "bash -c 'cat' <<< 'rm -rf x'", "echo sh -c '{ rm -rf x; }'",
    # quoted text where a program would stand is a program name, never a group
    '"{ rm -rf x; }"', 'env "{ rm -rf x; }"', "! '{ rm -rf x; }'",
])
def test_quoted_data_stays_data(command):
    """A here-string to `cat`, a pipe into anything but a shell reading its
    script there, a separator that is not a pipe, a compound that closed before
    the shell, and a command string holding quoted text: none runs `rm`."""
    assert not refused(command), command


#: Named, because pytest puts a test's id in an environment variable and
#: Windows refuses one longer than 32,767 characters.
LONG = {
    "100 KB of a pipe through programs": "cat <<< 'a' | " * 7000 + "sh",
    "100 KB of output substitutions": "cat <<< 'a' | tee >(sh) | " * 4000 + "sh",
    "100 KB of subshells after a pipe": "echo a | " + "( " * 50000 + "sh",
    "100 KB of groups after a pipe": "echo a | " + "{ " * 50000 + "sh",
    "100 KB of compounds after a pipe": "echo a | " + "if true; then " * 7000 + "sh",
    "100 KB of closed groups": "echo a | { cat; }; " * 5000 + "sh",
    "100 KB of groups in command strings": "bash -c \"{ rm x; }\"; " * 5000,
    "100 KB of nested command strings": "bash -c \"ssh h 'watch \\\"{ ls; }\\\"'\"; " * 2500,
    "100 KB of carriers": "unshare sh -c 'ls'; " * 5000,
    "a 100 KB carried string": "ssh h '" + "ls; " * 25000 + "'",
    "100 KB of carried assignments": "sh -c 'ls; GIT_DIR=x git status'; " * 3000,
    "100 KB of stdin files": "echo a | source /dev/stdin; " * 3500,
    "100 KB of here-strings to at": "at now <<< 'a'; " * 6000,
}


@pytest.mark.parametrize("command", list(LONG.values()), ids=list(LONG))
def test_a_long_carried_shape_is_still_judged_quickly(command):
    """Text a carrier runs is read again only to a fixed depth, and the data a
    pipe carries is linked, never copied per command, so no shape grows worse
    than linear (T695's bound, kept). The bound is T697's two seconds: `main`
    itself takes over one on a loaded Windows laptop for most of T697's shapes."""
    import time

    started = time.perf_counter()
    P.destructive(D, command)
    P.spellings(command)
    assert time.perf_counter() - started < 2
