"""T688 (Z6) — `approve.append_entry`: one hash-chained entry for ANY gate id.

A caller that runs its own pipeline (agentic-sdlc, which reuses this harness as a
pinned library) has gates that are not G1-G5 and artifacts that are not in a
P-phase's `state.json` map. `record` could not take them; `append_entry` can, and
`record` is now built on the same writer - so the entries `record` writes must
stay byte-for-byte what they were, which the golden test below pins.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import approve as A  # noqa: E402
from tools.sdlc import events as E  # noqa: E402
from tools.sdlc import state as L  # noqa: E402
from tools.tests import _runs as R  # noqa: E402

WHO = "lead@example.invalid"
AT = "2026-10-20T09:00:00+00:00"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / ".sdlc").mkdir(parents=True)
    (r / ".sdlc" / "profile.yml").write_text(
        "stack: python\ngate_profile: solo\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(r), "init", "-q"], check=True,
                   capture_output=True)
    return r


def _handoff(repo: Path) -> tuple[str, str]:
    """A run with one file of the caller's own, and its digest."""
    rid = L.create(repo, "requirement", "a pipeline of its own")["run_id"]
    path = L.run_dir(repo, rid) / "handoffs" / "design-1.md"
    path.parent.mkdir(parents=True)
    path.write_text("the design", encoding="utf-8")
    return rid, L.sha256_of(path)


# ── the generic writer ───────────────────────────────────────────────────────


def test_any_gate_id_is_recorded_and_gate_state_reads_it(repo):
    rid, digest = _handoff(repo)
    entry = A.append_entry(repo, rid, "design-approval", "approve", WHO,
                           {"handoffs/design-1.md": digest}, step="design")
    assert entry["gate"] == "design-approval" and entry["prev"] == ""
    assert entry["artifacts"] == {"handoffs/design-1.md": digest}
    assert A.verify_chain(repo, rid) == []
    assert A.gate_state(repo, rid, "design-approval") == (True, "")


def test_a_changed_file_voids_the_approval(repo):
    rid, digest = _handoff(repo)
    A.append_entry(repo, rid, "design-approval", "approve", WHO,
                   {"handoffs/design-1.md": digest})
    (L.run_dir(repo, rid) / "handoffs" / "design-1.md").write_text("changed",
                                                                   encoding="utf-8")
    ok, why = A.gate_state(repo, rid, "design-approval")
    assert not ok and "design-1.md" in why


def test_a_reject_over_the_same_bytes_wins(repo):
    rid, digest = _handoff(repo)
    pins = {"handoffs/design-1.md": digest}
    A.append_entry(repo, rid, "design-approval", "reject", "other@example.invalid", pins)
    A.append_entry(repo, rid, "design-approval", "approve", WHO, pins)
    assert A.gate_state(repo, rid, "design-approval")[0] is False


def test_entries_chain_and_extra_fields_are_hashed(repo):
    rid, digest = _handoff(repo)
    first = A.append_entry(repo, rid, "g1", "approve", WHO, {"handoffs/design-1.md": digest})
    second = A.append_entry(repo, rid, "g2", "approve", WHO,
                            {"handoffs/design-1.md": digest},
                            extra={"regime": "team", "four_eyes": True})
    assert second["prev"] == first["hash"] and second["regime"] == "team"
    assert second["hash"] == A.entry_hash(second)


def test_gate_decided_is_emitted_with_the_step_and_the_wait(repo):
    rid, digest = _handoff(repo)
    E.append(repo, rid, E.GATE_WAITING, "design", gate="design-approval", hard=True)
    A.append_entry(repo, rid, "design-approval", "approve", WHO,
                   {"handoffs/design-1.md": digest}, step="design")
    decided = E.last(repo, rid, E.GATE_DECIDED)
    assert decided["event"] == E.GATE_DECIDED and decided["step"] == "design"
    assert decided["gate"] == "design-approval" and decided["decision"] == "approve"
    assert decided["waited_ms"] is not None and decided["forced"] is False


@pytest.mark.parametrize(("args", "extra", "expect"), [
    (("", "approve", WHO), None, "names its gate"),
    (("g", "maybe", WHO), None, "unknown verdict"),
    (("g", "approve", ""), None, "attributed to nobody"),
    (("g", "approve", WHO), {"hash": "x"}, "own fields"),
    (("g", "approve", WHO), {"gate": "other"}, "own fields"),
])
def test_bad_input_is_refused_and_nothing_is_written(repo, args, extra, expect):
    rid, digest = _handoff(repo)
    with pytest.raises((ValueError, RuntimeError), match=expect):
        A.append_entry(repo, rid, *args, {"handoffs/design-1.md": digest}, extra=extra)
    assert not A.approvals_path(repo, rid).exists()


