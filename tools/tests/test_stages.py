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

import ast
import importlib.util
import inspect
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import approve as A  # noqa: E402
from tools.sdlc import policy as guardrails  # noqa: E402
from tools.sdlc import stage as ST  # noqa: E402
from tools.sdlc import state as L  # noqa: E402
from tools.tests import _runs as R  # noqa: E402
from tools.tests import _scan  # noqa: E402

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


def _load_init():
    """`sdlc-init.py` as a module, despite the hyphen. Called inside the one test
    that needs it, so importing this file costs nothing."""
    spec = importlib.util.spec_from_file_location("sdlc_init", ROOT / "sdlc-init.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("sdlc_init", mod)
    spec.loader.exec_module(mod)
    return mod


def test_the_skill_carries_the_harness_placeholder_the_installer_fills():
    """The SOPs are pointed at, never copied. An installed skill with a literal
    `@@HARNESS@@` in it points nowhere.

    The second half of this test used to read `sdlc-init.py` as TEXT and assert the
    source literal `skill_body.replace("@@HARNESS@@"`. Item 242 moved that fill into
    `render_skill_body`, where the local is spelled `body` — a pure RENAME, with the
    fill intact and a runtime refusal added beside it — and the assertion took `main`
    red anyway. A test that pins a spelling reports a rename as a regression, and
    would report a regression that kept the spelling as fine; it was never asking the
    question its name asks. So it now RENDERS the shipped body and asks that question
    directly: no placeholder may survive, and the path it was filled with must be an
    absolute directory that really holds the SOP of every phase the roster names.
    """
    skill = (ROOT / "sdlc_templates" / "SKILL.md").read_text(encoding="utf-8")
    assert "@@HARNESS@@/sdlc/stages/" in skill
    init = _load_init()
    rendered, why = init.render_skill_body(init.parse_frontmatter(skill)[1])
    assert why is None, f"this harness clone cannot fill the placeholder: {why}"
    assert "@@HARNESS@@" not in rendered, \
        "SKILL.md carries a placeholder the installer never fills"
    pointer = re.search(r"`([^`]*/sdlc/stages)/`", rendered)
    assert pointer, "the rendered skill no longer points at the SOP directory"
    sops = Path(pointer.group(1))
    assert sops.is_absolute(), \
        f"the placeholder was filled with {sops}, which is not a harness root"
    missing = [s.phase for s in ST.STAGES if not any(sops.glob(f"{s.phase}-*.md"))]
    assert not missing, f"the filled pointer {sops} carries no SOP for {missing}"


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


#: A default install on an UNRECOGNISED stack (sdlc-init.py `_BLANK_COMMANDS` /
#: `_DEFAULTS`) writes every command as `""`, so the value the phase reads is
#: absent-or-blank - one answer, per `profile_scalar`'s own docstring.
_BLANK_COMMAND_PROFILE = (
    "stack: unknown\ngate_profile: solo\n" + SOLO +
    'test_command: ""\ncoverage_command: ""\nbuild_command: ""\n'
    'deploy_command: ""\nreview:\n  block_on: [critical, high]\n')


@pytest.mark.parametrize("phase,render_key", [
    ("P0", "baseline-command"),
    ("P5", "test-command"),
    ("P5", "coverage-command"),
    ("P9", "deploy-command"),
])
def test_a_blank_profile_value_is_a_stated_absence_naming_the_key(
        tmp_path, phase, render_key):
    """**T134, item 132's rule reached for the VALUES.** A `profile:` value the
    phase names and a default install on an unrecognised stack leaves `""`
    rendered as a BARE BLANK `key: ` - byte-identical to a value the harness had
    FAILED to fill, which is the ambiguity item 132 spent four sentences removing
    for the DOCUMENTS. It is now a stated absence NAMING the profile key.

    Red-first against the unfixed source: the line was exactly `render_key: ` and
    did not carry the profile key, so the assertions fail on BEHAVIOUR (a wrong
    value), not on an import or attribute error.
    """
    repo = tmp_path / "repo"
    (repo / ".sdlc").mkdir(parents=True)
    (repo / ".sdlc" / "profile.yml").write_text(_BLANK_COMMAND_PROFILE,
                                                encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True,
                   capture_output=True, timeout=120)
    st = L.create(repo, "requirement", "a change")
    profile_key = next(v.source.partition(":")[2]
                       for v in ST.by_phase(phase).values
                       if v.key == render_key)
    text = ST.render_prompt(repo, st["run_id"], phase)
    line = next(ln for ln in text.splitlines()
                if ln.startswith(render_key + ":"))
    assert line != f"{render_key}: ", \
        f"{render_key} still renders as a bare blank: {line!r}"
    assert profile_key in line, \
        f"{render_key} does not name its profile key {profile_key!r}: {line!r}"


def test_p5_sop_explains_the_stated_absence_the_prompt_now_renders():
    """**T134's document half.** cell 3: *P5-test.md never says what a blank test
    command means*. The prompt now renders a blank `test-command` as a STATED
    ABSENCE (`stage._profile_absence`), and the SOP the runner is sent to
    explains the same thing in the same words - welded, so neither drifts silently.

    Derived from the contract (the profile key) and welded to the code's own
    phrase, NOT pinned to a sentence: rewording an unrelated SOP line does not
    fire, and removing the guidance does. `stated absence` is the ruling's own
    term of art, shared by the value the code renders and the SOP that explains
    it, which is what makes producer and consumer one fact rather than two.
    """
    p5 = ST.by_phase("P5")
    key = next(v.source.partition(":")[2] for v in p5.values
               if v.key == "test-command")            # -> "test_command"
    concept = "stated absence"
    assert concept in ST._profile_absence(key), ST._profile_absence(key)
    sop = sop_text(p5)
    assert key in sop, f"P5-test.md never names the profile key {key!r}"
    assert concept in sop.lower(), (
        f"P5-test.md never explains the {concept!r} the P5 prompt now states for "
        f"a blank {key}; a runner sent there finds no word about it")


def _repo_with_profile(tmp_path: Path, body: str) -> Path:
    repo = tmp_path / "repo"
    (repo / ".sdlc").mkdir(parents=True)
    (repo / ".sdlc" / "profile.yml").write_text(
        "stack: python\ngate_profile: solo\n" + SOLO + body, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True,
                   capture_output=True, timeout=120)
    return repo


def test_a_whitespace_only_command_folds_to_a_stated_absence(tmp_path):
    """**T134 round 2: the fold must TRIM, not merely test for empty.**
    `profile_scalar` hands back `test_command: "  "` verbatim as two spaces, and
    two spaces are not a runnable command. Without this the value renders a
    bare-ish blank and a `.strip()`-to-plain-emptiness mutant survives the whole
    suite - which the review measured. Red against that mutant, not against the
    shipped code, whose `.strip()` already folds it: this is the test that was
    missing, so the mutant now dies."""
    repo = _repo_with_profile(tmp_path, 'test_command: "  "\n')
    st = L.create(repo, "requirement", "a change")
    text = ST.render_prompt(repo, st["run_id"], "P5")
    line = next(ln for ln in text.splitlines() if ln.startswith("test-command:"))
    assert line.strip() != "test-command:" and "  " not in line[len("test-command:"):].rstrip(), \
        f"a whitespace-only command rendered a blank: {line!r}"
    assert "test_command" in line, \
        f"a whitespace-only command must name the profile key: {line!r}"


@pytest.mark.parametrize("phase,render_key,profile_key,body,supplied", [
    ("P6", "block-on", "block_on", "review:\n  block_on: []\n",
     {"diff": "d.patch", "component": "core", "prepass-out": "pp",
      "hitl-target": "."}),
    ("P0", "conventions", "conventions_files", "conventions_files: []\n", {}),
])
def test_a_declared_empty_list_is_declared_not_a_stated_absence(
        tmp_path, phase, render_key, profile_key, body, supplied):
    """**T134 round 2, and the reader this fix calls already ruled it.**
    `profile_list` returns None for an absent key and `[]` for a declared-empty
    one PRECISELY so a caller can tell them apart (its own docstring: opposite
    instructions), and `block_on: []` is a stated gate setting. The first cut
    folded any `profile:` value on emptiness, so `[]` rendered `""` through the
    join and read as a stated absence - the prompt then said the profile
    declared no such value WHEN IT DECLARED ONE. It gets its own sentence now.

    Red against the pre-round-2 tip for that exact reason: the line said
    `declares no {profile_key}` for a value the profile declares."""
    repo = _repo_with_profile(tmp_path, body)
    # the reader's own ruling, pinned beside the render that must respect it
    assert guardrails.profile_list(repo, profile_key) == []
    st = L.create(repo, "requirement", "a change")
    text = ST.render_prompt(repo, st["run_id"], phase, supplied)
    line = next(ln for ln in text.splitlines() if ln.startswith(render_key + ":"))
    assert "empty list" in line, \
        f"a declared-empty list must say it is declared and empty: {line!r}"
    assert "declares no" not in line, \
        f"a declared-empty list must NOT read as an absence: {line!r}"


def test_a_genuinely_absent_list_is_still_a_stated_absence(tmp_path):
    """The other half of the split: a list nobody declared (None, not `[]`)
    still folds to a stated absence naming the key. Only DECLARED-empty gets the
    other sentence, so the two facts stay two sentences."""
    repo = _repo_with_profile(tmp_path, "")  # no block_on anywhere
    assert guardrails.profile_list(repo, "block_on") is None
    st = L.create(repo, "requirement", "a change")
    text = ST.render_prompt(repo, st["run_id"], "P6", {
        "diff": "d.patch", "component": "core", "prepass-out": "pp",
        "hitl-target": str(repo)})
    line = next(ln for ln in text.splitlines() if ln.startswith("block-on:"))
    assert "declares no block_on" in line, \
        f"an absent list must be a stated absence naming the key: {line!r}"
    assert "empty list" not in line, f"absent is not declared-empty: {line!r}"


def test_a_nonempty_list_renders_its_joined_values(tmp_path):
    """**T134 round 3: the arm every adopter actually hits.** The shipped
    template defaults `review.block_on` to a NON-empty list, so the join arm is
    the one a real run takes - and round 2 left it unpinned, so a mutant handing
    it the declared-empty sentence survived the whole suite. A declared,
    non-empty list renders its values joined, exactly, and is neither a stated
    absence nor the declared-empty sentence."""
    repo = _repo_with_profile(tmp_path, "review:\n  block_on: [critical, high]\n")
    assert guardrails.profile_list(repo, "block_on") == ["critical", "high"]
    st = L.create(repo, "requirement", "a change")
    text = ST.render_prompt(repo, st["run_id"], "P6", {
        "diff": "d.patch", "component": "core", "prepass-out": "pp",
        "hitl-target": str(repo)})
    line = next(ln for ln in text.splitlines() if ln.startswith("block-on:"))
    assert line == "block-on: critical, high", \
        f"a nonempty list must render its values joined: {line!r}"
    assert "empty list" not in line and "declares no" not in line, \
        f"a nonempty list is neither declared-empty nor absent: {line!r}"


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


# ── item 145: a value the profile states is refused, not ignored ────────────

#: Every value any phase reads from `profile.yml`, DERIVED from the contract.
#: A roster would go stale the first time a ninth dial appeared, which is the
#: shape this project keeps finding to be the defect rather than the guard.
PROFILE_VALUES = sorted({(phase, value.key) for phase in ST.PHASES
                         for value in ST.by_phase(phase).values
                         if value.source.startswith("profile:")})


def test_the_derived_profile_value_set_is_not_empty():
    """**The sweep's own guard, and item 115's lesson.** A parametrisation over
    an empty list COLLECTS NOTHING and pytest reports no failure for it, so a
    derivation that quietly stopped finding anything would print exactly like a
    pass. The count is pinned so a ninth dial is noticed deliberately rather
    than absorbed."""
    assert len(PROFILE_VALUES) == 8, PROFILE_VALUES
    keys = {key for _phase, key in PROFILE_VALUES}
    assert {"deploy-command", "block-on", "test-command"} <= keys, keys


@pytest.mark.parametrize("phase,key", PROFILE_VALUES)
def test_a_value_the_profile_states_is_refused_when_a_caller_supplies_it(
        repo, phase, key):
    """**Item 145.** `resolve` refuses a key the phase does not name and
    SILENTLY IGNORED one it names and reads from `profile.yml` - the `extra`
    check cannot see it, because the contract does name the key.

    **The message is asserted, not just the exception type**, and the wrong
    refusal is asserted ABSENT: an unknown key is already refused, so a test
    asking only for `StageRefusal` would document behaviour instead of
    defending it. That is item 109's own lesson, six items on.
    """
    st = L.create(repo, "requirement", "a change")
    with pytest.raises(ST.StageRefusal) as caught:
        ST.render_prompt(repo, st["run_id"], phase, {key: "supplied by hand"})
    said = str(caught.value)
    assert f"reads {key} from profile.yml" in said, said
    assert "states no value called" not in said, (
        "this is the unknown-key refusal, not the profile-stated one: " + said)


def test_the_deploy_command_cannot_be_replaced_from_the_command_line(repo):
    """The instance worth naming on its own: an operator overriding the deploy
    for a dry run was silently handed the profile's, so the runner was told to
    perform the real one - at the phase that spends, behind the only gate that
    repeats (item 46's G5). Nothing about the discard reached them."""
    (repo / ".sdlc" / "profile.yml").write_text(
        "stack: python" + chr(10) + "gate_profile: solo" + chr(10) + SOLO +
        'release:' + chr(10) + '  deploy_command: "deploy --for-real"' + chr(10),
        encoding="utf-8")
    st = L.create(repo, "requirement", "a change")
    with pytest.raises(ST.StageRefusal, match="reads deploy-command"):
        ST.render_prompt(repo, st["run_id"], "P9",
                         {"deploy-command": "echo pretend"})


def test_the_cli_refuses_it_where_a_person_can_actually_see(repo):
    """**Item 96's rule: a CLI's contract is a process, not a function.** This
    defect was found by SPAWNING - the refusal has to arrive as a non-zero exit
    and a sentence, because in-process both a refusal and a traceback render as
    a failing test and neither is what an operator meets."""
    st = L.create(repo, "requirement", "a change")
    L.advance(repo, st["run_id"], phase="P6", status="running")
    done = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "sdlc" / "stage.py"),
         "--target", str(repo), "--run", st["run_id"], "--phase", "P6",
         "--prompt", "--set", "diff=d.patch", "--set", "component=c",
         "--set", "prepass-out=o", "--set", "hitl-target=" + str(repo),
         "--set", "block-on=nothing-at-all"],
        capture_output=True, text=True, timeout=300, cwd=str(ROOT))
    out = (done.stdout or "") + (done.stderr or "")
    assert done.returncode != 0, out
    assert "reads block-on from profile.yml" in out, out
    assert "block-on: critical, high" not in out, (
        "the profile's value was rendered as though nothing had been supplied")


