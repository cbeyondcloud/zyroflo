"""T463: `state.py` run AS A SCRIPT opens a run and records it.

`state.create` lazily imports its siblings (`events`, `stage`, `policy`) as
`tools.sdlc.*`. Run as `python tools/sdlc/state.py`, the harness root was never
on `sys.path`, so `--new` made the run folder and then died on
`ModuleNotFoundError: No module named 'tools'` - a half-opened run with no
`run_opened`. A CLI's contract is a process, so this spawns it - with `-S`, since
the dev venv's editable install puts the root on `sys.path` and hid the crash
from every test run inside it (a target repo's plain python has no such help).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_state_new_as_a_script_opens_a_recorded_run(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".sdlc").mkdir(parents=True)
    done = subprocess.run([sys.executable, "-S", str(ROOT / "tools" / "sdlc" / "state.py"),
                           "--target", str(repo), "--new", "--title", "a change"],
                          capture_output=True, text=True, encoding="utf-8", timeout=300)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "Traceback" not in done.stderr, done.stderr
    (log,) = (repo / ".sdlc" / "runs").glob("*/events.jsonl")
    kinds = [json.loads(line)["event"] for line in log.read_text(encoding="utf-8").splitlines()]
    assert kinds[:2] == ["run_opened", "step_started"], kinds
