"""T475 - an appended record starts its own line, whatever the file ends with.

Every JSONL writer appended with `open("a")` and assumed the file already ended in
a newline. An editor or merge tool that strips the final newline made the next
write share its line - `}{`, not readable JSON - and in a hash-chained ledger
that refused every later operation on the run until the file was fixed by hand
(found by the T468 review, re-run by the lead). In a plain log it was two records
a reader could no longer see.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import approve as A  # noqa: E402
from tools.sdlc import events as E  # noqa: E402
from tools.sdlc import state as L  # noqa: E402
from tools.tests import _runs as R  # noqa: E402

WHO = "reviewer@example.invalid"


def _strip_final_newline(path: Path) -> None:
    path.write_bytes(path.read_bytes().rstrip(b"\r\n"))


def test_both_ledgers_survive_a_stripped_final_newline(tmp_path):
    """Held as the process CI runs: each ledger loses its final newline, gets its
    next record, and the check still reads every line."""
    repo = tmp_path / "repo"
    (repo / ".sdlc").mkdir(parents=True)
    (repo / ".sdlc" / "profile.yml").write_text("gate_profile: solo\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True, capture_output=True)
    run = L.create(repo, "requirement", "a change")["run_id"]
    _strip_final_newline(E.events_path(repo, run))
    R.park(repo, run, "P3")                                   # appends events
    A.record(repo, run, "G2", "reject", WHO, notes="not yet")
    _strip_final_newline(A.approvals_path(repo, run))
    A.record(repo, run, "G2", "approve", WHO)                 # appends an approval
    for path in (E.events_path(repo, run), A.approvals_path(repo, run)):
        assert "}{" not in path.read_text(encoding="utf-8"), path.name
    done = subprocess.run([sys.executable, str(ROOT / "tools" / "sdlc" / "validate.py"),
                           "--ledger", "--target", str(repo)],
                          capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stdout + done.stderr


def test_the_helper_adds_a_line_break_only_where_one_is_missing(tmp_path):
    stripped, ordinary, absent = (tmp_path / n for n in ("a.jsonl", "b.jsonl", "c.jsonl"))
    stripped.write_text('{"n": 1}', encoding="utf-8")
    ordinary.write_text('{"n": 1}\n', encoding="utf-8")
    for path in (stripped, ordinary, absent):
        L.append_record(path, '{"n": 2}\n')
    assert stripped.read_text(encoding="utf-8") == '{"n": 1}\n{"n": 2}\n'
    assert ordinary.read_text(encoding="utf-8") == '{"n": 1}\n{"n": 2}\n'
    assert absent.read_text(encoding="utf-8") == '{"n": 2}\n'


@pytest.mark.parametrize("module", ["tools/sdlc/events.py", "tools/sdlc/approve.py",
                                    "tools/sdlc/adr.py", "tools/sdlc/feedback.py",
                                    "tools/sdlc/guard.py", "tools/sdlc/learning.py",
                                    "tools/review/hitl.py"])
def test_every_record_log_appends_through_the_one_helper(module):
    """The same assumption in every JSONL log the harness keeps; `http_runner`
    alone keeps its own write, because a runner imports nothing of the harness."""
    source = (ROOT / module).read_text(encoding="utf-8")
    assert '.open("a"' not in source and "append_record(" in source, module