# ── one scope per door ───────────────────────────────────────────────────────
#
# Two defects of one shape, both measured by SPAWNING before any of this was
# written: a door answering about the wrong scope.
#
#   T252  `--verify --upto P5` printed ONE contradicted claim FIVE times and
#         tallied `10 owed` where six things are owed, because the run-wide
#         claims check sat inside the body `owed` runs once per phase.
#   T253  `--set` was read under `--prompt` and DISCARDED under `--list`,
#         `--verify` and `--advance` - three subcommands, not the two reported -
#         and `--advance --force --set this-is-not-a-pair` MOVED THE RUN from
#         P0 to P1 at exit 0 with the pair neither parsed nor mentioned.


def _claiming_run(repo: Path, claim: str, tests: int = 12) -> str:
    """A run whose recorded document states a count the baseline contradicts.

    ONE document, recorded ONCE, so any repetition downstream is the defect
    rather than the fixture. Shaped like `test_claims.py`'s `open_run`/`say`
    pair deliberately: that file owns the checker's own contract, this one owns
    who asks it and how often.
    """
    run_id = L.create(repo, "defect", "a title")["run_id"]
    folder = L.run_dir(repo, run_id)
    (folder / "00-intake.json").write_text(json.dumps(
        {"schema_version": 1, "run_id": run_id, "kind": "defect",
         "baseline": {"ran": True, "tests": tests, "exit_code": 0}},
        indent=2) + "\n", encoding="utf-8")
    (folder / "20-scope.md").write_text(claim, encoding="utf-8")
    L.record_artifact(repo, run_id, "20-scope.md")
    return run_id


