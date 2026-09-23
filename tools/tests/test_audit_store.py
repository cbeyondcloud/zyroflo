"""T447a: the queryable store - `audit.db`, its contract, and the Grafana pack.

`metrics.py --export sqlite` turns every run's events into tables Grafana's
SQLite source reads directly. Held here: the store answers the shipped panels'
own SQL with stdlib `sqlite3`; re-exporting never double-counts; unmeasured is
NULL, never 0; the column dictionary names every column (renaming one fails by
name); the store is gitignored while the record is not; and the pre-INIT
metadata rides every run as its roll-up key.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sqlite3
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
DICTIONARY = ROOT / "docs" / "metrics-columns.md"
GRAFANA = ROOT / "sdlc_templates" / "grafana" / "dashboard.json"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / ".sdlc").mkdir(parents=True)
    (r / ".sdlc" / "profile.yml").write_text(
        "stack: python\ngate_profile: solo\n"
        "project:\n  name: \"billing\"\n  owner: \"payments-team\"\n  criticality: high\n"
        "phases: [intake, requirements, design, implementation, test, review, "
        "triage, closeout]\n"
        "runners:\n  - name: writer\n"
        f"    command: '{RUNNER_CMD}'\n    capabilities: chat\n"
        "phase_runners:\n  intake: writer\n  requirements: writer\n", encoding="utf-8")
    for args in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "i"]):
        subprocess.run(["git", "-C", str(r), *args], check=True, capture_output=True)
    return r


def reporting(usage):
    def run(command, prompt, cwd, timeout=3600):
        path = os.environ.get(E.USAGE_ENV)
        if usage and path:
            with open(path, "a", encoding="utf-8") as fh:
                fh.writelines(json.dumps(u) + "\n" for u in usage)
        return 0, "doc\n"
    return run


def _walked(repo: Path) -> str:
    rid = entry.open_run(repo, "title: a change\n\nkind: requirement\n\n"
                               "## What\n\nA small change.\n")["run_id"]
    D.step(repo, rid, {}, run=reporting([{"input_tokens": 100, "output_tokens": 40}]))
    D.step(repo, rid, {}, run=reporting(None))
    R.park(repo, rid, "P3")
    A.record(repo, rid, "G2", "approve", "r@example.invalid")
    R.park(repo, rid, "P4", status="running")
    L.advance(repo, rid, phase="P3")
    return rid


def _export(repo: Path, out: Path) -> Path:
    done = subprocess.run([sys.executable, str(METRICS), "--target", str(repo),
                           "--export", "sqlite", "--out", str(out)], capture_output=True,
                          text=True, encoding="utf-8", timeout=300)
    assert done.returncode == 0, done.stdout + done.stderr
    return out / "audit.db"


def test_the_opening_event_carries_the_project(repo):
    rid = _walked(repo)
    opened = E.read(repo, rid)[0]
    assert (opened["project"], opened["owner"], opened["criticality"]) == (
        "billing", "payments-team", "high")


def test_the_store_holds_the_trail_and_unmeasured_is_null(repo, tmp_path):
    rid = _walked(repo)
    con = sqlite3.connect(_export(repo, tmp_path / "out"))
    assert con.execute("SELECT project, owner, criticality FROM runs").fetchall() == [
        ("billing", "payments-team", "high")]
    steps = con.execute("SELECT step, tokens_source, tokens_input FROM steps ORDER BY seq").fetchall()
    assert steps == [("P0", "reported", 100), ("P1", "unavailable", None)], \
        "an unreported count is NULL, never 0"
    assert con.execute("SELECT COUNT(*) FROM events").fetchone()[0] == len(E.read(repo, rid))
    assert con.execute("SELECT from_step, to_step FROM loop_backs").fetchall() == [("P4", "P3")]
    (wait,) = con.execute("SELECT waited_ms FROM gates WHERE event='gate_decided'").fetchone()
    assert isinstance(wait, int)
    assert con.execute("SELECT value FROM meta WHERE key='store_version'").fetchone() == ("1",)


def test_re_exporting_never_double_counts(repo, tmp_path):
    _walked(repo)
    db = _export(repo, tmp_path / "out")
    before = {t: sqlite3.connect(db).execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in M.STORE_TABLES}
    _export(repo, tmp_path / "out")
    after = {t: sqlite3.connect(db).execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
             for t in M.STORE_TABLES}
    assert before == after and before["events"] > 0


def test_every_shipped_grafana_panel_runs_against_the_store(repo, tmp_path):
    """The pack is a contract too: each panel's SQL executes, with stdlib sqlite3,
    against a store this harness wrote - a panel naming a column that does not
    exist would render an error in somebody's Grafana."""
    _walked(repo)
    con = sqlite3.connect(_export(repo, tmp_path / "out"))
    panels = json.loads(GRAFANA.read_text(encoding="utf-8"))["panels"]
    assert len(panels) >= 6
    for panel in panels:
        for target in panel["targets"]:
            assert target["rawQueryText"] == target["queryText"], panel["title"]
            con.execute(target["rawQueryText"]).fetchall()
    tokens = dict((m, i) for _, m, _, _, i, _, _ in con.execute(
        [t for p in panels if "model" in p["title"] for t in p["targets"]][0]["rawQueryText"]))
    assert tokens == {}, "no model-routed step here, so the model panel is empty, not zero"


def test_the_column_dictionary_names_every_column():
    """Renaming or dropping a column fails HERE, by name, before a dashboard breaks."""
    text = DICTIONARY.read_text(encoding="utf-8")
    sections = re.split(r"^## ", text, flags=re.M)
    for table, cols in M.STORE_TABLES.items():
        section = next((s for s in sections if s.startswith(f"`{table}`")), None)
        assert section is not None, f"no section for table {table}"
        documented = re.findall(r"^\| `([a-z_]+)` \|", section, flags=re.M)
        assert documented == cols, f"{table}: dictionary {documented} != store {cols}"
    assert f"store version {M.STORE_VERSION}" in text


def test_the_store_is_gitignored_and_the_record_is_not():
    spec = importlib.util.spec_from_file_location("sdlc_init_store", ROOT / "sdlc-init.py")
    si = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(si)
    ignore = si.sdlc_gitignore(False)
    assert "metrics/audit.db" in ignore.splitlines()
    assert not any(line.startswith("runs") for line in ignore.splitlines()), \
        "the run folders - events.jsonl with them - are the committed record"
