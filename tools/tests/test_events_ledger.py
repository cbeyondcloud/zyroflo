"""T446a: every run keeps an append-only, hash-chained audit trail.

Written by the paths that already act - `state.create`, `state.advance`,
`approve.record` - so it is the account of what happened, not a second one;
tamper-evident by the approval ledger's own chain; checked by `status --verify`,
`validate.integrity` and CI's base comparison; and it carries no free text.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import approve as A  # noqa: E402
from tools.sdlc import events as E  # noqa: E402
from tools.sdlc import state as L  # noqa: E402
from tools.sdlc import validate as V  # noqa: E402
from tools.tests import _runs as R  # noqa: E402

WHO = "reviewer@example.invalid"
SECRET_NOTE = "the note says sk-live-not-a-real-credential and a customer name"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / ".sdlc").mkdir(parents=True)
    (r / ".sdlc" / "profile.yml").write_text("gate_profile: solo\n", encoding="utf-8")
    _git(r.parent, "init", "-q", "-b", "main", str(r))
    return r


def _types(repo: Path, rid: str) -> list[str]:
    return [e["event"] for e in E.read(repo, rid)]


def test_opening_a_run_opens_its_trail(repo):
    rid = L.create(repo, "requirement", "a change")["run_id"]
    (first,) = E.read(repo, rid)
    assert (first["event"], first["seq"], first["step"], first["kind"]) == (
        "run_opened", 1, "P0", "requirement")
    assert first["prev"] == "" and E.verify(repo, rid) == []


def test_a_halt_a_decision_and_a_loop_are_all_recorded_and_chained(repo):
    rid = L.create(repo, "requirement", "a change")["run_id"]
    R.park(repo, rid, "P1", status="running")
    R.park(repo, rid, "P3")                              # halts at G2 (hard, solo)
    entry = A.record(repo, rid, "G2", "approve", WHO, notes=SECRET_NOTE)
    for phase in ("P4", "P5"):
        R.park(repo, rid, phase, status="running")
    L.advance(repo, rid, phase="P4", note=SECRET_NOTE)   # P5 -> P4: rework
    L.advance(repo, rid, status="closed")
    assert _types(repo, rid) == ["run_opened", "gate_waiting", "gate_decided",
                                 "loop_back", "run_closed"]
    waiting, decided, loop, closed = E.read(repo, rid)[1:]
    assert (waiting["gate"], waiting["hard"], waiting["step"]) == ("G2", True, "P3")
    assert decided["ledger_hash"] == entry["hash"], "points at the decision, never copies it"
    assert decided["decision"] == "approve" and decided["approver"] == WHO
    assert isinstance(decided["waited_ms"], int) and decided["waited_ms"] >= 0
    assert (loop["from"], loop["to"]) == ("P5", "P4")
    assert closed["status"] == "closed"
    assert E.verify(repo, rid) == []


def test_a_decision_with_no_halt_before_it_has_no_wait(repo):
    """An approval taken while the run was not waiting measured no human wait:
    None, never 0."""
    rid = L.create(repo, "requirement", "a change")["run_id"]
    R.park(repo, rid, "P3", status="running")
    A.record(repo, rid, "G2", "approve", WHO, force=True)
    assert E.last(repo, rid, E.GATE_DECIDED)["waited_ms"] is None


def test_no_free_text_reaches_the_trail(repo):
    rid = L.create(repo, "requirement", "a change")["run_id"]
    R.park(repo, rid, "P3")
    A.record(repo, rid, "G2", "approve", WHO, notes=SECRET_NOTE)
    L.advance(repo, rid, phase="P1", note=SECRET_NOTE)
    text = E.events_path(repo, rid).read_text(encoding="utf-8")
    assert "sk-live" not in text and "customer" not in text


def test_an_edited_event_is_named_by_every_door(repo):
    """The chain breaks where the edit is, and `status --verify` - spawned, as
    an operator runs it - exits 1 saying so."""
    rid = L.create(repo, "requirement", "a change")["run_id"]
    R.park(repo, rid, "P3")
    path = E.events_path(repo, rid)
    rows = path.read_text(encoding="utf-8").splitlines()
    first = json.loads(rows[0])
    first["kind"] = "defect"
    rows[0] = json.dumps(first, sort_keys=True)
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    problems = E.verify(repo, rid)
    assert any("event 1 (run_opened)" in p and "edited" in p for p in problems), problems
    assert any("events.jsonl" in p for p in V.integrity(repo))
    done = subprocess.run([sys.executable, str(ROOT / "tools" / "sdlc" / "status.py"),
                           "--target", str(repo), "--verify"], capture_output=True,
                          text=True, encoding="utf-8", timeout=300)
    assert done.returncode == 1 and "events.jsonl" in done.stdout, done.stdout


def test_ci_sees_a_committed_trail_rewritten_or_truncated(repo):
    rid = L.create(repo, "requirement", "a change")["run_id"]
    R.park(repo, rid, "P3")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=d@e", "-c", "user.name=d", "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD").strip()
    path = E.events_path(repo, rid)
    original = path.read_text(encoding="utf-8")
    path.write_text(original.splitlines()[0] + "\n", encoding="utf-8")
    assert any("audit trail was truncated" in p for p in V.append_only(repo, base, []))
    lines = original.splitlines()
    path.write_text("\n".join([lines[0].replace('"requirement"', '"defect"'), *lines[1:]])
                    + "\n", encoding="utf-8")
    assert any("audit trail was rewritten" in p for p in V.append_only(repo, base, []))
    path.write_text(original, encoding="utf-8")
    E.append(repo, rid, E.LOOP_BACK, "P1", **{"from": "P3", "to": "P1"})
    assert not [p for p in V.append_only(repo, base, []) if "events.jsonl" in p], \
        "appending is what the trail is for"


def test_the_writer_refuses_what_the_schema_does_not_declare(repo):
    rid = L.create(repo, "requirement", "a change")["run_id"]
    with pytest.raises(E.EventError, match="unknown event"):
        E.append(repo, rid, "step_skipped")
    with pytest.raises(E.EventError, match="does not carry note"):
        E.append(repo, rid, E.RUN_CLOSED, "P0", status="closed", note="free text")


def test_every_event_written_validates_against_the_committed_schema(repo):
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((ROOT / "sdlc_templates" / "schemas" / "events.schema.json")
                        .read_text(encoding="utf-8"))
    rid = L.create(repo, "requirement", "a change")["run_id"]
    R.park(repo, rid, "P3")
    A.record(repo, rid, "G2", "reject", WHO)
    L.advance(repo, rid, phase="P1")
    L.advance(repo, rid, status="rejected")
    rows = E.read(repo, rid)
    assert {r["event"] for r in rows} >= {"run_opened", "gate_waiting", "gate_decided",
                                          "loop_back", "run_closed"}
    for row in rows:
        jsonschema.validate(row, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**rows[0], "note": "x"}, schema)
