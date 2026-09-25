"""The learning loop — item 50.

`feedback.jsonl` has recorded what humans CHANGED about an agent's work since
item 34 — an artifact edited before approval, a finding rejected, an ADR trigger
dismissed. **Nothing read it back.** A record of corrections that never becomes
a correction is a diary.

The properties worth more than the rest of this file:

**A group counts RUNS, not occurrences.** Two rejections inside one review are
one person deciding one thing once, and counting them twice is how a single
reviewer's preference becomes a rule everybody inherits.

**Applying is gated twice, and refuses rather than assumes** — the corpus
regression must have run and be clean, and a run's G3 must be approved AND still
speak for what it approved.

**Undo appends; it never deletes.** The M5 kill gate reads *"reverts via
`--undo` (audit shows both)"*, and an undo that erased its own application would
leave an audit showing neither.

**Anything undeclared still fires.** The suppression is the third instance of
the idiom `known_failures` and `sca_known_findings` already use, and it is only
worth having while that stays true.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.review import hitl  # noqa: E402
from tools.review import loop as LP  # noqa: E402
from tools.sdlc import feedback as FB  # noqa: E402
from tools.sdlc import learning as L  # noqa: E402
from tools.sdlc import policy as PL  # noqa: E402
from tools.sdlc import state as ledger  # noqa: E402

PROFILE = ("stack: python\ngate_profile: solo\n"
           "phases: [intake, requirements, design, implementation, test, review, "
           "triage, closeout]\n"
           'test_command: "pytest -q"\nreview:\n  block_on: [critical, high]\n')


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / ".sdlc").mkdir(parents=True)
    (r / ".sdlc" / "profile.yml").write_text(PROFILE, encoding="utf-8")
    subprocess.run(["git", "-C", str(r), "init", "-q"], check=True,
                   capture_output=True, timeout=120)
    return r


def reviewed(repo: Path, run_id: str, findings: list[tuple[str, str]],
             started: str = "2026-08-29T01:00:00+00:00") -> None:
    """One review session on `run_id`, rejecting what it is told to."""
    reviews, _ = hitl.paths(repo)
    reviews.parent.mkdir(parents=True, exist_ok=True)
    row = {"kind": "review", "run": run_id, "component": "api",
           "started_at": started, "findings": [
               {"id": fid, "disposition": disp, "severity": "low",
                "source": "agent", "path": "a/b.py"} for fid, disp in findings]}
    with reviews.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


def a_run(repo: Path) -> str:
    return ledger.create(repo, "defect", "a title")["run_id"]


def a_proposal(repo: Path, finding: str = "noisy-rule") -> dict:
    return {"schema_version": 1, "id": "learn:test0000000000",
            "finding": finding, "home": "repo", "path": ".sdlc/profile.yml",
            "change": {"kind": "append-to-list", "key": L.SUPPRESS_KEY,
                       "add": [finding]},
            "evidence": {"runs": ["r1", "r2"], "run_count": 2},
            "why": "rejected twice"}


# ── grouping: evidence, not a decision ──────────────────────────────────────


def test_the_same_finding_rejected_in_two_runs_is_a_group(repo):
    r1, r2 = a_run(repo), a_run(repo)
    reviewed(repo, r1, [("noisy-rule", "rejected")])
    reviewed(repo, r2, [("noisy-rule", "rejected")], started="2026-08-29T02:00:00+00:00")
    groups = FB.group_corrections(repo)
    assert len(groups) == 1
    assert groups[0]["finding"] == "noisy-rule" and groups[0]["run_count"] == 2


def test_the_floor_counts_runs_not_occurrences(repo):
    """**The property that keeps one reviewer's preference from becoming
    everybody's rule.** Two rejections in one review are one decision."""
    r1 = a_run(repo)
    reviewed(repo, r1, [("noisy-rule", "rejected")])
    reviewed(repo, r1, [("noisy-rule", "rejected")], started="2026-08-29T02:00:00+00:00")
    assert FB.group_corrections(repo) == []


def test_a_finding_rejected_once_does_not_group(repo):
    r1 = a_run(repo)
    reviewed(repo, r1, [("seen-once", "rejected")])
    assert FB.group_corrections(repo) == []
    assert len(FB.group_corrections(repo, floor=1)) == 1


def test_an_accepted_finding_is_not_a_correction(repo):
    """Accepting a finding is the reviewer agreeing with it. Learning from that
    would suppress the findings that WORK."""
    r1, r2 = a_run(repo), a_run(repo)
    reviewed(repo, r1, [("good-rule", "accepted")])
    reviewed(repo, r2, [("good-rule", "accepted")], started="2026-08-29T02:00:00+00:00")
    assert FB.group_corrections(repo) == []


def test_a_deferred_finding_counts_as_a_correction(repo):
    r1, r2 = a_run(repo), a_run(repo)
    reviewed(repo, r1, [("later-rule", "deferred")])
    reviewed(repo, r2, [("later-rule", "deferred")], started="2026-08-29T02:00:00+00:00")
    assert len(FB.group_corrections(repo)) == 1


# ── two homes, decided by the file ──────────────────────────────────────────


def test_a_harness_file_is_product_and_an_adopter_file_is_not(repo):
    """Rev 12's split, read off the PATH rather than guessed from intent — a
    path is checkable and an intention is not."""
    assert L.home_of(ROOT / "sdlc_templates" / "profile.yml") == "harness"
    assert L.home_of(repo / ".sdlc" / "profile.yml") == "repo"


def test_a_proposal_carries_its_evidence_and_its_home(repo):
    r1, r2 = a_run(repo), a_run(repo)
    reviewed(repo, r1, [("noisy-rule", "rejected")])
    reviewed(repo, r2, [("noisy-rule", "rejected")], started="2026-08-29T02:00:00+00:00")
    got = L.propose(repo)
    assert len(got) == 1
    assert got[0]["home"] == "repo" and got[0]["finding"] == "noisy-rule"
    assert got[0]["evidence"]["run_count"] == 2
    assert sorted(got[0]["evidence"]["runs"]) == sorted([r1, r2])
    assert got[0]["change"]["key"] == L.SUPPRESS_KEY


def test_a_proposal_id_is_stable_across_runs_of_the_tool(repo):
    """A proposal a human is about to approve must be the same object next time
    they look."""
    assert (L.proposal_id("f", "repo", ".sdlc/profile.yml")
            == L.proposal_id("f", "repo", ".sdlc/profile.yml"))
    assert (L.proposal_id("f", "repo", ".sdlc/profile.yml")
            != L.proposal_id("f", "harness", ".sdlc/profile.yml"))


# ── the gates refuse rather than assume ─────────────────────────────────────


def test_applying_refuses_when_no_corpus_replay_has_ever_run(repo):
    """Could-not-look is not a pass."""
    owed = L.corpus_gate(repo)
    assert len(owed) == 1 and "nobody has run" in owed[0]


def test_applying_refuses_without_a_live_gate(repo):
    run_id = a_run(repo)
    owed = L.gate_taken(repo, run_id)
    assert len(owed) == 1 and "no live G3" in owed[0]


def test_a_learned_change_takes_the_same_gate_every_change_takes(repo):
    run_id = a_run(repo)
    with pytest.raises(L.LearningError) as exc:
        L.apply(repo, a_proposal(repo), run_id, "someone")
    assert "G3" in str(exc.value)


# ── applying, and putting it back ───────────────────────────────────────────


