"""Item 36 — the stage contract: what a phase receives, and what it must leave.

The tests are mostly about three ways a contract stops being one. It can drift
from the spec it claims to implement, so `STAGES` is checked against
`LIFECYCLE.md` row by row. It can be satisfiable by doing nothing, so an empty
artifact and an unrecorded one are distinguished from a missing one. And it can
name a value nobody supplies, which is the defect `RUNNER_CONTRACT.md` §9
records twice — a prompt that omits a value a CLI demands produces a runner
that behaves perfectly and cannot finish.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import approve as A  # noqa: E402
from tools.sdlc import stage as ST  # noqa: E402
from tools.sdlc import state as L  # noqa: E402
from tools.tests import _runs as R  # noqa: E402

WHO = "dev@example.invalid"
SOLO = ("phases: [intake, requirements, design, implementation, test, review, "
        "triage, closeout]\n")

def cli_sources() -> list[Path]:
    """Every shipped module that parses arguments — DERIVED, never listed.

    This was a hand-written roster of fifteen paths, and a roster is the shape
    this project keeps finding to be a defect. Five CLIs had been added since it
    was written — `defect`, `claims`, `corpus_replay`, `learning`, `validate` —
    so every flag any of them accepts read as a flag NO CLI accepts. **The guard
    was getting weaker with each one, silently**, and it surfaced only when an
    SOP finally cited one of the missing flags.

    Derived the way `test_cli._shipped` derives the same population, and for the
    same reason: the filesystem is the authority on what ships, and a list
    maintained beside it is a second answer that goes stale on its own.
    """
    return [p for p in sorted((ROOT / "tools").rglob("*.py"))
            if "tests" not in p.parts and "__pycache__" not in p.parts
            and "argparse.ArgumentParser" in p.read_text(encoding="utf-8")]
PREFIX_FLAGS = {"--project", "--porcelain"}

CONTRACT = re.compile(
    r"^\*\*Phase\*\* `(?P<phase>P\d+)` · \*\*produces\*\* (?P<produces>.+?) · "
    r"\*\*gate\*\* (?P<gate>`G\d`|none) · \*\*active\*\* (?P<active>.+)$", re.M)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / ".sdlc").mkdir(parents=True)
    (r / ".sdlc" / "profile.yml").write_text(
        "stack: python\ngate_profile: solo\n" + SOLO +
        'test_command: "pytest -q"\nreview:\n  block_on: [critical, high]\n',
        encoding="utf-8")
    subprocess.run(["git", "-C", str(r), "init", "-q"], check=True,
                   capture_output=True, timeout=120)
    return r


def sop_text(stage) -> str:
    return ST.sop_path(stage, ROOT).read_text(encoding="utf-8")


# ── the table is the spec's, not a second opinion ────────────────────────────


def test_every_phase_in_the_spec_has_a_stage_and_the_reverse():
    spec = (ROOT / "sdlc" / "LIFECYCLE.md").read_text(encoding="utf-8")
    rows = re.findall(r"^\| (\d+) \| `(\w+)` \|", spec, re.M)
    assert len(rows) == 12, f"LIFECYCLE.md lists {len(rows)} phases, not 12"
    from_spec = [(f"P{n}", name) for n, name in rows]
    from_table = [(s.phase, s.name) for s in ST.STAGES]
    assert from_spec == from_table, \
        f"the spec and the stage table disagree: {from_spec} vs {from_table}"


def test_every_stage_artifact_appears_in_the_spec_row_for_that_phase():
    """A produced artifact renamed in one place and not the other is the drift
    this pairing exists to make impossible."""
    spec = (ROOT / "sdlc" / "LIFECYCLE.md").read_text(encoding="utf-8").splitlines()
    for s in ST.STAGES:
        row = next(line for line in spec
                   if line.startswith(f"| {s.phase[1:]} | `{s.name}`"))
        for rel in s.produces:
            assert f"`{rel}`" in row, f"{s.phase} produces {rel}, absent from the spec row"


def test_the_gate_map_and_the_stage_table_are_inverses():
    """`approve.GATE_PHASE` and `stage.gate_of` are the same fact from two
    directions, and the approval CLI reads one while the SOPs read the other."""
    for gate, phase in A.GATE_PHASE.items():
        assert ST.gate_of(phase) == gate, f"{gate} stands at {phase}, which says " \
                                          f"{ST.gate_of(phase) or 'no gate'}"
    assert sorted(s.gate for s in ST.STAGES if s.gate) == sorted(A.GATE_PHASE)


def test_the_merge_gate_stands_at_a_phase_that_owes_something():
    """The hole this item found: P7 named no artifact, and it is where G4 —
    hard in every regime — stands. A gate over a phase that owes nothing is a
    yes about no bytes."""
    assert ST.by_phase(A.GATE_PHASE["G4"]).produces


def test_every_phase_owes_at_least_one_artifact():
    for s in ST.STAGES:
        assert s.produces, f"{s.phase} produces nothing checkable"


# ── the SOPs, and their currency ─────────────────────────────────────────────


@pytest.mark.parametrize("stage", ST.STAGES, ids=lambda s: s.phase)
def test_every_stage_ships_an_sop(stage):
    assert ST.sop_path(stage, ROOT).is_file(), \
        f"{stage.phase} points at {ST.sop_path(stage, ROOT)}, which does not ship"


@pytest.mark.parametrize("stage", ST.STAGES, ids=lambda s: s.phase)
def test_the_sop_contract_line_matches_the_stage_table(stage):
    """The SOP states its own contract in one machine-readable line, so prose
    and data cannot disagree about what a phase owes."""
    found = CONTRACT.search(sop_text(stage))
    assert found, f"{stage.phase}'s SOP has no parseable contract line"
    assert found["phase"] == stage.phase
    cited = set(re.findall(r"`([^`]+)`", found["produces"]))
    assert set(stage.produces) <= cited, \
        f"{stage.phase}'s SOP claims {cited}, the table says {stage.produces}"
    assert found["gate"] == (f"`{stage.gate}`" if stage.gate else "none")
    expected = {"": "always", "graph": "graph capability",
                "deploy": "deploy_command set", "probes": "probes configured"}
    assert found["active"] == expected[stage.needs]


@pytest.mark.parametrize("stage", ST.STAGES, ids=lambda s: s.phase)
def test_the_sop_says_what_the_phase_owes_and_how_to_record_it(stage):
    text = sop_text(stage)
    assert "## Steps" in text and "## Purpose" in text
    assert "state.py --target" in text and "--record" in text, \
        f"{stage.phase}'s SOP never says how to pin its artifact"


# ── item 128's RULING: this guard stays a union, and here is why ───────────
#
# Walk 3 found P2's SOP handing a runner a command it could not run, and the
# obvious repair was to strengthen this guard two ways: judge each flag against
# the CLI named ON ITS OWN LINE rather than against a union of all of them, and
# require every REQUIRED argument of a cited script to appear beside it.
#
# BOTH WERE MEASURED BEFORE BEING BUILT AND BOTH ARE REJECTED. Between them
# they produced four findings against the real tree and ALL FOUR ARE FALSE
# POSITIVES, every one from reading prose as a command line:
#
#   * P10 shows two commands inside ONE fenced block, so flattening it hands
#     `state.py`'s --record and --artifact to `verify.py`.
#   * P0's `intake.py --baseline` NAMES A MECHANISM inside a sentence. It omits
#     --target correctly, because it is not telling anybody to run it.
#   * P6's `hitl.py --finding <id> --source <s>` continues a command whose
#     --target sits two lines above it.
#
# That is item 100's and item 123's recorded cost arriving a third time: a
# reader that treats prose as a claim makes the DOCUMENTS worse, because the
# only way past a false positive is to write around it. `defect.py` filed seven
# false obligations that way and `adr.py` named three sibling artifacts as files
# a change would touch.
#
# So item 128 fixes the two P2 steps and gives `pack.py` a message its reader
# can act on, and this guard is left alone.
#
# WHAT WOULD OVERTURN THIS: a runnable-command GRAMMAR the SOPs actually
# declare - a fenced block marked as a command, say - so the reader reads a
# DECLARATION instead of guessing at prose. Every false positive above is
# unreachable under such a rule, and both strengthenings become worth building.


def test_item_128s_ruling_records_what_would_overturn_it():
    """A decision recorded without its reversal condition is one the next
    person has to re-derive - item 124's rule, which ruled two of its four
    observations into writing rather than into code for the same reason.

    The ruling is asserted here rather than trusted, because a comment nothing
    reads is exactly the class this project keeps finding.

    **The first cut of this test was VACUOUS and two mutations proved it.** It
    read the whole file, so every phrase it searched for was present in its own
    assertions and deleting the ruling changed nothing. Item 67's mirror shape,
    met again. It reads only the lines ABOVE itself now, which is the half of
    the file the ruling actually lives in.
    """
    lines = Path(__file__).read_text(encoding="utf-8").splitlines()
    here = next(i for i, ln in enumerate(lines)
                if ln.startswith("def test_item_128s_ruling_records"))
    ruling = chr(10).join(lines[:here])
    assert "ALL FOUR ARE FALSE" in ruling, "the measurement behind the ruling is gone"
    assert "WHAT WOULD OVERTURN THIS" in ruling, (
        "a ruling with no reversal condition is one the next person re-derives")
    assert "runnable-command GRAMMAR" in ruling


def test_every_flag_a_stage_sop_cites_exists_in_a_shipped_cli():
    """The currency guard. A flag documented in an SOP and accepted by no CLI
    is an instruction that fails for whoever follows it."""
    known = set(PREFIX_FLAGS)
    sources = cli_sources()
    # The guard's own guard, and it now has TWO parts. The flag assertion below
    # catches a broken EXTRACTION; this one catches a broken DISCOVERY, which is
    # the failure that actually happened - a roster five CLIs out of date still
    # extracted flags perfectly, from the wrong set of files.
    assert len(sources) >= 15, f"only {len(sources)} CLI(s) discovered"
    for cli in sources:
        src = cli.read_text(encoding="utf-8")
        known.update(re.findall(r'add_argument\("(--[a-z][a-z0-9-]*)"', src))
    assert {"--prompt", "--record", "--iterate", "--diff"} <= known, \
        "the extraction found nothing it should - the guard would pass vacuously"
    for s in ST.STAGES:
        cited = set(re.findall(r"--[a-z][a-z0-9-]*", sop_text(s)))
        assert not (cited - known), \
            f"{s.phase}'s SOP cites {sorted(cited - known)}, which no CLI accepts"


@pytest.mark.parametrize("stage", ST.STAGES, ids=lambda s: s.phase)
def test_no_machine_specific_paths_in_an_sop(stage):
    text = sop_text(stage)
    assert not re.search(r"[A-Za-z]:\\", text), f"{stage.phase} carries a drive path"
    assert not re.search(r"(/home/|/Users/)", text), f"{stage.phase} carries a home path"


def test_the_skill_names_every_phase_and_every_sop_it_names_ships():
    skill = (ROOT / "sdlc_templates" / "SKILL.md").read_text(encoding="utf-8")
    for s in ST.STAGES:
        assert f"| {s.phase} |" in skill, f"SKILL.md never lists {s.phase}"
    for cited in set(re.findall(r"`(P\d+-[a-z]+\.md)`", skill)):
        assert (ROOT / ST.SOP_DIR / cited).is_file(), \
            f"SKILL.md points at {cited}, which does not ship"


def test_the_skill_carries_the_harness_placeholder_the_installer_fills():
    """The SOPs are pointed at, never copied. An installed skill with a literal
    `@@HARNESS@@` in it points nowhere."""
    skill = (ROOT / "sdlc_templates" / "SKILL.md").read_text(encoding="utf-8")
    assert "@@HARNESS@@/sdlc/stages/" in skill
    init = (ROOT / "sdlc-init.py").read_text(encoding="utf-8")
    assert 'skill_body.replace("@@HARNESS@@"' in init, \
        "SKILL.md carries a placeholder the installer never fills"


def test_the_spec_no_longer_defers_this_wiring_to_a_plan_item():
    """`LIFECYCLE.md` said the wiring was 'tracked as plan item 36'. Leaving
    that sentence after doing it is how a document starts lying quietly."""
    spec = (ROOT / "sdlc" / "LIFECYCLE.md").read_text(encoding="utf-8")
    assert "until then" not in spec.lower()
    assert "| **here** |" not in spec, "the two-numbering mapping table survives"


# ── which phases run ─────────────────────────────────────────────────────────


def test_the_solo_default_is_the_one_the_spec_names(repo):
    (repo / ".sdlc" / "profile.yml").write_text("stack: python\n", encoding="utf-8")
    names, source = ST.declared(repo)
    assert names == list(ST.SOLO_DEFAULT) and "solo default" in source
    spec = (ROOT / "sdlc" / "LIFECYCLE.md").read_text(encoding="utf-8")
    assert f"`[{', '.join(ST.SOLO_DEFAULT)}]`" in spec


def test_a_phase_name_that_is_not_a_phase_is_refused_not_dropped(repo):
    (repo / ".sdlc" / "profile.yml").write_text(
        "phases: [intake, reveiw, closeout]\n", encoding="utf-8")
    with pytest.raises(ST.StageError, match="'reveiw'"):
        ST.declared(repo)


def test_an_empty_phase_list_is_refused(repo):
    (repo / ".sdlc" / "profile.yml").write_text("phases: []\n", encoding="utf-8")
    with pytest.raises(ST.StageError, match="not a shorter"):
        ST.declared(repo)


def test_order_is_the_specs_and_not_the_profiles(repo):
    (repo / ".sdlc" / "profile.yml").write_text(
        "phases: [closeout, review, intake]\n", encoding="utf-8")
    assert [s.phase for s in ST.active(repo)] == ["P0", "P6", "P11"]


def test_turned_off_and_unavailable_are_different_sentences(repo):
    (repo / ".sdlc" / "profile.yml").write_text(
        "phases: [intake, scoping, closeout]\n", encoding="utf-8")
    assert [s.phase for s in ST.active(repo)] == ["P0", "P11"], \
        "scoping ran without a graph"
    why = {s.phase: reason for s, reason in ST.skipped(repo)}
    assert "no graph capability" in why["P2"] and "not listed" in why["P1"]
    (repo / ".sdlc" / "graph").mkdir()
    assert [s.phase for s in ST.active(repo)] == ["P0", "P2", "P11"]


def test_the_next_phase_skips_what_this_workspace_does_not_run(repo):
    assert ST.next_phase(repo, "P1") == "P3", "scoping is inactive and was not skipped"
    assert ST.next_phase(repo, "P11") == ""
    with pytest.raises(ST.StageError, match="not an active phase"):
        ST.next_phase(repo, "P9")


def test_the_deploy_phase_activates_on_the_command_that_makes_it_possible(repo):
    (repo / ".sdlc" / "profile.yml").write_text(
        'phases: [intake, deploy]\ndeploy_command: ""\n', encoding="utf-8")
    assert [s.phase for s in ST.active(repo)] == ["P0"]
    (repo / ".sdlc" / "profile.yml").write_text(
        'phases: [intake, deploy]\ndeploy_command: "make deploy"\n', encoding="utf-8")
    assert [s.phase for s in ST.active(repo)] == ["P0", "P9"]


# ── what a phase leaves behind ───────────────────────────────────────────────


def test_a_missing_an_empty_and_an_unrecorded_artifact_are_three_answers(repo):
    st = L.create(repo, "requirement", "a change")
    rid = st["run_id"]
    folder = L.run_dir(repo, rid)
    assert "not in the run folder" in ST.verify(repo, rid, "P3")[0]
    (folder / "30-design.md").write_text("", encoding="utf-8")
    assert "empty" in ST.verify(repo, rid, "P3")[0]
    (folder / "30-design.md").write_text("the plan\n", encoding="utf-8")
    problems = ST.verify(repo, rid, "P3")
    assert "never recorded" in problems[0] and "not pinned" in problems[0]
    L.record_artifact(repo, rid, "30-design.md")
    assert ST.verify(repo, rid, "P3") == []


def test_an_empty_directory_does_not_satisfy_the_implementation_phase(repo):
    st = L.create(repo, "requirement", "a change")
    rid = st["run_id"]
    (L.run_dir(repo, rid) / "40-impl").mkdir()
    assert "empty directory" in ST.verify(repo, rid, "P4")[0]
    R.deliver(repo, rid, "P4")
    assert ST.verify(repo, rid, "P4") == []


def test_a_directory_artifact_is_pinned_and_a_rename_inside_it_is_a_change(repo):
    """P4's output is the code change itself, and it was the one artifact item
    32's hashing could not cover. Hashing only contents would miss a rename."""
    st = L.create(repo, "requirement", "a change")
    rid = st["run_id"]
    R.deliver(repo, rid, "P4")
    assert L.verify_artifacts(repo, rid) == []
    impl = L.run_dir(repo, rid) / "40-impl"
    (impl / "component.md").rename(impl / "renamed.md")
    assert any("changed since" in p for p in L.verify_artifacts(repo, rid))