def _spawn_stage(*args: str) -> tuple[int, str]:
    """`stage.py` as a person meets it: a process, an exit code, and output."""
    done = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "sdlc" / "stage.py"), *args],
        capture_output=True, text=True, timeout=300, cwd=str(ROOT))
    return done.returncode, (done.stdout or "") + (done.stderr or "")


def test_list_refuses_an_unclosed_inline_list_not_self_contradicts(tmp_path):
    """**T130 (W30 A4-5), the `--list` half.** `phases: [intake, implementation,
    release` (one missing bracket) was read as ABSENT, so `--list` took the solo
    default and reported `release` as *not listed* — about a phase the file
    lists. The reader now refuses a list it cannot close (item 117), and every
    consumer including `--list` meets that refusal as exit 2, never exit 0 with a
    self-contradiction. A process, an exit code, and output (item 96)."""
    (tmp_path / ".sdlc").mkdir(parents=True)
    (tmp_path / ".sdlc" / "profile.yml").write_text(
        "scope: repo\nphases: [intake, implementation, release\n", encoding="utf-8")
    code, out = _spawn_stage("--target", str(tmp_path), "--list")
    assert code == 2, out
    assert "profile.yml:2" in out and "]" in out, out
    assert "not listed" not in out, (
        "a refused run must not also print a phase-skip verdict", out)