def test_applying_writes_the_declaration_and_logs_it(repo):
    row = L.apply(repo, a_proposal(repo), "r1", "someone", skip_gates=True)
    text = (repo / ".sdlc" / "profile.yml").read_text(encoding="utf-8")
    assert f'{L.SUPPRESS_KEY}: ["noisy-rule"]' in text
    assert row["action"] == "applied" and row["home"] == "repo"
    assert L.history(repo)[0]["proposal"] == row["proposal"]


def test_applying_twice_is_refused(repo):
    """The second application would record a change that changed nothing, and
    leave an undo pointing at the wrong bytes."""
    L.apply(repo, a_proposal(repo), "r1", "someone", skip_gates=True)
    with pytest.raises(L.LearningError) as exc:
        L.apply(repo, a_proposal(repo), "r1", "someone", skip_gates=True)
    assert "already applied" in str(exc.value)


def test_undo_restores_the_bytes_and_the_audit_shows_BOTH(repo):
    """The M5 kill gate in one test: *reverts via `--undo` (audit shows both)*.
    An undo that deleted its own application would leave a record showing
    neither."""
    before = (repo / ".sdlc" / "profile.yml").read_text(encoding="utf-8")
    prop = a_proposal(repo)
    L.apply(repo, prop, "r1", "someone", skip_gates=True)
    L.undo(repo, prop["id"], "someone")
    assert (repo / ".sdlc" / "profile.yml").read_text(encoding="utf-8") == before
    rows = L.history(repo)
    assert [r["action"] for r in rows] == ["applied", "undone"]
    assert rows[1]["restored_sha"] == rows[0]["before_sha"]


def test_a_crlf_profile_keeps_its_line_endings_and_undo_restores_its_bytes(repo):
    """T511. `apply` read the profile with `read_text`, which folds CRLF to LF,
    and wrote it back as LF throughout - and the `before` copy it kept for
    `--undo` was that folded text, so undo put back LF, not the original bytes.
    The test above passed on Windows regardless: its fixture writes CRLF there
    and both of its reads fold, so it compared two foldings of different bytes.
    Bytes are compared here."""
    p = repo / ".sdlc" / "profile.yml"
    original = PROFILE.replace("\n", "\r\n").encode("utf-8")
    p.write_bytes(original)
    prop = a_proposal(repo)
    L.apply(repo, prop, "r1", "someone", skip_gates=True)
    changed = p.read_bytes()
    assert L.SUPPRESS_KEY.encode() in changed, "the change did not land"
    assert b"\n" not in changed.replace(b"\r\n", b""), "the profile was rewritten as LF"
    assert L._drift_mark(repo, L.history(repo)[0]) == "", \
        "a standing apply on a CRLF profile read as drifted"
    L.undo(repo, prop["id"], "someone")
    assert p.read_bytes() == original, "undo did not restore the original bytes"


def test_an_lf_profile_stays_lf_through_apply_and_undo(repo):
    """Rule-28 control for T511, in bytes so it holds on every platform."""
    p = repo / ".sdlc" / "profile.yml"
    original = PROFILE.encode("utf-8")
    p.write_bytes(original)
    prop = a_proposal(repo)
    L.apply(repo, prop, "r1", "someone", skip_gates=True)
    assert b"\r" not in p.read_bytes()
    L.undo(repo, prop["id"], "someone")
    assert p.read_bytes() == original


def test_undo_refuses_when_nothing_is_standing(repo):
    with pytest.raises(L.LearningError) as exc:
        L.undo(repo, "learn:nothing", "someone")
    assert "the audit is the authority" in str(exc.value)


def test_undo_records_drift_rather_than_refusing_it(repo):
    """Somebody editing the file by hand after a learned change landed is
    ordinary. Silently overwriting their work without saying so is not."""
    prop = a_proposal(repo)
    L.apply(repo, prop, "r1", "someone", skip_gates=True)
    p = repo / ".sdlc" / "profile.yml"
    p.write_text(p.read_text(encoding="utf-8") + "\nlint_command: \"x\"\n",
                 encoding="utf-8")
    row = L.undo(repo, prop["id"], "someone")
    assert row["drifted"] is True


def test_apply_undo_apply_is_standing_again(repo):
    prop = a_proposal(repo)
    L.apply(repo, prop, "r1", "someone", skip_gates=True)
    L.undo(repo, prop["id"], "someone")
    L.apply(repo, prop, "r2", "someone", skip_gates=True)
    assert L.standing(L.history(repo), prop["id"]) is not None
    assert [r["action"] for r in L.history(repo)] == ["applied", "undone", "applied"]


