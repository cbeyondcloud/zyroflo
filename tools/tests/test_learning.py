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
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.review import hitl  # noqa: E402
from tools.review import loop as LP  # noqa: E402
from tools.sdlc import feedback as FB  # noqa: E402
from tools.sdlc import learning as L  # noqa: E402
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
    from tools import corpus_replay as replay
    (repo / replay.CORPUS).mkdir(parents=True, exist_ok=True)
    (repo / replay.CORPUS / "aaaaaaaaaaaa.json").write_text(json.dumps({
        "schema_version": 1, "repo": "api", "sha": "a" * 40, "parent": "b" * 40,
        "date": "2026-08-01T00:00:00Z", "title": "t", "classes": ["scoping"],
        "files": ["a/b/Thing.java"]}), encoding="utf-8")
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