def test_list_surfaces_the_profile_limit_like_verify_does(tmp_path):
    """**T130, the second half of the ruling.** `profile_limits` records what the
    profile's shape was NOT cross-checked against; `--verify` prints it and
    `--list` was left silent, so `--list` reported the same confidence as a run
    that had a second opinion. A profile PyYAML cannot read but the stdlib reader
    accepts (`scope: repo` then a top-level `- stray`) is a stated LIMIT, never a
    refusal — `--list` now surfaces it exactly as `--verify` does, exit untouched."""
    (tmp_path / ".sdlc").mkdir(parents=True)
    (tmp_path / ".sdlc" / "profile.yml").write_text(
        "scope: repo\n- stray\n", encoding="utf-8")
    code, out = _spawn_stage("--target", str(tmp_path), "--list")
    assert code == 0, out
    assert "[not checked]" in out and "PyYAML cannot read" in out, out


def test_the_run_wide_claim_is_asked_once_per_run_not_once_per_phase(repo):
    """**T252.** The claims checker is told no phase - `problems(target,
    run_id)` - and it was called from inside the function `owed` runs once per
    active phase. One contradiction, five phases, five identical sentences.

    Both halves are asserted: once across the run, and STILL ONCE for a single
    phase, because a wrong count is a contradiction wherever it is noticed and
    `test_claims.py` pins the per-phase reading at P1, P2 and P5.
    """
    run_id = _claiming_run(repo, "The other fifteen tests are out of scope.\n")
    across = [p for p in ST.owed(repo, run_id, "P5") if "fifteen" in p]
    assert len(across) == 1, across
    one_phase = [p for p in ST.verify(repo, run_id, "P5") if "fifteen" in p]
    assert len(one_phase) == 1, one_phase