def test_a_failed_audit_append_does_not_strand_a_profile_change(repo, monkeypatch):
    """T232 - apply must not leave the profile changed unless the audit undo
    reads is durably recorded first.

    `undo` treats the audit as the authority (*"not the file's current
    contents"*), and the `before` bytes it restores live ONLY in the applied
    row. So if the profile is written before the audit and the append then
    fails, the change is stranded: `undo` finds no standing entry and refuses,
    and the bytes it would have restored were never persisted. The audit is
    therefore written first, and a failed append leaves the file as it was.
    Measured reachable: a disk-full / permission / path-collision at the append
    (here a stand-in that raises) with the profile already on disk.
    """
    prop = a_proposal(repo)
    profile = repo / ".sdlc" / "profile.yml"
    before = profile.read_text(encoding="utf-8")

    def boom(*_a, **_k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(L, "append_log", boom)
    with pytest.raises(OSError):
        L.apply(repo, prop, "r1", "someone", skip_gates=True)

    # The audit is empty, so undo is blind to any change - it can only refuse.
    assert L.history(repo) == []
    with pytest.raises(L.LearningError):
        L.undo(repo, prop["id"], "someone")
    # Which is only safe if the profile was never changed. A difference here is
    # exactly the stranded change the ordering exists to prevent.
    assert profile.read_text(encoding="utf-8") == before


def test_a_failed_restore_during_undo_is_not_swallowed(repo):
    """**T380 window A - a failed profile RESTORE must raise, not be swallowed.**

    undo is write-first (restore, then append). If the restore fails - a
    read-only profile - undo must raise (could-not-look, exit 3), never swallow
    it and append a clean `undone`. Swallowing would record the change as
    reverted while the file is still suppressing findings, which is the
    permanently-stranded state the write-first ordering exists to avoid. Here
    the residue is safe: the file still suppresses AND the audit still shows it
    standing (they agree), and a retry once writable reconciles. Closes the
    surviving mutant that wrapped the restore in a swallowing try/except.
    """
    prop = a_proposal(repo)
    profile = repo / ".sdlc" / "profile.yml"
    L.apply(repo, prop, "r1", "someone", skip_gates=True)
    applied_text = profile.read_text(encoding="utf-8")
    assert L.SUPPRESS_KEY in applied_text                 # premise: it landed
    os.chmod(profile, 0o444)                              # make the restore fail
    try:
        with pytest.raises(OSError):
            L.undo(repo, prop["id"], "someone")
    finally:
        os.chmod(profile, 0o644)
    # Nothing swallowed: file still suppressing, audit still standing - agree.
    assert profile.read_text(encoding="utf-8") == applied_text
    assert [r["action"] for r in L.history(repo)] == ["applied"]
    assert L.standing(L.history(repo), prop["id"]) is not None
    # And a retry once writable reconciles at exit 0.
    L.undo(repo, prop["id"], "someone")
    assert L.SUPPRESS_KEY not in profile.read_text(encoding="utf-8")
    assert [r["action"] for r in L.history(repo)] == ["applied", "undone"]


def test_a_failed_append_during_undo_is_visible_and_recoverable(repo):
    """**T380 window B - a failed audit APPEND leaves a visible, recoverable
    residue, and pins write-first against a reordering to record-first.**

    With write-first, a restore that succeeds followed by an append that fails
    leaves the file reverted while the audit still shows the change standing.
    That is understatement, but T379's `--history` drift marker makes it VISIBLE
    and a retry reconciles at exit 0 - unlike record-first, which on this same
    failure would leave the file still suppressing with the audit already
    calling it undone. Reordering to append-first would leave the file
    unreverted here (append fails before the restore runs), so this test is red
    against that wrong direction and green on write-first. A read-only audit log
    is the real failed append.
    """
    prop = a_proposal(repo)
    profile = repo / ".sdlc" / "profile.yml"
    before = profile.read_text(encoding="utf-8")
    L.apply(repo, prop, "r1", "someone", skip_gates=True)
    log = repo / L.LOG
    os.chmod(log, 0o444)                                  # make the append fail
    try:
        with pytest.raises(OSError):
            L.undo(repo, prop["id"], "someone")
    finally:
        os.chmod(log, 0o644)
    # write-first: the file WAS reverted; the audit was not.
    assert profile.read_text(encoding="utf-8") == before
    assert [r["action"] for r in L.history(repo)] == ["applied"]
    # T379's marker makes the mismatch visible through the door.
    proc, said = spawn_learning(repo, "--history")
    assert proc.returncode == 0 and "[drifted]" in said, said
    # And a retry reconciles at exit 0.
    L.undo(repo, prop["id"], "someone")
    assert [r["action"] for r in L.history(repo)] == ["applied", "undone"]


# ── the declaration is extended, never replaced ─────────────────────────────


def test_an_existing_declaration_is_extended_not_overwritten(repo):
    """A learned rule must never quietly drop a suppression somebody wrote."""
    p = repo / ".sdlc" / "profile.yml"
    p.write_text(p.read_text(encoding="utf-8")
                 + f'{L.SUPPRESS_KEY}: ["written-by-hand"]\n', encoding="utf-8")
    L.apply(repo, a_proposal(repo), "r1", "someone", skip_gates=True)
    text = p.read_text(encoding="utf-8")
    assert "written-by-hand" in text and "noisy-rule" in text


def test_rendering_is_idempotent_on_an_entry_already_present(repo):
    out = L.render(f'{L.SUPPRESS_KEY}: ["a"]\n', L.SUPPRESS_KEY, ["a"])
    assert out.count('"a"') == 1


# ── the suppression actually suppresses, and only what is declared ──────────


def test_a_declared_suppression_does_not_hold_the_loop_open(repo):
    f = {"id": "noisy-rule", "severity": "critical", "disposition": "",
         "source": "agent"}
    assert LP.assess([f], ["critical"], ["noisy-rule"])["open"] == []


def test_an_undeclared_finding_still_fires(repo):
    """**The property the mechanism lives by.** A suppression list that
    silenced anything it was not asked to would be an off switch."""
    f = {"id": "other-rule", "severity": "critical", "disposition": "",
         "source": "agent"}
    assert len(LP.assess([f], ["critical"], ["noisy-rule"])["open"]) == 1


def test_what_was_suppressed_is_counted_not_dropped(repo):
    """Zero silent drops. A loop that quietly stops reporting is the failure
    this project treats as worst."""
    f = {"id": "noisy-rule", "severity": "critical", "disposition": "",
         "source": "agent"}
    assert LP.assess([f], ["critical"], ["noisy-rule"])["suppressed"] == ["noisy-rule"]


def test_no_declaration_suppresses_nothing(repo):
    f = {"id": "noisy-rule", "severity": "critical", "disposition": "",
         "source": "agent"}
    assert len(LP.assess([f], ["critical"])["open"]) == 1
    assert LP.suppress_of(repo) == []


def test_the_loop_reads_the_declaration_from_the_profile(repo):
    p = repo / ".sdlc" / "profile.yml"
    p.write_text(p.read_text(encoding="utf-8")
                 + f'{L.SUPPRESS_KEY}: ["noisy-rule"]\n', encoding="utf-8")
    assert LP.suppress_of(repo) == ["noisy-rule"]


# ── the audit ───────────────────────────────────────────────────────────────


def test_a_holed_audit_is_refused(repo):
    (repo / L.LOG).parent.mkdir(parents=True, exist_ok=True)
    (repo / L.LOG).write_text('{"action": "applied"}\n{not json\n', encoding="utf-8")
    with pytest.raises(L.LearningError) as exc:
        L.history(repo)
    assert "cannot say what to undo" in str(exc.value)


def test_the_audit_lives_where_policy_already_protects_it(repo):
    """`learning_log.jsonl` was listed in `policy.yml` before anything existed
    to write it — so the audit could never be written by a run."""
    assert L.LOG.parts[:2] == (".sdlc", "metrics")
    text = (ROOT / "sdlc_templates" / "policy.yml").read_text(encoding="utf-8")
    assert "learning_log.jsonl" in text


# ── the CLI ─────────────────────────────────────────────────────────────────


def test_the_cli_proposes_nothing_when_nothing_groups(repo, capsys):
    assert L.main(["--target", str(repo), "--propose"]) == 0
    assert "0 proposal(s)" in capsys.readouterr().out


def test_the_cli_refuses_an_apply_with_no_run(repo, capsys):
    r1, r2 = a_run(repo), a_run(repo)
    reviewed(repo, r1, [("noisy-rule", "rejected")])
    reviewed(repo, r2, [("noisy-rule", "rejected")], started="2026-08-29T02:00:00+00:00")
    pid = L.propose(repo)[0]["id"]
    assert L.main(["--target", str(repo), "--apply", pid]) == 2
    assert "needs --run" in capsys.readouterr().err


def test_the_cli_refuses_an_unknown_proposal(repo, capsys):
    assert L.main(["--target", str(repo), "--apply", "learn:nope",
                   "--run", "r1"]) == 2
    assert "no current proposal" in capsys.readouterr().err


def test_the_cli_history_shows_both_directions(repo, capsys):
    prop = a_proposal(repo)
    L.apply(repo, prop, "r1", "someone", skip_gates=True)
    L.undo(repo, prop["id"], "someone")
    assert L.main(["--target", str(repo), "--history"]) == 0
    out = capsys.readouterr().out
    assert "applied" in out and "undone" in out


def test_history_marks_a_standing_apply_whose_profile_write_failed(repo):
    """**T379 - SPAWNED, red-first with a real failed profile write.**

    `apply` writes the audit row before the profile (T232's ordering), so a
    profile write that fails - here a read-only file - leaves the audit row
    `applied` and standing while the file never changed. `--history` read that
    row and printed a clean `applied`, telling a person a suppression stands
    when the write that would have made it stand never landed - could-not-look
    wearing the costume of a durable change. It now runs the same digest check
    `undo` does and marks the row. Spawned, because the door is what a person
    reads and an in-process call cannot stand in for its output.
    """
    prop = a_proposal(repo)
    profile = repo / ".sdlc" / "profile.yml"
    before = profile.read_text(encoding="utf-8")
    os.chmod(profile, 0o444)                       # make the profile write fail
    try:
        with pytest.raises(OSError):
            L.apply(repo, prop, "r1", "someone", skip_gates=True)
    finally:
        os.chmod(profile, 0o644)
    # The audit row landed; the file did not - the stranded state T379 is about.
    assert [r["action"] for r in L.history(repo)] == ["applied"]
    assert profile.read_text(encoding="utf-8") == before      # write never took

    proc, said = spawn_learning(repo, "--history")
    assert proc.returncode == 0, said
    assert "applied" in said
    assert "[drifted]" in said or "[not on disk]" in said, (
        "a standing apply whose profile write failed printed as a clean "
        f"'applied', hiding that the change never landed: {said!r}")


def test_history_leaves_a_genuinely_applied_row_unmarked(repo):
    """**The control for T379.** An apply whose file DID change prints a plain
    `applied` with no drift marker - the mark is for a stranded or drifted file
    only, never for the ordinary standing case."""
    prop = a_proposal(repo)
    L.apply(repo, prop, "r1", "someone", skip_gates=True)
    proc, said = spawn_learning(repo, "--history")
    assert proc.returncode == 0, said
    assert "applied" in said
    assert "[drifted]" not in said and "[not on disk]" not in said, said


def test_history_marks_a_standing_apply_whose_file_is_gone_as_not_on_disk(repo):
    """**Closes a surviving mutant: folding the not-on-disk marker to nothing.**
    A standing apply whose profile has since been deleted is not merely drifted,
    it is GONE, and the two states are different facts a reader should be able
    to tell apart. SPAWNED."""
    prop = a_proposal(repo)
    profile = repo / ".sdlc" / "profile.yml"
    L.apply(repo, prop, "r1", "someone", skip_gates=True)
    profile.unlink()
    proc, said = spawn_learning(repo, "--history")
    assert proc.returncode == 0, said
    assert "[not on disk]" in said, (
        f"a standing apply whose file was deleted must say so, not print a "
        f"clean 'applied': {said!r}")


def test_history_does_not_crash_on_a_corrupt_profile(repo):
    """**The guarded read - a regression the first cut of this fix introduced.**
    The drift check read the profile with the default strict decode, so a
    profile corrupted into non-UTF-8 bytes - exactly the drift the check exists
    to catch - crashed `--history` at exit 3 where the unfixed door exited 0.
    The read now decodes with `errors="replace"`, which cannot equal a digest of
    valid text, so a corrupt profile marks drift rather than killing the listing.
    SPAWNED, because a crash mid-listing is could-not-look (exit 3), which an
    in-process call cannot tell from a traceback."""
    prop = a_proposal(repo)
    profile = repo / ".sdlc" / "profile.yml"
    L.apply(repo, prop, "r1", "someone", skip_gates=True)
    profile.write_bytes(b"\xff\xfe\xfd not valid utf-8 \xff\x00")
    proc, said = spawn_learning(repo, "--history")
    assert proc.returncode == 0, (
        f"a corrupt profile must not crash the history listing; got exit "
        f"{proc.returncode} saying {said!r}")
    assert "[drifted]" in said, (
        f"a profile that no longer decodes cannot match its after_sha and must "
        f"read as drifted: {said!r}")


def test_history_marks_only_the_standing_apply_when_a_reapply_drifts(repo):
    """**Closes a surviving mutant: loosening `is row` to `is not None`.** After
    apply-undo-apply there are TWO applied rows for one proposal; only the last
    is standing, and only it is checked against the file. With the profile then
    drifted by hand, the standing row alone must be marked - `is not None` would
    match the earlier, non-standing apply too (its after_sha is identical) and
    print a second, wrong `[drifted]`. SPAWNED, asserting exactly one marker."""
    prop = a_proposal(repo)
    profile = repo / ".sdlc" / "profile.yml"
    L.apply(repo, prop, "r1", "someone", skip_gates=True)
    L.undo(repo, prop["id"], "someone")
    L.apply(repo, prop, "r2", "someone", skip_gates=True)
    profile.write_text(profile.read_text(encoding="utf-8") + "\nlint_command: \"x\"\n",
                       encoding="utf-8")
    proc, said = spawn_learning(repo, "--history")
    assert proc.returncode == 0, said
    assert said.count("[drifted]") == 1, (
        "only the standing apply should be drift-checked; a second marker means "
        f"a non-standing apply was checked too: {said!r}")


# ── the gates are COMPOSED into apply, not merely available ─────────────────


def approved_run(repo: Path) -> str:
    """A run carrying a real, live G3 through `approve.record`."""
    from tools.sdlc import approve
    run_id = ledger.create(repo, "requirement", "the learned rule")["run_id"]
    folder = ledger.run_dir(repo, run_id) / "40-impl"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "README.md").write_text("the learned rule\n", encoding="utf-8")
    ledger.record_artifact(repo, run_id, "40-impl/")
    ledger.advance(repo, run_id, phase="P4", note="implemented")
    approve.record(repo, run_id, "G3", "approve", "owner@example.test",
                   notes="read and approved")
    return run_id