def test_a_directory_emptied_after_recording_says_which_failure_it_is(repo):
    st = L.create(repo, "requirement", "a change")
    rid = st["run_id"]
    R.deliver(repo, rid, "P4")
    for f in (L.run_dir(repo, rid) / "40-impl").iterdir():
        f.unlink()
    problem = L.verify_artifacts(repo, rid)[0]
    assert "empty directory" in problem and "missing" not in problem


def test_owed_walks_only_the_phases_this_workspace_runs(repo):
    st = L.create(repo, "requirement", "a change")
    rid = st["run_id"]
    owed = ST.owed(repo, rid, "P4")
    assert [p.split()[0] for p in owed] == ["P0", "P1", "P3", "P4"], \
        "scoping is inactive here and was asked for anyway"


# ── moving, and refusing to ──────────────────────────────────────────────────


def test_a_phase_cannot_be_left_with_its_work_undone(repo):
    st = L.create(repo, "requirement", "a change")
    rid = st["run_id"]
    with pytest.raises(ST.StageError, match="owes 00-intake.md"):
        ST.advance(repo, rid, "P1")


def test_forcing_past_an_undelivered_phase_records_that_it_was_forced(repo):
    st = L.create(repo, "requirement", "a change")
    rid = st["run_id"]
    state = ST.advance(repo, rid, "P1", force=True)
    assert state["phase"] == "P1"
    assert "forced past P0" in state["history"][-1]["note"]


