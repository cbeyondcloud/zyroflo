"""Item 34 — the human-vs-agent delta, and the gate defect capturing it found.

The headline tests are the ones that were red before this item: a human who
improved the agent's artifact before approving it pinned bytes that no longer
existed, so `gate_state` reported the gate void about an edit the approver had
just made. Every fixture in the suite had played the human as a rubber stamp,
which is exactly why nothing caught it — so the tests here edit first and
approve second, which is what a review actually is.

The controls matter as much as the fix. Item 32's property is that a gate is
about specific bytes: a POST-approval edit must still void it, and an artifact
that has gone missing must not be re-pinned away into a clean gate. Both are
asserted beside the change they could have been broken by.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.review import hitl as H  # noqa: E402
from tools.sdlc import approve as A  # noqa: E402
from tools.sdlc import feedback as F  # noqa: E402
from tools.sdlc import policy as P  # noqa: E402
from tools.sdlc import state as L  # noqa: E402
from tools.sdlc import suite as ENGINE  # noqa: E402
from tools.tests import _runs as R  # noqa: E402

WHO = "dev@example.invalid"
DESIGN = "30-design.md"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / ".sdlc").mkdir(parents=True)
    (r / ".sdlc" / "profile.yml").write_text(
        "stack: python\ngate_profile: solo\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(r), "init", "-q"], check=True,
                   capture_output=True)
    return r


def parked(repo: Path, phase: str = "P3") -> str:
    """A run that has done the phase's work and recorded it — the state a
    human is looking at when they decide."""
    st = L.create(repo, "requirement", "a change worth gating")
    R.park(repo, st["run_id"], phase)
    return st["run_id"]


def edit(repo: Path, run_id: str, rel: str = DESIGN,
         text: str = "P3 did its work\n\nand the human added the rollback plan\n"):
    (L.run_dir(repo, run_id) / rel).write_text(text, encoding="utf-8")


def rows_of(repo: Path, run_id: str, kind: str = "") -> list[dict]:
    rows = [r for r in F.read(repo, run_id) if "_unreadable" not in r]
    return [r for r in rows if r.get("kind") == kind] if kind else rows


# ── the headline: a human edits before saying yes ────────────────────────────


def test_the_verdict_pins_what_the_human_accepted_not_what_the_agent_wrote(repo):
    """The measurement this item started from. `state.json`'s map holds the
    hash from the moment the AGENT wrote the file; an approval that copies it
    is about bytes the approver never saw."""
    run = parked(repo)
    proposed = L.load(repo, run)["artifacts"][DESIGN]
    edit(repo, run)
    entry = A.record(repo, run, "G2", "approve", WHO)
    assert entry["artifacts"][DESIGN] != proposed
    assert entry["artifacts"][DESIGN] == L.load(repo, run)["artifacts"][DESIGN]


def test_the_gate_stands_after_the_human_improves_the_artifact(repo):
    """Before this, `gate_state` answered "G2 approved different bytes -
    30-design.md: changed since G2 approved it" about an edit made BEFORE the
    yes. An approval void the instant it is written is not a gate."""
    run = parked(repo)
    edit(repo, run)
    A.record(repo, run, "G2", "approve", WHO)
    assert A.gate_state(repo, run, "G2") == (True, "")


def test_the_edit_is_recorded_with_both_digests_and_no_contents(repo):
    run = parked(repo)
    proposed = L.load(repo, run)["artifacts"][DESIGN]
    edit(repo, run)
    A.record(repo, run, "G2", "approve", WHO)
    (row,) = rows_of(repo, run, "artifact-edit")
    assert row["artifact"] == DESIGN
    assert row["proposed"] == proposed and row["accepted"] != proposed
    blob = json.dumps(row)
    assert "rollback plan" not in blob, "the contents belong in git, not here"


def test_an_untouched_artifact_is_not_a_correction(repo):
    """A rubber stamp is a real outcome and must not read as an edit."""
    run = parked(repo)
    A.record(repo, run, "G2", "approve", WHO)
    assert rows_of(repo, run, "artifact-edit") == []
    (gate,) = rows_of(repo, run, "gate")
    assert gate["corrections"] == 0


# ── the controls: what the fix must not have broken ──────────────────────────


def test_a_post_approval_edit_still_voids_the_gate(repo):
    """Item 32's property, unchanged. Re-pinning at the verdict is not the same
    as re-pinning whenever asked."""
    run = parked(repo)
    A.record(repo, run, "G2", "approve", WHO)
    edit(repo, run)
    ok, why = A.gate_state(repo, run, "G2")
    assert not ok and "different bytes" in why


def test_an_artifact_that_went_missing_is_not_repinned_away(repo):
    """The dangerous shape of this fix: refreshing the pin for an artifact that
    is GONE would turn a run whose own output vanished into a clean gate."""
    run = parked(repo)
    (L.run_dir(repo, run) / DESIGN).unlink()
    A.record(repo, run, "G2", "approve", WHO, force=True)
    ok, why = A.gate_state(repo, run, "G2")
    assert not ok and "missing" in why
    (row,) = rows_of(repo, run, "artifact-lost")
    assert row["artifact"] == DESIGN and row["problem"] == "missing"


def test_a_reject_pins_the_bytes_an_approve_would(repo):
    """Item 42 compares a reject and an approve by their pinned fingerprint. A
    stale map on one side and a fresh one on the other would make two verdicts
    about one diff stop contesting each other."""
    run = parked(repo)
    edit(repo, run)
    rejected = A.record(repo, run, "G2", "reject", "other@example.invalid")
    approved = A.record(repo, run, "G2", "approve", WHO)
    assert A.about(rejected) == A.about(approved)
    ok, why = A.gate_state(repo, run, "G2")
    assert not ok and "rejected by other@example.invalid" in why


# ── capture is wired, not offered ────────────────────────────────────────────


def test_recording_a_verdict_is_the_only_thing_anyone_has_to_do(repo):
    """No capture step exists for anyone to skip: the rows accrue on the one
    call every gate in the harness already goes through."""
    run = parked(repo)
    assert not F.path_of(repo, run).is_file()
    A.record(repo, run, "G2", "approve", WHO, notes="fine by me")
    assert F.path_of(repo, run).is_file()


def test_the_gate_row_carries_the_humans_own_words(repo):
    run = parked(repo)
    A.record(repo, run, "G2", "approve", WHO, notes="I rewrote the rollback step")
    (row,) = rows_of(repo, run, "gate")
    assert row["notes"] == "I rewrote the rollback step"
    assert row["verdict"] == "approve" and row["by"] == WHO


def test_a_refused_approval_leaves_no_feedback(repo):
    """Nothing was accepted, so there is no correction to learn from. Capture
    runs after every refusal for exactly this reason."""
    st = L.create(repo, "requirement", "not yet")
    with pytest.raises(RuntimeError):
        A.record(repo, st["run_id"], "G4", "approve", WHO)
    assert not F.path_of(repo, st["run_id"]).is_file()


def test_two_verdicts_at_one_gate_are_two_rows(repo):
    run = parked(repo)
    A.record(repo, run, "G2", "reject", WHO, notes="no")
    edit(repo, run)
    A.record(repo, run, "G2", "approve", WHO, notes="better")
    assert [r["verdict"] for r in rows_of(repo, run, "gate")] == ["reject", "approve"]


# ── the same correction is not learned twice ─────────────────────────────────


def test_capturing_twice_at_one_gate_does_not_duplicate_a_correction(repo):
    run = parked(repo)
    edit(repo, run)
    F.capture(repo, run, "G2", "approve", WHO)
    before = len(rows_of(repo, run))
    F.capture(repo, run, "G2", "approve", WHO)
    assert len(rows_of(repo, run, "artifact-edit")) == 1
    assert len(rows_of(repo, run)) == before + 1  # the second gate row, only


def test_a_second_capture_does_not_claim_corrections_it_did_not_write(repo):
    """The gate row counts what LANDED, not what was built. Counting the
    candidates would have the second gate row claim a correction it deduped
    away — a number a learning loop then divides by.

    The correction here is a DISMISSAL, not an edit, and that is the whole
    fixture: an edited artifact is re-pinned by the first capture, so the
    second finds no drift and both counts are zero. The mutation pass caught
    the first version of this test asserting exactly that — twice in one item,
    which is how reliably a test can measure nothing and look green."""
    run = parked(repo)
    dismissal(repo, run)
    F.capture(repo, run, "G2", "approve", WHO)
    F.capture(repo, run, "G2", "approve", WHO)
    assert [r["corrections"] for r in rows_of(repo, run, "gate")] == [1, 0]


def test_the_same_correction_made_twice_is_recorded_twice(repo):
    """A RECURRENCE is the signal item 50 distils: the human fixed the same
    thing again. The first cut of this test edited to different text each time,
    so the digests differed and it passed no matter what the id was made of —
    the mutation pass caught it asserting a property the fixture could not
    exercise, which is the defect item 39's mutation pass found in item 39."""
    run = parked(repo)
    proposed = (L.run_dir(repo, run) / DESIGN).read_text(encoding="utf-8")
    edit(repo, run)
    A.record(repo, run, "G2", "approve", WHO)
    L.advance(repo, run, phase="P4")
    R.deliver(repo, run, "P4")
    # P4 regenerates the design from the agent's own template and records it,
    # so the correction has to be made a second time.
    (L.run_dir(repo, run) / DESIGN).write_text(proposed, encoding="utf-8")
    L.record_artifact(repo, run, DESIGN)
    edit(repo, run)
    A.record(repo, run, "G3", "approve", WHO)
    rows = rows_of(repo, run, "artifact-edit")
    assert [r["gate"] for r in rows] == ["G2", "G3"]
    assert rows[0]["accepted"] == rows[1]["accepted"], "the same correction twice"