def a_clean_corpus(repo: Path) -> None:
    """A replay that actually SCORED a case cleanly. Two subject files, so
    `seed_and_truth` spends one on the seed and leaves a non-empty truth, and the
    ranker (which returns both) finds it - so a measure is a real number, not
    None. An all-unmeasurable replay is could-not-look, NOT a clean gate (T294),
    so a genuinely clean gate has to have measured something."""
    from tools import corpus_replay as replay
    (repo / replay.CORPUS).mkdir(parents=True, exist_ok=True)
    (repo / replay.CORPUS / "aaaaaaaaaaaa.json").write_text(json.dumps({
        "schema_version": 1, "repo": "api", "sha": "a" * 40, "parent": "b" * 40,
        "date": "2026-08-01T00:00:00Z", "title": "t", "classes": ["scoping"],
        "files": ["a/b/Thing.java", "c/d/Other.java"]}), encoding="utf-8")
    replay.append(repo, replay.replay(
        repo, replay.cases(repo), [], repo, repo / "s", limit=1,
        ranker=lambda c, alias, seed: [(alias, f) for f in c["files"]], at="t1"))


def an_unmeasurable_corpus(repo: Path) -> None:
    """A replay that RAN but graded nothing: its one case has a single subject
    file, so `seed_and_truth` spends it on the seed and leaves an empty truth,
    and every measure is None. `replayed` is 1 and `measured` is 0 - the
    all-unmeasurable state T294 is about, distinct from `replayed == 0`."""
    from tools import corpus_replay as replay
    (repo / replay.CORPUS).mkdir(parents=True, exist_ok=True)
    (repo / replay.CORPUS / "cccccccccccc.json").write_text(json.dumps({
        "schema_version": 1, "repo": "api", "sha": "c" * 40, "parent": "d" * 40,
        "date": "2026-08-01T00:00:00Z", "title": "t", "classes": ["scoping"],
        "files": ["a/b/Only.java"]}), encoding="utf-8")
    replay.append(repo, replay.replay(
        repo, replay.cases(repo), [], repo, repo / "s", limit=1,
        ranker=lambda c, alias, seed: [(alias, f) for f in c["files"]], at="t1"))


def test_apply_consults_the_corpus_gate_even_with_a_live_G3(repo):
    """**Found by a mutation that survived every other test in this file.**
    `corpus_gate` was tested, `apply` was tested, and nothing composed them -
    so dropping the corpus gate out of `apply` reddened nothing. Producer and
    consumer each correct alone, which is the class this project keeps meeting.
    """
    run_id = approved_run(repo)
    assert L.gate_taken(repo, run_id) == []          # G3 really is live
    with pytest.raises(L.LearningError) as exc:
        L.apply(repo, a_proposal(repo), run_id, "someone")
    assert "corpus replay" in str(exc.value)


