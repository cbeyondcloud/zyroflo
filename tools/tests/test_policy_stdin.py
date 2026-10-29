"""T697 (Z9): a destroying command handed to a shell through its stdin is still refused.

T695 taught `policy.destructive` the spellings a shell removes before it runs a
command. A shell can also be handed its script without the script ever standing
in the command line as a command: a here-string, a process substitution, or the
text another program prints into a pipe. `bash 2>&1 <<< 'rm -rf x'`, `<<< 'rm
-rf x' bash`, `bash < <(echo rm -rf x)` and `echo 'rm -rf x' | sh` each ran `rm`
(MEASURED with a harmless marker in its place, bash 5 on Windows 11) while this
wall allowed them, and so did PowerShell's `del -Recurse x` and `erase -Recurse
x`, two aliases of `Remove-Item` whose siblings were already refused.

The rule the fix keeps is T695's: the readings only ADD refusals, and a script
is read from stdin only where the program receiving it reads its script there -
`cat <<< 'rm -rf x'`, `bash run.sh <<< 'rm -rf x'` and `echo 'rm -rf x' | grep
rm` stay allowed, as the controls at the end say.
"""
from __future__ import annotations

import json

import pytest

from tools.sdlc import policy as P

D = dict(P.DEFAULTS)
#: A drive letter, built from pieces: `test_no_private_coordinates.py` and the
#: history guard read every literal for a machine path, and a Windows system
#: path here is a shape under test, not a coordinate.
DRIVE = "C" + ":"


def refused(command: str, policy: dict | None = None) -> bool:
    return bool(P.destructive(policy or D, command))


# ── PowerShell's Remove-Item, under every alias ──────────────────────────────


@pytest.mark.parametrize("command", [
    "del -Recurse x", "erase -Recurse x", "rd -Recurse x", "rmdir -Recurse x",
    "ri -Recurse x", "rm -Recurse x", "Remove-Item -Recurse x", "del -r x", "erase -r x",
    "DEL -REC x", "Erase -recu x", "del -Recurse:$true x", "erase -Recurse:$true x",
    "del -Force -Recurse x", "del -Recurse -Force x", "erase -Path x -Recurse",
    "del -fo -r x", "powershell -Command \"del -Recurse x\"",
    "pwsh -c 'erase -r -Force x'", "del /s /q x", "del -R x", "del -re x",
    "erase -RECURSE:$true x", "del -r:$true x", "del -Recurse: $true x",
])
def test_every_remove_item_alias_is_refused_a_recursive_delete(command):
    """`del` and `erase` are PowerShell aliases of `Remove-Item`, as `ri`, `rd`,
    `rmdir` and `rm` are; a parameter name may be abbreviated (`-r`, `-rec`) and
    given a value (`-Recurse:$true`)."""
    assert refused(command), command


@pytest.mark.parametrize("command", [
    "del x", "erase notes.txt", "del /q x", "del -WhatIf x", "del -Force x.txt",
    "del -Verbose x.txt", "del -LiteralPath x.txt", "del -Filter *.tmp",
    "del -ErrorAction SilentlyContinue x", "del -Confirm:$false x", "del -Credential c x",
    "del -Stream s x", "del -ErrorVariable e x", "erase -Force x", "del x -Force",
    "del -Path x -Force", "ERASE -FORCE x", "del -fo x",
])
def test_a_delete_that_is_not_recursive_is_not_refused(command):
    """Review F2: `-Recurse` is matched as the PowerShell parameter it is, any
    prefix of its name with or without a value, never as a letter `r` inside
    another parameter's name - `-Force`, `-Verbose`, `-Filter` delete one file."""
    assert not refused(command), command


@pytest.mark.parametrize("command", [
    "Remove-Item -Force x", "ri -Force x", "rd -Force x", "rmdir -Force x", "rm -Force x",
])
def test_the_older_aliases_keep_the_verdict_they_had(command):
    """Their rules read `-r` as a letter in a flag cluster, as on `main`: a
    narrower reading there would allow what `main` refuses (no loosening)."""
    assert refused(command), command


def test_a_powershell_parameter_requirement_matches_any_prefix_of_its_name():
    """A `-name:` requirement is a PowerShell parameter: PowerShell takes any
    unambiguous prefix of its name, with a value after `:` or none, and refuses
    to run an ambiguous one, so every prefix may count and nothing else does."""
    rules = {"deny_commands": ["tool -purge:"]}
    for command in ("tool -p x", "tool -PUR x", "tool -purge x", "tool -purge:$true x",
                    "tool -Purge: $true x"):
        assert P.denied_command(rules, command), command
    for command in ("tool -pa x", "tool -xp x", "tool --purge x", "tool -purged x",
                    "tool purge x", "tool -: x"):
        assert not P.denied_command(rules, command), command