# ── the proposals a human answered, or did not ───────────────────────────────


def dismissal(repo: Path, run: str, trigger: str = "dependency:api") -> None:
    from tools.sdlc import adr as records
    path = L.run_dir(repo, run) / records.DISMISSED
    path.write_text(json.dumps({"schema": 1, "trigger": trigger,
                                "because": "the dependency is a test-only one",
                                "by": WHO, "at": "2026-08-25T09:00:00+00:00",
                                "run": run}) + "\n", encoding="utf-8")


def test_a_dismissed_adr_trigger_is_a_correction(repo):
    """The harness said an ADR was owed and a person said it was not. Item 64
    recorded that with a reason and a name, and nothing had ever read it as
    feedback about the harness — which is the only thing it is."""
    run = parked(repo)
    dismissal(repo, run)
    A.record(repo, run, "G2", "approve", WHO)
    (row,) = rows_of(repo, run, "finding-rejected")
    assert row["source"] == "adr-trigger" and row["finding"] == "dependency:api"
    assert "test-only" in row["because"]


def test_a_dismissal_is_one_correction_across_every_later_gate(repo):
    run = parked(repo)
    dismissal(repo, run)
    A.record(repo, run, "G2", "approve", WHO)
    L.advance(repo, run, phase="P4")
    R.deliver(repo, run, "P4")
    A.record(repo, run, "G3", "approve", WHO)
    assert len(rows_of(repo, run, "finding-rejected")) == 1