def test_apply_passes_once_both_gates_are_satisfied(repo):
    """The other half: the gates must be satisfiable, or they are a wall rather
    than a gate. Item 33's lesson - a contract nobody can meet."""
    run_id = approved_run(repo)
    a_clean_corpus(repo)
    row = L.apply(repo, a_proposal(repo), run_id, "owner@example.test")
    assert row["action"] == "applied" and row["run"] == run_id


def test_a_gate_voided_by_an_edited_artifact_is_not_an_approval(repo):
    """**The second surviving mutation.** `verify_artifacts_for` answers "is
    this still what was approved", and a learned change may not ride on a gate
    whose bytes have moved. Item 32's void, arriving where it matters most:
    the change that edits the repository's own rules."""
    run_id = approved_run(repo)
    assert L.gate_taken(repo, run_id) == []
    (ledger.run_dir(repo, run_id) / "40-impl" / "README.md").write_text(
        "something else entirely\n", encoding="utf-8")
    owed = L.gate_taken(repo, run_id)
    assert owed and "no longer speaks for what it approved" in owed[0]


def test_a_voided_gate_blocks_the_apply_too(repo):
    run_id = approved_run(repo)
    a_clean_corpus(repo)
    (ledger.run_dir(repo, run_id) / "40-impl" / "README.md").write_text(
        "something else entirely\n", encoding="utf-8")
    with pytest.raises(L.LearningError) as exc:
        L.apply(repo, a_proposal(repo), run_id, "someone")
    assert "no longer speaks" in str(exc.value)


# ── a replay that graded NOTHING is could-not-look, not a clean gate (T294) ──


def test_corpus_gate_refuses_a_replay_that_graded_nothing(repo):
    """**T294 (W187).** A replay in which EVERY case is unmeasurable returns no
    problems, so the corpus gate item 50 built - so a learned rule cannot ride on
    unproven ground - was satisfied by a run that scored nothing at all.
    `corpus_gate`'s own docstring says could-not-look is not a pass; this makes
    that true. `replayed == 0` was already refused by `problems`; this is its
    sibling one level in - replayed > 0 with every case unmeasurable."""
    an_unmeasurable_corpus(repo)
    owed = L.corpus_gate(repo)
    assert owed, "an all-unmeasurable replay graded nothing; that is not a clean gate"
    assert "measurabl" in owed[0] and "graded nothing" in owed[0], owed


def test_the_all_unmeasurable_refusal_is_not_the_no_replay_refusal(repo):
    """The two could-not-looks are different repairs and must not collapse into
    one message. `replayed == 0` (or no history) says nobody has run it; this one
    says it ran and scored nothing - the distinction item 50's row depends on."""
    an_unmeasurable_corpus(repo)
    owed = L.corpus_gate(repo)
    assert "nobody has run" not in owed[0], owed
    assert "scored" in owed[0], owed


def test_a_measured_clean_replay_still_passes_the_corpus_gate(repo):
    """The gate must stay SATISFIABLE (item 33). A replay that actually scored a
    case with no regression is clean and returns no problem - otherwise the new
    check is a wall, not a gate."""
    a_clean_corpus(repo)
    assert L.corpus_gate(repo) == []


def test_a_learned_change_cannot_land_on_a_replay_that_graded_nothing(repo):
    """**SPAWNED - the headline, because a learned change LANDING is the harm.**
    With a live G3 and a real proposal, the only thing between the change and the
    profile is the corpus gate; on a replay that graded nothing it let the change
    through and the file was edited. Could-not-look is exit 3, which an in-process
    call cannot tell from a traceback."""
    r1, r2 = a_run(repo), a_run(repo)
    reviewed(repo, r1, [("noisy-rule", "rejected")])
    reviewed(repo, r2, [("noisy-rule", "rejected")], started="2026-08-29T02:00:00+00:00")
    run_id = approved_run(repo)
    an_unmeasurable_corpus(repo)
    pid = L.propose(repo)[0]["id"]
    proc, said = spawn_learning(repo, "--apply", pid, "--run", run_id)
    assert proc.returncode == 3, \
        f"could-not-look is exit 3; got {proc.returncode}: {said!r}"
    assert "graded nothing" in said, said
    text = (repo / ".sdlc" / "profile.yml").read_text(encoding="utf-8")
    assert L.SUPPRESS_KEY not in text, \
        "the learned change landed on a replay that scored nothing"


# ── a suppression nobody learned is visible ─────────────────────────────────


def test_a_hand_written_suppression_is_visible_as_unbacked(repo):
    """**The hole the gates cannot close.** `profile.yml` is not protected and
    must not be — a run legitimately empties `known_failures` when the failure
    is fixed. So an agent could write `review_suppress` by hand and silence a
    finding with no corpus check, no gate and no audit line. Protecting the
    file breaks the legitimate case; making the difference visible does not."""
    p = repo / ".sdlc" / "profile.yml"
    p.write_text(p.read_text(encoding="utf-8")
                 + f'{L.SUPPRESS_KEY}: ["snuck-in"]\n', encoding="utf-8")
    owed = L.unbacked(repo)
    assert len(owed) == 1 and "snuck-in" in owed[0]
    assert "written by hand rather than learned" in owed[0]


def test_a_learned_suppression_is_accounted_for(repo):
    L.apply(repo, a_proposal(repo), "r1", "someone", skip_gates=True)
    assert L.unbacked(repo) == []


def test_an_undone_suppression_stops_backing_anything(repo):
    """After an undo the declaration is gone, so there is nothing to account
    for - and if somebody puts it back by hand, it is unbacked again."""
    prop = a_proposal(repo)
    L.apply(repo, prop, "r1", "someone", skip_gates=True)
    L.undo(repo, prop["id"], "someone")
    assert L.unbacked(repo) == []
    p = repo / ".sdlc" / "profile.yml"
    p.write_text(p.read_text(encoding="utf-8")
                 + f'{L.SUPPRESS_KEY}: ["noisy-rule"]\n', encoding="utf-8")
    assert len(L.unbacked(repo)) == 1


# ── item 129: the only WRITER of the profile read it its own way ────────────
#
# `learning.render`'s docstring has always promised that "an existing
# declaration is EXTENDED rather than replaced, because a learned rule must
# never quietly drop one somebody wrote". It matched
# `line.startswith(f"{key}:")` and parsed with `policy.parse`, which reads the
# INLINE spelling alone - so a block-form declaration yielded `existing = []`
# while its `- "x"` rows, which that walk never matched, survived into `kept`.
#
# Writing one new entry therefore DELETED every existing suppression and left
# its items dangling under the previous key. Item 118 moved the consumers to
# both spellings and the producer was not moved with them.

NEWLINE = chr(10)

BLOCK = ('scope: repo\nreview_suppress:\n  - "a"\n  - "b"\n'
         'gate_profile: solo\n')
INLINE = 'scope: repo\nreview_suppress: ["a", "b"]\ngate_profile: solo\n'
ABSENT = 'scope: repo\ngate_profile: solo\n'


def _read_back(tmp_path: Path, text: str, key: str):
    """What the CONSUMERS see after the writer has written.

    `loop.py` and `learning.unbacked` both read this key through
    `policy.profile_list`, so that is what the assertion asks - not the
    writer's own idea of what it wrote.
    """
    from tools.sdlc import policy as guardrails
    root = tmp_path / f"back-{abs(hash(text)) % 10000}"
    (root / ".sdlc").mkdir(parents=True, exist_ok=True)
    (root / ".sdlc" / "profile.yml").write_text(text, encoding="utf-8")
    return guardrails.profile_list(root, key)