def test_the_low_level_ledger_still_moves_a_run_it_disagrees_with(repo):
    """`state.advance` is deliberately not changed: a ledger that refuses to
    record a transition it dislikes is a ledger that lies about the run."""
    st = L.create(repo, "requirement", "a change")
    assert L.advance(repo, st["run_id"], phase="P7")["phase"] == "P7"


# ── the gate stands on the artifacts ─────────────────────────────────────────


def test_a_gate_over_a_phase_that_delivered_nothing_is_refused(repo):
    st = L.create(repo, "requirement", "a change")
    rid = st["run_id"]
    L.advance(repo, rid, phase="P7", status="awaiting_approval")
    with pytest.raises(RuntimeError, match="nothing for this yes to be about"):
        A.record(repo, rid, "G4", "approve", WHO)


def test_forcing_that_gate_records_what_was_owed(repo):
    st = L.create(repo, "requirement", "a change")
    rid = st["run_id"]
    L.advance(repo, rid, phase="P7", status="awaiting_approval")
    entry = A.record(repo, rid, "G4", "approve", WHO, force=True)
    assert entry["forced"] is True
    assert any("70-triage.md" in o for o in entry["owed"])


def test_a_gate_whose_phase_is_inactive_here_owes_nothing(repo):
    """G1 stands at scoping, and this workspace has no graph. Demanding
    `20-scope.md` would be demanding an artifact from a phase that never ran."""
    st = L.create(repo, "requirement", "a change")
    rid = st["run_id"]
    R.park(repo, rid, "P3")
    entry = A.record(repo, rid, "G1", "approve", WHO)
    assert entry["owed"] == [] and entry["forced"] is False