@pytest.mark.parametrize("ticks,expect", [
    (["- [x] widen the scope: it re-enters at P3"], "accepted"),
    (["- [X] out of scope: a pre-existing break"], "declined"),
    (["- [ ] widen the scope", "- [ ] out of scope"], "undecided"),
    ([], "undecided"),
    (["- [x] widen the scope", "- [x] out of scope"], "contradictory"),
])
def test_the_scope_add_box_is_read(ticks, expect):
    """`suite.obligations` requires the file to EXIST and never looks inside
    it, so an unanswered proposal sits under a green P5. `undecided` is the
    honest reading of that, and it is a correction worth learning from."""
    assert F.scope_decision("\n".join(["# proposal", ""] + ticks))[0] == expect


def test_the_phrases_read_here_are_the_ones_the_engine_writes():
    """The currency guard. A checkbox this file matches and item 38 renames is
    a silent `undecided` forever — the two-spellings defect item 64 found
    between the profile and the graph, one document along."""
    rendered = ENGINE.scope_add_text({"run": "r", "unscoped": ["api"],
                                      "steps": []}).lower()
    for phrase, _ in F.SCOPE_DECISION:
        assert phrase in rendered, phrase


def test_the_scope_add_is_the_only_p5_proposal_this_file_can_read():
    """`scope_proposals` walks P5's conditional artifacts and reads each as a
    scope-add decision. The day item 38 adds a second one, that labelling is
    wrong — so the assumption is a test rather than a comment."""
    from tools.sdlc import stage as stages
    assert stages.by_phase("P5").conditional == ("50-scope-add.md",)