@pytest.mark.parametrize("before,expected", [
    (BLOCK, ["a", "b", "c"]),
    (INLINE, ["a", "b", "c"]),
    (ABSENT, ["c"]),
])
def test_render_extends_every_spelling_a_reader_accepts(tmp_path, before, expected):
    """**The crossing this defect existed for want of.** render had tests and
    profile_list had tests and nothing ran one THROUGH the other - so the
    assertion is what the consumers read back, not what the writer believes."""
    after = L.render(before, "review_suppress", ["c"])
    assert _read_back(tmp_path, after, "review_suppress") == expected


def test_render_leaves_no_orphaned_list_items(tmp_path):
    """The wreck in one assertion. The old walk kept the `- \"a\"` rows because
    it only ever matched the `key:` line, so they ended up dangling under
    whatever key happened to precede the declaration."""
    after = L.render(BLOCK, "review_suppress", ["c"])
    assert '- "a"' not in after and '- "b"' not in after
    assert after.splitlines()[1] == "gate_profile: solo", (
        "a list item was left behind under the previous key")


def test_render_refuses_a_declaration_it_cannot_extend():
    """**Item 117's rule, in the one place that WRITES the governing document.**
    A key that is present and is not a list cannot be extended without dropping
    it, and a writer that guesses corrupts the file it was asked to improve."""
    with pytest.raises(L.LearningError) as caught:
        L.render('scope: repo\nreview_suppress: notalist\n',
                 "review_suppress", ["c"])
    assert "is not a list" in str(caught.value)
    assert "fix the declaration by hand" in str(caught.value)


def test_the_writer_asks_the_readers_reader(tmp_path):
    """Composed, not merely equal. `policy.list_from_text` IS `profile_list`'s
    body, so what the writer believes is declared cannot diverge from what the
    readers will find - which is the property, rather than the two of them
    happening to agree on the cases somebody thought to test."""
    from tools.sdlc import policy as guardrails
    for text in (BLOCK, INLINE, ABSENT):
        assert (guardrails.list_from_text(text, "review_suppress")
                == _read_back(tmp_path, text, "review_suppress"))


def test_the_writer_strips_comments_the_way_its_readers_do(tmp_path):
    """**A mutation survivor, and a real gap.** `list_from_text` comment-strips
    exactly the way `_profile_source` does, and nothing proved it: a
    declaration carrying a trailing note would otherwise parse one way for the
    writer and another for every reader, which is the whole class this item is
    about arriving through punctuation instead of through indentation."""
    commented = ('scope: repo' + NEWLINE
                 + 'review_suppress: ["a"]  # keep this one, it is noisy'
                 + NEWLINE + 'gate_profile: solo' + NEWLINE)
    after = L.render(commented, "review_suppress", ["c"])
    assert _read_back(tmp_path, after, "review_suppress") == ["a", "c"], (
        "the writer read a commented declaration differently from its readers")


def test_a_learned_rule_never_silently_drops_one_somebody_wrote(tmp_path):
    """The docstring's own promise, asserted. Two suppressions a human placed
    by hand survive a learned third - and the consequence of them not surviving
    is that findings a person deliberately silenced start firing again with no
    audit row, which is the invisibility `unbacked` exists to prevent."""
    after = L.render(BLOCK, "review_suppress", ["c"])
    read = _read_back(tmp_path, after, "review_suppress")
    assert "a" in read and "b" in read, "a human's declaration was dropped"


# ── item 146: the writer locates a declaration with the readers' own walk ───

#: The spellings `policy` reads and `learning.render` had to rewrite. Every one
#: of them is legal YAML that PyYAML and both harness readers already agreed
#: about BEFORE the render - which is what makes the writer the odd one out.
SPELLINGS = {
    "plain block list":
        'gate_profile: team\nreview_suppress:\n  - "a"\n  - "b"\n'
        'phases: [implementation]\n',
    "a comment between two items":
        'gate_profile: team\nreview_suppress:\n  - "a"\n'
        '  # keep b, it is noisy\n  - "b"\nphases: [implementation]\n',
    "a blank line between two items":
        'gate_profile: team\nreview_suppress:\n  - "a"\n\n  - "b"\n'
        'phases: [implementation]\n',
    "a space before the colon":
        'gate_profile: team\nreview_suppress : ["a"]\nphases: [implementation]\n',
    "a space before the colon, block form":
        'gate_profile: team\nreview_suppress :\n  - "a"\nphases: [implementation]\n',
}


def _declared_once(text: str, key: str) -> int:
    """How many times `key` is declared at the top level of this document.

    Counted here rather than asked of the reader on purpose: the reader answers
    about the FIRST declaration and PyYAML about the LAST, and the defect was
    exactly that a second one appeared and they stopped agreeing.
    """
    return sum(1 for line in text.splitlines()
               if line[:1].strip() and line.partition(":")[0].strip() == key)


#: The CHARACTER axis. **Item T229, and the guard below is why the row exists.**
#: Its assertion — `yaml.safe_load(after)` must not raise — was already exactly
#: right, and it was parametrised over the SPELLING of the document with the
#: added values `a`, `b`, `c`, so no input it could be given was able to violate
#: it. A guard on the wrong DIMENSION is item 115 one turn further in: not a
#: guard nobody watches fail, but one nothing can make fail. Every value here is
#: an ordinary scanner finding, not an attack.
CHARACTERS = {
    "a plain rule": "py/lint",
    "a double quote": 'semgrep: use of "eval"',
    "a trailing backslash": "ruff\\",
    "a backslash before a quote": 'bandit: a\\"b',
    "a quote then a hash": 'semgrep: "x" #2',
    "a quote then a comma": 'semgrep: "a","b"',
    # **Item T238.** These two carry the row's `" #` and `","` sequences where an
    # escaped quote unbalances the walker scan, so they were REFUSED before
    # `policy._strip_comment`/`_split_items` honoured the backslash - the danger
    # shape the even-quote values above happened to dodge.
    "an escaped quote then a hash": 'a" #b',
    "an escaped quote then a comma": 'a","b',
    "a colon and a space": "bandit: B108 hardcoded tmp",
    "a hash": "ruff: E501 #noqa",
    "a bracket": "semgrep: a] , [b",
}


@pytest.mark.parametrize("added", sorted(CHARACTERS))
@pytest.mark.parametrize("spelling", sorted(SPELLINGS))
def test_a_learned_rule_leaves_a_profile_every_reader_still_agrees_about(
        spelling, added):
    """**Item 146, and the harm is the document rather than the rule.**

    `learning.render` is the only writer of `.sdlc/profile.yml` and it carried
    a locator of its own. Item 129 made the writer READ what the readers read
    and left the walk that decides WHERE the declaration sits restating the
    rule - so two spellings every reader handles destroyed the file.

    **Measured, before the fix**: a comment between two items ended the writer's
    walk, so the rows below were left orphaned at top level and PyYAML raised
    `ParserError` on the result - the profile was no longer valid YAML, and
    because item 117 records an unreadable reference reader as a printed LIMIT
    rather than a refusal, the harness carried on. A space before the colon made
    the writer find NOTHING, so a SECOND declaration was appended and the two
    readers then disagreed about which one counts - `ProfileError`, refusing the
    run at all of this reader's call sites. A learned rule bricked the profile
    it was written into.
    """
    before = SPELLINGS[spelling]
    existing = yaml.safe_load(before)["review_suppress"]
    value = CHARACTERS[added]

    after = L.render(before, "review_suppress", [value])

    parsed = yaml.safe_load(after)          # must not raise: still valid YAML
    assert parsed["review_suppress"] == existing + [value], after
    assert PL.list_from_text(after, "review_suppress") == existing + [value], after
    assert parsed["review_suppress"] == PL.list_from_text(after, "review_suppress")
    assert _declared_once(after, "review_suppress") == 1, (
        "the key is declared twice, so the readers disagree about which "
        "one counts: " + after)