def test_the_shipped_template_and_schema_carry_the_same_rules():
    """The template an adopter installs and the schema a wizard reads say what
    `DEFAULTS` says, so `del`/`erase` are walled for every one of them."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    template = (root / "sdlc_templates" / "policy.yml").read_text(encoding="utf-8")
    rules = P.parse(template)["deny_commands"]
    assert rules == D["deny_commands"]
    schema = json.loads((root / "sdlc_templates" / "schemas" / "policy.schema.json")
                        .read_text(encoding="utf-8"))
    assert schema["properties"]["deny_commands"]["default"] == rules
    assert "del /s|-recurse:" in rules and "erase /s|-recurse:" in rules


# ── a here-string, wherever it is written ────────────────────────────────────


@pytest.mark.parametrize("command", [
    "bash 2>&1 <<< 'rm -rf x'", "bash >log <<< 'rm -rf x'", "bash 2>/dev/null <<< 'rm -rf x'",
    "bash 2> /dev/null <<< 'rm -rf x'", "bash >\"my log\" <<< 'rm -rf x'",
    "sh -e 2>&1 <<<'rm -r build'", "bash < in.txt <<< 'rm -rf x'",
    "bash <<< 'rm -rf x' 2>&1", "sudo bash 2>&1 <<< 'rm -rf x'",
])
def test_a_redirect_before_a_here_string_does_not_hide_the_script(command):
    """T695 read `bash <<< 'rm -rf x'` only when nothing stood between the shell
    and the here-string; a redirect there made the here-string look like a
    script file's stdin."""
    assert refused(command), command


@pytest.mark.parametrize("command", [
    "<<< 'rm -rf x' bash", "<<< 'rm -rf x' 2>&1 bash", "2>&1 <<< 'rm -rf x' bash",
    "<<<'rm -rf x' sh", '<<< "rm -rf x" bash -s', "true && <<< 'rm -rf x' bash",
])
def test_a_here_string_written_before_the_program_is_its_script(command):
    """A redirect may stand anywhere in a simple command, the program's name
    included: `<<< 'rm -rf x' bash` is `bash <<< 'rm -rf x'`."""
    assert refused(command), command


# ── a process substitution ───────────────────────────────────────────────────


@pytest.mark.parametrize("command", [
    "bash < <(echo rm -rf x)", "sh < <(printf 'rm -rf x')", "bash < <(echo 'rm -rf x')",
    "bash <(echo rm -rf x)", "source <(echo rm -rf x)", ". <(echo 'rm -rf x')",
    "bash -e < <(echo rm -rf x)", "bash < <(true; echo rm -rf x)",
])
def test_a_process_substitution_a_shell_reads_is_judged_as_its_script(command):
    """`< <(...)` is the shell's stdin and `<(...)` named as its script file is
    its script: what the producer inside prints is what the shell runs."""
    assert refused(command), command


# ── a pipeline into a shell ──────────────────────────────────────────────────


@pytest.mark.parametrize("command", [
    "echo 'rm -rf x' | sh", "printf 'rm -rf x' | bash", "echo rm -rf x | bash",
    'echo "rm -rf x" | zsh', "echo 'rm -rf x' | dash -s", "echo 'rm -rf x' | ksh -",
    "echo -n 'rm -rf x' | sh", "printf '%s\\n' 'rm -rf x' | bash",
    "printf 'true\\nrm -rf x' | bash", "echo 'rm -rf x' |& bash",
    "echo 'rm -rf x' | sudo bash", "echo 'rm -rf x; ls' | sh",
    "echo 'git clean -df' | sh", "ls && echo 'rm -rf x' | sh",
    "echo 'Remove-Item -Recurse x' | pwsh -Command -",
    "echo 'del -Recurse x' | powershell -NoProfile -", "echo 'ri -r x' | powershell",
    "Write-Output 'Remove-Item -Recurse x' | pwsh -c -",
    "echo 'rd -r x' | powershell -ExecutionPolicy Bypass -Command -",
    "echo 'del -r x' | powershell -File -", "echo rmdir /s /q x | cmd",
    "echo rmdir /s /q x | cmd /d", "echo \"echo 'rm -rf x' | sh\" | sh",
    "echo 'rm -rf x' | \"\\Program Files\\Git\\bin\\bash.exe\"",
    "\"\\Program Files\\Git\\bin\\bash.exe\" 2>&1 <<< 'rm -rf x'",
    f"echo 'del -r x' | \"{DRIVE}\\Program Files\\PowerShell\\7\\pwsh.exe\" -Command -",
    "echo rmdir /s /q x | c^md",
    f"echo rmdir /s /q x | \"\"{DRIVE}\\Windows\\System32\\cmd.exe\"\"",
    f"echo rmdir /s /q x | {DRIVE}\\Windows\\System32\\cmd.exe",
    f"{DRIVE}\\Git\\bin\\bash.exe 2>&1 <<< 'rm -rf x'",
    f"echo 'del -r x' | {DRIVE}\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe -c -",
])
def test_a_destroying_text_piped_into_a_shell_is_refused(command):
    """A shell reading its script from stdin runs what is piped into it. Each
    receiving shape was MEASURED running a marker: a POSIX shell with no `-c` and
    no script file (or with `-s` or `-`), PowerShell with no command or with
    `-Command -`/`-File -`, and cmd without `/c` or `/k`."""
    assert refused(command), command