def test_the_upto_tally_counts_each_owed_thing_once(repo):
    """**T252, as the number a human actually reads.** Item 99's checker is
    what somebody reads before approving and item 140 is the recorded cost of
    it reporting a number the document does not carry - it blocked a gate. A
    tally that multiplies is that failure from the other side.

    The tally is asserted against the lines PRINTED rather than against a
    literal, so the multiplication cannot return without this failing: five
    copies would make the claim count 5 and the tally disagree with the lines.
    """
    run_id = _claiming_run(repo, "The other fifteen tests are out of scope.\n")
    code, out = _spawn_stage("--target", str(repo), "--run", run_id,
                            "--phase", "P5", "--verify", "--upto")
    assert code == 1, out
    said = [line for line in out.splitlines() if line.startswith("!! ")]
    assert sum("fifteen" in line for line in said) == 1, out
    assert len(set(said)) == len(said), "a sentence printed twice is a sentence"
    assert f"P5: {len(said)} owed" in out, out


@pytest.mark.parametrize("phase,expected", [
    ("P0", ["P0"]),
    ("P3", ["P0", "P1", "P3"]),
    ("P5", ["P0", "P1", "P3", "P4", "P5"]),
])
def test_upto_judges_every_active_phase_through_the_one_named(repo, phase,
                                                              expected):
    """GREEN BY DESIGN - `--upto` shipped with no test anywhere in the tree, so
    this documents the walk rather than defending a fix. It is worth having
    because the T252 fix moved what `owed` returns, and nothing said which
    phases it was supposed to reach: scoping is inactive in this profile and
    must not be asked, and the named phase must be included, not stopped at.
    """
    run_id = _claiming_run(repo, "nothing claimed here\n")
    code, out = _spawn_stage("--target", str(repo), "--run", run_id,
                            "--phase", phase, "--verify", "--upto")
    assert code == 1, out
    named = [line.split()[1] for line in out.splitlines()
             if line.startswith("!! P")]
    assert sorted(set(named)) == expected, out
    assert "P2" not in named, "scoping is inactive here and was asked anyway"


def test_upto_on_a_phase_that_is_not_a_phase_is_refused_not_dropped(repo):
    """GREEN BY DESIGN, and the one `--upto` branch that could answer about the
    wrong scope silently: an unknown `--phase` under `--upto` is read by
    `PHASES.index`, not by `by_phase`, so it has its own refusal."""
    run_id = _claiming_run(repo, "nothing claimed here\n")
    code, out = _spawn_stage("--target", str(repo), "--run", run_id,
                            "--phase", "P99", "--verify", "--upto")
    assert code == 2, out
    assert "unknown phase 'P99'" in out, out


def test_upto_is_clean_when_every_phase_through_the_one_named_delivered(repo):
    """GREEN BY DESIGN - the negative control `--upto` never had. A walk that
    reports problems for everything is indistinguishable from one that reports
    them for nothing, and exit 0 is the half a gate stands on."""
    run_id = _claiming_run(repo, "nothing claimed here\n")
    for phase in ("P0", "P1"):
        R.deliver(repo, run_id, phase)
    code, out = _spawn_stage("--target", str(repo), "--run", run_id,
                            "--phase", "P1", "--verify", "--upto")
    assert code == 0, out
    assert "P1: clean" in out, out


#: What `--set` is offered with, per subcommand, so each can be spawned for
#: real. LITERAL, like the parametrise below it, and checked against
#: `ST.SUBCOMMANDS` and against the parser by the guard: a list computed from
#: the code under test agrees with it by construction and asserts nothing.
_SUBCOMMAND_ARGS = {"--list": [], "--prompt": [], "--verify": [],
                    "--advance": ["P1"]}

#: The subcommands that do NOT read `--set`. A literal, and the guard below is
#: what keeps it equal to the module's own table.
_DISCARDED_SET_ON = ["--list", "--verify", "--advance"]


