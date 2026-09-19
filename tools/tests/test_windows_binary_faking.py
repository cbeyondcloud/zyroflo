"""T414: a batch-file shim on PATH does not intercept a subprocess launched
without a shell on Windows, so a test that fakes an external binary with a
`.bat`/`.cmd` shim can pass while exercising the REAL tool - a vacuous test of the
rule "an external binary returning nothing at exit 0 is not evidence of absence".

This is the RUNNABLE proof of CLAUDE.md's Windows note, and it DISTINGUISHES a
bypassed shim from an ABSENT one - a negative expectation alone cannot, and would
pass on a machine where the shim was simply never created. So the shim is first proven
to EXIST and to be reachable in the mode where it SHOULD run (a shell on Windows, the
execute bit on POSIX); only then is it shown to be bypassed by a bare-name no-shell
spawn. The working alternative - a real executable image - is then shown to be
intercepted. Both platforms, no skip (rule 1.16): the shape holds on each, and the two
branches assert it rather than skipping either.
"""
import os
import shutil
import subprocess

import pytest


def test_a_batch_shim_is_not_intercepted_without_a_shell_but_a_real_image_is(
        tmp_path, monkeypatch):
    shimdir = tmp_path / "shim"
    shimdir.mkdir()
    # The shim dir goes on the CURRENT process's real PATH. On Windows subprocess's
    # env= does NOT steer the SAME call's executable search (CreateProcess resolves the
    # program against the spawning process's own environment); env= only reaches a
    # grandchild that then spawns by bare name - the route test_metrics.py's fake_git
    # fixture uses. monkeypatch restores PATH at teardown.
    monkeypatch.setenv("PATH", str(shimdir) + os.pathsep + os.environ["PATH"])

    if os.name == "nt":
        shim = shimdir / "faketool.bat"
        shim.write_text("@echo BAT-ANSWERED\r\n", encoding="ascii")
        assert shim.is_file()
        # PROVE THE SHIM EXISTS AND IS REACHABLE: a shell DOES honour it. Absence would
        # fail HERE, so the bypass below cannot be mistaken for an absent file.
        via_shell = subprocess.run("faketool", shell=True, capture_output=True, text=True)
        assert via_shell.returncode == 0 and "BAT-ANSWERED" in via_shell.stdout, via_shell
        # ...yet a bare-name NO-shell spawn never reaches it: CreateProcess appends only
        # .exe to a bare name and never consults PATHEXT.
        with pytest.raises(FileNotFoundError):
            subprocess.run(["faketool"], capture_output=True, text=True)
        # THE WORKING ALTERNATIVE: a real executable image under the same bare name IS
        # intercepted. A copy of ComSpec (cmd.exe) is a real image whose OUTPUT we
        # control via /c echo and whose only dependencies are always-present system
        # DLLs, so it runs on any host - not a common tool whose behaviour we do not own.
        shutil.copyfile(os.environ["ComSpec"], shimdir / "faketool.exe")
        got = subprocess.run(["faketool", "/c", "echo", "REAL-IMAGE-W490"],
                             capture_output=True, text=True)
        assert got.returncode == 0 and "REAL-IMAGE-W490" in got.stdout, got
    else:
        shim = shimdir / "faketool"
        shim.write_text("echo SHOULD-NOT-RUN\n", encoding="ascii")  # no execute bit
        assert shim.is_file()
        # A present-but-non-executable file raises EACCES (PermissionError), which is
        # DISTINCT from an absent file (FileNotFoundError). Accepting ONLY PermissionError
        # is what tells a bypass from an absence on the gate of record.
        with pytest.raises(PermissionError):
            subprocess.run(["faketool"], capture_output=True, text=True)
        # THE WORKING ALTERNATIVE: made executable, the real file IS reached and run.
        shim.write_text("#!/bin/sh\necho REAL-IMAGE-W490\n", encoding="ascii")
        shim.chmod(0o755)
        got = subprocess.run(["faketool"], capture_output=True, text=True)
        assert got.returncode == 0 and "REAL-IMAGE-W490" in got.stdout, got