@pytest.mark.parametrize("command", [
    "echo 'rm -rf x' |\n sh", "echo 'rm -rf x' |\r\n sh", "echo 'rm -rf x' |\n\n sh",
    "echo 'rm -rf x' | # why\n sh", "echo 'rm -rf x' |\n# why\n sh",
    "echo 'rm -rf x' |&\n bash", "echo 'rm -rf x' | sh # why", "echo 'rm -rf x' | sh #",
    "bash 2>&1 <<< 'rm -rf x' # why", "<<< 'rm -rf x' bash # why",
    "bash < <(echo rm -rf x) # why",
])
def test_a_line_break_or_a_comment_does_not_end_the_shells_stdin(command):
    """Review F1: bash reads on past a line break, a blank line or a comment
    after `|`, so the shell on the next line is still the pipe's reader; and a
    comment after the shell is not a script file it reads instead of stdin."""
    assert refused(command), command


# ── a command string handed to an interpreter ────────────────────────────────


@pytest.mark.parametrize("command", [
    "bash -c \"echo 'rm -rf x' | sh\"", "bash -lc \"echo 'rm -rf x' | sh\"",
    "powershell -Command \"echo 'rm -rf x' | bash\"", "cmd /c \"echo rmdir /s x | cmd\"",
    "bash -c 'bash < <(echo rm -rf x)'", "sh -c \"bash 2>&1 <<< 'rm -rf x'\"",
    "pwsh -c \"echo 'del -r x' | pwsh -Command -\"", "sudo sh -c \"<<< 'rm -rf x' bash\"",
    "cmd /c\"echo rmdir /s x | cmd\"",
])
def test_a_shells_stdin_inside_a_command_string_is_read_too(command):
    """Review F3: the command string of `bash -c`, `powershell -Command` or `cmd
    /c` is a command line of its own, and a shell it feeds through stdin runs the
    script as surely as one typed at the prompt (`sh -c 'bash <<< "rm -rf x"'`
    was refused on `main`; the shapes T697 reads were not, one wrapper deeper)."""
    assert refused(command), command


def test_a_message_names_the_command_as_typed():
    command = "echo 'rm -rf x' | sh"
    found = P.destructive(D, command)
    assert found and all(reason.startswith(repr(command) + ":") for reason in found), found