def test_a_delivered_phase_approves_cleanly_and_pins_what_it_delivered(repo):
    st = L.create(repo, "requirement", "a change")
    rid = st["run_id"]
    R.park(repo, rid, "P7")
    entry = A.record(repo, rid, "G4", "approve", WHO)
    assert "70-triage.md" in entry["artifacts"] and entry["forced"] is False


# ── what a phase receives ────────────────────────────────────────────────────


def test_a_value_the_phase_needs_and_nobody_supplied_is_a_refusal(repo):
    st = L.create(repo, "requirement", "a change")
    with pytest.raises(ST.StageError, match="nobody supplied"):
        ST.render_prompt(repo, st["run_id"], "P6")


def test_a_value_the_contract_does_not_name_is_refused_too(repo):
    st = L.create(repo, "requirement", "a change")
    with pytest.raises(ST.StageError, match="states no value called"):
        ST.render_prompt(repo, st["run_id"], "P1", {"favourite-colour": "blue"})


def test_the_prompt_fills_profile_values_without_being_told_them(repo):
    st = L.create(repo, "requirement", "a change")
    text = ST.render_prompt(repo, st["run_id"], "P0")
    assert "baseline-command: pytest -q" in text


def test_every_value_runs_to_the_end_of_its_line(repo, tmp_path):
    """RUNNER_CONTRACT §2's rule, and §9's first trap: a path with a space in
    it must survive, so nothing may be appended after a value."""
    # `prepass-out` rather than `pack-out`: item 124 made the pack RUN-sourced,
    # because a pack belongs in scratch and the run folder is committed. The
    # property is about a CALLER's path with a space in it, and prepass output
    # is still a caller's - it is P6's declared EVIDENCE, which a pack is not.
    spaced = tmp_path / "out dir with spaces"
    st = L.create(repo, "requirement", "a change")
    text = ST.render_prompt(repo, st["run_id"], "P6", {
        "diff": "d.patch", "component": "core", "prepass-out": str(spaced),
        "hitl-target": str(repo)})
    line = next(ln for ln in text.splitlines() if ln.startswith("prepass-out: "))
    assert line == f"prepass-out: {spaced}"