def test_the_subcommand_table_and_the_parser_name_the_same_subcommands():
    """**T253's durable half.** Three doors answering one caller mistake the
    same way is a property a guard can hold; three independently patched
    branches is not. `main` derives its refusal from `SUBCOMMANDS` and this
    derives its cases from the same table AND from the parser, so a subcommand
    added to one and not the other is a failure rather than a gap.

    The modifiers are listed because they are the judgement: `--upto`, `--force`
    and `--note` change how a subcommand behaves and none of them is one.
    """
    modifiers = {"--help", "--target", "--run", "--phase", "--upto", "--force",
                 "--note", "--set"}
    from_parser = {opt for action in ST._parser()._actions
                   for opt in action.option_strings
                   if opt.startswith("--")} - modifiers
    assert from_parser == {flag for flag, _ in ST.SUBCOMMANDS}
    assert set(_SUBCOMMAND_ARGS) == from_parser
    # T335: the roster order is load-bearing — `_chosen` iterates
    # SUBCOMMANDS first-match-wins, so the tuple order IS the dispatch
    # priority, and a SET assertion lets it be reordered without complaint.
    # Derive the dispatch chain from main's own `if args.<flag>:` sequence
    # via AST, then weld the roster to it: reordering EITHER side fails
    # by name, and a deliberate fifth subcommand added to both passes.
    roster_order = [flag for flag, _ in ST.SUBCOMMANDS]
    tree = ast.parse(inspect.getsource(ST.main))
    func = tree.body[0]
    chain_order = []
    try_body = [n for n in ast.walk(func) if isinstance(n, ast.Try)]
    for node in (try_body[0].body if try_body else func.body):
        if (isinstance(node, ast.If)
                and isinstance(node.test, ast.Attribute)
                and isinstance(node.test.value, ast.Name)
                and node.test.value.id == "args"):
            chain_order.append(f"--{node.test.attr}")
    assert roster_order == chain_order, (
        f"SUBCOMMANDS says {roster_order} but main's if-chain dispatches "
        f"{chain_order} — _chosen iterates the roster first-match-wins, so "
        f"the two must agree or the chooser names one door while main runs "
        f"another; update SUBCOMMANDS or main's if-chain to match"
    )
    reads = [flag for flag, yes in ST.SUBCOMMANDS if yes]
    assert reads == ["--prompt"], (
        "the table says some other subcommand states values to a runner")
    assert sorted(_DISCARDED_SET_ON) == sorted(from_parser - set(reads)), (
        "the cases below and the module's table no longer name the same doors")


@pytest.mark.parametrize("flag", _DISCARDED_SET_ON)
def test_a_set_the_subcommand_does_not_read_is_refused_by_name(repo, flag):
    """**T253.** Item 145's ruling is that *a supplied value that is silently
    ignored is indistinguishable from one that was honoured*, and `resolve`
    REFUSES one - pinned above, where a person can see it. This door stayed
    silent, and `--set this-is-not-a-pair` was not even parsed.

    Exit 2, which is what `--prompt` already answers a malformed `--set` with
    and what this module means by *the caller got it wrong*. Not 1: that is
    `--verify` finding things owed, and a refusal is not an inventory.
    """
    run_id = _claiming_run(repo, "nothing claimed here\n")
    code, out = _spawn_stage("--target", str(repo), "--run", run_id,
                            "--phase", "P1", flag, *_SUBCOMMAND_ARGS[flag],
                            "--set", "this-is-not-a-pair")
    assert code == 2, out
    assert "--set" in out and flag in out, out


def test_the_advance_refuses_the_set_before_the_run_moves(repo):
    """**T253's worst half, and why it is not noise.** Of the three doors that
    discarded `--set`, the one that stayed silent is the one that CHANGES
    STATE. Measured by spawning: exit 0, and the run had already moved from P0
    to P1. A refusal printed after that would be a refusal of something that
    already happened, so the state is what is asserted here, not the sentence.
    """
    run_id = _claiming_run(repo, "nothing claimed here\n")
    where = L.run_dir(repo, run_id) / "state.json"
    before = json.loads(where.read_text(encoding="utf-8"))["phase"]
    code, out = _spawn_stage("--target", str(repo), "--run", run_id,
                            "--advance", "P1", "--force",
                            "--set", "this-is-not-a-pair")
    assert code == 2, out
    assert "--set" in out, out
    after = json.loads(where.read_text(encoding="utf-8"))["phase"]
    assert after == before == "P0", f"the run moved to {after} anyway"


def test_the_subcommand_that_does_read_set_still_does(repo):
    """The positive control for the refusal above: a guard that refuses
    everything is indistinguishable from one that refuses the right thing."""
    run_id = _claiming_run(repo, "nothing claimed here\n")
    code, out = _spawn_stage("--target", str(repo), "--run", run_id,
                            "--phase", "P4", "--prompt",
                            "--set", "component=api", "--set", "branch=b")
    assert code == 0, out
    assert "component: api" in out, out


# ── T254 / T353 / T356: an unreadable, missing or invalid phase label ─────────
# W280. One theme, three doors. `state.phase_of` (T336) is the primitive - read
# the run's phase or refuse BY NAME - and the bugs were the doors that read the
# label their own way: a raw `PHASES.index`, a silent `.get("phase", P0)`, and a
# judge per active phase each rediscovering one unreadable label.

_MISSING = object()


def _relabel_stage(repo: Path, run_id: str, phase) -> None:
    """Overwrite the run's stored phase while its state.json still loads."""
    path = L.run_dir(repo, run_id) / L.STATE_NAME
    state = json.loads(path.read_text(encoding="utf-8"))
    if phase is _MISSING:
        state.pop("phase", None)
    else:
        state["phase"] = phase
    path.write_text(json.dumps(state), encoding="utf-8")


def test_advance_into_an_unknown_phase_is_refused_by_name_not_by_tuple_index(repo):
    """**T254.** `--advance P99` reached `PHASES.index(phase)` with no guard and
    answered with `tuple.index(x): x not in tuple`, while `--verify` names the
    phase and lists the valid ones. Same exit 2, a raw Python internal for one."""
    rid = L.create(repo, "requirement", "a change")["run_id"]
    with pytest.raises(ST.StageError, match=r"unknown phase 'P99' - one of P0"):
        ST.advance(repo, rid, "P99")