def test_the_door_refuses_a_script_piped_into_a_shell_as_a_process(tmp_path):
    """A CLI's contract is a process (CLAUDE.md)."""
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    done = subprocess.run(
        [sys.executable, str(root / "tools" / "sdlc" / "policy.py"),
         "--target", str(tmp_path), "--git", "bash 2>&1 <<< 'rm -rf x'"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(root), timeout=120)
    assert done.returncode == 1 and "REFUSED" in done.stdout, (done.stdout, done.stderr)


# ── what must stay allowed ───────────────────────────────────────────────────


@pytest.mark.parametrize("command", [
    "cat <<< 'rm -rf x'", "grep -c rm <<< 'rm -rf x'", "bash run.sh <<< 'rm -rf x'",
    "bash 2>&1 run.sh <<< 'rm -rf x'", "<<< 'rm -rf x' bash run.sh",
    "bash -c 'cat' <<< 'rm -rf x'", "cat <(echo rm -rf x)", "diff <(echo rm -rf x) b",
    "bash run.sh < <(echo rm -rf x)", 'echo "{ rm -rf x; }"', "echo 'rm -rf x' | grep rm",
    "echo 'rm -rf x' | bash run.sh", "echo 'rm -rf x' | bash -c 'cat'",
    "echo 'rm -rf x' | pwsh -Command Get-Content", "echo 'rm -rf x' | pwsh -File run.ps1",
    "echo rmdir /s /q x | cmd /c sort", "echo hello | bash", "echo ls | sh",
    "echo if do !", 'git commit -m "explain push and the -f flag"',
    "cat notes.txt | bash", "echo 'rm -rf x' > notes.txt", "echo 'rm -rf x' || sh",
    "echo 'rm -rf x' && sh", "echo 'rm -rf x'; sh", "echo 'rm -rf x' |\n grep rm",
    "echo 'rm -rf x' &&\n sh", "echo 'rm -rf x' | sh run.sh # why",
    "bash -c \"echo 'rm -rf x' | grep rm\"", "bash -c 'cat <<< \"rm -rf x\"'",
    "powershell -Command \"echo 'rm -rf x' | Select-String rm\"",
])
def test_stdin_that_no_shell_runs_is_still_data(command):
    """A here-string to `cat`, a script file's stdin, a process substitution a
    program reads as a file, a pipe into anything but a shell reading its script
    there, and a separator that is not a pipe: none is a script that runs."""
    assert not refused(command), command


#: Named, because pytest puts a test's id in an environment variable and
#: Windows refuses one longer than 32,767 characters.
LONG = {
    "100 KB of redirected here-strings": "bash 2>&1 <<< 'a b'; " * 5000,
    "100 KB of leading here-strings": "<<< 'a b' " * 10000 + "bash",
    "100 KB of process substitutions": "bash < <(echo a); " * 6000,
    "100 KB of nested process substitutions": "bash < <(" * 11000,
    "100 KB of unclosed process substitutions": "<(" * 50000,
    "100 KB of pipes into a shell": "echo a | sh | " * 7000,
    "a 100 KB here-string": "bash <<< '" + "a " * 50000 + "'",
    "a 100 KB piped script": "echo '" + "a " * 50000 + "' | sh",
    "100 KB of nested pipes": "echo \"echo 'a' | sh\" | sh; " * 4000,
    "100 KB of unpaired quotes into a shell": "echo 'a | sh " * 7000,
    "100 KB of escapes": "echo \\' | sh " * 7000,
    "100 KB of pipes that end a line": "echo a |\n" * 11000 + "sh",
    "100 KB of comments after a pipe": "echo a | # c\n" * 8000 + "sh",
    "100 KB of trailing comments": "bash <<< 'a' # c\n" * 6000,
    "100 KB of command strings": "bash -c \"echo a | sh\"; " * 4500,
    "100 KB of nested command strings": "bash -c \"sh -c 'echo a | sh'\"; " * 3200,
    "a 100 KB command string": "bash -c \"" + "echo a | sh; " * 8000 + "\"",
    "100 KB of PowerShell parameters": "del " + "-r:$true -Force " * 6000 + "x",
}


@pytest.mark.parametrize("command", list(LONG.values()), ids=list(LONG))
def test_a_long_stdin_shape_is_still_judged_quickly(command):
    """Each new reading is one pass over the words, and a script read from stdin
    is read again only to a fixed depth, so no shape may grow worse than linear
    (T695's bound, kept)."""
    import time

    started = time.perf_counter()
    P.destructive(D, command)
    assert time.perf_counter() - started < 2


#: Every character `str.isspace` calls a space that the stdin reading's own
#: separators do not name. The parity run found `'x\u2028NEEDLE'`, a test
#: literal, never finishing: the word reader stopped at the space and the command
#: reader, which skipped only a blank or a tab, asked it for a word again, an
#: empty one each time, until the process ran out of memory.
OTHER_SPACES = ["\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x1f", "\x85", "\xa0",
                "\u1680", "\u2000", "\u2028", "\u2029", "\u202f", "\u205f", "\u3000"]


@pytest.mark.parametrize("space", OTHER_SPACES, ids=[f"U+{ord(s):04X}" for s in OTHER_SPACES])
def test_any_unicode_space_ends_a_word_and_the_reading(space):
    """Run in a thread, so a reading that never ends fails this test rather than
    hanging the suite; the command is refused as the rest of the file reads a
    space (`_segment_commands` splits at every `\\s`)."""
    import threading

    verdict: list[bool] = []
    command = f"echo{space}'rm -rf x'{space}|{space}sh"
    worker = threading.Thread(target=lambda: verdict.append(refused(command)), daemon=True)
    worker.start()
    worker.join(timeout=5)
    assert not worker.is_alive(), f"the reading of {command!r} did not end"
    assert verdict == [True]
