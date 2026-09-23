"""T452: the org roll-up - many repositories' stores folded into one, and the
people in them pseudonymised when the constitution says so.

`metrics.py --merge a.db b.db --into org.db` keeps every row with its `repo`,
idempotently (INSERT OR IGNORE on the same keys); `identity: hash` makes every
export write a stable pseudonym for each approver while the ledger stays exact.
"""
from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import approve as A  # noqa: E402
from tools.sdlc import metrics as M  # noqa: E402
from tools.sdlc import policy as P  # noqa: E402
from tools.sdlc import state as L  # noqa: E402
from tools.tests import _runs as R  # noqa: E402

METRICS = ROOT / "tools" / "sdlc" / "metrics.py"
WHO = "alex@example.invalid"


def _repo(tmp_path: Path, name: str, identity: str = "email") -> Path:
    r = tmp_path / name
    (r / ".sdlc").mkdir(parents=True)
    (r / ".sdlc" / "profile.yml").write_text(
        f'gate_profile: solo\nproject:\n  name: "{name}"\n  owner: "t"\n'
        f"  criticality: low\n", encoding="utf-8")
    (r / ".sdlc" / "policy.yml").write_text(f"identity: {identity}\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(r)], check=True, capture_output=True)
    rid = L.create(r, "requirement", "a change")["run_id"]
    R.park(r, rid, "P3")
    A.record(r, rid, "G2", "approve", WHO)
    return r


def _metrics(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(METRICS), *args], capture_output=True,
                          text=True, encoding="utf-8", timeout=300)


def _store(repo: Path, out: Path) -> Path:
    done = _metrics("--target", str(repo), "--export", "sqlite", "--out", str(out))
    assert done.returncode == 0, done.stdout + done.stderr
    return out / "audit.db"


def test_two_repositories_fold_into_one_store_with_their_repo(tmp_path):
    a = _store(_repo(tmp_path, "billing"), tmp_path / "sa")
    b = _store(_repo(tmp_path, "search"), tmp_path / "sb")
    done = _metrics("--merge", str(a), str(b), "--into", str(tmp_path / "org" / "audit.db"))
    assert done.returncode == 0, done.stdout + done.stderr
    con = sqlite3.connect(tmp_path / "org" / "audit.db")
    assert sorted(con.execute("SELECT repo, project FROM runs")) == [
        ("billing", "billing"), ("search", "search")]
    one = sqlite3.connect(a).execute("SELECT COUNT(*) FROM events").fetchone()[0]
    two = sqlite3.connect(b).execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert con.execute("SELECT COUNT(*) FROM events").fetchone()[0] == one + two
    assert con.execute("SELECT COUNT(DISTINCT repo) FROM gates").fetchone()[0] == 2


def test_re_merging_never_double_counts(tmp_path):
    a = _store(_repo(tmp_path, "billing"), tmp_path / "sa")
    org = tmp_path / "org.db"
    for _ in range(2):
        assert _metrics("--merge", str(a), "--into", str(org)).returncode == 0
    con, src = sqlite3.connect(org), sqlite3.connect(a)
    for table in M.STORE_TABLES:
        assert (con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
                == src.execute(f"SELECT COUNT(*) FROM {table}").fetchone()), table


def test_merge_refuses_a_missing_store_and_a_missing_into(tmp_path):
    done = _metrics("--merge", str(tmp_path / "nope.db"), "--into", str(tmp_path / "o.db"))
    assert done.returncode != 0 and "--export sqlite first" in done.stdout + done.stderr
    done = _metrics("--merge", str(tmp_path / "nope.db"))
    assert done.returncode != 0 and "--into" in done.stdout + done.stderr


def test_identity_hash_pseudonymises_every_export_and_not_the_ledger(tmp_path):
    repo = _repo(tmp_path, "billing", identity="hash")
    token = M.pseudonym(WHO)
    assert token.startswith("person-") and WHO not in token
    db = _store(repo, tmp_path / "s")
    assert sqlite3.connect(db).execute(
        "SELECT approver FROM gates WHERE event='gate_decided'").fetchall() == [(token,)]
    assert _metrics("--target", str(repo), "--export", "csv", "--out",
                    str(tmp_path / "csv")).returncode == 0
    exported = "".join(p.read_text(encoding="utf-8") for p in (tmp_path / "csv").rglob("*.csv"))
    assert WHO not in exported and token in exported
    ledger = next(repo.rglob("approvals.jsonl")).read_text(encoding="utf-8")
    assert WHO in ledger, "the local ledger stays exact - only what leaves is pseudonymised"


def test_identity_email_is_the_default_and_an_unknown_mode_is_refused():
    assert P.DEFAULTS["identity"] == "email"
    assert any("identity" in p for p in P.validate({"identity": "anonymous"}))
    assert P.validate({"identity": "hash"}) == []


def test_the_same_person_is_the_same_pseudonym_everywhere():
    """A roll-up still counts per person without naming anyone."""
    assert M.pseudonym(WHO) == M.pseudonym(WHO) != M.pseudonym("sam@example.invalid")


def test_the_sample_collector_runs_the_real_commands():
    """A template naming a flag that does not exist is a nightly job that fails."""
    text = (ROOT / "sdlc_templates" / "collector" / "nightly-sdlc-metrics.yml").read_text(
        encoding="utf-8")
    yaml.safe_load(text)
    assert "--export sqlite --out" in text and "--merge stores/*/audit.db --into" in text
    assert "sparse-checkout set .sdlc" in text