def test_a_note_somebody_wrote_survives_the_rewrite():
    """The stated limit, pinned. A comment inside the declaration is not part
    of it and is not removed with it, so it stays where its author put it even
    though the declaration has moved to the end of the file. Deleting a human's
    note to tidy up is the one thing this module's docstring forbids."""
    before = SPELLINGS["a comment between two items"]
    after = L.render(before, "review_suppress", ["c"])
    assert "# keep b, it is noisy" in after, after


def test_the_writer_asks_the_readers_where_a_declaration_sits(monkeypatch):
    """**The composition, which is the gap this project finds more often than
    any other.** `policy.declared_lines` has tests and `render` has tests, and
    what matters is that the writer ASKS rather than walking the lines itself -
    which is exactly what it did before. Blinding the locator must change what
    render writes; if it does not, `render` is answering for itself again.
    """
    monkeypatch.setattr(PL, "declared_lines", lambda lines, key: set())
    after = L.render(SPELLINGS["plain block list"], "review_suppress", ["c"])
    assert _declared_once(after, "review_suppress") == 2, (
        "render did not consult the locator at all: " + after)
# -- item T229: what the character axis is guarding, through the real readers --


@pytest.mark.parametrize("value", ["first\nsecond: injected", "a\rb",
                                   "trailing\n", "first\u0085second"])
def test_a_value_with_a_line_break_is_refused_rather_than_written(value):
    """**Measured, and the measurement decided this.** There is no spelling of
    a line break both readers of this profile return: PyYAML reads a raw
    newline inside a flow scalar as a FOLD and hands back `first second`, and
    its own emitter writes the multi-line form, which `policy.parse` cannot
    read at all. Encoding one would therefore be inventing a shape nothing
    here reads, so the writer refuses and says which value and why.
    """
    with pytest.raises(L.LearningError) as caught:
        L.render(SPELLINGS["plain block list"], "review_suppress", [value])
    assert "refused rather than written" in str(caught.value), caught.value


def test_the_refusal_says_which_character_when_the_weld_would_only_say_they_disagreed():
    """**The weld found this one, and that is the point of the test.**

    U+0085 (NEL) is a YAML line break PyYAML folds exactly as it folds `\\n` -
    measured: it hands back `first second`, and the stdlib reader answers
    `None` on the same document. The explicit set in `scalar` had not thought
    of it, and `render` refused anyway, because it reads its own output back
    through both readers. So the OUTCOME was already right and only the
    MESSAGE was poor: a person met the readers-disagree sentence and was not
    told which character did it.

    Both halves are pinned here. The refusal must now name the character, and
    the weld must still be what catches a character nobody listed - so the
    test also feeds a value the set still does not name (U+000B, which PyYAML
    refuses outright with a `ReaderError`) and requires a refusal for it too.
    """
    with pytest.raises(L.LearningError) as caught:
        L.render(SPELLINGS["plain block list"], "review_suppress",
                 ["first\u0085second"])
    said = str(caught.value)
    assert "a next line" in said, said
    assert "refused rather than written" in said, said

    # The backstop, unreplaced: a line break the set does NOT name is still
    # refused, by the weld, with the weld's own sentence.
    with pytest.raises(L.LearningError) as unlisted:
        L.render(SPELLINGS["plain block list"], "review_suppress", ["a\vb"])
    assert "do not read review_suppress back" in str(unlisted.value), unlisted


@pytest.mark.parametrize("ch,name", [("\u2028", "a line separator"),
                                      ("\u2029", "a paragraph separator")])
def test_unicode_line_separator_refusal_states_the_measured_reason(ch, name):
    """**T240 - the premise was false and the reason is corrected.**

    ``scalar`` refused U+2028 and U+2029, and its error message blamed PyYAML
    folding - but W144 measured PyYAML returning both **verbatim**, not folded.
    The REAL reason both characters break the round-trip is that Python's
    ``str.splitlines()`` treats them as line boundaries, so the stdlib reader
    (``list_from_text``) splits the value across lines and corrupts it.

    Both halves are pinned here: the refusal must name ``splitlines`` as the
    reason, and the control below proves the corruption is real.
    """
    with pytest.raises(L.LearningError) as caught:
        L.scalar(f"first{ch}second")
    said = str(caught.value)
    # The refusal must name the real reason, not the false PyYAML-folding one.
    assert "splitlines" in said, (
        f"the refusal for {name} should mention splitlines, got: {said}")
    assert name in said, said
    assert "refused rather than written" in said, said

    # Control: demonstrate that str.splitlines() IS the mechanism.
    profile = f'test_key: ["first{ch}second"]'
    lines = profile.splitlines()
    assert len(lines) > 1, (
        f"{name} should make splitlines produce multiple lines but got "
        f"{len(lines)}")

    # The mechanism: splitlines is a Python definition, not a runtime
    # accident, so this stays true regardless of reader changes.
    parts = f"first{ch}second".splitlines()
    assert len(parts) == 2 and parts[0] == "first" and parts[1] == "second", (
        f"str.splitlines() should split on {name} but got {parts!r}")


def test_the_yaml_escape_spelling_of_a_line_break_is_read_differently_too():
    """**The third leg of `scalar`'s argument, measured rather than asserted.**

    `scalar` refuses a line break, and its stated reason ran on the raw-newline
    FOLD and on PyYAML's own emitter form. A reader who knows YAML proposes the
    remaining spelling next: the `\\n` ESCAPE inside a double-quoted scalar,
    which is legal and which nothing above rules out. It fails as well, and for
    a different reason from either - PyYAML decodes it to a real newline while
    `policy._scalar` decodes `\\"` and `\\\\` only, so the hand reader hands
    back the two literal characters.

    Green by design: this pins a fact about the two READERS, not a branch of
    this module, and it is what makes the docstring's `no spelling survives
    both` exhaustive rather than a claim about the two spellings it lists.
    """
    doc = ('gate_profile: team\n'
           'review_suppress: ["first\\nsecond: injected"]\n')
    by_hand = PL.list_from_text(doc, "review_suppress")
    by_pyyaml = yaml.safe_load(doc)["review_suppress"]

    assert by_hand == ["first\\nsecond: injected"], by_hand   # two characters
    assert by_pyyaml == ["first\nsecond: injected"], by_pyyaml  # one newline
    assert by_hand != by_pyyaml, (
        "the readers agree about the escape spelling after all, so scalar's "
        "reason for refusing a line break outright needs re-measuring")

    # And the escaping this module DOES emit is not that spelling: a value
    # holding a backslash and an `n` round-trips through both readers.
    rendered = L.render(SPELLINGS["plain block list"], "review_suppress",
                        ["ruff: no\\name"])
    assert PL.list_from_text(rendered, "review_suppress") == \
        yaml.safe_load(rendered)["review_suppress"] == \
        ["a", "b", "ruff: no\\name"], rendered