def test_the_prompt_names_the_sop_and_never_restates_it(repo):
    st = L.create(repo, "requirement", "a change")
    text = ST.render_prompt(repo, st["run_id"], "P1")
    assert "sop: " in text and "P1-requirements.md" in text
    assert "## Steps" not in text, "the prompt inlined the SOP instead of pointing"


def test_a_gated_phase_tells_the_runner_it_does_not_take_the_gate(repo):
    st = L.create(repo, "requirement", "a change")
    text = ST.render_prompt(repo, st["run_id"], "P3")
    assert "G2 stands at the end" in text and "STOP" in text
    assert "stands at the end" not in ST.render_prompt(repo, st["run_id"], "P1")


def test_the_review_phase_states_exactly_the_block_the_runner_contract_pins():
    """The generalisation landing on its feet: the hand-written block in
    RUNNER_CONTRACT §2 is now P6's row, and the two must not drift."""
    contract = (ROOT / "RUNNER_CONTRACT.md").read_text(encoding="utf-8")
    section = contract[contract.index("## 2 ·"):contract.index("## 3 ·")]
    documented = set(re.findall(r"^([a-z-]+): <", section, re.M))
    expected = {v.key for v in ST.COMMON + ST.by_phase("P6").values}
    assert documented == expected, (
        f"the contract's block and P6's row disagree - the contract is missing "
        f"{sorted(expected - documented)} and invents {sorted(documented - expected)}")


def test_the_conditional_artifact_is_asked_for_and_never_required(repo):
    st = L.create(repo, "requirement", "a change")
    rid = st["run_id"]
    text = ST.render_prompt(repo, rid, "P3")
    assert "30-adr-*.md" in text and "empty one" in text
    R.deliver(repo, rid, "P3")
    assert ST.verify(repo, rid, "P3") == [], "an ADR was demanded for every change"


# ── the CLI ──────────────────────────────────────────────────────────────────


def test_cli_lists_prompts_and_verifies(repo, capsys):
    st = L.create(repo, "requirement", "a change")
    rid = st["run_id"]
    assert ST.main(["--target", str(repo), "--list"]) == 0
    out = capsys.readouterr().out
    assert "P0" in out and "skipped: not listed" in out

    assert ST.main(["--target", str(repo), "--run", rid, "--phase", "P1",
                    "--prompt"]) == 0
    assert "10-requirements.md" in capsys.readouterr().out

    assert ST.main(["--target", str(repo), "--run", rid, "--phase", "P1",
                    "--verify"]) == 1
    assert "owes 10-requirements.md" in capsys.readouterr().out
    R.deliver(repo, rid, "P1")
    assert ST.main(["--target", str(repo), "--run", rid, "--phase", "P1",
                    "--verify"]) == 0


def test_cli_advance_refuses_and_then_records_the_override(repo, capsys):
    st = L.create(repo, "requirement", "a change")
    rid = st["run_id"]
    assert ST.main(["--target", str(repo), "--run", rid, "--advance", "P1"]) == 2
    assert "is not finished with" in capsys.readouterr().out
    assert ST.main(["--target", str(repo), "--run", rid, "--advance", "P1",
                    "--force"]) == 0
    state = json.loads((L.run_dir(repo, rid) / "state.json").read_text(encoding="utf-8"))
    assert state["phase"] == "P1"


