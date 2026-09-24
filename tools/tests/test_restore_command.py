"""T415: the restore command the harness prints runs exactly as printed.

It was `tools/sdlc/state.py --target <repo> --restore ...` - no interpreter, no
harness root, and a placeholder where the target goes - so a person who pasted
it at the moment they were stuck met "not recognised" instead of their bytes.
The command is taken OUT of the harness's own output and run from somewhere
else entirely, because an assembled command would test this test.
"""
from __future__ import annotations

import re
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import intake as entry  # noqa: E402
from tools.sdlc import state as ledger  # noqa: E402
from tools.tests import _runs as R  # noqa: E402


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / ".sdlc").mkdir(parents=True)
    (repo / ".sdlc" / "profile.yml").write_text("stack: python\ngate_profile: solo\n",
                                               encoding="utf-8")
    for args in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "i"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    return repo


def test_the_printed_restore_runs_as_printed_and_puts_the_bytes_back(tmp_path):
    repo = _repo(tmp_path)
    rid = entry.open_run(repo, "title: a change\n\nkind: requirement\n\n"
                               "## What\n\nA small change.\n")["run_id"]
    R.deliver(repo, rid, "P0")
    rel = "00-intake.md"
    path = ledger.run_dir(repo, rid) / rel
    kept = path.read_bytes()
    path.unlink()

    verified = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "sdlc" / "stage.py"), "--target",
         str(repo), "--run", rid, "--phase", "P0", "--verify"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    seen = verified.stdout + verified.stderr
    printed = re.search(r"put its kept bytes back \((.+? --digest \S+?)\)", seen)
    assert printed, seen

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    ran = subprocess.run(shlex.split(printed.group(1)), cwd=elsewhere,
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=600)
    assert ran.returncode == 0, printed.group(1) + "\n" + ran.stdout + ran.stderr
    assert path.read_bytes() == kept
