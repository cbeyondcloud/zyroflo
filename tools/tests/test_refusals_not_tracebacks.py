"""Batch 3's CLI contracts: an input a command cannot use is refused, never crashed on.

Each is spawned, because a CLI's contract is a process: an in-process call cannot tell a
refusal from a traceback. Every row here answered with a traceback or wrote something it
should not have, at an exit code that meant something else:

| row  | command                              | the input                      | now          |
|------|--------------------------------------|--------------------------------|--------------|
| T484 | `stage.py --report-usage`            | a mistyped `--run`             | exit 2       |
| T488 | `approve.py --verify`                | an unsafe run id               | exit 2       |
| T489 | `state.py --advance --status`        | a `state.json` with no phase   | exit 2       |
| T490 | `status.py --json`                   | a pipeline that cannot resolve | said, in-band|
| T492 | `stage.py --diagram`, `--emit-check` | an output that cannot be written | 2 and 3    |

A directory stands in for an unwritable output file: read-only bits do not stop root,
which CI's containers run as, and a directory refuses a write on every platform.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import state as L  # noqa: E402


def _spawn(script: str, *args: str) -> tuple[int, str, str]:
    proc = subprocess.run([sys.executable, str(ROOT / "tools" / "sdlc" / script), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=300)
    return proc.returncode, proc.stdout, proc.stderr


@pytest.fixture
def target(tmp_path: Path) -> Path:
    t = tmp_path / "repo"
    (t / ".sdlc").mkdir(parents=True)
    (t / ".sdlc" / "profile.yml").write_text("stack: python\ngate_profile: solo\n",
                                             encoding="utf-8")
    subprocess.run(["git", "-C", str(t), "init", "-q"], check=True,
                   capture_output=True, timeout=120)
    return t


def test_report_usage_refuses_a_run_that_does_not_exist_and_writes_nothing(target):
    """T484. The phase was validated and the run was not, and `run_dir` sends a
    missing run to the archive path - so a typo created a phantom archived run
    with one event and no state, and every merge check failed on it after."""
    code, out, err = _spawn("stage.py", "--target", str(target), "--run", "no-such-run",
                            "--phase", "P1", "--report-usage", "input=10,output=5")
    assert code == 2 and "Traceback" not in out + err, out + err
    assert "no run no-such-run" in out, out
    assert not (L.archive_dir(target) / "no-such-run").exists(), "a phantom run was made"


def test_report_usage_still_records_on_an_archived_run(target):
    """Rule-28 control for T484: a run that IS archived still takes the usage."""
    rid = L.create(target, "requirement", "a change worth making")["run_id"]
    L.advance(target, rid, status="closed")
    L.archive(target, rid)
    code, out, err = _spawn("stage.py", "--target", str(target), "--run", rid,
                            "--phase", "P0", "--report-usage", "input=10,output=5")
    assert code == 0, out + err
    events = (L.archive_dir(target) / rid / "events.jsonl").read_text(encoding="utf-8")
    assert '"self-reported"' in events


def test_approve_verify_refuses_an_unsafe_run_id(target):
    """T488: refused by `_safe_run_id` with a ValueError, which `--verify` caught
    only as RuntimeError - a traceback at exit 1, this CLI's "has problems"."""
    code, out, err = _spawn("approve.py", "--target", str(target), "--run",
                            "../../outside", "--verify")
    assert code == 2 and "Traceback" not in out + err, out + err
    assert "unsafe run id" in out, out


def test_a_status_only_advance_refuses_a_record_with_no_phase(target):
    """T489: `advance` read `state["phase"]` bare, so a record missing the key
    answered a status-only move with a KeyError traceback."""
    rid = L.create(target, "requirement", "a change worth making")["run_id"]
    path = L.run_dir(target, rid) / L.STATE_NAME
    record = json.loads(path.read_text(encoding="utf-8"))
    del record["phase"]
    path.write_text(json.dumps(record), encoding="utf-8")
    code, out, err = _spawn("state.py", "--target", str(target), "--advance", rid,
                            "--status", "halted")
    assert code == 2 and "Traceback" not in out + err, out + err
    assert "records no phase" in out and "--phase" in out, out
    # The refusal names its own way out, and that way works.
    code, out, err = _spawn("state.py", "--target", str(target), "--advance", rid,
                            "--phase", "P1", "--status", "halted")
    assert code == 0, out + err


def test_status_json_says_the_pipeline_could_not_be_resolved(target):
    """T490: text mode said why gates read "a gate"; `--json` dropped it. stdout
    stays one JSON document, and the reason rides each row and stderr."""
    L.create(target, "requirement", "a change worth making")
    (target / ".sdlc" / "profile.yml").write_text(
        "stack: python\ngate_profile: solo\nextra_gates: [nonsense]\n", encoding="utf-8")
    code, out, err = _spawn("status.py", "--target", str(target), "--json")
    rows = json.loads(out)
    assert rows and all("nonsense" in r.get("gates_unresolved", "") for r in rows), rows
    assert "could not be resolved" in err, err


def test_status_json_is_unchanged_when_the_pipeline_resolves(target):
    """Rule-28 control for T490: the key exists only when there is a reason."""
    L.create(target, "requirement", "a change worth making")
    code, out, err = _spawn("status.py", "--target", str(target), "--json")
    assert code == 0 and not any("gates_unresolved" in r for r in json.loads(out)), out


def test_the_diagram_refuses_an_output_it_cannot_write(target):
    """T492: a PermissionError traceback at exit 1, which this command's `--check`
    uses for "stale" - a crash wearing the answer to another question."""
    (target / ".sdlc" / "pipeline.md").mkdir()
    code, out, err = _spawn("stage.py", "--target", str(target), "--diagram", "mermaid")
    assert code == 2 and "Traceback" not in out + err, out + err
    assert "could not write .sdlc/pipeline.md" in out, out


def test_emit_check_answers_cannot_emit_for_an_output_it_cannot_write(tmp_path):
    """T492: exit 3 is `--emit-check`'s reserved "cannot emit", and an OSError
    escaped it as a traceback at 1 - which this CLI means as a failed ledger."""
    out_path = tmp_path / "ledger_check.py"
    out_path.mkdir()
    code, out, err = _spawn("validate.py", "--emit-check", str(out_path))
    assert code == 3 and "Traceback" not in out + err, out + err
    assert "cannot emit the ledger check" in out, out