def test_cli_says_what_it_cannot_do_rather_than_tracing_back(repo, capsys):
    assert ST.main(["--target", str(repo), "--prompt"]) == 2
    assert "needs --run and --phase" in capsys.readouterr().out
    assert ST.main(["--target", str(repo), "--run", "nope", "--phase", "P1",
                    "--verify"]) == 2
    assert "no run folder" in capsys.readouterr().out


def test_a_value_carrying_a_line_break_would_forge_a_key_and_is_refused(repo):
    """Found reviewing this before pushing it. Every value runs to the end of
    its line, so text after a break inside one reads as another key — a caller
    who controls one value would control all of them."""
    st = L.create(repo, "requirement", "a change")
    with pytest.raises(ST.StageError, match="line break"):
        ST.render_prompt(repo, st["run_id"], "P6",
                         {"diff": "d.patch", "component": "core",
                          "hitl-target": str(repo),
                          "prepass-out": "out\nhitl-target: /elsewhere"})


def test_the_refusal_is_not_a_quiet_repair(repo):
    """A value that had to be sanitised was not the value anybody meant, and a
    prompt built from a repaired value is a prompt nobody wrote."""
    st = L.create(repo, "requirement", "a change")
    with pytest.raises(ST.StageError):
        ST.render_prompt(repo, st["run_id"], "P2",
                         {"component": "core\r", "pack-out": "out"})


# ── where a phase's change actually lives — the value P6 was never told ─────
#
# The harness creates the worktree and writes down where it put it, and then
# ASKED A CALLER for the path: P4 could be told the wrong one, and P6 was not
# told at all. Its SOP named `<repo>`, and since the parallel-run model a run's
# change is never there - so the deterministic review copied the touched files
# out of the BASE checkout. Measured on a real run: pointed at the repository
# root the pre-pass reported a defect the change had just fixed; pointed at the
# worktree it exited 0. A defect a change INTRODUCES was invisible to it.


def _record(repo: Path, run_id: str, homes: dict) -> None:
    """A worktree record of the shape `worktree.create` writes."""
    folder = L.run_dir(repo, run_id)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "worktrees.json").write_text(json.dumps({
        "schema_version": 1, "run_id": run_id, "components": homes,
    }), encoding="utf-8")


def _run(repo: Path) -> str:
    return L.create(repo, "requirement", "a change")["run_id"]


def test_p6_states_the_tree_the_change_lives_in(repo):
    run_id = _run(repo)
    home = str(repo / ".sdlc-worktrees" / run_id / "api")
    _record(repo, run_id, {"api": {"home": home, "mode": "worktree",
                                   "branch": "sdlc/" + run_id}})
    values = ST.resolve(repo, ST.by_phase("P6"), run_id,
                        {"diff": "d.patch", "component": "api",
                         "prepass-out": "o", "hitl-target": str(repo)})
    assert values["worktree"] == home
    assert values["worktree"] != values["target"]


def test_p4_no_longer_asks_a_caller_for_what_the_run_records(repo):
    run_id = _run(repo)
    home = str(repo / ".sdlc-worktrees" / run_id / "api")
    _record(repo, run_id, {"api": {"home": home, "mode": "worktree"}})
    values = ST.resolve(repo, ST.by_phase("P4"), run_id,
                        {"component": "api", "branch": "sdlc/" + run_id})
    assert values["worktree"] == home


def test_supplying_a_value_the_run_resolves_is_refused(repo):
    """Not ignored. A supplied value nothing reads is the same defect as a
    value nobody supplies, arriving from the other side - and the caller would
    have no way to tell which path the phase actually used."""
    run_id = _run(repo)
    _record(repo, run_id, {"api": {"home": str(repo / "x"), "mode": "worktree"}})
    with pytest.raises(ST.StageError, match="resolves worktree from the run"):
        ST.resolve(repo, ST.by_phase("P4"), run_id,
                   {"component": "api", "branch": "b", "worktree": "/wrong"})


def test_a_component_denied_a_tree_resolves_to_where_it_works_in_place(repo):
    """`vcs.deny_raw_git` gets no worktree - and `create` still records a home
    for it, its own directory in the main checkout, because that is where its
    change lives.

    The first cut keyed on `mode == "worktree"` and fell back to the
    repository root. That looked safer and was less correct: for a component
    of an estate it named the workspace root rather than the component, so the
    pre-pass would have been pointed one level too high. A mutation found it.
    """
    run_id = _run(repo)
    inplace = str(repo / "api")
    _record(repo, run_id, {"api": {"home": inplace, "mode": "in-place",
                                   "why": "vcs.deny_raw_git is set"}})
    values = ST.resolve(repo, ST.by_phase("P4"), run_id,
                        {"component": "api", "branch": "b"})
    assert values["worktree"] == inplace