def test_advance_into_an_unknown_phase_says_so_as_a_process(repo):
    """T254 as a person meets it: a CLI's contract is a process."""
    rid = L.create(repo, "requirement", "a change")["run_id"]
    code, out = _spawn_stage("--target", str(repo), "--run", rid, "--advance", "P99")
    assert code == 2, out
    assert "unknown phase 'P99' - one of P0" in out, out
    assert "tuple.index" not in out, "a raw builtin message reached the operator"


def test_advance_refuses_an_invalid_stored_phase_not_a_raw_valueerror(repo):
    """**T353.** The phase being LEFT was read as `state.get("phase", P0)`, so a
    present-but-invalid label hit `PHASES.index` and raised the same raw
    ValueError T254 is about. `phase_of` refuses by name."""
    rid = L.create(repo, "requirement", "a change")["run_id"]
    _relabel_stage(repo, rid, "P99")
    with pytest.raises(L.Unreadable,
                       match=r"records the phase 'P99', which is not one of"):
        ST.advance(repo, rid, "P1")


def test_advance_refuses_a_missing_stored_phase_not_a_silent_p0(repo):
    """**T353, the silent half.** A MISSING label became P0, and `advance` would
    then verify and move a phase the run was never at. `phase_of` refuses rather
    than invent one."""
    rid = L.create(repo, "requirement", "a change")["run_id"]
    _relabel_stage(repo, rid, _MISSING)
    with pytest.raises(L.Unreadable,
                       match=r"records the phase None, which is not one of"):
        ST.advance(repo, rid, "P1")


def test_owed_states_an_unreadable_phase_once_not_once_per_judge(tmp_path):
    """**T356.** `owed` invokes every active conditional phase's judge, and the
    three that read the run's phase (release, deploy, verify) each caught the same
    `Unreadable` and appended their own "could not tell" - T252's shape (a
    run-wide fact reported once per phase) reintroduced by T336. `owed` reads the
    label once and states it a single time."""
    repo = tmp_path / "repo"
    (repo / ".sdlc").mkdir(parents=True)
    (repo / ".sdlc" / "profile.yml").write_text(
        "stack: python\ngate_profile: solo\n"
        "phases: [intake, requirements, design, implementation, test, review, "
        "triage, release, deploy, verify, closeout]\n"
        'test_command: "pytest -q"\n'
        'deploy_command: "echo built here"\n'
        'probes: ["http://127.0.0.1:9/health"]\n'
        "review:\n  block_on: [critical, high]\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True,
                   capture_output=True, timeout=120)
    rid = L.create(repo, "requirement", "a change")["run_id"]
    active = {s.phase for s in ST.active(repo)}
    assert {"P8", "P9", "P10"} <= active, active
    _relabel_stage(repo, rid, "P12")
    unreadable = [p for p in ST.owed(repo, rid, "P10")
                  if "which is not one of" in p]
    assert len(unreadable) == 1, unreadable
    assert "no conditional phase can say what it owes" in unreadable[0], unreadable


# ---- T256: refusal for a missing run names no machine path, names correct location --------

class TestMissingRunRefusal:
    """T256: the 'no run folder' refusal must not print an absolute machine
    path, and must name the location where the run WOULD be (the active
    directory), not the archive directory for a run that was never archived.

    Tested as a spawned process, because that is what a person sees.
    """

    def test_no_machine_path_and_correct_location_via_cli(self, repo):
        """Spawn --verify with a run id that was never created.

        Two assertions in one test because they are one requirement:
        (1) no absolute / drive-letter path in the message, and
        (2) the location it names exists for an ACTIVE run (not archive).
        """
        result = subprocess.run(
            [sys.executable, "-m", "tools.sdlc.stage",
             "--target", str(repo), "--run", "never-existed",
             "--phase", "P1", "--verify"],
            capture_output=True, text=True, cwd=str(ROOT), timeout=30,
        )
        assert result.returncode == 2, f"expected exit 2, got {result.returncode}"
        out = result.stdout
        assert "no run folder" in out, f"expected refusal, got: {out!r}"
        # (1) No absolute / drive-letter path
        #
        # ROUND 2 (W425, T396 review-W424): this used to be two inline checks, and the POSIX
        # half (`not out.startswith("/")`) only ever looked at the START of the whole output,
        # which never begins with "/" here regardless of what appears mid-string - so it could
        # not see a bare POSIX path like `/tmp/...`, exactly what pytest's `tmp_path` resolves
        # to on the Ubuntu CI runner. Now the shared, Linux-aware `_scan.has_machine_path`
        # (also used by test_approvals.py and test_run_ledger.py) closes that in one place.
        assert not _scan.has_machine_path(out), (
            f"absolute machine path in refusal: {out!r}")
        # (2) Names the active location, not the archive
        assert "archive" not in out, (
            f"refusal names archive for a never-archived run: {out!r}")
        # The relative path .sdlc/runs/never-existed should appear
        assert ".sdlc" in out and "runs" in out and "never-existed" in out, (
            f"refusal does not name the expected relative path: {out!r}")

    def test_no_machine_path_via_function_verify(self, repo):
        """The same bug via the verify() function directly."""
        with pytest.raises(ST.StageError, match="no run folder") as exc_info:
            ST.verify(repo, "never-existed", "P1")
        msg = str(exc_info.value)
        assert not re.search(r"[A-Za-z]:[\\/]", msg), (
            f"absolute machine path in StageError: {msg!r}")
        assert "archive" not in msg, (
            f"StageError names archive for a never-archived run: {msg!r}")
        assert f"{L.RUNS_REL.as_posix()}/never-existed" in msg, msg

    def test_no_machine_path_via_function_stale(self, repo):
        """The same bug via the stale() function directly."""
        with pytest.raises(ST.StageError, match="no run folder") as exc_info:
            ST.stale(repo, "never-existed", "P1")
        msg = str(exc_info.value)
        assert not re.search(r"[A-Za-z]:[\\/]", msg), (
            f"absolute machine path in StageError: {msg!r}")
        assert "archive" not in msg, (
            f"StageError names archive for a never-archived run: {msg!r}")
        assert f"{L.RUNS_REL.as_posix()}/never-existed" in msg, msg