@pytest.mark.parametrize("added", sorted(CHARACTERS))
def test_the_real_readers_answer_correctly_about_a_rendered_profile(
        tmp_path, added):
    """**The composition, not `yaml.safe_load` alone.** A round-trip that
    satisfies PyYAML and not the hand reader is this project's oldest defect
    class, so the rendered bytes go to disk and the three readers a run
    actually calls are asked: `policy.profile_list`, `policy.profile_limits`
    and `review.suppress_of`.

    `profile_limits` is the one the P1 turned on. Before the fix a `"` made it
    answer *PyYAML cannot read this file ... so its shape was not cross-checked*
    — the SECOND independent statement of shape disarmed, permanently, because
    re-rendering the wreck reproduces it byte for byte.
    """
    value = CHARACTERS[added]
    before = SPELLINGS["plain block list"]
    after = L.render(before, "review_suppress", [value])

    (tmp_path / ".sdlc").mkdir()
    (tmp_path / ".sdlc" / "profile.yml").write_text(
        after, encoding="utf-8", newline="\n")

    assert PL.profile_list(tmp_path, "review_suppress") == ["a", "b", value]
    assert PL.profile_list(tmp_path, "phases") == ["implementation"]
    assert LP.suppress_of(tmp_path) == ["a", "b", value]
    limits = PL.profile_limits(tmp_path)
    assert not any("cross-check" in line or "PyYAML" in line
                   for line in limits), limits
    assert L.render(after, "review_suppress", [value]) == after, (
        "re-rendering is not idempotent, so a second rule would move the file")


def test_a_finding_carrying_an_escaped_hash_or_comma_is_no_longer_refused():
    """**Item T238 - the refusal narrows to what genuinely cannot round-trip.**

    Before this item `policy._strip_comment` and `_split_items` tracked quotes
    without honouring the backslash `scalar` emits, so a finding carrying `" #`
    or `","` was REFUSED by the weld even though PyYAML read it back correctly -
    a legitimate suppression that could not be written. Now it round-trips
    through both readers; a genuinely unencodable value (a line break) is still
    refused, so the narrowing is exactly to what round-trips and no wider.
    """
    before = SPELLINGS["plain block list"]
    for value in ('a" #b', 'a","b'):
        after = L.render(before, "review_suppress", [value])
        assert PL.list_from_text(after, "review_suppress")[-1] == value, after
        assert yaml.safe_load(after)["review_suppress"][-1] == value, after
    # Not a widening: a true line break has no spelling both readers return and
    # is still refused.
    with pytest.raises(L.LearningError):
        L.render(before, "review_suppress", ["first\nsecond"])


def test_a_quoted_rule_somebody_wrote_by_hand_survives_an_unrelated_rule(
        tmp_path):
    """**The case a user actually meets, with nothing adversarial in it.**

    A human writes `- 'semgrep: use of "eval"'` — valid YAML, read back clean
    by every reader. Then an ordinary UNRELATED learned rule is applied, and
    before the fix the writer re-emitted that hand-written value unescaped and
    the document stopped being readable. No hostile input, no odd spelling:
    one quote in a finding and any second rule.
    """
    hand = ('gate_profile: team\n'
            'review_suppress:\n'
            "  - 'semgrep: use of \"eval\"'\n")
    (tmp_path / ".sdlc").mkdir()
    profile = tmp_path / ".sdlc" / "profile.yml"
    profile.write_text(hand, encoding="utf-8", newline="\n")
    quoted = 'semgrep: use of "eval"'
    assert LP.suppress_of(tmp_path) == [quoted], "the premise: it reads clean"
    assert PL.profile_limits(tmp_path) == []

    after = L.render(hand, "review_suppress", ["py/lint"])
    profile.write_text(after, encoding="utf-8", newline="\n")

    assert LP.suppress_of(tmp_path) == [quoted, "py/lint"], after
    assert PL.profile_limits(tmp_path) == [], after
    assert yaml.safe_load(after)["review_suppress"] == [quoted, "py/lint"]


def test_the_writer_reads_its_own_output_back_before_handing_it_over(
        monkeypatch):
    """**The weld.** Escaping is a CLAIM about what the readers decode, and a
    producer and a consumer each tested and never composed is the failure this
    repository finds more often than any other. So `render` reads its own
    bytes back with the reader every caller uses. Break the encoder and the
    writer must refuse rather than hand over a document meaning something else.
    """
    monkeypatch.setattr(L, "scalar", lambda v: '"' + v + '"')
    with pytest.raises(L.LearningError) as caught:
        L.render(SPELLINGS["plain block list"], "review_suppress",
                 ['semgrep: use of "eval"'])
    assert "do not read review_suppress back" in str(caught.value), caught.value
def test_both_readers_are_asked_and_either_one_can_refuse(monkeypatch):
    """**The M6 gap, and it is the composition again.** The weld is worth
    exactly what it ASKS. Dropping the stdlib half left every test green,
    because the two readers agree on all nine characters above - so nothing
    watched that half be consulted. Blind each in turn: render must refuse.

    Which half matters is not academic. The stdlib reader is the one that runs
    where PyYAML is absent, and PyYAML is the one that catches the P1 - fed the
    unescaped form, `policy.list_from_text` hands back the right list while
    PyYAML refuses the file. Either alone is a reader that cannot see the other
    one's failure.
    """
    good = SPELLINGS["plain block list"]
    monkeypatch.setattr(PL, "list_from_text", lambda text, key: ["wrong"])
    with pytest.raises(L.LearningError) as caught:
        L.render(good, "review_suppress", ["c"])
    assert "reads back as ['wrong']" in str(caught.value), caught.value
    monkeypatch.undo()

    monkeypatch.setattr(L, "_pyyaml_list", lambda decl, key: ["wrong too"])
    with pytest.raises(L.LearningError) as caught:
        L.render(good, "review_suppress", ["c"])
    assert "reads back as ['wrong too']" in str(caught.value), caught.value


def test_pyyaml_missing_is_not_pyyaml_agreeing(monkeypatch):
    """**Item 52's distinction, in the writer.** A reader that could not be
    ASKED and a reader that said yes are different facts. With PyYAML absent
    the stdlib reader still decides alone and the rule still lands; what must
    not happen is the absence being read as a second agreement.
    """
    import builtins
    real = builtins.__import__

    def refuse(name, *a, **k):
        if name == "yaml":
            raise ImportError("no yaml here")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", refuse)
    assert L._pyyaml_list('review_suppress: ["a"]\n', "review_suppress") is L._ABSENT
    after = L.render(SPELLINGS["plain block list"], "review_suppress", ["c"])
    monkeypatch.undo()
    assert PL.list_from_text(after, "review_suppress") == ["a", "b", "c"], after


# ── absent is could-not-look, not "nothing to learn" ────────────────────────


def spawn_learning(target: Path, *extra: str):
    """The real door, spawned - a CLI's contract is a process, and `3` is
    could-not-look, which an in-process call cannot tell from a traceback."""
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "sdlc" / "learning.py"),
         "--target", str(target), *extra],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180)
    return proc, (proc.stdout or "") + (proc.stderr or "")


def test_an_absent_target_is_could_not_look_not_zero_proposals(tmp_path):
    """**SPAWNED. Absent is not "nothing to learn" (T230).** A `--target` that
    does not exist read through `feedback.group_corrections` as an empty record,
    so the door printed `0 proposal(s)` at exit 0 - the same measured-zero
    `defect.py` and `adr.py` refuse. It is could-not-look at the whole target, so
    the door names it and exits 3, exactly as it does for an UNREADABLE profile."""
    absent = tmp_path / "no-such-dir"                    # never created
    proc, said = spawn_learning(absent)
    assert proc.returncode == 3, \
        f"could-not-look is exit 3; got {proc.returncode} saying {said!r}"
    assert "does not exist" in said and absent.name in said, \
        f"the door did not name the absent target: {said!r}"
    assert "proposal(s)" not in said, \
        f"an absent target was answered as zero proposals: {said!r}"