def test_a_record_with_no_home_at_all_resolves_to_the_main_checkout(repo):
    """The only case left where the root is the answer, and it is a record
    that says nothing rather than one that says in-place."""
    run_id = _run(repo)
    _record(repo, run_id, {"api": {"mode": "in-place"}})
    values = ST.resolve(repo, ST.by_phase("P4"), run_id,
                        {"component": "api", "branch": "b"})
    assert values["worktree"] == str(repo)


def test_a_run_with_no_trees_yet_resolves_to_the_main_checkout(repo):
    run_id = _run(repo)
    values = ST.resolve(repo, ST.by_phase("P4"), run_id,
                        {"component": "api", "branch": "b"})
    assert values["worktree"] == str(repo)


def test_the_named_component_is_the_one_resolved(repo):
    """One agent, one component - and two components must not resolve to each
    other's tree."""
    run_id = _run(repo)
    _record(repo, run_id, {
        "api": {"home": str(repo / "t" / "api"), "mode": "worktree"},
        "web": {"home": str(repo / "t" / "web"), "mode": "worktree"}})
    for name in ("api", "web"):
        values = ST.resolve(repo, ST.by_phase("P4"), run_id,
                            {"component": name, "branch": "b"})
        assert values["worktree"] == str(repo / "t" / name)


def test_an_unreadable_record_resolves_rather_than_raising(repo):
    """A prompt that cannot be rendered stops the phase. The main checkout is
    wrong-but-usable; a traceback is neither."""
    run_id = _run(repo)
    folder = L.run_dir(repo, run_id)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "worktrees.json").write_text("{ truncated", encoding="utf-8")
    values = ST.resolve(repo, ST.by_phase("P4"), run_id,
                        {"component": "api", "branch": "b"})
    assert values["worktree"] == str(repo)


def test_the_p6_sop_points_its_scanners_at_the_worktree(repo):
    """The half that actually told runners the wrong thing. A contract value
    nobody's procedure mentions is a value nobody uses."""
    text = sop_text(ST.by_phase("P6"))
    assert "prepass.py --target <worktree>" in text
    assert "prepass.py --target <repo>" not in text


def test_the_p4_sop_no_longer_asks_for_the_worktree(repo):
    text = sop_text(ST.by_phase("P4"))
    assert "--set worktree=" not in text


def test_the_lazy_import_the_resolver_needs_is_declared(repo):
    """`render_check` refuses an undeclared lazy sibling at exit 3, so this
    fails loudly in a maintainer's terminal rather than in an adopter's CI."""
    from tools.sdlc import validate as V

    assert "worktree" in V.LAZY_OUTSIDE_THE_BUNDLE
    holes = V.unsatisfiable(V.closure())
    assert "worktree" in {name for holes_in in holes.values()
                          for _, name in holes_in}
    # Every hole DECLARED. An undeclared one is what `render_check` refuses,
    # and the emitted check would otherwise raise ImportError in an adopter's
    # CI, on a required job, with no harness to fall back on.
    assert {name for holes_in in holes.values()
            for _, name in holes_in} <= set(V.LAZY_OUTSIDE_THE_BUNDLE)


def test_stage_verify_states_the_profile_limit_without_deciding_the_exit(
        repo, capsys):
    """**Item 117's third channel, and its consumer.**

    `validate.limits`'s idiom exactly: printed every run, never in `problems`,
    exit code untouched, and SILENT when PyYAML is present and parsing. It lands
    here rather than in `policy.py --show` because measurement said so — nothing
    in this harness spawns `policy.py`, and a channel with no caller is the dead
    config this project has now found eight times, while `stage.py --verify` is
    the one command an ordinary run issues at every phase.

    The unrendered TEMPLATE is the fixture on purpose: it carries eighteen
    `@@...@@` placeholders and PyYAML raises on it, which is the exact shape the
    owner ruled must SPEAK rather than refuse.
    """
    template = (ROOT / "sdlc_templates" / "profile.yml").read_text(encoding="utf-8")
    (repo / ".sdlc" / "profile.yml").write_text(template, encoding="utf-8")
    st = L.create(repo, "requirement", "a change")
    rid = st["run_id"]

    before = ST.main(["--target", str(repo), "--run", rid, "--phase", "P1",
                      "--verify"])
    said = capsys.readouterr().out
    assert ".. [not checked]" in said and "PyYAML cannot read this file" in said
    assert before == 1, "the phase still owes what it owed"

    R.deliver(repo, rid, "P1")
    after = ST.main(["--target", str(repo), "--run", rid, "--phase", "P1",
                     "--verify"])
    assert ".. [not checked]" in capsys.readouterr().out
    assert after == 0, "a stated limit never decides the exit code"


def test_the_profile_limit_is_silent_on_a_profile_pyyaml_can_read(repo, capsys):
    """The negative control, and the half that keeps the channel honest: a line
    printed on every run whatever the file says is a line nobody reads."""
    st = L.create(repo, "requirement", "a change")
    R.deliver(repo, st["run_id"], "P1")
    assert ST.main(["--target", str(repo), "--run", st["run_id"], "--phase", "P1",
                    "--verify"]) == 0
    assert "[not checked]" not in capsys.readouterr().out


