"""T414: a batch-file shim on PATH does not intercept a subprocess launched
without a shell on Windows, so a test that fakes an external binary with a
`.bat`/`.cmd` shim can pass while exercising the REAL tool - a vacuous test of the
rule "an external binary returning nothing at exit 0 is not evidence of absence".

This is the RUNNABLE proof of CLAUDE.md's Windows note: the shim is NOT intercepted,
and the working alternative (a real executable image) IS. Both platforms, no skip
(rule 1.16): "a real image is intercepted, a non-image is not" holds on each - a
`.bat` / a non-executable file is bypassed by a bare-name no-shell spawn, a real
`.exe` / a `chmod +x` script is run - and the two branches assert that shape rather
than skipping either. Windows is the platform the note is about, so the proof there
is the reproduction; the POSIX branch keeps the control honest on the gate of record.
"""
import os
import shutil
import subprocess

import pytest


def test_a_batch_shim_is_not_intercepted_without_a_shell_but_a_real_image_is(
        tmp_path, monkeypatch):
    shimdir = tmp_path / "shim"
    shimdir.mkdir()
    # CreateProcess reads the CURRENT process's real environment for its own
    # executable search, NOT subprocess's env= block (measured 2026-09-19; see
    # test_metrics.py's fake_git for the same caveat), so the shim dir goes on
    # os.environ itself. monkeypatch restores it at teardown.
    monkeypatch.setenv("PATH", str(shimdir) + os.pathsep + os.environ["PATH"])

    if os.name == "nt":
        # A well-formed batch shim, FIRST on PATH, named like the tool, is invisible
        # to a bare-name no-shell spawn: CreateProcess appends only .exe and never
        # consults PATHEXT. The fake "faked nothing".
        (shimdir / "faketool.bat").write_text("@echo BAT-ANSWERED\r\n", encoding="ascii")
        with pytest.raises(FileNotFoundError):
            subprocess.run(["faketool"], capture_output=True, text=True)

        # THE WORKING ALTERNATIVE: a real executable image under the same bare name
        # IS intercepted. A copy of a dependency-free system .exe stands in for the
        # compiled console application CLAUDE.md points at (no compiler needed here).
        real_pe = shutil.which("hostname")
        assert real_pe, "premise: hostname.exe is on PATH on Windows"
        shutil.copyfile(real_pe, shimdir / "faketool.exe")
        got = subprocess.run(["faketool"], capture_output=True, text=True)
        real = subprocess.run(["hostname"], capture_output=True, text=True)
        assert got.returncode == 0
        assert got.stdout.strip() and got.stdout.strip() == real.stdout.strip(), (
            "a real executable image, not the shim, is what a bare-name no-shell "
            "spawn reaches - this is why a fake must be one")
    else:
        # POSIX analogue of the same shape (no cmd.exe, no PATHEXT): a non-executable
        # file on PATH is bypassed, a real executable file under the same name is run.
        shim = shimdir / "faketool"
        shim.write_text("echo SHOULD-NOT-RUN\n", encoding="ascii")  # no execute bit
        with pytest.raises((PermissionError, FileNotFoundError)):
            subprocess.run(["faketool"], capture_output=True, text=True)

        shim.write_text("#!/bin/sh\necho REAL-IMAGE\n", encoding="ascii")
        shim.chmod(0o755)
        got = subprocess.run(["faketool"], capture_output=True, text=True)
        assert got.returncode == 0 and got.stdout.strip() == "REAL-IMAGE", (
            "a real executable file, not the inert one, is what a bare-name "
            "no-shell spawn reaches")