def test_an_undecided_scope_add_reaches_the_record(repo):
    run = parked(repo, "P5")
    (L.run_dir(repo, run) / "50-scope-add.md").write_text(
        ENGINE.scope_add_text({"run": run, "unscoped": ["api"], "steps": []}),
        encoding="utf-8")
    A.record(repo, run, "G3", "approve", WHO, force=True)
    (row,) = rows_of(repo, run, "proposal")
    assert row["proposal"] == "scope-add" and row["decision"] == "undecided"


# ── the review half, joined through the run ──────────────────────────────────


def review(repo: Path, run: str | None, *findings, verdict: str = "approved",
           component: str = "api") -> None:
    """A real session through item 27's own CLI functions — never a hand-rolled
    row. Two fixtures spelling one format differently is the seam item 29 found
    under a green suite."""
    H.begin(repo, component=component, run=run or "")
    for fid, source, disposition in findings:
        H.finding(repo, fid, source, disposition)
    H.end(repo, verdict)


def test_a_rejected_review_finding_reaches_the_runs_feedback(repo):
    run = parked(repo)
    review(repo, run, ("no-secrets", "prepass", "rejected"),
           ("naming", "agent", "accepted"), ("perf", "agent", "deferred"))
    A.record(repo, run, "G2", "approve", WHO)
    got = {(r["finding"], r["disposition"])
           for r in rows_of(repo, run, "finding-rejected")}
    assert got == {("no-secrets", "rejected"), ("perf", "deferred")}


def test_the_gate_row_carries_the_denominator(repo):
    """A file of nothing but rejections invites "acceptance = 1 - len(file)",
    which is wrong. The totals travel with the corrections so item 52 cannot
    compute the rate from half the picture."""
    run = parked(repo)
    review(repo, run, ("a", "prepass", "rejected"), ("b", "agent", "accepted"),
           ("c", "agent", "accepted"))
    A.record(repo, run, "G2", "approve", WHO)
    (gate,) = rows_of(repo, run, "gate")
    assert gate["review"] == {"sessions": 1, "findings": 3, "corrections": 1,
                              "unreadable": 0}


def test_a_rejected_finding_is_not_attributed_to_whoever_took_the_gate(repo):
    """The reviewer who rejected it is not necessarily the person recording the
    verdict, and item 27 removed identity from review rows on purpose. A name
    stamped here would be an invention AND a reinstatement."""
    run = parked(repo)
    review(repo, run, ("no-secrets", "prepass", "rejected"))
    A.record(repo, run, "G2", "approve", WHO)
    (row,) = rows_of(repo, run, "finding-rejected")
    assert row["by"] == ""
    (gate,) = rows_of(repo, run, "gate")
    assert gate["by"] == WHO, "the verdict IS an act by a named person"


def test_another_runs_review_is_not_this_runs_feedback(repo):
    run = parked(repo)
    other = parked(repo)
    review(repo, other, ("elsewhere", "prepass", "rejected"))
    A.record(repo, run, "G2", "approve", WHO)
    assert rows_of(repo, run, "finding-rejected") == []


def test_a_session_with_no_run_joins_to_nothing_and_says_nothing(repo):
    """The provider smoke reviews against a scratch directory with no runs in
    it. That must keep working, and must not be attributed to a run at random."""
    run = parked(repo)
    review(repo, None, ("unattributed", "prepass", "rejected"))
    A.record(repo, run, "G2", "approve", WHO)
    (gate,) = rows_of(repo, run, "gate")
    assert gate["review"]["sessions"] == 0
    assert rows_of(repo, run, "finding-rejected") == []


def test_an_unreadable_review_line_is_counted_not_skipped(repo):
    run = parked(repo)
    reviews, _ = H.paths(repo)
    reviews.parent.mkdir(parents=True, exist_ok=True)
    reviews.write_text("{not json\n", encoding="utf-8")
    A.record(repo, run, "G2", "approve", WHO)
    (gate,) = rows_of(repo, run, "gate")
    assert gate["review"]["unreadable"] == 1


