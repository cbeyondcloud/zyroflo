"""T446c: the audit trail, counted per step and model, and exported flat.

Every number comes from `events.jsonl`; one that cannot be computed is null
WITH a reason, never 0; `metrics.py --export ndjson|csv` writes one file per
event type with stable columns a dashboard can depend on, deterministically.
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import approve as A  # noqa: E402
from tools.sdlc import drive as D  # noqa: E402
from tools.sdlc import events as E  # noqa: E402
from tools.sdlc import intake as entry  # noqa: E402
from tools.sdlc import metrics as M  # noqa: E402
from tools.sdlc import state as L  # noqa: E402
from tools.tests import _runs as R  # noqa: E402

RUNNER_CMD = '"' + sys.executable + '" -c pass'
METRICS = ROOT / "tools" / "sdlc" / "metrics.py"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / ".sdlc").mkdir(parents=True)
    (r / ".sdlc" / "profile.yml").write_text(
        "stack: python\ngate_profile: solo\n"
        "phases: [intake, requirements, design, implementation, test, review, "
        "triage, closeout]\n"
        "runners:\n  - name: writer\n"
        f"    command: '{RUNNER_CMD}'\n    capabilities: chat\n"
        "phase_runners:\n  intake: writer\n  requirements: writer\n", encoding="utf-8")
    for args in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "i"]):
        subprocess.run(["git", "-C", str(r), *args], check=True, capture_output=True)
    return r


def reporting(usage: list[dict] | None):
    def run(command, prompt, cwd, timeout=3600):
        path = os.environ.get(E.USAGE_ENV)
        if usage and path:
            with open(path, "a", encoding="utf-8") as fh:
                fh.writelines(json.dumps(u) + "\n" for u in usage)
        return 0, "doc\n"
    return run


def _walked(repo: Path) -> str:
    """P0 reports tokens, P1 reports none; then a halt, a decision, a loop."""
    rid = entry.open_run(repo, "title: a change\n\nkind: requirement\n\n"
                               "## What\n\nA small change.\n")["run_id"]
    D.step(repo, rid, {}, run=reporting([{"input_tokens": 100, "output_tokens": 40}]))
    D.step(repo, rid, {}, run=reporting(None))
    R.park(repo, rid, "P3")
    A.record(repo, rid, "G2", "approve", "r@example.invalid")
    R.park(repo, rid, "P4", status="running")
    L.advance(repo, rid, phase="P3")
    return rid


def test_steps_are_counted_and_the_unmeasured_is_null_with_a_reason(repo):
    _walked(repo)
    doc = M.compute(repo)["steps"]
    intake, req = doc["by_step"]["intake"], doc["by_step"]["requirements"]
    assert intake["attempts"]["value"] == 1
    assert intake["tokens"]["input"]["value"] == 100
    assert intake["tokens"]["output"]["value"] == 40
    assert isinstance(intake["duration_ms"]["p50"]["value"], int)
    assert req["tokens"]["input"] == {"value": None,
                                      "why": "no step in these runs reported tokens"}
    assert req["tokens"]["steps_not_reporting"]["value"] == 1
    assert doc["all"]["tokens"]["steps_reporting"]["value"] == 1
    assert doc["loop_backs"]["value"] == {"P4->P3": 1}
    assert isinstance(doc["gate_wait_ms"]["G2"]["p50"]["value"], int)
    assert doc["by_model"]["value"] is None and doc["by_model"]["why"]
    assert any(line.startswith("steps.by_step.requirements.tokens.input:")
               for line in M.compute(repo)["unmeasured"])


def test_no_runs_is_a_reason_not_a_zero(repo):
    doc = M.compute(repo)["steps"]
    assert doc["by_step"]["value"] is None and "no step has run" in doc["by_step"]["why"]
    assert doc["all"]["tokens"]["input"]["value"] is None


#: Pinned BY NAME: a renamed or dropped column breaks somebody's dashboard.
STEP_FINISHED_COLUMNS = [
    "schema_version", "run_id", "seq", "at", "event", "step", "step_name", "prev",
    "hash", "attempt", "started_at", "ended_at", "duration_ms", "actor", "runner",
    "provider", "model", "protocol", "tokens_source", "tokens_reason",
    "tokens_calls", "tokens_input", "tokens_output", "tokens_cached", "tokens_total",
    "cost_source", "cost_reason", "cost_amount", "cost_currency", "exit_code",
    "outcome", "artifacts", "forced"]


def test_the_exported_columns_are_stable():
    assert M.export_columns("step_finished") == STEP_FINISHED_COLUMNS
    assert M.export_columns("gate_decided")[-6:] == [
        "gate", "decision", "approver", "ledger_hash", "waited_ms", "forced"]


def _export(repo: Path, fmt: str, out: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(METRICS), "--target", str(repo),
                           "--export", fmt, "--out", str(out)], capture_output=True,
                          text=True, encoding="utf-8", timeout=300)


def _files(root: Path, fmt: str) -> list[Path]:
    """Every export file under the T454 layout, in a stable order."""
    return sorted(root.rglob(f"*.{fmt}"))


def test_a_csv_export_a_spreadsheet_can_open(repo, tmp_path):
    rid = _walked(repo)
    done = _export(repo, "csv", tmp_path / "out")
    assert done.returncode == 0, done.stdout + done.stderr
    step_files = list((tmp_path / "out" / "repo" / "step_finished").rglob("*.csv"))
    assert [f.name for f in step_files] == [f"{rid}.csv"], step_files
    with step_files[0].open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert list(rows[0]) == STEP_FINISHED_COLUMNS
    assert [r["step"] for r in rows] == ["P0", "P1"] and {r["run_id"] for r in rows} == {rid}
    assert rows[0]["tokens_input"] == "100"
    assert rows[1]["tokens_source"] == "unavailable" and rows[1]["tokens_input"] == "", \
        "unmeasured is an empty cell, never a 0"


def test_an_ndjson_export_round_trips_the_trail(repo, tmp_path):
    rid = _walked(repo)
    assert _export(repo, "ndjson", tmp_path / "out").returncode == 0
    back = [json.loads(l) for f in _files(tmp_path / "out", "ndjson")
            for l in f.read_text(encoding="utf-8").splitlines()]
    assert sorted(back, key=lambda e: e["seq"]) == E.read(repo, rid)


def test_two_exports_are_byte_identical(repo, tmp_path):
    _walked(repo)
    for n in (1, 2):
        assert _export(repo, "csv", tmp_path / f"out{n}").returncode == 0
    one, two = (_files(tmp_path / f"out{n}", "csv") for n in (1, 2))
    assert [f.relative_to(tmp_path / "out1") for f in one] == \
        [f.relative_to(tmp_path / "out2") for f in two]
    assert all(a.read_bytes() == b.read_bytes() for a, b in zip(one, two))


def test_out_without_export_is_refused(repo, tmp_path):
    done = subprocess.run([sys.executable, str(METRICS), "--target", str(repo),
                           "--out", str(tmp_path)], capture_output=True, text=True,
                          encoding="utf-8", timeout=300)
    assert done.returncode != 0 and "--export" in (done.stdout + done.stderr)