# ---- T283: a missing recorded artifact is reported with a remedy that WORKS ----


def _remedy_command(line: str) -> str:
    """The runnable command a stale report puts in parentheses.

    Parsed OUT of the message the report printed, so the test follows what a
    person reads rather than a command it typed itself (WORKER-RULES §28: a
    control that derives the command from the message can see a wrong one; a
    control that hard-codes it cannot).
    """
    m = re.search(r"\(([^()]*state\.py[^()]*)\)", line)
    assert m, f"no state.py remedy in parentheses: {line!r}"
    return m.group(1)


class TestMissingArtifactRemedy:
    """T283: `stale` reported a recorded artifact that has gone MISSING and told
    a person to `--record` it — but `--record` hashes what is in the run folder,
    and item 32 refuses to hash what is gone, so it exits 2. The tool named a
    problem and declined the only action it suggested.

    The fix names the remedy that actually works: a recorded file's bytes are
    KEPT (item 139), so `--restore` puts them back. Proven by following the
    printed command literally, as a spawned process, end to end.
    """

    def test_missing_file_names_a_remedy_followed_end_to_end(self, repo):
        st = L.create(repo, "requirement", "a change")
        rid = st["run_id"]
        folder = L.run_dir(repo, rid)
        (folder / "30-design.md").write_text("the plan\n", encoding="utf-8")
        L.record_artifact(repo, rid, "30-design.md")
        (folder / "30-design.md").unlink()

        report = ST.stale(repo, rid, "P3")
        assert len(report) == 1 and "no longer in the run folder" in report[0], report

        # Follow WHATEVER command the report names — parsed from the message,
        # placeholders substituted the way a person would, then SPAWNED. Split
        # BEFORE substituting so a repo path with spaces stays one argument.
        cmd = _remedy_command(report[0])
        tokens = cmd.split()
        argv = [str(repo) if t == "<repo>" else rid if t == "<id>" else t
                for t in tokens]
        assert argv[0] == "tools/sdlc/state.py", argv
        done = subprocess.run(
            [sys.executable, str(ROOT / argv[0])] + argv[1:],
            capture_output=True, text=True, cwd=str(ROOT), timeout=60)
        assert done.returncode == 0, (
            f"the remedy the report named exits {done.returncode}, so it cannot "
            f"be followed: {done.stdout!r} {done.stderr!r}")
        # End to end: following it puts the artifact back and clears the drift.
        assert (folder / "30-design.md").read_text(encoding="utf-8") == "the plan\n"
        assert ST.stale(repo, rid, "P3") == [], "still stale after the remedy ran"

    def test_missing_directory_is_not_sent_to_a_command_that_fails(self, repo):
        """A directory keeps only its per-file map, not its bytes (item 139), so
        neither `--restore` nor `--record` can put a missing one back. The report
        must name NO state.py command for it — every one would fail — and point
        at where the bytes actually live instead."""
        st = L.create(repo, "requirement", "a change")
        rid = st["run_id"]
        R.deliver(repo, rid, "P4")  # writes + records 40-impl/, a directory
        impl = L.run_dir(repo, rid) / "40-impl"
        for f in impl.iterdir():
            f.unlink()
        impl.rmdir()  # the whole directory is gone -> drift kind 'missing'

        report = ST.stale(repo, rid, "P4")
        assert len(report) == 1 and "no longer in the run folder" in report[0], report
        line = report[0]
        assert not re.search(r"state\.py", line), (
            f"report names a state.py command for a missing directory, but none "
            f"can succeed once its bytes are gone: {line!r}")
        assert "not kept" in line and "branch" in line, (
            f"report does not point at where a missing directory's bytes are: "
            f"{line!r}")