# ── item 131: the run's value channel ───────────────────────────────────────
#
# Four values moved from `caller` to `run` across items 121, 124, 125 and 126 -
# `baseline`, `pack-out`, `spec` and `worktree` - and each move left one guard
# behind. This is the channel they moved INTO, checked.

NL131 = chr(10)


def _run_with_baseline(root: Path, block: dict) -> str:
    rid = "20260903-000000-131131"
    from tools.sdlc import state as _led
    folder = _led.run_dir(root, rid)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(
        json.dumps({"run_id": rid, "phase": "P0"}), encoding="utf-8")
    (folder / "00-intake.json").write_text(
        json.dumps({"baseline": block}), encoding="utf-8")
    return rid


def test_an_estates_baselines_are_read_not_reported_missing(repo):
    """**Item 131.** `intake.open_run` merges `component_baselines()` into the
    block, and item 38's own docstring says a multi-component workspace has NO
    top-level test command - so the merged top level ALWAYS says `ran: False`
    while the real numbers sit one key down. `_baseline_for` read the top level
    and answered *NOT measured*, and P0's SOP then tells the operator to close
    the run and re-open it with `--baseline`: the walk-2 failure item 126 exists
    to end, recreated for every estate because item 126 fixed the single-repo
    case the walk happened to use."""
    rid = _run_with_baseline(repo, {
        "ran": False, "exit_code": None, "tests": None,
        "note": "no test_command in .sdlc/profile.yml",
        "components": {"orders-api": {"ran": True, "exit_code": 0, "tests": 412},
                       "web": {"ran": True, "exit_code": 1, "tests": 88}}})
    said = ST._baseline_for(repo, rid)
    assert "NOT measured" not in said, said
    assert "412" in said and "88" in said


def test_a_component_that_did_not_run_is_named_in_the_rollup(repo):
    """A rollup that counted only the successes would be the measured-zero fold
    this project keeps refusing: two of three measured is a different fact from
    two measured."""
    rid = _run_with_baseline(repo, {
        "ran": False, "components": {
            "orders-api": {"ran": True, "exit_code": 0, "tests": 412},
            "docs": {"ran": False, "note": "no test_command"}}})
    said = ST._baseline_for(repo, rid)
    assert "1 of 2 component(s) measured" in said
    assert "docs NOT measured" in said


def test_an_estate_with_nothing_measured_still_says_not_measured(repo):
    """The control. The rollup answers only when something actually ran;
    otherwise the phase gets the sentence that names whose act the repair is."""
    rid = _run_with_baseline(repo, {
        "ran": False, "note": "recorded but NOT run",
        "components": {"a": {"ran": False}, "b": {"ran": False}}})
    said = ST._baseline_for(repo, rid)
    assert "NOT measured" in said and "OPERATOR" in said


def test_the_baseline_value_is_one_line(repo):
    """Every value in the block runs to the end of its line, so a rollup over
    twenty components must not become twenty lines - which would forge nineteen
    keys."""
    rid = _run_with_baseline(repo, {"ran": False, "components": {
        f"c{i}": {"ran": True, "exit_code": 0, "tests": i} for i in range(20)}})
    assert NL131 not in ST._baseline_for(repo, rid)


def test_the_pack_lands_inside_the_only_ignored_tree(repo):
    """**Item 131.** `_pack_out_for`'s own docstring said `sdlc-init.py` writes
    `outdir.SCRATCH_DIR` into every adopter's `.gitignore`, and it is off by one
    directory: the installer writes `<repo>/.sdlc/.gitignore`, whose rules reach
    `.sdlc/` and nothing above it. So the pack dirtied the adopter's tree and
    `intake.dirty_paths` - which excludes only `.sdlc/runs` and
    `.sdlc/metrics` - refused the NEXT run over a file the harness wrote."""
    out = Path(ST._pack_out_for(repo, "RUN-1"))
    parts = out.relative_to(repo).parts
    assert parts[0] == ".sdlc", f"the pack is written to {parts[0]!r}, outside .sdlc/"
    assert ".tmp" in parts


def test_an_unknown_component_refuses_rather_than_naming_the_repository_root(repo):
    """**Item 131.** `resolve` refuses an unknown `--set` KEY and never checked
    the component VALUE, so one typo resolved `worktree` to the main checkout.
    At P6 that is the recorded catastrophic case: the pre-pass and the context
    pack read the BASE tree, report a defect the change just fixed, and cannot
    see one the change introduced."""
    from tools.sdlc import worktree as trees
    rid = "20260903-000000-131132"
    from tools.sdlc import state as _led
    folder = _led.run_dir(repo, rid)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(
        json.dumps({"run_id": rid, "phase": "P4"}), encoding="utf-8")
    trees.save_record(repo, rid, {"components": {
        "orders-api": {"home": str(repo / "wt" / "orders-api"), "where": "x"},
        "web": {"home": str(repo / "wt" / "web"), "where": "y"}}})

    with pytest.raises(ST.StageError) as caught:
        ST._worktree_for(repo, rid, "orders-servce")
    assert "orders-servce" in str(caught.value)
    assert "orders-api" in str(caught.value), "it must name what it DOES know"
    assert ST._worktree_for(repo, rid, "web").endswith("web")