# ── the run id on a review row ───────────────────────────────────────────────


def test_hitl_refuses_a_run_the_target_does_not_have(repo):
    message = H.begin(repo, component="api", run="20260825-000000-abcdef")
    assert message.startswith("!!") and "join to nothing" in message
    assert H.load_pending(repo) is None


def test_a_review_row_carries_the_run_and_still_no_identity(repo):
    """Item 27's pin, amended and re-stated. The key set grows by exactly one —
    `run`, which names WORK — and the half that carries the anti-Goodhart
    property is the identity ban, which is unchanged."""
    run = parked(repo)
    review(repo, run, ("a", "human", "accepted"))
    reviews, _ = H.paths(repo)
    (row,) = [json.loads(line) for line
              in reviews.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert set(row) == {"schema_version", "kind", "started_at", "finished_at",
                        "duration_s", "verdict", "component", "findings",
                        "counts", "run"}
    assert row["run"] == run
    blob = json.dumps(row).lower()
    for banned in ("user", "author", "email", "login", "reviewer"):
        assert banned not in blob


def test_an_abandoned_session_keeps_its_run(repo):
    run = parked(repo)
    H.begin(repo, component="api", run=run)
    H.abandon(repo)
    reviews, _ = H.paths(repo)
    (row,) = [json.loads(line) for line
              in reviews.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert row["kind"] == "review-abandoned" and row["run"] == run


# ── the record as a record ───────────────────────────────────────────────────


def test_an_unreadable_row_is_reported_not_dropped(repo):
    """A corpus that quietly loses lines teaches from a sample nobody chose."""
    run = parked(repo)
    A.record(repo, run, "G2", "approve", WHO)
    with F.path_of(repo, run).open("a", encoding="utf-8") as fh:
        fh.write("{half a row\n")
    assert F.summary(repo, run)["unreadable"] == 1
    assert "not readable JSON" in F._render(repo, run)


def test_every_kind_this_file_writes_is_a_declared_one(repo):
    """`KINDS` is the vocabulary items 50 and 52 will switch on. A constant
    listing kinds nobody emits, or an emitted kind nobody listed, is the same
    defect this item is about — one file along."""
    run = parked(repo, "P5")
    dismissal(repo, run)
    edit(repo, run, rel="50-tests.md", text="the human rewrote the test plan\n")
    (L.run_dir(repo, run) / "50-scope-add.md").write_text(
        ENGINE.scope_add_text({"run": run, "unscoped": ["api"], "steps": []}),
        encoding="utf-8")
    (L.run_dir(repo, run) / "50-suite.json").unlink(missing_ok=True)
    review(repo, run, ("a", "prepass", "rejected"))
    A.record(repo, run, "G3", "approve", WHO, force=True)
    emitted = {r["kind"] for r in rows_of(repo, run)}
    assert emitted <= set(F.KINDS)
    assert emitted == {"gate", "artifact-edit", "finding-rejected", "proposal"}


def test_summary_counts_by_kind(repo):
    run = parked(repo)
    dismissal(repo, run)
    edit(repo, run)
    A.record(repo, run, "G2", "approve", WHO)
    assert F.summary(repo, run)["by_kind"] == {
        "artifact-edit": 1, "finding-rejected": 1, "gate": 1}


def test_the_cli_refuses_a_run_it_cannot_find(repo, capsys):
    assert F.main(["--target", str(repo), "--run", "nope"]) == 2
    assert "no run nope" in capsys.readouterr().out


def test_the_cli_says_so_when_a_run_has_taken_no_gate(repo, capsys):
    run = parked(repo)
    assert F.main(["--target", str(repo), "--run", run]) == 0
    assert "no feedback recorded" in capsys.readouterr().out


def test_the_cli_json_view_carries_the_rows(repo, capsys):
    run = parked(repo)
    edit(repo, run)
    A.record(repo, run, "G2", "approve", WHO, notes="ok")
    assert F.main(["--target", str(repo), "--run", run, "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["summary"]["by_kind"] == {"artifact-edit": 1, "gate": 1}
    assert {r["kind"] for r in doc["rows"]} == {"artifact-edit", "gate"}


# ── the evidence a learned rule rests on ─────────────────────────────────────


def test_an_agent_may_not_write_the_feedback_record(repo):
    """`learning_log.jsonl` was protected before anything wrote it and the file
    it is distilled FROM was not. An agent that can append its own corrections
    can author the rules a retro agent proposes."""
    rules = P.load(repo)
    assert P.protected(rules, repo, [".sdlc/runs/r1/feedback.jsonl"])
    assert P.protected(rules, repo, ["sdlc/runs/r1/feedback.jsonl"])


def test_the_constitution_and_the_defaults_protect_the_same_paths():
    """A template an adopter fills and a default nobody edits that disagree
    would mean the protection depends on whether the file was installed."""
    template = (ROOT / "sdlc_templates" / "policy.yml").read_text(encoding="utf-8")
    for glob in P.DEFAULTS["protected_paths"]:
        assert f'"{glob}"' in template, glob


# ── the corpus reader, held to what its own file already argues ──────────────


def test_a_corrupt_review_row_is_counted_not_folded_into_another_runs(repo):
    """`read()` two functions above says an unreadable line "is never silently
    skipped, because a learning set that quietly loses rows teaches from a
    sample nobody chose". `review_corrections` had an `unreadable` counter and
    then let a row that PARSES but is not an object leave through the same
    `continue` as a row belonging to a different run — so a corrupt corpus and
    a small one were the same number.
    """
    run_id = "r-1"
    reviews, _ = H.paths(repo)
    reviews.parent.mkdir(parents=True, exist_ok=True)
    reviews.write_text(
        '"a row that parses but is not an object"\n'
        + "not json at all\n"
        + json.dumps({"kind": "review", "run": "other-run",
                      "findings": []}) + "\n",
        encoding="utf-8")
    _, totals = F.review_corrections(repo, run_id)
    # Two damaged lines counted; the legitimate other-run row is NOT damage.
    assert totals["unreadable"] == 2
    assert totals["sessions"] == 0


def test_a_session_whose_findings_are_null_is_damage_not_an_empty_review(repo):
    """A PRESENT null defeats `.get("findings", [])`, so `for f in None` raised
    TypeError out of the distiller. `hitl.show` reads this SAME file and was
    hardened for exactly this; this reader of it was not."""
    reviews, _ = H.paths(repo)
    reviews.parent.mkdir(parents=True, exist_ok=True)
    reviews.write_text(
        json.dumps({"kind": "review", "run": "r-1", "findings": None}) + "\n",
        encoding="utf-8")
    _, totals = F.review_corrections(repo, "r-1")
    assert totals["unreadable"] == 1 and totals["findings"] == 0


def test_a_finding_that_is_not_an_object_is_counted_not_a_traceback(repo):
    """`f.get("disposition")` on a string raised AttributeError — the same
    defect one level down from the row check, which is where it has kept
    turning up."""
    reviews, _ = H.paths(repo)
    reviews.parent.mkdir(parents=True, exist_ok=True)
    reviews.write_text(
        json.dumps({"kind": "review", "run": "r-1",
                    "findings": ["oops", {"source": "agent",
                                          "disposition": "rejected",
                                          "id": "f1"}]}) + "\n",
        encoding="utf-8")
    rows, totals = F.review_corrections(repo, "r-1")
    assert totals["unreadable"] == 1
    assert totals["findings"] == 1 and len(rows) == 1


def test_an_intact_record_reports_no_damage(repo):
    """The negative control: none of the above may make an ordinary record
    look corrupt, or the counter becomes noise nobody reads."""
    reviews, _ = H.paths(repo)
    reviews.parent.mkdir(parents=True, exist_ok=True)
    reviews.write_text(
        json.dumps({"kind": "review", "run": "r-1",
                    "findings": [{"source": "agent", "id": "f1",
                                  "disposition": "rejected"}]}) + "\n",
        encoding="utf-8")
    rows, totals = F.review_corrections(repo, "r-1")
    assert totals["unreadable"] == 0 and totals["sessions"] == 1
    assert len(rows) == 1
