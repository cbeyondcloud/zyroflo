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
from tools.sdlc import stage  # noqa: E402
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


# ── T474: an awkward name stays one argument, and never becomes a command ────


def _adr_remedy(tmp_path: Path, name: str) -> tuple[Path, Path, bytes, str]:
    """A P3 ADR recorded under `name`, deleted, and the drift sentence `stale`
    composes for it - the one `drive` prints as its STALE stop. P3 owes
    `30-adr-*.md`, a glob, so ANY name that matches it reaches the printed
    line, which is how an artifact name gets there at all."""
    repo = _repo(tmp_path)
    rid = entry.open_run(repo, "title: a change\n\nkind: requirement\n\n"
                               "## What\n\nA small change.\n")["run_id"]
    R.park(repo, rid, "P3")
    path = ledger.run_dir(repo, rid) / name
    path.write_text("# ADR\n\nthe decision\n", encoding="utf-8")
    ledger.record_artifact(repo, rid, name)
    kept = path.read_bytes()
    path.unlink()
    moved = stage.stale(repo, rid, "P3")
    line = next((ln for ln in moved if name in ln), "")
    assert line, f"the drift of {name!r} was not reported at all: {moved}"
    return repo, path, kept, line


def test_a_name_a_shell_would_split_is_pasted_as_one_argument(tmp_path):
    """`--artifact {rel}` was printed bare: bash split `a b;echo INJECTED` at the
    space and ran `echo INJECTED` as a second command. Quoted, the printed line
    restores the bytes - run as printed, from somewhere else."""
    name = "30-adr-a b;echo INJECTED.md"
    _repo_, path, kept, line = _adr_remedy(tmp_path, name)
    printed = re.search(r"put its kept bytes back \((.+? --digest \S+?)\)", line)
    assert printed, line
    lexer = shlex.shlex(printed.group(1), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    tokens = list(lexer)
    assert name in tokens and ";" not in tokens, f"a shell reads this as: {tokens}"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    ran = subprocess.run(shlex.split(printed.group(1)), cwd=elsewhere,
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=600)
    assert ran.returncode == 0, printed.group(1) + "\n" + ran.stdout + ran.stderr
    assert path.read_bytes() == kept


def test_a_name_no_one_spelling_can_quote_is_not_printed_as_a_line(tmp_path):
    """`$` expands inside bash's and PowerShell's double quotes, and single quotes
    are no quotes at all to cmd - so no line pastes the same way in all three.
    The remedy says how instead of handing over a line that runs something."""
    name = "30-adr-$(echo INJECTED).md"
    _repo_, _path, _kept, line = _adr_remedy(tmp_path, name)
    assert "put its kept bytes back (" not in line or "--artifact" not in line, line
    assert "by hand" in line, line


def test_the_record_remedy_spells_the_name_the_same_way():
    """`stale` sends a CHANGED artifact to `record_command`, which printed its
    name bare as well - split, or run, once the placeholders were filled in."""
    assert stage.record_command("30-design.md").endswith("--artifact 30-design.md")
    assert stage.record_command("30-adr-a b;x.md").endswith('--artifact "30-adr-a b;x.md"')
    assert "$" not in stage.record_command("30-adr-$(x).md")