@pytest.mark.parametrize("rel", ["../escape.md", "/abs/path.md", "C:abs.md", ""])
def test_an_artifact_outside_the_run_folder_is_refused(repo, rel):
    rid, digest = _handoff(repo)
    with pytest.raises(ValueError):
        A.append_entry(repo, rid, "g", "approve", WHO, {rel: digest})
    assert not A.approvals_path(repo, rid).exists()


def test_a_run_that_does_not_exist_is_refused(repo):
    with pytest.raises(RuntimeError, match="no run folder"):
        A.append_entry(repo, "r-nobody", "g", "approve", WHO, {})


# ── record is unchanged ──────────────────────────────────────────────────────

GOLDEN = [
    {"approver": "dev@example.invalid", "artifacts": {
        "40-impl/": "sha256:0ce82933297fad87dd89715db23095ef3a30b49d27a21afe7460c74a8b4e4f91",
        "design.md": "sha256:aab8f06fecd1c041e9a3d5a86114f9e522b546918db8503a045fe4a423b3642a"},
     "at": AT, "forced": False, "gate": "G3", "gate_profile": "solo", "gate_regime": "solo",
     "hard_gates": ["G2", "G4"],
     "hash": "sha256:3241feec7723f9d3db5441ff37439db281319b62a0fa07f6cd0c3a0e99ad64e1",
     "kind": "approval", "notes": "golden", "owed": [], "phase": "P4",
     "policy_source": "defaults", "prev": "", "resolved_gates": {"G2": "P3", "G4": "P7"},
     "run_id": "r-z6-golden", "schema_version": 1, "signing": "none", "verdict": "reject"},
    {"approver": "dev@example.invalid", "artifacts": {
        "40-impl/": "sha256:0ce82933297fad87dd89715db23095ef3a30b49d27a21afe7460c74a8b4e4f91",
        "design.md": "sha256:aab8f06fecd1c041e9a3d5a86114f9e522b546918db8503a045fe4a423b3642a"},
     "at": AT, "forced": False, "gate": "G3", "gate_profile": "solo", "gate_regime": "solo",
     "hard_gates": ["G2", "G4"],
     "hash": "sha256:caf508e475431eff1254727142803425d50387375ddc927f566365eebfa47dd7",
     "kind": "approval", "notes": "golden", "owed": [], "phase": "P4",
     "policy_source": "defaults",
     "prev": "sha256:3241feec7723f9d3db5441ff37439db281319b62a0fa07f6cd0c3a0e99ad64e1",
     "resolved_gates": {"G2": "P3", "G4": "P7"}, "run_id": "r-z6-golden",
     "schema_version": 1, "signing": "none", "verdict": "approve"},
]


def _on_this_platform(written: list[dict]) -> list[dict]:
    """GOLDEN as this platform writes it. `_runs.park` builds `40-impl/` with bytes
    that differ between Windows (where GOLDEN was captured) and Linux, so that one
    digest is taken from what was written and the chain is re-linked with the
    UNCHANGED `entry_hash`. Every other field, and on Windows both hashes, are the
    literal values captured before the refactor."""
    impl = written[0]["artifacts"]["40-impl/"]
    expected, prev = [], ""
    for golden in GOLDEN:
        entry = {**golden, "artifacts": {**golden["artifacts"], "40-impl/": impl}, "prev": prev}
        if impl != GOLDEN[0]["artifacts"]["40-impl/"]:
            entry["hash"] = A.entry_hash(entry)
        expected.append(entry)
        prev = entry["hash"]
    return expected


def test_record_writes_exactly_what_it_wrote_before(repo, monkeypatch):
    """Captured from `record` on main (e436475) before the refactor, time and run id
    fixed: a reject then an approve. Every field and both hashes must match (see
    `_on_this_platform` for the one digest that depends on the platform)."""
    monkeypatch.setattr(A, "_now", lambda: AT)
    st = L.create(repo, "requirement", "a change worth gating", run_id="r-z6-golden",
                  now=datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc))
    (L.run_dir(repo, st["run_id"]) / "design.md").write_text("the plan", encoding="utf-8")
    L.record_artifact(repo, st["run_id"], "design.md")
    R.park(repo, st["run_id"], "P4")
    written = [A.record(repo, st["run_id"], "G3", verdict, "dev@example.invalid",
                        notes="golden", author="author@example.invalid")
               for verdict in ("reject", "approve")]
    expected = _on_this_platform(written)
    assert written == expected
    lines = A.approvals_path(repo, st["run_id"]).read_text(encoding="utf-8").splitlines()
    assert [json.loads(line) for line in lines] == expected
