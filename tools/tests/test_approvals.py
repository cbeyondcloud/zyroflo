"""Item 32 — the canonical approval ledger, and what is waiting for a human.

The tests are mostly about refusing and about detecting: an approval that
names nobody, a yes about work that does not exist yet, a line edited after
the fact, an artifact edited after its yes. Each is a way a gate could look
passed without being passed, which is the only failure mode that matters in a
file whose whole job is to be the record a merge is defended with.
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
from tools.sdlc import state as L  # noqa: E402
from tools.sdlc import status as S  # noqa: E402
from tools.tests import _runs as R  # noqa: E402

WHO = "dev@example.invalid"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / ".sdlc").mkdir(parents=True)
    (r / ".sdlc" / "profile.yml").write_text(
        "stack: python\ngate_profile: solo\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(r), "init", "-q"], check=True,
                   capture_output=True)
    return r


def _run_at(repo: Path, phase: str = "P4") -> str:
    """A run parked at a gate, with one recorded artifact to pin — and having
    delivered what the phase owes, because item 36 made the gate stand on it."""
    st = L.create(repo, "requirement", "a change worth gating")
    (L.run_dir(repo, st["run_id"]) / "design.md").write_text("the plan",
                                                             encoding="utf-8")
    L.record_artifact(repo, st["run_id"], "design.md")
    R.park(repo, st["run_id"], phase)
    return st["run_id"]


# ── the entry, and the chain ─────────────────────────────────────────────────


def test_the_hash_covers_everything_except_itself():
    entry = {"gate": "G3", "verdict": "approve", "prev": ""}
    first = A.entry_hash(entry)
    assert A.entry_hash({**entry, "hash": "anything at all"}) == first
    assert A.entry_hash({**entry, "notes": "changed"}) != first


def test_the_first_entry_is_a_genesis_and_pins_the_artifacts(repo):
    rid = _run_at(repo)
    entry = A.record(repo, rid, "G3", "approve", WHO, notes="looks right")
    assert entry["prev"] == "" and entry["hash"].startswith("sha256:")
    assert entry["artifacts"] == L.load(repo, rid)["artifacts"]
    assert entry["gate_profile"] == "solo" and entry["signing"] == "none"
    assert A.verify_chain(repo, rid) == []


def test_each_entry_links_to_the_one_before(repo):
    rid = _run_at(repo)
    first = A.record(repo, rid, "G3", "approve", WHO)
    R.park(repo, rid, "P7")
    second = A.record(repo, rid, "G4", "approve", WHO)
    assert second["prev"] == first["hash"]
    assert A.verify_chain(repo, rid) == []


def test_an_edited_entry_stops_matching_its_own_hash(repo):
    rid = _run_at(repo)
    A.record(repo, rid, "G3", "approve", WHO)
    path = A.approvals_path(repo, rid)
    row = json.loads(path.read_text(encoding="utf-8").strip())
    row["notes"] = "silently widened"
    path.write_text(json.dumps(row, sort_keys=True) + "\n", encoding="utf-8",
                    newline="\n")
    assert "does not match its contents" in " ".join(A.verify_chain(repo, rid))


def test_re_hashing_an_edited_entry_breaks_the_next_ones_link(repo):
    """The knowledgeable tamper: fix up the hash you just invalidated."""
    rid = _run_at(repo)
    A.record(repo, rid, "G3", "approve", WHO)
    R.park(repo, rid, "P7")
    A.record(repo, rid, "G4", "approve", WHO)
    path = A.approvals_path(repo, rid)
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    rows[0]["notes"] = "widened, and re-hashed to cover it"
    rows[0]["hash"] = A.entry_hash(rows[0])
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows),
                    encoding="utf-8", newline="\n")
    problems = " ".join(A.verify_chain(repo, rid))
    assert "entry 2" in problems and "edited, inserted or removed" in problems


def test_an_unreadable_line_stops_the_walk_and_says_so(repo):
    rid = _run_at(repo)
    A.record(repo, rid, "G3", "approve", WHO)
    with A.approvals_path(repo, rid).open("a", encoding="utf-8", newline="\n") as fh:
        fh.write("{not json\n")
    assert "not readable JSON" in " ".join(A.verify_chain(repo, rid))
    with pytest.raises(RuntimeError, match="repair it"):
        A.record(repo, rid, "G4", "approve", WHO, force=True)


def test_the_ledger_is_written_lf(repo):
    rid = _run_at(repo)
    A.record(repo, rid, "G3", "approve", WHO)
    assert b"\r\n" not in A.approvals_path(repo, rid).read_bytes()


# ── an approval is of specific bytes ─────────────────────────────────────────


@pytest.mark.parametrize("wreck, expect", [
    (lambda p: p.write_text("edited after the yes", encoding="utf-8"), "changed since"),
    (lambda p: p.unlink(), "now missing"),
])
def test_an_artifact_touched_after_approval_voids_the_gate(repo, wreck, expect):
    rid = _run_at(repo)
    A.record(repo, rid, "G3", "approve", WHO)
    assert A.gate_state(repo, rid, "G3") == (True, "")
    wreck(L.run_dir(repo, rid) / "design.md")
    ok, why = A.gate_state(repo, rid, "G3")
    assert not ok and expect in why


def test_gate_state_answers_the_runners_question(repo):
    rid = _run_at(repo)
    ok, why = A.gate_state(repo, rid, "G3")
    assert not ok and "no entry" in why
    A.record(repo, rid, "G3", "reject", WHO, notes="not yet")
    ok, why = A.gate_state(repo, rid, "G3")
    assert not ok and "rejected by" in why


# ── refusing ─────────────────────────────────────────────────────────────────


def test_an_approval_attributed_to_nobody_is_refused(repo):
    rid = _run_at(repo)
    with pytest.raises(RuntimeError, match="attributed to nobody"):
        A.record(repo, rid, "G3", "approve", "")


def test_unknown_gates_and_verdicts_are_refused(repo):
    rid = _run_at(repo)
    with pytest.raises(ValueError, match="unknown gate"):
        A.record(repo, rid, "G9", "approve", WHO)
    with pytest.raises(ValueError, match="unknown verdict"):
        A.record(repo, rid, "G3", "maybe", WHO)


def test_a_yes_about_work_that_does_not_exist_is_refused(repo):
    """G4 stands at P7; a run at P4 has not produced what G4 approves."""
    rid = _run_at(repo, phase="P4")
    with pytest.raises(RuntimeError, match="does not exist yet"):
        A.record(repo, rid, "G4", "approve", WHO)
    entry = A.record(repo, rid, "G4", "approve", WHO, force=True)
    assert entry["forced"] is True, "the override is recorded, not just allowed"


def test_an_abandoned_run_takes_no_further_approvals(repo):
    """`rejected` is the operator ABANDONING a run, and that is terminal.

    It used to be what a rejected GATE did too, until item 42 found that made
    the plan's own concurrent-verdict rule unimplementable - a halt "until a
    fresh approval is recorded" cannot happen on a run nothing may be recorded
    against. The two decisions are now distinct; this one still ends the run.
    """
    rid = _run_at(repo)
    L.advance(repo, rid, status="rejected", note="not going to happen")
    with pytest.raises(RuntimeError, match="terminal run takes no further"):
        A.record(repo, rid, "G3", "approve", WHO)


def test_a_rejected_gate_halts_the_run_rather_than_ending_it(repo):
    """Rejection at a gate is the ORDINARY case - the SOPs say open findings
    loop back to design. A run that a single `no` killed for good could not do
    that, and could never record the fresh approval the rule requires."""
    rid = _run_at(repo)
    A.record(repo, rid, "G3", "reject", WHO, notes="not yet")
    state = L.load(repo, rid)
    assert state["status"] == "halted" and state["status"] not in L.TERMINAL
    assert "halts until the work changes" in state["history"][-1]["note"]


def test_only_a_ledger_entry_unblocks_the_run(repo):
    rid = _run_at(repo)
    assert L.load(repo, rid)["status"] == "awaiting_approval"
    A.record(repo, rid, "G3", "approve", WHO)
    st = L.load(repo, rid)
    assert st["status"] == "running"
    assert "G3 approved by" in st["history"][-1]["note"]


# ── signing ──────────────────────────────────────────────────────────────────


def test_dsse_is_refused_with_every_blocker_named(repo, monkeypatch):
    """Never a silent downgrade to unsigned: an entry that looks attributed
    and is not is worse than no entry."""
    (repo / ".sdlc" / "profile.yml").write_text(
        "gate_profile: regulated\nsigning: dsse\n", encoding="utf-8")
    monkeypatch.delenv(A.SIGNING_KEY_ENV, raising=False)
    rid = _run_at(repo)
    with pytest.raises(RuntimeError) as exc:
        A.record(repo, rid, "G3", "approve", WHO)
    said = str(exc.value)
    assert A.SIGNING_KEY_ENV in said and "not implemented yet" in said
    assert A.approvals_path(repo, rid).exists() is False, "nothing was written"


def test_an_unknown_signing_mode_is_refused(repo):
    (repo / ".sdlc" / "profile.yml").write_text("signing: homemade\n",
                                                encoding="utf-8")
    rid = _run_at(repo)
    with pytest.raises(RuntimeError, match="unknown signing mode"):
        A.record(repo, rid, "G3", "approve", WHO)


def test_a_profile_without_a_gate_profile_records_unset(tmp_path):
    r = tmp_path / "bare"
    (r / ".sdlc").mkdir(parents=True)
    rid = _run_at(r)
    entry = A.record(r, rid, "G3", "approve", WHO)
    assert entry["gate_profile"] == "unset" and entry["signing"] == "none"


def test_the_approver_falls_back_to_the_repositorys_git_identity(repo):
    subprocess.run(["git", "-C", str(repo), "config", "user.email", WHO],
                   check=True, capture_output=True)
    assert A.who(repo, None) == WHO
    assert A.who(repo, "  someone-else  ") == "someone-else"


# ── the CLI ──────────────────────────────────────────────────────────────────


def test_cli_records_and_then_verifies(repo, capsys):
    rid = _run_at(repo)
    assert A.main(["--target", str(repo), "--run", rid, "--gate", "G3",
                   "--verdict", "approve", "--approver", WHO]) == 0
    assert "G3 approved" in capsys.readouterr().out
    assert A.main(["--target", str(repo), "--run", rid, "--verify"]) == 0
    assert "intact" in capsys.readouterr().out
    (L.run_dir(repo, rid) / "design.md").write_text("moved on", encoding="utf-8")
    assert A.main(["--target", str(repo), "--run", rid, "--verify"]) == 1


def test_cli_refuses_without_a_gate_or_a_target(repo, capsys):
    rid = _run_at(repo)
    assert A.main(["--target", str(repo), "--run", rid]) == 2
    assert "--gate and --verdict" in capsys.readouterr().out
    assert A.main(["--target", str(repo / "nowhere"), "--run", rid,
                   "--verify"]) == 2


# ── what is waiting for a human ──────────────────────────────────────────────


def test_status_names_the_gate_a_run_waits_at(repo, capsys):
    rid = _run_at(repo, phase="P7")
    assert S.waiting_on(L.load(repo, rid)) == "G4"
    assert S.main(["--target", str(repo), "--awaiting"]) == 0
    out = capsys.readouterr().out
    assert rid in out and "waiting G4" in out


def test_status_hides_runs_nobody_is_waiting_on(repo, capsys):
    rid = _run_at(repo)
    A.record(repo, rid, "G3", "approve", WHO)
    assert S.main(["--target", str(repo), "--awaiting"]) == 0
    assert "nothing is waiting" in capsys.readouterr().out


def test_status_verify_is_the_signal_ci_will_read(repo, capsys):
    rid = _run_at(repo)
    A.record(repo, rid, "G3", "approve", WHO)
    assert S.main(["--target", str(repo), "--verify"]) == 0
    (L.run_dir(repo, rid) / "design.md").write_text("edited after the yes",
                                                    encoding="utf-8")
    assert S.main(["--target", str(repo), "--verify"]) == 1
    assert "changed since" in capsys.readouterr().out


def test_status_shows_one_run_in_full(repo, capsys):
    rid = _run_at(repo)
    A.record(repo, rid, "G3", "approve", WHO, notes="diff read line by line")
    assert S.main(["--target", str(repo), "--run", rid]) == 0
    out = capsys.readouterr().out
    assert "G3 approved by" in out and "diff read line by line" in out
    assert S.main(["--target", str(repo), "--run", "ghost"]) == 2


def test_status_json_carries_the_survey(repo, capsys):
    rid = _run_at(repo)
    assert S.main(["--target", str(repo), "--json"]) == 0
    (row,) = json.loads(capsys.readouterr().out)
    assert row["run_id"] == rid and row["waiting"] == "G3" and row["entries"] == 0


def test_status_reads_and_never_writes(repo):
    rid = _run_at(repo)
    A.record(repo, rid, "G3", "approve", WHO)
    before = {p: p.read_bytes() for p in L.run_dir(repo, rid).iterdir()
              if p.is_file()}
    S.main(["--target", str(repo)])
    S.main(["--target", str(repo), "--run", rid, "--verify"])
    assert {p: p.read_bytes() for p in L.run_dir(repo, rid).iterdir()
            if p.is_file()} == before


# ── a gate must not be voided by a phase that runs after it ──────────────────
#
# Found on the first run to take the lifecycle's own P6-to-P4 loop, by asking
# the merge gate directly: nine problems, eight of them phases moving forward
# exactly as designed. Two mechanisms, and both had to go.


def _artifact(repo: Path, run_id: str, rel: str, body: str) -> None:
    (L.run_dir(repo, run_id) / rel).write_text(body, encoding="utf-8")
    L.record_artifact(repo, run_id, rel)


def test_a_gate_pins_its_own_work_and_not_a_later_phases(repo):
    """G3 is a verdict about an implementation. A test report written after it
    is not G3's to approve, and pinning one made the run's own progress void
    the gate that had authorised it."""
    rid = _run_at(repo)
    for rel in ("50-tests.md", "50-sca.json", "60-review.md", "prepass.json"):
        _artifact(repo, rid, rel, "written by a later phase")

    A.record(repo, rid, "G3", "approve", "lead@example.invalid", "")
    pinned = set(A.read_chain(repo, rid)[-1]["artifacts"])
    assert "design.md" in pinned, "the gate must still pin the work it is about"
    assert not (pinned & {"50-tests.md", "50-sca.json", "60-review.md",
                          "prepass.json"}), sorted(pinned)


def test_rerunning_a_later_phase_does_not_void_the_gate_that_authorised_it(repo):
    """The composition, which is the actual defect: approve, then let P5 and P6
    run again as the loop requires, and ask whether the gate still stands."""
    rid = _run_at(repo)
    _artifact(repo, rid, "50-tests.md", "round one")
    A.record(repo, rid, "G3", "approve", "lead@example.invalid", "")

    _artifact(repo, rid, "50-tests.md", "round two, re-measured")
    entry = A.read_chain(repo, rid)[-1]
    assert A.verify_artifacts_for(repo, rid, entry) == [], \
        "P5 re-running is the mandated next step and must not void G3"
    assert A.gate_state(repo, rid, "G3")[0] is True


def test_a_superseded_entry_is_not_checked_against_todays_tree(repo):
    """Round one approved bytes round two replaced. Reporting that is true and
    about nothing - and it kept a correct change out of a merge."""
    rid = _run_at(repo)
    A.record(repo, rid, "G3", "approve", "lead@example.invalid", "round one")
    _artifact(repo, rid, "design.md", "the plan, revised after review")
    A.record(repo, rid, "G3", "approve", "lead@example.invalid", "round two")

    live = A.live_entries(repo, rid)
    assert [e["notes"] for e in live] == ["round two"]
    assert A.verify_artifacts_for(repo, rid, live[0]) == []
    assert len(A.read_chain(repo, rid)) == 2, \
        "superseded entries stay in the chain; only the VERDICT is superseded"
    assert A.verify_chain(repo, rid) == [], "and the chain still covers both"


def test_the_real_edit_is_still_caught(repo):
    """The control that matters. Loosening this to fix the loop would have
    given away the property item 32 exists for."""
    rid = _run_at(repo)
    A.record(repo, rid, "G3", "approve", "lead@example.invalid", "")
    _artifact(repo, rid, "design.md", "edited after the yes")
    entry = A.live_entries(repo, rid)[-1]
    assert any("changed since G3" in p
               for p in A.verify_artifacts_for(repo, rid, entry))


def test_an_artifact_no_phase_declares_is_still_pinned(repo):
    """The rule is stated as an EXCLUSION on purpose. An include-list would
    quietly stop pinning anything the contract does not name, and an adopter
    recording their own evidence would find no gate covering it."""
    rid = _run_at(repo)
    _artifact(repo, rid, "notes-from-the-vendor.md", "not in any phase")
    A.record(repo, rid, "G3", "approve", "lead@example.invalid", "")
    assert "notes-from-the-vendor.md" in A.read_chain(repo, rid)[-1]["artifacts"]


def test_evidence_is_attribution_and_carries_no_obligation():
    """`conditional` means owed when a trigger fires and must have a judge -
    a test enforces that, and enforced it against the first cut of this field,
    which abused `conditional` for attribution."""
    from tools.sdlc import stage as ST

    assert {"prepass.json", "50-sca.json"} <= {
        name for s in ST.STAGES for name in s.evidence}
    judged = {s.phase for s in ST.STAGES if s.conditional}
    assert judged == set(ST.CONDITIONAL_JUDGE), \
        "evidence must not have leaked into conditional"
